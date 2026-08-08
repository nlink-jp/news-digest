"""Evaluation profiles — where all domain knowledge lives.

The engine knows no subject area. A profile supplies the axes an article is
scored on, the decision table that turns those scores into a reading
priority, the rubric the agent reads before scoring, the digest layout, and
the shape the corpus's `interests.toml` must have.

Axis IDs are shared vocabulary across profiles. A profile may sharpen what an
axis *means*; repurposing an existing ID would make an accumulated corpus
incomparable with itself, so adding an axis requires a `profile_version` bump
and is recorded in every article written afterwards.

Standard library only.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Fields the decision table may test that are not scored by the agent.
# `credibility` is derived from the source tier: it is a property of where the
# article came from, not a judgement about the article.
DERIVED_FIELDS = {"credibility"}

_OP_RE = re.compile(r"^(>=|<=|==|!=|>|<)\s*(-?\d+)$")

# A profile chain longer than this is a configuration mistake, not a design.
MAX_EXTENDS_DEPTH = 8


class ProfileError(Exception):
    """A profile that cannot be used. The message names the file and the fix."""


@dataclass(frozen=True)
class Axis:
    id: str
    question: str
    min: int
    max: int
    labels: dict[int, str]

    def validate_score(self, value: Any) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ProfileError(f"axis '{self.id}': score must be an integer, got {value!r}")
        if not (self.min <= value <= self.max):
            raise ProfileError(
                f"axis '{self.id}': score {value} outside {self.min}..{self.max}"
            )
        return value


@dataclass(frozen=True)
class Rule:
    when: dict[str, Any]
    priority: str

    def matches(self, values: dict[str, Any]) -> bool:
        return all(_condition_holds(values.get(field), test) for field, test in self.when.items())


def _condition_holds(value: Any, test: Any) -> bool:
    """Evaluate one condition.

    Integers and comparison strings (`">=3"`) test numeric fields; a plain
    string tests equality; a list tests membership. Anything else is rejected
    at load time, not here.
    """
    if value is None:
        return False
    if isinstance(test, list):
        return value in test
    if isinstance(test, int) and not isinstance(test, bool):
        return value == test
    if isinstance(test, str):
        m = _OP_RE.match(test.strip())
        if m is None:
            return value == test
        op, raw = m.group(1), int(m.group(2))
        if not isinstance(value, int) or isinstance(value, bool):
            return False
        return {
            ">=": value >= raw, "<=": value <= raw, "==": value == raw,
            "!=": value != raw, ">": value > raw, "<": value < raw,
        }[op]
    return False


@dataclass(frozen=True)
class DecisionTable:
    rules: tuple[Rule, ...]
    default: str

    def decide(self, values: dict[str, Any]) -> tuple[str, int | None]:
        """Return `(priority, index of the rule that fired)`.

        Rules are evaluated in order and the first match wins, so a table reads
        top-down as "most urgent case first". The index is reported so a digest
        can say which rule produced a verdict.
        """
        for i, rule in enumerate(self.rules):
            if rule.matches(values):
                return rule.priority, i
        return self.default, None


@dataclass(frozen=True)
class Layout:
    sections: tuple[dict[str, Any], ...]
    include_priorities: tuple[str, ...]
    deep_read_priorities: tuple[str, ...]
    max_items: int


@dataclass(frozen=True)
class Profile:
    name: str
    version: int
    priorities: tuple[str, ...]
    axes: tuple[Axis, ...]
    table: DecisionTable
    layout: Layout
    rubric_path: Path
    interests_schema: dict[str, Any]
    lineage: tuple[str, ...]

    @property
    def axis_ids(self) -> tuple[str, ...]:
        return tuple(a.id for a in self.axes)

    def axis(self, axis_id: str) -> Axis:
        for a in self.axes:
            if a.id == axis_id:
                return a
        raise ProfileError(f"profile '{self.name}': no axis '{axis_id}'")

    def rubric(self) -> str:
        return self.rubric_path.read_text(encoding="utf-8")


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{path}: invalid TOML — {exc}") from exc


def _resolve_chain(profiles_dir: Path, name: str) -> list[Path]:
    """Walk `extends` from the named profile up to its root ancestor.

    Returns directories ordered ancestor-first, which is the order their files
    are overlaid in.
    """
    chain: list[Path] = []
    seen: list[str] = []
    current = name
    while True:
        if current in seen:
            raise ProfileError(
                f"profile '{name}': extends cycle — {' -> '.join(seen + [current])}"
            )
        seen.append(current)
        if len(seen) > MAX_EXTENDS_DEPTH:
            raise ProfileError(f"profile '{name}': extends chain deeper than {MAX_EXTENDS_DEPTH}")
        directory = profiles_dir / current
        if not directory.is_dir():
            available = sorted(p.name for p in profiles_dir.iterdir() if p.is_dir())
            raise ProfileError(
                f"profile '{current}' not found in {profiles_dir} (available: {', '.join(available) or 'none'})"
            )
        meta_path = directory / "profile.toml"
        if not meta_path.is_file():
            raise ProfileError(f"{directory}: missing profile.toml")
        chain.append(directory)
        parent = _read_toml(meta_path).get("extends")
        if parent is None:
            break
        current = str(parent)
    chain.reverse()
    return chain


def _overlay(chain: list[Path], filename: str) -> Path | None:
    """The last directory in the chain that provides `filename`.

    A file present in a child replaces its parent's copy whole. Merging their
    contents key-by-key would make a child's table depend on a parent's rule
    ordering, which is exactly the kind of action-at-a-distance a profile is
    supposed to remove.
    """
    found = None
    for directory in chain:
        candidate = directory / filename
        if candidate.is_file():
            found = candidate
    return found


def _require_file(chain: list[Path], filename: str, name: str) -> Path:
    path = _overlay(chain, filename)
    if path is None:
        raise ProfileError(f"profile '{name}': no {filename} anywhere in its extends chain")
    return path


def _load_axes(path: Path) -> tuple[Axis, ...]:
    doc = _read_toml(path)
    raw = doc.get("axis")
    if not isinstance(raw, list) or not raw:
        raise ProfileError(f"{path}: expected at least one [[axis]] table")
    axes: list[Axis] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        where = f"{path}: [[axis]] #{i + 1}"
        if not isinstance(entry, dict):
            raise ProfileError(f"{where}: not a table")
        for key in ("id", "question", "min", "max"):
            if key not in entry:
                raise ProfileError(f"{where}: missing '{key}'")
        axis_id = str(entry["id"])
        if axis_id in seen:
            raise ProfileError(f"{where}: duplicate axis id '{axis_id}'")
        if axis_id in DERIVED_FIELDS:
            raise ProfileError(
                f"{where}: '{axis_id}' is a derived field and cannot be an axis "
                f"— it is computed, not judged"
            )
        seen.add(axis_id)
        lo, hi = int(entry["min"]), int(entry["max"])
        if lo >= hi:
            raise ProfileError(f"{where}: min ({lo}) must be below max ({hi})")
        labels_raw = entry.get("labels") or {}
        if not isinstance(labels_raw, dict):
            raise ProfileError(f"{where}: 'labels' must be a table keyed by score")
        labels: dict[int, str] = {}
        for key, text in labels_raw.items():
            try:
                score = int(key)
            except (TypeError, ValueError):
                raise ProfileError(f"{where}: label key '{key}' is not an integer") from None
            if not (lo <= score <= hi):
                raise ProfileError(f"{where}: label {score} outside {lo}..{hi}")
            labels[score] = str(text)
        axes.append(
            Axis(id=axis_id, question=str(entry["question"]), min=lo, max=hi, labels=labels)
        )
    return tuple(axes)


def _load_table(
    path: Path, axis_ids: set[str], priorities: tuple[str, ...]
) -> DecisionTable:
    doc = _read_toml(path)
    default = doc.get("default")
    if not isinstance(default, str):
        raise ProfileError(f"{path}: missing top-level 'default' priority")
    if default not in priorities:
        raise ProfileError(f"{path}: default '{default}' is not one of {list(priorities)}")

    known = axis_ids | DERIVED_FIELDS
    raw = doc.get("rule") or []
    if not isinstance(raw, list):
        raise ProfileError(f"{path}: [[rule]] must be an array of tables")

    rules: list[Rule] = []
    for i, entry in enumerate(raw):
        where = f"{path}: [[rule]] #{i + 1}"
        if not isinstance(entry, dict):
            raise ProfileError(f"{where}: not a table")
        priority = entry.get("priority")
        if not isinstance(priority, str):
            raise ProfileError(f"{where}: missing 'priority'")
        if priority not in priorities:
            raise ProfileError(f"{where}: priority '{priority}' is not one of {list(priorities)}")
        when = entry.get("when")
        if not isinstance(when, dict) or not when:
            raise ProfileError(f"{where}: 'when' must be a non-empty table")
        for field, test in when.items():
            if field not in known:
                raise ProfileError(
                    f"{where}: tests unknown field '{field}' "
                    f"(axes: {sorted(axis_ids)}; derived: {sorted(DERIVED_FIELDS)})"
                )
            if isinstance(test, str) and _OP_RE.match(test.strip()) is None and not test.strip():
                raise ProfileError(f"{where}: empty condition for '{field}'")
            if not isinstance(test, (str, int, list)) or isinstance(test, bool):
                raise ProfileError(
                    f"{where}: condition for '{field}' must be an integer, "
                    f"a comparison string like \">=2\", a string, or a list"
                )
        rules.append(Rule(when=dict(when), priority=priority))
    return DecisionTable(rules=tuple(rules), default=default)


def _load_layout(path: Path, priorities: tuple[str, ...]) -> Layout:
    doc = _read_toml(path)

    def priority_list(key: str) -> tuple[str, ...]:
        raw = doc.get(key)
        if not isinstance(raw, list) or not raw:
            raise ProfileError(f"{path}: '{key}' must be a non-empty array")
        for item in raw:
            if item not in priorities:
                raise ProfileError(f"{path}: {key} names unknown priority '{item}'")
        return tuple(str(x) for x in raw)

    sections = doc.get("section") or []
    if not isinstance(sections, list):
        raise ProfileError(f"{path}: [[section]] must be an array of tables")
    for i, section in enumerate(sections):
        if not isinstance(section, dict) or "id" not in section or "title" not in section:
            raise ProfileError(f"{path}: [[section]] #{i + 1} needs 'id' and 'title'")

    max_items = doc.get("max_items", 25)
    if not isinstance(max_items, int) or max_items < 1:
        raise ProfileError(f"{path}: 'max_items' must be a positive integer")

    return Layout(
        sections=tuple(dict(s) for s in sections),
        include_priorities=priority_list("include_priorities"),
        deep_read_priorities=priority_list("deep_read_priorities"),
        max_items=max_items,
    )


def load(profiles_dir: Path, name: str) -> Profile:
    """Load `name` from `profiles_dir`, applying its `extends` chain.

    Everything a later step needs to trust is checked here: axis IDs are
    unique and are not derived fields, the decision table tests only known
    fields and produces only declared priorities, and the layout names only
    declared priorities. A profile that loads is a profile that can run.
    """
    profiles_dir = Path(profiles_dir)
    if not profiles_dir.is_dir():
        raise ProfileError(f"{profiles_dir}: no profiles directory")

    chain = _resolve_chain(profiles_dir, name)
    lineage = tuple(d.name for d in chain)

    meta: dict[str, Any] = {}
    for directory in chain:
        meta.update(_read_toml(directory / "profile.toml"))

    version = meta.get("profile_version", 1)
    if not isinstance(version, int) or version < 1:
        raise ProfileError(f"profile '{name}': profile_version must be a positive integer")

    priorities_raw = meta.get("priorities")
    if not isinstance(priorities_raw, list) or not priorities_raw:
        raise ProfileError(f"profile '{name}': profile.toml must declare a non-empty 'priorities'")
    priorities = tuple(str(p) for p in priorities_raw)
    if len(set(priorities)) != len(priorities):
        raise ProfileError(f"profile '{name}': duplicate entries in 'priorities'")

    axes = _load_axes(_require_file(chain, "axes.toml", name))
    table = _load_table(_require_file(chain, "decision-table.toml", name), set(a.id for a in axes), priorities)
    layout = _load_layout(_require_file(chain, "layout.toml", name), priorities)
    rubric_path = _require_file(chain, "rubric.md", name)

    schema_path = _overlay(chain, "interests.schema.toml")
    interests_schema = _read_toml(schema_path) if schema_path else {}

    return Profile(
        name=name,
        version=version,
        priorities=priorities,
        axes=axes,
        table=table,
        layout=layout,
        rubric_path=rubric_path,
        interests_schema=interests_schema,
        lineage=lineage,
    )


def available(profiles_dir: Path) -> list[str]:
    profiles_dir = Path(profiles_dir)
    if not profiles_dir.is_dir():
        return []
    return sorted(p.name for p in profiles_dir.iterdir() if (p / "profile.toml").is_file())
