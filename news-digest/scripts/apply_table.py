#!/usr/bin/env python3
"""Check the agent's scores and derive a reading priority for each candidate.

    apply_table.py --repo <corpus> --triage WORK/triage.json \
        --candidates WORK/candidates.jsonl --out WORK/triage-scored.json

The agent writes axis scores; this writes the priority, by applying the
profile's decision table. Nothing else in the pipeline may write one.

Every problem in the input is reported at once. Correcting a hundred-candidate
triage one error per run would be a hundred round trips.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import corpus as corpus_lib
from lib import profile as profile_lib
from lib import triage as triage_lib


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--skill-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--triage", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="output JSON (default: stdout)")
    parser.add_argument(
        "--check-only", action="store_true", help="validate without writing an output"
    )
    args = parser.parse_args()

    try:
        corpus = corpus_lib.load(args.repo)
        prof = profile_lib.load(args.skill_dir / "profiles", corpus.profile_name)
    except (corpus_lib.CorpusError, profile_lib.ProfileError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        entries = json.loads(args.triage.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {args.triage}: {exc}", file=sys.stderr)
        return 2

    candidates = [
        json.loads(line)
        for line in args.candidates.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    try:
        scored = triage_lib.apply(entries, candidates, prof)
    except triage_lib.TriageError as exc:
        for problem in exc.problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        print(
            f"\n{len(exc.problems)} problem(s). Fix {args.triage} and run this again — "
            f"there is no need to re-collect.",
            file=sys.stderr,
        )
        return 1

    payload = [item.as_dict(prof) for item in scored]
    counts: dict[str, int] = {}
    for item in payload:
        counts[item["priority"]] = counts.get(item["priority"], 0) + 1

    if not args.check_only:
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)

    summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing scored"
    print(f"{len(payload)} scored: {summary}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
