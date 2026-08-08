"""RSS 2.0, Atom, and RDF (RSS 1.0).

One parser for all three because the differences are shallow — a different
root element and a different place to keep the link — while the shape of what
they carry is the same. Namespaces are matched by local name, since feeds in
the wild declare them inconsistently and half use Dublin Core for dates.

Ported from the predecessor, where the element fallbacks were established by
running against real feeds rather than against the specifications.

Standard library only.
"""

from __future__ import annotations

import html
import re
from typing import Any, Mapping
from xml.etree import ElementTree

from .base import CollectorError, Entry, make_entry

NAME = "rss"

_DOCTYPE_RE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)
_ENTITY_DECL_RE = re.compile(rb"<!ENTITY", re.IGNORECASE)


def reject_unsafe_xml(body: bytes) -> None:
    """Refuse a document carrying a DTD or an entity declaration.

    ElementTree does not resolve external entities, so XXE does not apply, but
    it is vulnerable to expansion attacks built from internal declarations. A
    hardened parser would be a third-party dependency; refusing the construct
    costs nothing instead, because a well-formed RSS or Atom feed has no
    reason to carry one.
    """
    if _ENTITY_DECL_RE.search(body) or _DOCTYPE_RE.search(body[:65536]):
        raise CollectorError("feed carries a DTD or entity declaration; refusing to parse")


def localname(tag: str) -> str:
    """`{http://purl.org/dc/elements/1.1/}date` -> `date`"""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def children_by_name(elem: ElementTree.Element) -> dict[str, list[ElementTree.Element]]:
    out: dict[str, list[ElementTree.Element]] = {}
    for child in elem:
        if isinstance(child.tag, str):
            out.setdefault(localname(child.tag), []).append(child)
    return out


def first_text(kids: dict[str, list[ElementTree.Element]], *names: str) -> str:
    """The first non-empty text among the named children, in preference order."""
    for name in names:
        for el in kids.get(name, []):
            text = "".join(el.itertext()).strip()
            if text:
                return text
    return ""


def extract_link(kids: dict[str, list[ElementTree.Element]], item: ElementTree.Element) -> str:
    """RSS and RDF put the address in `<link>`'s text; Atom puts it in `href`.

    Falls back through the forms feeds actually use when the obvious one is
    absent, because an entry without an address is dropped entirely.
    """
    for el in kids.get("link", []):
        href = el.get("href")
        if href and (el.get("rel") or "alternate").lower() == "alternate":
            return href.strip()
        text = (el.text or "").strip()
        if text:
            return text
    # Atom feeds that offer no alternate link at all.
    for el in kids.get("link", []):
        if el.get("href"):
            return el.get("href", "").strip()
    # RSS 2.0 `<guid isPermaLink="true">`.
    for el in kids.get("guid", []):
        if (el.get("isPermaLink") or "true").lower() == "true":
            text = (el.text or "").strip()
            if text.startswith("http"):
                return text
    # RDF `rdf:about` on the item itself.
    for key, value in item.attrib.items():
        if localname(key) == "about" and value.startswith("http"):
            return value.strip()
    return ""


def _items(root: ElementTree.Element) -> tuple[str, list[ElementTree.Element]]:
    root_name = localname(root.tag)
    if root_name == "feed":
        found = [e for e in root.iter() if localname(e.tag) == "entry"]
        return "atom", found
    if root_name == "RDF":
        return "rss1", [e for e in root.iter() if localname(e.tag) == "item"]
    if root_name == "rss":
        return "rss2", [e for e in root.iter() if localname(e.tag) == "item"]
    return root_name, [e for e in root.iter() if localname(e.tag) in ("item", "entry")]


def parse(body: bytes, source: Mapping[str, Any] | None = None) -> list[Entry]:
    """Parse a feed document into entries.

    Raises `CollectorError` when the document is not a feed. An unparseable
    source has to be loud: returning an empty list would make a broken URL
    look exactly like a day with no news.
    """
    reject_unsafe_xml(body)
    text = body.decode("utf-8", errors="replace").lstrip("﻿ \r\n\t")
    if not text:
        raise CollectorError("empty document")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise CollectorError(f"not well-formed XML: {exc}") from exc

    fmt, item_elems = _items(root)
    if not item_elems and fmt not in ("atom", "rss1", "rss2"):
        raise CollectorError(f"root element <{fmt}> is not a known feed format")

    entries: list[Entry] = []
    for item in item_elems:
        kids = children_by_name(item)
        entry = make_entry(
            url=extract_link(kids, item),
            title=html.unescape(first_text(kids, "title")),
            summary=first_text(kids, "description", "summary", "encoded", "content"),
            published_raw=first_text(kids, "pubDate", "published", "date", "updated", "modified"),
        )
        if entry is not None:
            entries.append(entry)
    return entries
