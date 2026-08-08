#!/usr/bin/env python3
"""Write this run into the corpus: articles, stories, and the seen index.

    merge.py --repo <corpus> --prefiltered WORK/prefiltered.jsonl \
        --triage WORK/triage-scored.json --story-updates-out WORK/story-updates.json

Idempotent. Running it twice with the same input leaves the corpus byte for
byte as it was after the first run, which is what makes a run that failed
halfway safe to simply repeat.

Every collected article is stored, including the ones a rule dropped, with the
verdict attached. They also all enter the seen index — an article the corpus
holds must not be collected and judged a second time.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import corpus as corpus_lib
from lib import seen as seen_lib
from lib import stories as stories_lib


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def partition_date(record: dict[str, Any]) -> str:
    """The day a record files under: when it was published, else when it was
    collected. A record with neither would be unfileable, so today stands in."""
    for field in ("published_at", "collected_at"):
        value = str(record.get(field) or "")
        if len(value) >= 10 and value[4] == "-" and value[7] == "-":
            return value[:10]
    return datetime.now(timezone.utc).date().isoformat()


def merge_articles(path: Path, incoming: Iterable[dict[str, Any]]) -> tuple[int, int]:
    """Replace by id, keep everything else, write in a stable order.

    Replacing rather than appending is what makes a re-run a no-op: the second
    pass writes the same records over the same ids.
    """
    existing = {r["id"]: r for r in read_jsonl(path)}
    added = updated = 0
    for record in incoming:
        if record["id"] in existing:
            if existing[record["id"]] != record:
                updated += 1
        else:
            added += 1
        existing[record["id"]] = record

    rows = sorted(existing.values(), key=lambda r: (str(r.get("published_at") or ""), r["id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    return added, updated


def timeline_entry(record: dict[str, Any], note: str = "") -> dict[str, Any]:
    return {
        "date": partition_date(record),
        "article_id": record["id"],
        "source": (record.get("origin") or {}).get("source_name"),
        "title": record.get("title"),
        "url": record.get("url"),
        "note": note,
    }


def write_story(stories_dir: Path, story: dict[str, Any]) -> None:
    stories_dir.mkdir(parents=True, exist_ok=True)
    stories_lib.path_for(stories_dir, story["id"]).write_text(
        json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_story(stories_dir: Path, story_id: str) -> dict[str, Any] | None:
    path = stories_lib.path_for(stories_dir, story_id)
    if not path.is_file():
        return None
    try:
        story = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return story if isinstance(story, dict) else None


def attach_to_story(story: dict[str, Any], record: dict[str, Any], note: str) -> bool:
    """Add an article to a story unless it is already there. True if it changed."""
    article_ids = story.setdefault("article_ids", [])
    if record["id"] in article_ids:
        return False
    article_ids.append(record["id"])
    story.setdefault("timeline", []).append(timeline_entry(record, note))
    story["timeline"].sort(key=lambda e: (str(e.get("date") or ""), str(e.get("article_id") or "")))
    story["updated_at"] = partition_date(record)
    story["status"] = story.get("status") or "open"
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--prefiltered", type=Path, required=True)
    parser.add_argument("--triage", type=Path, required=True)
    parser.add_argument("--story-updates-out", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        corpus = corpus_lib.load(args.repo)
    except corpus_lib.CorpusError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    records = read_jsonl(args.prefiltered)
    try:
        scored = json.loads(args.triage.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {args.triage}: {exc}", file=sys.stderr)
        return 2

    by_id = {r["id"]: r for r in records}
    scored_by_id = {s["id"]: s for s in scored}

    unknown = sorted(set(scored_by_id) - set(by_id))
    if unknown:
        print(
            f"ERROR: triage references {len(unknown)} article(s) absent from the collection: "
            f"{', '.join(unknown[:5])}",
            file=sys.stderr,
        )
        return 2

    for article_id, verdict in scored_by_id.items():
        by_id[article_id]["triage"] = {
            k: v for k, v in verdict.items() if k not in ("id", "new_story")
        }

    if args.dry_run:
        print(f"dry run: {len(records)} article(s), {len(scored_by_id)} scored", file=sys.stderr)
        return 0

    # --- stories ---
    #
    # Before the articles, because attaching an article to a story sets a
    # field on the article. Writing the articles first and again afterwards
    # would leave a window in which the corpus holds records whose story
    # attachment has been reset to null.
    created: list[dict[str, Any]] = []
    touched: list[dict[str, Any]] = []
    minted: set[str] = set()

    # Which story already holds each article. Without this, re-running mints a
    # second story for every `new_story` in the input, because the request to
    # create one is still there on the second pass — the corpus is what
    # records that it was already honoured.
    membership: dict[str, str] = {}
    for story in stories_lib.read_all(corpus.stories_dir):
        for member in story.get("article_ids") or []:
            membership[str(member)] = story["id"]

    for verdict in scored:
        record = by_id[verdict["id"]]
        note = verdict.get("why", "")

        if record["id"] in membership:
            story_id = membership[record["id"]]
            verdict["story_id"] = story_id
            record["triage"]["story_id"] = story_id
            continue

        new_story = verdict.get("new_story")
        if isinstance(new_story, dict) and new_story.get("title"):
            year = partition_date(record)[:4]
            story_id = stories_lib.next_id(corpus.stories_dir, year, minted)
            minted.add(story_id)
            story = {
                "id": story_id,
                "title": str(new_story["title"]),
                "slug": stories_lib.slugify(str(new_story["title"])),
                "status": "open",
                "created_at": partition_date(record),
                "updated_at": partition_date(record),
                "article_ids": [],
                "timeline": [],
            }
            attach_to_story(story, record, note)
            write_story(corpus.stories_dir, story)
            created.append({"id": story_id, "title": story["title"], "article_id": record["id"]})
            membership[record["id"]] = story_id
            verdict["story_id"] = story_id
            record["triage"]["story_id"] = story_id
            continue

        story_id = verdict.get("story_id")
        if not story_id:
            continue
        story = load_story(corpus.stories_dir, story_id)
        if story is None:
            print(
                f"WARNING: triage references unknown story '{story_id}' — leaving the "
                f"article unattached",
                file=sys.stderr,
            )
            record["triage"]["story_id"] = None
            continue
        if attach_to_story(story, record, note):
            write_story(corpus.stories_dir, story)
            membership[record["id"]] = story_id
            touched.append(
                {"id": story_id, "title": story.get("title"), "article_id": record["id"]}
            )

    # --- articles ---
    by_day: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_day.setdefault(partition_date(record), []).append(record)
    added = updated = 0
    for day, rows in sorted(by_day.items()):
        a, u = merge_articles(corpus.article_file(day), rows)
        added += a
        updated += u

    # --- seen index ---
    by_year: dict[str, list[seen_lib.Row]] = {}
    for record in records:
        year = seen_lib.year_of(partition_date(record))
        by_year.setdefault(year, []).append(
            seen_lib.Row(
                id=record["id"],
                canonical_key=record.get("canonical_key", ""),
                first_seen_at=record.get("collected_at", ""),
                source_id=(record.get("origin") or {}).get("source_id", ""),
            )
        )
    indexed = 0
    for year, rows in sorted(by_year.items()):
        indexed += seen_lib.append(corpus.seen_file(year), rows)

    if args.story_updates_out:
        args.story_updates_out.parent.mkdir(parents=True, exist_ok=True)
        args.story_updates_out.write_text(
            json.dumps({"created": created, "updated": touched}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )

    print(
        f"stored {added} new article(s), {updated} updated; "
        f"{len(created)} story created, {len(touched)} updated; {indexed} newly indexed",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
