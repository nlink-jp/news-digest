#!/usr/bin/env python3
"""Render a digest.

    compile.py WORK/digest.json -o digests/YYYY-MM-DD.md
    compile.py WORK/digest.json --flavor slack

The one renderer: the file written to the corpus and the text that gets sent
come from here, so they cannot disagree, and when the wording is wrong this is
the file to change. Asking the model to re-render produces text that drifts
from the record beside it.

What must not disagree is the *content*, not the bytes. Destinations differ in
what markup they accept, and a first real delivery proved the difference
matters: an inline `[title](url)` link survived into the file and was silently
reduced to the title alone on the way to Slack, so the digest arrived naming
articles nobody could open. The `slack` flavour puts the address on its own
line, which no dialect can drop, and omits headings that dialect does not have.

Output is built as a list of **blocks** — a heading, one item, one list
section — which `render` joins. Delivery needs those boundaries to split a
long digest without cutting an item in half, and a splitter that recovered
them by matching markup breaks on the first flavour that spells the markup
differently. That happened, once, immediately.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

MARKDOWN = "markdown"
SLACK = "slack"
FLAVORS = (MARKDOWN, SLACK)


def escape(text: Any) -> str:
    """Neutralize Markdown control characters in third-party text.

    Titles come from feeds. A headline containing brackets or backticks must
    not be able to restructure the document it appears in.
    """
    out = str(text if text is not None else "")
    for char in ("\\", "[", "]", "`", "*", "_", "<", ">"):
        out = out.replace(char, "\\" + char)
    return out.replace("\n", " ").strip()


def _heading(title: str, flavor: str, level: int = 2) -> str:
    """`#`-prefixed in a file; bold where the dialect has no headings."""
    return f"{'#' * level} {title}" if flavor == MARKDOWN else f"**{title}**"


def axis_badge(item: dict[str, Any]) -> str:
    axes = item.get("axes") or {}
    parts = [f"{name[:3]} {value}" for name, value in axes.items()]
    if item.get("credibility"):
        parts.append(str(item["credibility"]))
    return " · ".join(parts)


def render_item(item: dict[str, Any], flavor: str = MARKDOWN) -> str:
    """One article, as a single block that must never be split."""
    title = escape(item.get("title") or "(untitled)")
    url = str(item.get("url") or "")
    linkable = url.startswith(("http://", "https://"))
    lines: list[str] = []

    if flavor == SLACK:
        # The address goes on its own line, unadorned. An inline link is the
        # first thing a markup converter drops, and losing it turns the digest
        # into a list of things the reader cannot reach.
        lines.append(f"**{title}**")
        if linkable:
            lines += ["", url]
    else:
        lines.append(_heading(f"[{title}]({url})" if linkable else title, flavor, 3))

    meta = [escape(item.get("source") or "unknown source")]
    if item.get("published_at"):
        meta.append(str(item["published_at"])[:16].replace("T", " "))
    badge = axis_badge(item)
    if badge:
        meta.append(badge)
    lines += ["", f"*{' — '.join(meta)}*"]

    if item.get("summary"):
        lines += ["", escape(item["summary"])]
    elif item.get("priority") == "must_read":
        # Saying so is the point: a must-read whose body was not read was
        # judged on its headline, and the reader should know that. Which of
        # the two reasons applies changes what they can do about it — one is
        # worth retrying, the other never will be.
        if item.get("body_fetchable", True):
            lines += ["", "*Body not retrieved — judged on the headline and feed excerpt.*"]
        else:
            lines += [
                "",
                "*This source does not serve article bodies to this tool — judged on the "
                "headline and feed excerpt. Open it to read the rest.*",
            ]

    if item.get("why"):
        lines += ["", f"**Why:** {escape(item['why'])}"]

    return "\n".join(lines)


def render_section(section: dict[str, Any], flavor: str = MARKDOWN) -> list[str]:
    """A section as blocks. Item sections yield a heading plus one per item."""
    kind = section.get("kind", "items")

    if kind == "items":
        items = section.get("items") or []
        if not items:
            if section.get("empty_text"):
                return [
                    _heading(section["title"], flavor)
                    + f"\n\n*{escape(section['empty_text'])}*"
                ]
            return []
        # The heading travels with the first item. On its own it can be packed
        # at the end of a message whose items went to the next one, leaving a
        # section title announcing nothing.
        rendered = [render_item(item, flavor) for item in items]
        heading = _heading(f"{section['title']} ({len(items)})", flavor)
        blocks = [f"{heading}\n\n{rendered[0]}"] + rendered[1:]
        if section.get("withheld"):
            blocks.append(f"*{section['withheld']} more not shown (item limit reached).*")
        return blocks

    if kind == "story_updates":
        created = section.get("created") or []
        updated = section.get("updated") or []
        if not created and not updated:
            return []
        lines = [_heading(section["title"], flavor), ""]
        lines += [f"- **New:** {escape(e.get('title'))} (`{e.get('id')}`)" for e in created]
        lines += [f"- Updated: {escape(e.get('title'))} (`{e.get('id')}`)" for e in updated]
        return ["\n".join(lines)]

    if kind == "stale_topics":
        topics = section.get("topics") or []
        if not topics:
            return []
        lines = [_heading(section["title"], flavor), ""]
        for topic in topics:
            sources = ", ".join(escape(s) for s in topic.get("sources") or [])
            detail = f" — {sources}" if sources else ""
            lines.append(
                f"- **{escape(topic.get('title'))}** ({topic.get('article_count')} article(s)"
                f"{detail}): {escape(topic.get('reason'))}"
            )
        return ["\n".join(lines)]

    if kind == "anomalies":
        anomalies = section.get("anomalies") or []
        if not anomalies:
            return []
        lines = [_heading(section["title"], flavor), ""]
        for anomaly in anomalies:
            if not isinstance(anomaly, dict):
                lines.append(f"- {escape(anomaly)}")
                continue
            where = anomaly.get("source") or anomaly.get("kind") or "—"
            # Lead with what it means for the reader. The mechanism is context;
            # an entry that only describes the mechanism leaves the reader
            # asking what they are supposed to do about it.
            effect = str(anomaly.get("effect") or "").strip()
            detail = str(anomaly.get("detail") or "").strip()
            body = effect or detail
            if effect and detail:
                body = f"{effect} ({detail})"
            lines.append(f"- **{escape(where)}:** {escape(body)}")
        return ["\n".join(lines)]

    if kind == "stats":
        stats = section.get("stats") or {}
        lines = [_heading(section["title"], flavor), ""]
        lines.append(
            f"- Collected {stats.get('collected', 0)} → "
            f"{stats.get('candidates', 0)} candidates → "
            f"{stats.get('evaluated', 0)} scored"
        )
        by_priority = ", ".join(f"{k} {v}" for k, v in (stats.get("by_priority") or {}).items())
        if by_priority:
            lines.append(f"- Priorities: {by_priority}")
        dropped = stats.get("dropped_by_rule") or {}
        if dropped:
            lines.append("- Dropped: " + ", ".join(f"{k} {v}" for k, v in sorted(dropped.items())))
        for label, key in (
            ("Sources with errors", "source_errors"),
            ("Sources with nothing in window", "silent_sources"),
            ("Collection gaps", "gaps"),
            ("Disabled sources", "disabled_sources"),
        ):
            values = stats.get(key) or []
            if values:
                lines.append(f"- {label}: {', '.join(escape(v) for v in values)}")
        return ["\n".join(lines)]

    return []


def render_blocks(digest: dict[str, Any], flavor: str = MARKDOWN) -> list[str]:
    """The digest as atomic blocks, in order. Delivery may reorder nothing and
    split none of them."""
    if flavor not in FLAVORS:
        raise ValueError(f"unknown flavor {flavor!r} (known: {', '.join(FLAVORS)})")

    blocks = [_heading(f"News digest — {escape(digest.get('date'))}", flavor, 1)]

    if digest.get("natural_language_summary"):
        blocks.append(escape(digest["natural_language_summary"]))

    counts = (digest.get("stats") or {}).get("by_priority") or {}
    blocks.append(
        f"*{counts.get('must_read', 0)} must-read of {digest.get('items_total', 0)} scored "
        f"({digest.get('profile')} profile).*"
    )

    for section in digest.get("sections") or []:
        blocks += render_section(section, flavor)
    return [b for b in blocks if b.strip()]


def render(digest: dict[str, Any], flavor: str = MARKDOWN) -> str:
    return "\n\n".join(render_blocks(digest, flavor)) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("digest", type=Path)
    parser.add_argument("-o", "--out", type=Path, help="output file (default: stdout)")
    parser.add_argument("--flavor", choices=FLAVORS, default=MARKDOWN)
    args = parser.parse_args()

    try:
        digest = json.loads(args.digest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {args.digest}: {exc}", file=sys.stderr)
        return 2

    text = render(digest, args.flavor)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
