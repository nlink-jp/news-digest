"""What the agent wrote, checked against what it was asked to score.

The agent produces axis scores. Priority is derived here from the profile's
decision table, which is why the triage schema has no priority field: a score
and its verdict cannot disagree if only one of them is written down.

Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from . import profile as profile_lib

# Tier is a property of the source, so credibility is read off, not judged.
# Kept as an identity mapping rather than dropped: naming it makes the derived
# field explicit where a decision table refers to it.
CREDIBILITY_FROM_TIER = {
    "primary": "primary",
    "analysis": "analysis",
    "secondary": "secondary",
    "community": "community",
}
UNKNOWN_CREDIBILITY = "community"


class TriageError(Exception):
    """Triage that cannot be applied. Carries every problem, not just the first."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


@dataclass(frozen=True)
class Scored:
    id: str
    axes: dict[str, int]
    priority: str
    credibility: str
    why: str
    rule_index: int | None
    story_id: str | None
    new_story: dict[str, Any] | None

    def as_dict(self, prof: profile_lib.Profile) -> dict[str, Any]:
        return {
            "id": self.id,
            "profile": prof.name,
            "profile_version": prof.version,
            "axes": dict(self.axes),
            "credibility": self.credibility,
            "priority": self.priority,
            "decided_by": (
                f"rule {self.rule_index + 1}" if self.rule_index is not None else "default"
            ),
            "why": self.why,
            "story_id": self.story_id,
            "new_story": self.new_story,
        }


def credibility_of(record: Mapping[str, Any]) -> str:
    tier = (record.get("origin") or {}).get("tier")
    return CREDIBILITY_FROM_TIER.get(str(tier), UNKNOWN_CREDIBILITY)


def apply(
    entries: Any, candidates: Sequence[Mapping[str, Any]], prof: profile_lib.Profile
) -> list[Scored]:
    """Validate the agent's scores and derive a priority for each candidate.

    Every problem found is reported together. Returning after the first one
    would make correcting a hundred-candidate triage a hundred round trips.
    """
    problems: list[str] = []

    if not isinstance(entries, list):
        raise TriageError(["triage must be a JSON array of entries"])

    by_id = {c["id"]: c for c in candidates}
    axis_ids = set(prof.axis_ids)

    seen: dict[str, dict[str, Any]] = {}
    for i, entry in enumerate(entries):
        where = f"entry #{i + 1}"
        if not isinstance(entry, dict):
            problems.append(f"{where}: not an object")
            continue
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            problems.append(f"{where}: missing 'id'")
            continue
        if entry_id not in by_id:
            problems.append(
                f"{where}: id '{entry_id}' is not one of the candidates — "
                f"copy ids from triage-input.json rather than constructing them"
            )
            continue
        if entry_id in seen:
            problems.append(f"{where}: duplicate entry for '{entry_id}'")
            continue
        seen[entry_id] = entry

        if "priority" in entry:
            problems.append(
                f"{where}: carries a 'priority'. Priority is derived from the profile's "
                f"decision table; write the axis scores only"
            )

        axes = entry.get("axes")
        if not isinstance(axes, dict):
            problems.append(f"{where}: missing 'axes' object")
            continue
        missing = sorted(axis_ids - set(axes))
        extra = sorted(set(axes) - axis_ids)
        if missing:
            problems.append(f"{where}: no score for {', '.join(missing)}")
        if extra:
            problems.append(f"{where}: scores unknown axis {', '.join(extra)}")
        for axis in prof.axes:
            if axis.id in axes:
                try:
                    axis.validate_score(axes[axis.id])
                except profile_lib.ProfileError as exc:
                    problems.append(f"{where}: {exc}")

        if not str(entry.get("why", "")).strip():
            problems.append(f"{where}: 'why' is empty — state a fact from the article")

    unscored = sorted(set(by_id) - set(seen))
    if unscored:
        shown = ", ".join(unscored[:5]) + (" …" if len(unscored) > 5 else "")
        problems.append(
            f"{len(unscored)} candidate(s) have no entry: {shown}. "
            f"Every candidate needs one; an unscored article is silently lost."
        )

    if problems:
        raise TriageError(problems)

    out: list[Scored] = []
    for entry_id, entry in seen.items():
        record = by_id[entry_id]
        axes = {a.id: int(entry["axes"][a.id]) for a in prof.axes}
        credibility = credibility_of(record)
        priority, rule_index = prof.table.decide({**axes, "credibility": credibility})
        new_story = entry.get("new_story")
        out.append(
            Scored(
                id=entry_id,
                axes=axes,
                priority=priority,
                credibility=credibility,
                why=str(entry["why"]).strip(),
                rule_index=rule_index,
                story_id=(str(entry["story_id"]) if entry.get("story_id") else None),
                new_story=(new_story if isinstance(new_story, dict) else None),
            )
        )
    return out
