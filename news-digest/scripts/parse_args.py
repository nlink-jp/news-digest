#!/usr/bin/env python3
"""Turn the skill's argument string into a resolved plan.

    parse_args.py --skill-dir <dir> -- [--repo P] [--since D] [--dry-run] ...

The predecessor documented its arguments in a table and left the agent to
interpret them at read time, which makes the meaning of `--dry-run` depend on
how carefully the table was read. Parsing them deterministically means the
rest of the phases act on resolved values rather than on an interpretation.

Prints one JSON object. Exits non-zero, with the reason, when the target is
not a corpus — this skill never guesses which corpus to operate on.

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
from lib import window as window_lib


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="/news-digest", add_help=False)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-post", action="store_true")
    parser.add_argument("--no-commit", action="store_true")
    return parser


def main() -> int:
    outer = argparse.ArgumentParser(description=__doc__)
    outer.add_argument("--skill-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    outer.add_argument("args", nargs=argparse.REMAINDER)
    outer_args = outer.parse_args()

    raw = list(outer_args.args)
    if raw and raw[0] == "--":
        raw = raw[1:]

    parser = build_parser()
    try:
        parsed, unknown = parser.parse_known_args(raw)
    except SystemExit:
        print("ERROR: could not read the arguments", file=sys.stderr)
        return 2
    if unknown:
        print(f"ERROR: unrecognized argument(s): {' '.join(unknown)}", file=sys.stderr)
        return 2

    try:
        corpus = corpus_lib.load(Path(parsed.repo))
        prof = profile_lib.load(outer_args.skill_dir / "profiles", corpus.profile_name)
        win = window_lib.resolve(parsed.since, parsed.until)
    except (corpus_lib.CorpusError, profile_lib.ProfileError, window_lib.WindowError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # --dry-run is the union, not a third mode: one flag that means "change
    # nothing outside this directory" is easier to reach for correctly than
    # remembering to pass both.
    post = not (parsed.no_post or parsed.dry_run)
    commit = not (parsed.no_commit or parsed.dry_run)

    plan = {
        "repo": str(corpus.root),
        "work_dir": str(corpus.root / ".news-digest-work"),
        "window": win.as_dict(),
        "sources": parsed.source,
        "post": post,
        "commit": commit,
        "profile": {
            "name": prof.name,
            "version": prof.version,
            "lineage": list(prof.lineage),
            "axes": list(prof.axis_ids),
            "rubric_path": str(prof.rubric_path),
            "axes_path": str(prof.rubric_path.parent / "axes.toml"),
            "priorities": list(prof.priorities),
            "deep_read_priorities": list(prof.layout.deep_read_priorities),
            "include_priorities": list(prof.layout.include_priorities),
        },
        "destinations": [
            {
                "id": d.id, "kind": d.kind, "channel": d.channel,
                "thread": d.thread, "broadcast": d.broadcast,
            }
            for d in corpus.destinations
        ],
        "paths": {
            "config": str(corpus.config_dir),
            "data": str(corpus.data_dir),
            "digests": str(corpus.digests_dir),
        },
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
