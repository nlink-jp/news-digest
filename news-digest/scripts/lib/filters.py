"""Rule-based prefiltering — `config/filters.toml` in a corpus.

The purpose is to spend judgement on articles that need it, not to make the
judgement. Anything a regular expression cannot settle is passed through.

Rules address *classes* of sources — a category, a tier — rather than listing
feed IDs, so adding a feed means labelling it once instead of editing every
rule that should apply to it. Listing IDs is still available for the genuine
exception.

Standard library only.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

DEFAULT_MAX_CANDIDATES = 120
DEFAULT_MIN_TITLE_CHARS = 8


class FilterError(Exception):
    """A filter set that cannot be applied. The message names the fix."""


def _compile(patterns: Any, where: str) -> list[re.Pattern[str]]:
    if patterns is None:
        return []
    if not isinstance(patterns, list):
        raise FilterError(f"{where}: expected an array of regular expressions")
    out = []
    for pattern in patterns:
        try:
            out.append(re.compile(str(pattern)))
        except re.error as exc:
            raise FilterError(f"{where}: invalid regular expression {pattern!r} — {exc}") from exc
    return out


@dataclass(frozen=True)
class Selector:
    """Which articles a rule applies to. Empty means all of them."""

    sources: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    tiers: frozenset[str] = frozenset()

    @property
    def universal(self) -> bool:
        return not (self.sources or self.categories or self.tiers)

    def matches(self, origin: Mapping[str, Any]) -> bool:
        if self.universal:
            return True
        return (
            origin.get("source_id") in self.sources
            or origin.get("category") in self.categories
            or origin.get("tier") in self.tiers
        )


@dataclass(frozen=True)
class Rule:
    id: str
    reason: str
    selector: Selector
    title_regex: list[re.Pattern[str]] = field(default_factory=list)
    url_regex: list[re.Pattern[str]] = field(default_factory=list)

    def matches(self, record: Mapping[str, Any]) -> bool:
        if not self.selector.matches(record.get("origin") or {}):
            return False
        title = record.get("title") or ""
        url = record.get("url") or ""
        return any(p.search(title) for p in self.title_regex) or any(
            p.search(url) for p in self.url_regex
        )


@dataclass(frozen=True)
class Filters:
    keep: list[re.Pattern[str]]
    gate: Selector
    rules: tuple[Rule, ...]
    max_candidates: int
    min_title_chars: int

    def rescued(self, record: Mapping[str, Any]) -> bool:
        """A keep pattern outranks every rule.

        Noisy feeds carry the occasional article that matters, and a gate or a
        noise rule would otherwise bury it. This is the escape hatch that makes
        aggressive rules safe to write.
        """
        title = record.get("title") or ""
        return any(p.search(title) for p in self.keep)

    def gated(self, record: Mapping[str, Any]) -> bool:
        """True when this source only contributes articles a keep pattern hits."""
        if self.gate.universal:
            return False
        return self.gate.matches(record.get("origin") or {})


def _selector(table: Mapping[str, Any], where: str, keys=("sources", "categories", "tiers")) -> Selector:
    values = {}
    for key in keys:
        raw = table.get(key)
        if raw is None:
            values[key] = frozenset()
            continue
        if not isinstance(raw, list):
            raise FilterError(f"{where}: '{key}' must be an array")
        values[key] = frozenset(str(x) for x in raw)
    return Selector(**values)


def load(path: Path, known_sources: set[str] | None = None) -> Filters:
    """Read and validate a filter set.

    An empty or absent file is valid and filters nothing — a corpus that wants
    every article evaluated is a legitimate configuration, not a mistake.
    """
    path = Path(path)
    doc: dict[str, Any] = {}
    if path.is_file():
        try:
            with path.open("rb") as fh:
                doc = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise FilterError(f"{path}: invalid TOML — {exc}") from exc

    keep = _compile((doc.get("keep") or {}).get("title_regex"), f"{path}: [keep] title_regex")

    gate_table = doc.get("gate") or {}
    gate = _selector(
        {
            "sources": gate_table.get("keep_only_sources"),
            "categories": gate_table.get("keep_only_categories"),
            "tiers": gate_table.get("keep_only_tiers"),
        },
        f"{path}: [gate]",
    )
    if gate.sources and not keep:
        raise FilterError(
            f"{path}: [gate] names sources but [keep] has no patterns — "
            f"every article from those sources would be dropped"
        )

    raw_rules = doc.get("rule") or []
    if not isinstance(raw_rules, list):
        raise FilterError(f"{path}: [[rule]] must be an array of tables")

    rules: list[Rule] = []
    seen_ids: set[str] = set()
    for i, entry in enumerate(raw_rules):
        where = f"{path}: [[rule]] #{i + 1}"
        if not isinstance(entry, dict):
            raise FilterError(f"{where}: not a table")
        rule_id = str(entry.get("id", "")).strip()
        if not rule_id:
            raise FilterError(f"{where}: missing 'id'")
        if rule_id in seen_ids:
            raise FilterError(f"{where}: duplicate rule id '{rule_id}'")
        seen_ids.add(rule_id)
        title_regex = _compile(entry.get("title_regex"), f"{where} title_regex")
        url_regex = _compile(entry.get("url_regex"), f"{where} url_regex")
        if not title_regex and not url_regex:
            raise FilterError(f"{where}: needs at least one of title_regex or url_regex")
        rules.append(
            Rule(
                id=rule_id,
                reason=str(entry.get("reason", "")).strip() or rule_id,
                selector=_selector(entry, where),
                title_regex=title_regex,
                url_regex=url_regex,
            )
        )

    named = set(gate.sources)
    for rule in rules:
        named |= rule.selector.sources
    if "*" in named:
        raise FilterError(
            f"{path}: 'sources = [\"*\"]' is not a wildcard. A rule with no selector "
            f"already applies to every article — remove the line."
        )
    if known_sources is not None:
        unknown = sorted(named - known_sources)
        if unknown:
            raise FilterError(
                f"{path}: names source id(s) that do not exist in sources.toml: "
                f"{', '.join(unknown)}"
            )

    limits = doc.get("limits") or {}
    max_candidates = limits.get("max_candidates", DEFAULT_MAX_CANDIDATES)
    min_title_chars = limits.get("min_title_chars", DEFAULT_MIN_TITLE_CHARS)
    for name, value in (("max_candidates", max_candidates), ("min_title_chars", min_title_chars)):
        if not isinstance(value, int) or value < 1:
            raise FilterError(f"{path}: [limits] {name} must be a positive integer")

    return Filters(
        keep=keep,
        gate=gate,
        rules=tuple(rules),
        max_candidates=max_candidates,
        min_title_chars=min_title_chars,
    )
