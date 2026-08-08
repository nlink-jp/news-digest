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
matters: an inline `[title](url)` link survived the file and was silently
reduced to the title alone on the way to Slack-style mrkdwn, so the digest
arrived naming articles nobody could open. The `slack` flavour emits the
address on its own line, which no dialect can drop.

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

PRIORITY_LABEL = {
    "must_read": "must read",
    "should_read": "worth reading",
    "skim": "skim",
    "minor_update": "no new facts",
    "archive": "archived",
}


def escape(text: Any) -> str:
    """Neutralize Markdown control characters in third-party text.

    Titles come from feeds. A headline containing brackets or backticks must
    not be able to restructure the document it appears in.
    """
    out = str(text if text is not None else "")
    for char in ("\\", "[", "]", "`", "*", "_", "<", ">"):
        out = out.replace(char, "\\" + char)
    return out.replace("\n", " ").strip()


def _section_heading(title: str, flavor: str) -> str:
    """`##` in a file; bold for a destination whose dialect has no headings."""
    return f"## {title}" if flavor == MARKDOWN else f"**{title}**"


def axis_badge(item: dict[str, Any]) -> str:
    axes = item.get("axes") or {}
    parts = [f"{name[:3]} {value}" for name, value in axes.items()]
    if item.get("credibility"):
        parts.append(str(item["credibility"]))
    return " · ".join(parts)


def render_item(item: dict[str, Any], lines: list[str], flavor: str = MARKDOWN) -> None:
    title = escape(item.get("title") or "(untitled)")
    url = str(item.get("url") or "")
    linkable = url.startswith(("http://", "https://"))

    if flavor == SLACK:
        # The address goes on its own line, unadorned. An inline link is the
        # first thing a markup converter drops, and losing it turns the digest
        # into a list of things the reader cannot reach.
        lines.append(f"**{title}**")
        lines.append("")
        if linkable:
            lines.append(url)
            lines.append("")
    else:
        heading = f"[{title}]({url})" if linkable else title
        lines.append(f"### {heading}")
        lines.append("")

    meta = [escape(item.get("source") or "unknown source")]
    if item.get("published_at"):
        meta.append(str(item["published_at"])[:16].replace("T", " "))
    badge = axis_badge(item)
    if badge:
        meta.append(badge)
    lines.append(f"*{' — '.join(meta)}*")
    lines.append("")

    if item.get("summary"):
        lines.append(escape(item["summary"]))
        lines.append("")
    elif item.get("priority") == "must_read":
        # Saying so is the point: a must-read whose body could not be fetched
        # was judged on its headline, and the reader should know that.
        lines.append("*Body not retrieved — judged on the headline and feed excerpt.*")
        lines.append("")

    if item.get("why"):
        lines.append(f"**Why:** {escape(item['why'])}")
        lines.append("")


def render_section(section: dict[str, Any], lines: list[str], flavor: str = MARKDOWN) -> None:
    kind = section.get("kind", "items")

    if kind == "items":
        items = section.get("items") or []
        if not items:
            if section.get("empty_text"):
                lines.append(_section_heading(section["title"], flavor))
                lines.append("")
                lines.append(f"*{escape(section['empty_text'])}*")
                lines.append("")
            return
        lines.append(_section_heading(f"{section['title']} ({len(items)})", flavor))
        lines.append("")
        for item in items:
            render_item(item, lines, flavor)
        if section.get("withheld"):
            lines.append(f"*{section['withheld']} more not shown (item limit reached).*")
            lines.append("")
        return

    if kind == "story_updates":
        created = section.get("created") or []
        updated = section.get("updated") or []
        if not created and not updated:
            return
        lines.append(_section_heading(section["title"], flavor))
        lines.append("")
        for entry in created:
            lines.append(f"- **New:** {escape(entry.get('title'))} (`{entry.get('id')}`)")
        for entry in updated:
            lines.append(f"- Updated: {escape(entry.get('title'))} (`{entry.get('id')}`)")
        lines.append("")
        return

    if kind == "stale_topics":
        topics = section.get("topics") or []
        if not topics:
            return
        lines.append(_section_heading(section["title"], flavor))
        lines.append("")
        for topic in topics:
            sources = ", ".join(escape(s) for s in topic.get("sources") or [])
            detail = f" — {sources}" if sources else ""
            lines.append(
                f"- **{escape(topic.get('title'))}** ({topic.get('article_count')} article(s)"
                f"{detail}): {escape(topic.get('reason'))}"
            )
        lines.append("")
        return

    if kind == "anomalies":
        anomalies = section.get("anomalies") or []
        if not anomalies:
            return
        lines.append(_section_heading(section["title"], flavor))
        lines.append("")
        for anomaly in anomalies:
            if isinstance(anomaly, dict):
                where = anomaly.get("source") or anomaly.get("kind") or "—"
                lines.append(f"- **{escape(where)}:** {escape(anomaly.get('detail'))}")
            else:
                lines.append(f"- {escape(anomaly)}")
        lines.append("")
        return

    if kind == "stats":
        stats = section.get("stats") or {}
        lines.append(_section_heading(section["title"], flavor))
        lines.append("")
        by_priority = ", ".join(f"{k} {v}" for k, v in (stats.get("by_priority") or {}).items())
        lines.append(
            f"- Collected {stats.get('collected', 0)} → "
            f"{stats.get('candidates', 0)} candidates → "
            f"{stats.get('evaluated', 0)} scored"
        )
        if by_priority:
            lines.append(f"- Priorities: {by_priority}")
        dropped = stats.get("dropped_by_rule") or {}
        if dropped:
            lines.append(
                "- Dropped: " + ", ".join(f"{k} {v}" for k, v in sorted(dropped.items()))
            )
        for label, key in (
            ("Sources with errors", "source_errors"),
            ("Sources with nothing in window", "silent_sources"),
            ("Collection gaps", "gaps"),
            ("Disabled sources", "disabled_sources"),
        ):
            values = stats.get(key) or []
            if values:
                lines.append(f"- {label}: {', '.join(escape(v) for v in values)}")
        lines.append("")
        return


def render(digest: dict[str, Any], flavor: str = MARKDOWN) -> str:
    if flavor not in FLAVORS:
        raise ValueError(f"unknown flavor {flavor!r} (known: {', '.join(FLAVORS)})")
    heading = f"# News digest — {escape(digest.get('date'))}"
    lines: list[str] = [heading if flavor == MARKDOWN else f"**{heading[2:]}**", ""]

    if digest.get("natural_language_summary"):
        lines.append(escape(digest["natural_language_summary"]))
        lines.append("")

    counts = (digest.get("stats") or {}).get("by_priority") or {}
    must = counts.get("must_read", 0)
    lines.append(
        f"*{must} must-read of {digest.get('items_total', 0)} scored "
        f"({digest.get('profile')} profile).*"
    )
    lines.append("")

    for section in digest.get("sections") or []:
        render_section(section, lines, flavor)

    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


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
