"""What every collector returns, and the normalization they all share.

A collector's whole job is to turn one fetched document into `Entry` objects.
It does not fetch — `lib/http.py` does that for every source type, so the
politeness and safety rules cannot be bypassed by adding a collector — and it
does not normalize text by hand: `make_entry` is the only way to build an
`Entry`, so two collectors cannot drift into two ideas of what a summary is.

Standard library only.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import records  # noqa: E402


class CollectorError(Exception):
    """A document that cannot be parsed as the declared type.

    Raised rather than returning nothing: a feed that silently yields zero
    entries is indistinguishable from a quiet day, and that is exactly the
    failure this tool exists to make visible.
    """


@dataclass(frozen=True)
class Entry:
    """One article as a collector found it, before origin is attached."""

    url: str
    title: str
    summary: str
    published_at: datetime | None


def make_entry(
    url: str,
    title: str = "",
    summary: str = "",
    published_raw: str = "",
    *,
    published_at: datetime | None = None,
) -> Entry | None:
    """Build an `Entry`, or return None when there is nothing to build.

    An entry with no address cannot be stored, deduplicated, or read, so it is
    dropped here rather than becoming a record with an empty identity.

    Titles are derived from the body when a feed omits them — microblog-shaped
    feeds publish untitled entries, and without this every one of them would
    be dropped later by the minimum-title-length rule.
    """
    url = (url or "").strip()
    if not url:
        return None

    clean_summary = records.strip_html(summary)
    clean_title = records.strip_html(title, limit=300).strip()
    if not clean_title:
        clean_title = records.derive_title(clean_summary)

    when = published_at if published_at is not None else records.parse_date(published_raw)
    return Entry(url=url, title=clean_title, summary=clean_summary, published_at=when)
