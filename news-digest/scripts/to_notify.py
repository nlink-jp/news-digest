#!/usr/bin/env python3
"""Split the digest into messages a destination will accept.

    to_notify.py digests/2026-08-08.json --repo <corpus> --out-prefix WORK/msg

Writes `WORK/msg-01.md`, `msg-02.md`, … and stops. **It does not send.**

Sending needs a messaging tool, and the scripts have none — the corpus names
a destination, not a transport, and the agent resolves one at run time from
whatever it can reach. Taking that split as a constraint is what keeps this
tool from depending on a particular local CLI or MCP server.

The text is rendered by `compile.py` for the destination's dialect and split
on the block boundaries that renderer states, so a message never begins in the
middle of an article.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import compile as compile_mod
from lib import corpus as corpus_lib

# Slack accepts 4000 characters per message; the margin absorbs the
# continuation marker and any per-destination decoration.
LIMITS = {"slack": 3800, "file": 1_000_000, "stdout": 1_000_000}
DEFAULT_LIMIT = 3800

CONTINUED = "*(continued)*"


def split_blocks(blocks: list[str], limit: int, continued: str = CONTINUED) -> list[str]:
    """Pack rendered blocks into messages the destination will accept.

    Blocks come from `compile.render_blocks`, so an item is never cut in half:
    the renderer states the boundaries rather than the splitter recovering them
    from markup, which is what broke the moment a second flavour spelled its
    headings differently.

    Parts already carry the continuation marker, so "every part is within the
    limit" is a property of the result rather than something the caller has to
    preserve. Adding the marker afterwards pushed a real message seven
    characters over.

    Character counts, not bytes: the destination limit is expressed in
    characters, and a Japanese digest is roughly three bytes to the character.
    """
    blocks = [b for b in blocks if b.strip()]
    if not blocks:
        return []

    prefix = f"{continued}\n\n"
    budget = max(1, limit - len(prefix))

    # A block that cannot fit at all is cut — better a hard break inside one
    # item than a message the destination rejects whole.
    units: list[str] = []
    for block in blocks:
        if len(block) <= budget:
            units.append(block)
        else:
            units += [block[i: i + budget] for i in range(0, len(block), budget)]

    parts: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if len(candidate) <= budget:
            current = candidate
            continue
        if current:
            parts.append(current)
        current = unit
    if current:
        parts.append(current)

    return [parts[0]] + [prefix + p for p in parts[1:]]


def limit_for(kind: str) -> int:
    return LIMITS.get(kind, DEFAULT_LIMIT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("digest", type=Path)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--out-prefix", type=Path, required=True)
    parser.add_argument("--kind", help="destination kind (default: the corpus's first)")
    parser.add_argument("--limit", type=int, help="override the character limit")
    parser.add_argument("--flavor", choices=compile_mod.FLAVORS, help="override the markup flavour")
    args = parser.parse_args()

    try:
        corpus = corpus_lib.load(args.repo)
    except corpus_lib.CorpusError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        digest: dict[str, Any] = json.loads(args.digest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {args.digest}: {exc}", file=sys.stderr)
        return 2

    kind = args.kind or (corpus.destinations[0].kind if corpus.destinations else "slack")
    limit = args.limit or limit_for(kind)
    # Render for the destination. Shipping one dialect everywhere is what cost
    # a real digest its links.
    flavor = args.flavor or (compile_mod.SLACK if kind == "slack" else compile_mod.MARKDOWN)

    messages = split_blocks(compile_mod.render_blocks(digest, flavor), limit)
    args.out_prefix.parent.mkdir(parents=True, exist_ok=True)

    # Clear parts left by an earlier invocation. The caller sends msg-01,
    # msg-02, … in order, so a run that produces fewer parts than the last one
    # would otherwise deliver stale messages after its own.
    stale = 0
    for existing in sorted(args.out_prefix.parent.glob(f"{args.out_prefix.name}-*.md")):
        existing.unlink()
        stale += 1

    written = []
    for i, message in enumerate(messages, start=1):
        path = args.out_prefix.with_name(f"{args.out_prefix.name}-{i:02d}.md")
        path.write_text(message, encoding="utf-8")
        written.append(str(path))

    print(
        json.dumps(
            {
                "kind": kind,
                "flavor": flavor,
                "limit": limit,
                "parts": written,
                "destinations": [
                    {
                        "id": d.id, "kind": d.kind, "channel": d.channel,
                        "thread": d.thread, "broadcast": d.broadcast,
                    }
                    for d in corpus.destinations
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    cleared = f", {stale} stale part(s) removed" if stale else ""
    print(
        f"{len(written)} part(s) written{cleared}; sending is not this script's job",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
