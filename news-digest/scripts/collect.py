#!/usr/bin/env python3
"""Fetch every enabled source and write normalized article records.

    collect.py --repo <corpus> --out WORK/collected.jsonl \
               --stats-out WORK/collect-stats.json [--since ...] [--until ...]

One failing source never stops the run — but it is never hidden either. Every
source appears in the stats with an outcome, so "collected nothing" can always
be told apart from "nothing happened".

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collectors
from lib import corpus as corpus_lib
from lib import http, records, sources as sources_lib, state as state_lib, window as window_lib


@dataclass
class SourceResult:
    """One source's outcome, kept apart from the shared state so the fetches
    can run concurrently and the state is applied in one place afterwards."""

    source: sources_lib.Source
    status: str
    entries: list[Any] = field(default_factory=list)
    fetched: int = 0
    http_status: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    newest_published: str | None = None
    oldest_published: str | None = None
    error_kind: str | None = None
    error_message: str | None = None


def fetch_source(
    source: sources_lib.Source,
    client: http.HttpClient,
    st: state_lib.SourceState,
    *,
    conditional: bool = True,
) -> SourceResult:
    try:
        headers = source.auth.resolve() if source.auth else None
        response = client.get(
            source.url,
            etag=st.etag if conditional else None,
            last_modified=st.last_modified if conditional else None,
            accept_language=source.language_header(),
            headers=headers,
        )
    except (http.HttpError, sources_lib.SourceError) as exc:
        kind = getattr(exc, "kind", "config")
        return SourceResult(
            source=source, status="error", error_kind=kind, error_message=str(exc),
            http_status=getattr(exc, "status", None),
        )

    if response.not_modified:
        return SourceResult(source=source, status="not_modified", http_status=304)

    try:
        entries = collectors.get(source.type).parse(response.body, source.origin())
    except collectors.CollectorError as exc:
        return SourceResult(
            source=source, status="error", error_kind="unparseable", error_message=str(exc),
            http_status=response.status,
        )

    # Both ends of what the feed currently offers. The oldest is what makes a
    # gap detectable: a feed that no longer reaches back to our marker has
    # rolled past it, and those items are gone.
    published = [e.published_at.isoformat() for e in entries if e.published_at]
    return SourceResult(
        source=source,
        status="ok",
        entries=entries,
        fetched=len(entries),
        http_status=response.status,
        etag=response.etag,
        last_modified=response.last_modified,
        newest_published=max(published) if published else None,
        oldest_published=min(published) if published else None,
    )


def to_record(entry: Any, source: sources_lib.Source, collected_at: str) -> dict[str, Any]:
    key = records.canonical_key(entry.url)
    return {
        "id": records.article_id(key),
        "schema_version": corpus_lib.SCHEMA_WRITE_VERSION,
        "canonical_key": key,
        "url": entry.url,
        "title": entry.title,
        "summary": entry.summary or None,
        "published_at": entry.published_at.isoformat() if entry.published_at else None,
        "collected_at": collected_at,
        "origin": source.origin(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--since", help="ISO date/time, or 'all' for no lower bound")
    parser.add_argument("--until", help="ISO date/time (default: now)")
    parser.add_argument("--source", action="append", default=[], help="source id (repeatable)")
    parser.add_argument("--out", type=Path, help="output JSONL (default: stdout)")
    parser.add_argument("--stats-out", type=Path, help="per-source outcomes as JSON")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--no-conditional", action="store_true",
        help="ignore stored validators and re-download every source",
    )
    parser.add_argument(
        "--keep-work", action="store_true",
        help="do not clear the work directory first (to inspect a previous run)",
    )
    args = parser.parse_args()

    try:
        corpus = corpus_lib.load(args.repo)
        all_sources = sources_lib.load(
            corpus.sources_file, tuple(collectors.available()), collectors.DEFAULT_TYPE
        )
        chosen, skipped = sources_lib.select(all_sources, args.source)
        win = window_lib.resolve(args.since, args.until)
    except (corpus_lib.CorpusError, sources_lib.SourceError, window_lib.WindowError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not chosen:
        print("ERROR: no sources selected", file=sys.stderr)
        return 2

    if not args.keep_work:
        # A run starts from an empty scratch directory. Anything left by the
        # last one is either already persisted or was abandoned, and either way
        # must not be mistaken for this run's output.
        cleared = corpus.reset_work_dir()
        print(f"work directory cleared: {cleared}", file=sys.stderr)

    store = state_lib.Store.load(corpus.state_file)
    client = http.HttpClient()
    collected_at = datetime.now(timezone.utc).isoformat()

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = list(
            pool.map(
                lambda s: fetch_source(
                    s, client, store.get(s.id), conditional=not args.no_conditional
                ),
                chosen,
            )
        )

    out_records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    duplicates = 0
    stats_sources: list[dict[str, Any]] = []

    for result in results:
        source = result.source
        st = store.get(source.id)
        gap = window_lib.rolled_past(result.oldest_published, st.last_seen_published_at)
        # The whole page the feed served fell inside the window, so the window
        # is wider than the feed's reach and there may have been more it never
        # showed. Weaker than a confirmed gap — it says "cannot tell", not
        # "lost" — so it is reported to the operator, not to the reader.
        saturated = result.status == "ok" and result.fetched > 0

        in_window = 0
        for entry in result.entries:
            if not win.contains(entry.published_at):
                continue
            in_window += 1
            record = to_record(entry, source, collected_at)
            if record["id"] in seen_ids:
                duplicates += 1
                continue
            seen_ids.add(record["id"])
            out_records.append(record)

        if result.status == "ok":
            st.record_success(fetched_at=collected_at, newest_published=result.newest_published)
        elif result.status == "not_modified":
            st.record_not_modified(fetched_at=collected_at)
        else:
            st.record_error(fetched_at=collected_at, kind=result.error_kind or "unknown")
        if result.status == "ok":
            st.etag = result.etag
            st.last_modified = result.last_modified

        stats_sources.append(
            {
                "id": source.id,
                "name": source.name,
                "type": source.type,
                "tier": source.tier,
                "status": result.status,
                "http_status": result.http_status,
                "fetched": result.fetched,
                "in_window": in_window,
                "error_kind": result.error_kind,
                "error": result.error_message,
                "gap_detected": gap,
                "saturated": saturated and in_window == result.fetched,
                "probably_dead": st.probably_dead,
                "stale_days": (
                    round(age, 1) if (age := window_lib.days_since(st.last_seen_published_at)) else None
                ),
                "stale": bool(age is not None and age > source.stale_after_days),
                "last_seen_published_at": st.last_seen_published_at,
            }
        )

    store.save()

    out_records.sort(key=lambda r: (r["published_at"] or "", r["id"]), reverse=True)
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out_records)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)

    stats = {
        "window": win.as_dict(),
        "collected_at": collected_at,
        "totals": {
            "sources_selected": len(chosen),
            "sources_ok": sum(1 for s in stats_sources if s["status"] == "ok"),
            "sources_not_modified": sum(1 for s in stats_sources if s["status"] == "not_modified"),
            "sources_error": sum(1 for s in stats_sources if s["status"] == "error"),
            "fetched": sum(s["fetched"] for s in stats_sources),
            "in_window": sum(s["in_window"] for s in stats_sources),
            "collected": len(out_records),
            "cross_source_duplicates": duplicates,
        },
        "sources": sorted(stats_sources, key=lambda s: s["id"]),
        "disabled_sources": sorted(s.id for s in skipped),
        "gaps": sorted(s["id"] for s in stats_sources if s["gap_detected"]),
        "errors": sorted(s["id"] for s in stats_sources if s["status"] == "error"),
        # Maintenance, not reader-facing: a feed frozen since 2022 is not
        # missing today's news, it has none. Same for saturation, which only
        # says the window may have been wider than the feed's reach.
        "stale_sources": sorted(s["id"] for s in stats_sources if s["stale"]),
        "saturated_sources": sorted(s["id"] for s in stats_sources if s["saturated"]),
        "silent_sources": sorted(
            s["id"] for s in stats_sources if s["status"] == "ok" and s["in_window"] == 0
        ),
    }
    if args.stats_out:
        args.stats_out.parent.mkdir(parents=True, exist_ok=True)
        args.stats_out.write_text(
            json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    totals = stats["totals"]
    print(
        f"collected {totals['collected']} from {totals['sources_ok']} source(s); "
        f"{totals['sources_not_modified']} unchanged, {totals['sources_error']} failed",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
