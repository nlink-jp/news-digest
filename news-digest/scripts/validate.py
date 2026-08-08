#!/usr/bin/env python3
"""Check the summaries and overall picture the agent wrote.

    validate.py --narrative WORK/narrative.json --triage WORK/triage-scored.json \
        --repo <corpus>

This is the second and last artefact the agent produces (the first, triage, is
checked by apply_table.py). Checking it matters because the failure is silent:
a narrative that cannot be read, or that misses an article, produces a digest
that is structurally perfect and missing the summaries the reader wanted.

Every problem is reported at once.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import corpus as corpus_lib
from lib import profile as profile_lib

MIN_OVERVIEW_CHARS = 40


def index_items(narrative: Any) -> tuple[dict[str, dict[str, Any]], list[str]]:
    problems: list[str] = []
    items = narrative.get("items")
    if items is None:
        return {}, problems
    if isinstance(items, dict):
        return {str(k): v for k, v in items.items() if isinstance(v, dict)}, problems
    if not isinstance(items, list):
        problems.append("'items' must be an array or an object keyed by article id")
        return {}, problems
    out: dict[str, dict[str, Any]] = {}
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append(f"items[{i}]: not an object")
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            problems.append(f"items[{i}]: missing 'id'")
            continue
        out[item_id] = item
    return out, problems


def check_anomalies(anomalies: list[Any]) -> list[str]:
    """Every caveat must say what it means for the reader.

    The section sits in a digest someone reads to decide what to do. An entry
    that only describes a mechanism — a feed formatting its excerpts oddly, a
    parser quirk — leaves that reader with nothing to do about it, and buries
    the entries that do. Those observations are maintenance: they belong in
    the run's report to the operator, not in the digest.
    """
    problems: list[str] = []
    for i, anomaly in enumerate(anomalies):
        where = f"anomalies[{i}]"
        if not isinstance(anomaly, dict):
            problems.append(f"{where}: must be an object with 'detail' and 'effect'")
            continue
        if not str(anomaly.get("detail") or "").strip():
            problems.append(f"{where}: 'detail' is empty — state what happened")
        if not str(anomaly.get("effect") or "").strip():
            problems.append(
                f"{where}: 'effect' is empty. Say what this changes for someone reading "
                f"the digest — what is missing, what was judged on thin evidence, what "
                f"they should check elsewhere. If it changes nothing for the reader, it "
                f"is maintenance: drop it here and raise it in your Phase 9 report instead."
            )
    return problems


def check(narrative: Any, scored: list[dict[str, Any]], prof: profile_lib.Profile) -> list[str]:
    if not isinstance(narrative, dict):
        return ["narrative must be a JSON object"]

    problems: list[str] = []
    items, index_problems = index_items(narrative)
    problems.extend(index_problems)

    scored_ids = {s["id"] for s in scored}
    unknown = sorted(set(items) - scored_ids)
    for item_id in unknown:
        problems.append(
            f"items: '{item_id}' was not scored in this run — copy ids from triage-scored.json"
        )

    deep = {s["id"] for s in scored if s.get("priority") in prof.layout.deep_read_priorities}
    for article_id in sorted(deep):
        item = items.get(article_id)
        if item is None:
            problems.append(
                f"no entry for '{article_id}', which is {'/'.join(prof.layout.deep_read_priorities)}. "
                f"Read its body and summarize it, or record why it could not be read."
            )
            continue
        summary = item.get("summary")
        read = bool(item.get("deep_read", False))
        if read and not str(summary or "").strip():
            problems.append(f"'{article_id}': deep_read is true but the summary is empty")
        if not read and summary:
            problems.append(
                f"'{article_id}': carries a summary with deep_read false. A summary that did "
                f"not come from the body is a guess, and guesses are the thing to avoid here."
            )

    unread = [i for i in deep if i in items and not items[i].get("deep_read", False)]
    anomalies = narrative.get("anomalies") or []
    if unread and not anomalies:
        problems.append(
            f"{len(unread)} must-read article(s) were not read, but no anomaly explains why"
        )
    if not isinstance(anomalies, list):
        problems.append("'anomalies' must be an array")
    else:
        problems.extend(check_anomalies(anomalies))

    overview = str(narrative.get("natural_language_summary") or "").strip()
    if not overview:
        problems.append(
            "'natural_language_summary' is empty. Say what is happening in a few sentences; "
            "'nothing notable today' is a legitimate answer, an empty field is not."
        )
    elif len(overview) < MIN_OVERVIEW_CHARS:
        problems.append("'natural_language_summary' is too short to be an overview")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--skill-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--narrative", type=Path, required=True)
    parser.add_argument("--triage", type=Path, required=True)
    args = parser.parse_args()

    try:
        corpus = corpus_lib.load(args.repo)
        prof = profile_lib.load(args.skill_dir / "profiles", corpus.profile_name)
    except (corpus_lib.CorpusError, profile_lib.ProfileError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        narrative = json.loads(args.narrative.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {args.narrative}: {exc}", file=sys.stderr)
        return 1

    try:
        scored = json.loads(args.triage.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {args.triage}: {exc}", file=sys.stderr)
        return 2

    problems = check(narrative, scored, prof)
    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} problem(s). Fix {args.narrative} and run this again.", file=sys.stderr)
        return 1

    print("narrative OK", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
