#!/usr/bin/env python3
"""Split the digest into messages a destination will accept.

    to_notify.py digests/2026-08-08.json --repo <corpus> --out-prefix WORK/msg

Writes `WORK/msg-01.md`, `msg-02.md`, … and stops. **It does not send.**

Sending needs a messaging tool, and the scripts have none — the corpus names
a destination, not a transport, and the agent resolves one at run time from
whatever it can reach. Taking that split as a constraint is what keeps this
tool from depending on a particular local CLI or MCP server.

The text is the same text `compile.py` writes to the corpus, split at the
largest structural boundary that fits, so a message never begins mid-item.

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


def split_markdown(text: str, limit: int) -> list[str]:
    """Split at the largest boundary that fits: sections, then items, then lines.

    Splitting mid-item would produce a message whose first line is a fragment
    of a headline, which reads as corruption rather than as continuation.
    """
    if len(text) <= limit:
        return [text] if text.strip() else []

    for separator in ("\n## ", "\n### ", "\n\n", "\n"):
        blocks = _blocks(text, separator)
        if all(len(b) <= limit for b in blocks):
            return _pack(blocks, limit)

    # A single block longer than the limit: nothing structural is left, so cut
    # it rather than emit a message the destination will reject.
    return [text[i: i + limit] for i in range(0, len(text), limit)]


def _blocks(text: str, separator: str) -> list[str]:
    parts = text.split(separator)
    out = [parts[0]]
    for part in parts[1:]:
        out.append(separator.lstrip("\n") + part)
    return [p for p in out if p.strip()]


def _pack(blocks: list[str], limit: int) -> list[str]:
    messages: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            messages.append(current)
        current = block
    if current:
        messages.append(current)
    return messages


def limit_for(kind: str) -> int:
    return LIMITS.get(kind, DEFAULT_LIMIT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("digest", type=Path)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--out-prefix", type=Path, required=True)
    parser.add_argument("--kind", help="destination kind (default: the corpus's first)")
    parser.add_argument("--limit", type=int, help="override the character limit")
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

    messages = split_markdown(compile_mod.render(digest), limit)
    args.out_prefix.parent.mkdir(parents=True, exist_ok=True)
    written = []
    for i, message in enumerate(messages, start=1):
        body = message if i == 1 else f"{CONTINUED}\n\n{message}"
        path = args.out_prefix.with_name(f"{args.out_prefix.name}-{i:02d}.md")
        path.write_text(body, encoding="utf-8")
        written.append(str(path))

    print(
        json.dumps(
            {
                "kind": kind,
                "limit": limit,
                "parts": written,
                "destinations": [
                    {"id": d.id, "kind": d.kind, "channel": d.channel, "thread": d.thread}
                    for d in corpus.destinations
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"{len(written)} part(s) written; sending is not this script's job", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
