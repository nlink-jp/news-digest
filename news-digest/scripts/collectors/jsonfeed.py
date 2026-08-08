"""JSON Feed 1.0 / 1.1 — https://jsonfeed.org/version/1.1

Standard library only.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .base import CollectorError, Entry, make_entry

NAME = "jsonfeed"


def _address_like(value: object) -> str:
    text = str(value or "").strip()
    return text if text.startswith(("http://", "https://")) else ""


def parse(body: bytes, source: Mapping[str, Any] | None = None) -> list[Entry]:
    try:
        doc = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CollectorError(f"not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise CollectorError("JSON Feed must be an object")

    items = doc.get("items")
    if items is None:
        raise CollectorError("no 'items' array — not a JSON Feed")
    if not isinstance(items, list):
        raise CollectorError("'items' is not an array")

    entries: list[Entry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # `url` is the article; `external_url` points somewhere else and is
        # not what this feed published. `id` is only an address when it
        # happens to be one — the specification allows any unique string, and
        # treating "3" as a location would mint a record with an identity
        # nothing can resolve.
        entry = make_entry(
            url=str(item.get("url") or _address_like(item.get("id")) or ""),
            title=str(item.get("title") or ""),
            summary=str(item.get("content_html") or item.get("content_text") or item.get("summary") or ""),
            published_raw=str(item.get("date_published") or item.get("date_modified") or ""),
        )
        if entry is not None:
            entries.append(entry)
    return entries
