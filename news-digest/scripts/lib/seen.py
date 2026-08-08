"""The seen index — which articles this corpus already holds.

TSV split by year, so a run appends a handful of lines instead of rewriting
every line it has ever written. The predecessor rewrote one flat file each
run, which made every commit's diff the size of the whole corpus.

Standard library only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator, NamedTuple

HEADER = ("id", "canonical_key", "first_seen_at", "source_id")


class Row(NamedTuple):
    id: str
    canonical_key: str
    first_seen_at: str
    source_id: str


def _escape(value: str) -> str:
    # Tabs and newlines are the format; nothing else needs quoting.
    return (value or "").replace("\t", " ").replace("\n", " ").replace("\r", " ")


def read(path: Path) -> Iterator[Row]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            yield Row(*parts[:4])


def load(paths: Iterable[Path]) -> dict[str, Row]:
    """Every article ever recorded, keyed by id.

    Later files win on a duplicate id, which cannot normally happen; when it
    does, the newest statement of a fact is the one to keep.
    """
    index: dict[str, Row] = {}
    for path in paths:
        for row in read(path):
            index[row.id] = row
    return index


def append(path: Path, rows: Iterable[Row]) -> int:
    """Add rows, skipping ids the file already carries.

    Idempotent by construction: running the same merge twice adds nothing the
    second time, which is what makes a failed run safe to repeat.
    """
    rows = list(rows)
    if not rows:
        return 0
    existing = {row.id for row in read(path)}
    fresh = []
    seen_here: set[str] = set()
    for row in rows:
        if row.id in existing or row.id in seen_here:
            continue
        seen_here.add(row.id)
        fresh.append(row)
    if not fresh:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", encoding="utf-8") as fh:
        if new_file:
            fh.write("#" + "\t".join(HEADER) + "\n")
        for row in fresh:
            fh.write("\t".join(_escape(v) for v in row) + "\n")
    return len(fresh)


def year_of(timestamp: str) -> str:
    """The partition a row belongs to. Falls back to a stable bucket rather
    than raising: an unparseable timestamp must not lose the row."""
    text = (timestamp or "").strip()
    return text[:4] if len(text) >= 4 and text[:4].isdigit() else "unknown"
