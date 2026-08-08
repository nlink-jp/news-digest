"""Collector registry.

A source declares a `type`; this maps it to a module with a
`parse(body, source) -> list[Entry]`. Adding a source kind is adding one
module and one line here — the pipeline downstream of collection never reads
the type again.

Collectors parse; they do not fetch. All network access goes through
`lib/http.py`, so a new collector cannot acquire its own idea of timeouts,
size limits, redirects, or politeness.
"""

from __future__ import annotations

from types import ModuleType

from . import jsonfeed, rss
from .base import CollectorError, Entry, make_entry  # noqa: F401  (re-exported)

_REGISTRY: dict[str, ModuleType] = {
    rss.NAME: rss,
    jsonfeed.NAME: jsonfeed,
}

# Feeds are overwhelmingly RSS or Atom, and `rss` reads both. A source that
# says nothing means the common case.
DEFAULT_TYPE = rss.NAME


def available() -> list[str]:
    return sorted(_REGISTRY)


def get(name: str | None) -> ModuleType:
    """Resolve a collector by name.

    An unknown type is an error rather than a fallback to the default: a typo
    that silently parsed as RSS would produce zero entries and look like a
    quiet feed.
    """
    key = (name or DEFAULT_TYPE).strip()
    try:
        return _REGISTRY[key]
    except KeyError:
        raise CollectorError(
            f"unknown collector type '{key}' (available: {', '.join(available())})"
        ) from None
