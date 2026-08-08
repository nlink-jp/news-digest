"""Continuing stories — the matters this corpus is already following.

A story is what makes "no new facts today" expressible. Without one, a
long-running incident and a fresh one look identical, and the digest either
repeats itself daily or drops the thread.

Standard library only.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterator

OPEN_STATUSES = ("open", "dormant")

# How many recent stories to offer as context when scoring. Enough to catch a
# follow-up, small enough that the context stays readable.
CONTEXT_LIMIT = 80


def slugify(text: str, limit: int = 40) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    slug = re.sub(r"[^\w\-]+", "-", normalized, flags=re.UNICODE).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:limit].strip("-")


def path_for(stories_dir: Path, story_id: str) -> Path:
    return Path(stories_dir) / f"{story_id}.json"


def read_all(stories_dir: Path) -> Iterator[dict[str, Any]]:
    stories_dir = Path(stories_dir)
    if not stories_dir.is_dir():
        return
    for path in sorted(stories_dir.glob("story-*.json")):
        try:
            story = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(story, dict) and story.get("id"):
            yield story


def context(stories_dir: Path, limit: int = CONTEXT_LIMIT) -> list[dict[str, Any]]:
    """Open stories, most recently updated first, reduced to what scoring needs.

    Deliberately not the whole story: the agent is deciding whether today's
    article adds anything, which needs the title, the latest state, and when it
    was last touched — not the full timeline.
    """
    live = [s for s in read_all(stories_dir) if s.get("status", "open") in OPEN_STATUSES]
    live.sort(key=lambda s: str(s.get("updated_at") or ""), reverse=True)
    out = []
    for story in live[:limit]:
        timeline = story.get("timeline") or []
        out.append(
            {
                "id": story["id"],
                "title": story.get("title", ""),
                "status": story.get("status", "open"),
                "updated_at": story.get("updated_at"),
                "article_count": len(story.get("article_ids") or []),
                "latest": timeline[-1] if timeline else None,
            }
        )
    return out


def next_id(stories_dir: Path, year: str, taken: set[str] | None = None) -> str:
    """`story-YYYY-NNNN`, continuing the year's sequence.

    `taken` covers ids minted earlier in the same run but not yet written.
    """
    taken = taken or set()
    highest = 0
    prefix = f"story-{year}-"
    for path in Path(stories_dir).glob(f"{prefix}*.json"):
        suffix = path.stem[len(prefix):]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    for story_id in taken:
        if story_id.startswith(prefix) and story_id[len(prefix):].isdigit():
            highest = max(highest, int(story_id[len(prefix):]))
    return f"{prefix}{highest + 1:04d}"
