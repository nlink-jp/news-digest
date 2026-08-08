#!/usr/bin/env python3
"""Assemble the digest from this run's artefacts.

    build_digest.py --repo <corpus> --prefiltered ... --triage ... \
        --narrative ... --story-updates ... --prefilter-summary ... \
        --collect-stats ... --out WORK/digest.json

Everything here is computed. The agent contributed axis scores (Phase 3) and
the summaries and overall picture (Phase 5); counts, stale-topic derivation,
ordering, and section layout are arithmetic over those, and arithmetic done by
a model is arithmetic that can be wrong in ways nobody checks.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import corpus as corpus_lib
from lib import profile as profile_lib
from lib import stories as stories_lib

# The axis whose zero means "nothing new". Named here rather than assumed
# throughout, so a profile that renames it fails visibly at one place.
NOVELTY_AXIS = "novelty"


class InputError(Exception):
    """An artefact that was named but cannot be read."""


def read_json(path: Path | None, default: Any, *, required: bool = False) -> Any:
    """Read an artefact, or fall back.

    A file that was named on the command line but cannot be parsed raises
    rather than falling back. Defaulting there would produce a digest that is
    structurally perfect and quietly missing whatever that file held — the
    summaries, most of the time.
    """
    if path is None:
        return default
    if not Path(path).is_file():
        if required:
            raise InputError(f"{path}: not found")
        return default
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"{path}: {exc}") from exc


def read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not Path(path).is_file():
        return []
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def narrative_index(narrative: Any) -> dict[str, dict[str, Any]]:
    """Accept either a list of items or an object keyed by id."""
    items = (narrative or {}).get("items") if isinstance(narrative, dict) else None
    if isinstance(items, dict):
        return {str(k): dict(v) for k, v in items.items() if isinstance(v, dict)}
    if isinstance(items, list):
        return {str(i["id"]): dict(i) for i in items if isinstance(i, dict) and i.get("id")}
    return {}


def build_items(
    scored: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    summaries: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out = []
    for verdict in scored:
        record = records.get(verdict["id"])
        if record is None:
            continue
        extra = summaries.get(verdict["id"], {})
        origin = record.get("origin") or {}
        out.append(
            {
                "id": verdict["id"],
                "title": record.get("title"),
                "url": record.get("url"),
                "source": origin.get("source_name"),
                "source_id": origin.get("source_id"),
                "tier": origin.get("tier"),
                "published_at": record.get("published_at"),
                "priority": verdict["priority"],
                "axes": verdict.get("axes", {}),
                "credibility": verdict.get("credibility"),
                "why": verdict.get("why"),
                "story_id": verdict.get("story_id"),
                "summary": extra.get("summary"),
                "deep_read": bool(extra.get("deep_read", False)),
            }
        )
    return out


def order(items: list[dict[str, Any]], axis_ids: list[str]) -> list[dict[str, Any]]:
    """Most consequential first, then most recent.

    The sort key is every axis in the order the profile declares them, which
    keeps the ordering meaningful for a profile whose axes are not the base
    three without the engine knowing what any of them mean.
    """
    def key(item: dict[str, Any]):
        axes = item.get("axes") or {}
        return tuple(-int(axes.get(a, 0)) for a in axis_ids) + (
            str(item.get("published_at") or ""),
        )

    return sorted(items, key=key)


def stale_topics(
    items: list[dict[str, Any]], stories: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Stories that appeared today with nothing new in any of their articles.

    This is the section that gives a reader a reason *not* to read something.
    A story qualifies only when every one of today's articles for it scored
    zero novelty — one article with a new fact makes the whole story live.
    """
    by_story: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if item.get("story_id"):
            by_story.setdefault(item["story_id"], []).append(item)

    out = []
    for story_id, members in sorted(by_story.items()):
        if not all((m.get("axes") or {}).get(NOVELTY_AXIS) == 0 for m in members):
            continue
        story = stories.get(story_id) or {}
        out.append(
            {
                "story_id": story_id,
                "title": story.get("title") or story_id,
                "article_count": len(members),
                "sources": sorted({m.get("source") for m in members if m.get("source")}),
                "reason": members[0].get("why") or "no new facts in today's coverage",
            }
        )
    return out


def build_sections(
    layout: profile_lib.Layout,
    items: list[dict[str, Any]],
    story_updates: dict[str, Any],
    stale: list[dict[str, Any]],
    stats: dict[str, Any],
    anomalies: list[Any],
) -> tuple[list[dict[str, Any]], int]:
    """Fill the profile's layout. Item sections share one budget."""
    shown = 0
    sections = []
    for spec in layout.sections:
        kind = spec.get("kind", "items")
        section = {"id": spec["id"], "title": spec["title"], "kind": kind}
        if kind == "items":
            wanted = [i for i in items if i["priority"] in (spec.get("priorities") or [])]
            room = max(0, layout.max_items - shown)
            section["items"] = wanted[:room]
            section["withheld"] = len(wanted) - len(section["items"])
            shown += len(section["items"])
            if spec.get("empty_text"):
                section["empty_text"] = spec["empty_text"]
        elif kind == "story_updates":
            section["created"] = story_updates.get("created", [])
            section["updated"] = story_updates.get("updated", [])
        elif kind == "stale_topics":
            section["topics"] = stale
        elif kind == "stats":
            section["stats"] = stats
        elif kind == "anomalies":
            section["anomalies"] = anomalies
        sections.append(section)
    return sections, shown


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--skill-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--prefiltered", type=Path, required=True)
    parser.add_argument("--triage", type=Path, required=True)
    parser.add_argument("--narrative", type=Path)
    parser.add_argument("--story-updates", type=Path)
    parser.add_argument("--prefilter-summary", type=Path)
    parser.add_argument("--collect-stats", type=Path)
    parser.add_argument("--date", help="digest date (default: today)")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    try:
        corpus = corpus_lib.load(args.repo)
        prof = profile_lib.load(args.skill_dir / "profiles", corpus.profile_name)
    except (corpus_lib.CorpusError, profile_lib.ProfileError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        records = {r["id"]: r for r in read_jsonl(args.prefiltered)}
        scored = read_json(args.triage, [], required=True)
        narrative = read_json(args.narrative, {})
        story_updates = read_json(args.story_updates, {"created": [], "updated": []})
        prefilter_summary = read_json(args.prefilter_summary, {})
        collect_stats = read_json(args.collect_stats, {})
    except InputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    items = order(build_items(scored, records, narrative_index(narrative)), list(prof.axis_ids))

    stories = {s["id"]: s for s in stories_lib.read_all(corpus.stories_dir)}
    stale = stale_topics(items, stories)

    by_priority: dict[str, int] = {}
    for item in items:
        by_priority[item["priority"]] = by_priority.get(item["priority"], 0) + 1

    totals = collect_stats.get("totals", {})
    stats = {
        "collected": totals.get("collected", prefilter_summary.get("collected", 0)),
        "candidates": prefilter_summary.get("candidates", len(items)),
        "evaluated": len(items),
        "by_priority": dict(sorted(by_priority.items())),
        "dropped_by_rule": prefilter_summary.get("dropped_by_rule", {}),
        "overflowed": prefilter_summary.get("overflowed", 0),
        "sources_ok": totals.get("sources_ok", 0),
        "source_errors": collect_stats.get("errors", []),
        "silent_sources": collect_stats.get("silent_sources", []),
        "gaps": collect_stats.get("gaps", []),
        "disabled_sources": collect_stats.get("disabled_sources", []),
        "window": collect_stats.get("window", {}),
    }

    anomalies = list(narrative.get("anomalies") or []) if isinstance(narrative, dict) else []
    # Facts the collector observed are anomalies whether or not the agent
    # noticed them. Reporting them only when it did would make the section a
    # measure of attention rather than of what happened.
    for source_id in stats["gaps"]:
        anomalies.append(
            {
                "kind": "collection_gap",
                "source": source_id,
                "detail": "the window opened after the newest article already seen from this "
                          "source; anything published in between is gone from the feed",
            }
        )
    for source_id in stats["silent_sources"]:
        anomalies.append(
            {
                "kind": "silent_source",
                "source": source_id,
                "detail": "the feed answered but had nothing in the window",
            }
        )

    sections, shown = build_sections(
        prof.layout, items, story_updates, stale, stats, anomalies
    )

    digest = {
        "date": args.date or datetime.now().date().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": prof.name,
        "profile_version": prof.version,
        "natural_language_summary": (
            narrative.get("natural_language_summary") if isinstance(narrative, dict) else None
        ),
        "sections": sections,
        "stats": stats,
        "anomalies": anomalies,
        "stale_topics": stale,
        "story_updates": story_updates,
        "items_shown": shown,
        "items_total": len(items),
    }

    text = json.dumps(digest, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)

    must = by_priority.get("must_read", 0)
    print(
        f"digest {digest['date']}: {shown} item(s) shown of {len(items)}, "
        f"{must} must-read, {len(stale)} stale topic(s)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
