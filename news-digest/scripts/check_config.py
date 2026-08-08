#!/usr/bin/env python3
"""Check a corpus before collecting from it.

    check_config.py --repo <corpus> [--probe]

Runs before anything is fetched, because the alternative is a run that
completes on broken configuration and reports "collected 0 articles" — which
is indistinguishable from a quiet day. Every ERROR here is a thing that would
otherwise have produced a plausible, wrong digest.

`--probe` additionally connects to each feed URL and confirms something
parseable comes back.

Exit codes: 0 clean or warnings only, 1 errors found, 2 the corpus could not
be read at all.

Standard library only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collectors
from lib import corpus as corpus_lib
from lib import filters as filters_lib
from lib import http as http_lib
from lib import profile as profile_lib
from lib import sources as sources_lib

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ is required
    tomllib = None


def dotted(table: dict[str, Any], path: str) -> Any:
    current: Any = table
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "array": lambda v: isinstance(v, list),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "table": lambda v: isinstance(v, dict),
}


def check_interests(
    path: Path, prof: profile_lib.Profile, errors: list[str], warnings: list[str]
) -> None:
    """Check the relevance profile against what the evaluation profile needs.

    A profile and a configuration that do not fit produce relevance scores made
    from nothing, which is the one failure that looks entirely normal in the
    output.
    """
    if not path.is_file():
        errors.append(f"{path}: missing — the relevance axis has nothing to score against")
        return
    try:
        with path.open("rb") as fh:
            doc = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        errors.append(f"{path}: invalid TOML — {exc}")
        return

    schema = prof.interests_schema
    for requirement in schema.get("require", []) or []:
        key = str(requirement.get("path", ""))
        value = dotted(doc, key)
        why = str(requirement.get("why", "")).strip().replace("\n", " ")
        if value is None:
            errors.append(f"{path}: [{key}] is required by the '{prof.name}' profile. {why}")
            continue
        checker = TYPE_CHECKS.get(str(requirement.get("type", "")))
        if checker and not checker(value):
            errors.append(f"{path}: [{key}] must be a {requirement['type']}")
            continue
        if isinstance(value, str) and len(value.strip()) < int(requirement.get("min_chars", 0)):
            errors.append(
                f"{path}: [{key}] is shorter than {requirement['min_chars']} characters. {why}"
            )
        if isinstance(value, list) and len(value) < int(requirement.get("min_items", 0)):
            errors.append(
                f"{path}: [{key}] needs at least {requirement['min_items']} entr(ies). {why}"
            )

    for optional in schema.get("optional", []) or []:
        key = str(optional.get("path", ""))
        if dotted(doc, key) is None:
            warnings.append(f"{path}: [{key}] not set — {str(optional.get('why', '')).strip()}")


def probe(sources: list[sources_lib.Source], errors: list[str], warnings: list[str]) -> None:
    client = http_lib.HttpClient(retries=0, timeout=20)
    for source in sources:
        try:
            response = client.get(source.url, accept_language=source.language_header())
        except http_lib.HttpError as exc:
            errors.append(f"[{source.id}] {source.url}: {exc}")
            continue
        if response.not_modified:
            continue
        try:
            entries = collectors.get(source.type).parse(response.body, source.origin())
        except collectors.CollectorError as exc:
            errors.append(f"[{source.id}] {source.url}: {exc}")
            continue
        if not entries:
            warnings.append(f"[{source.id}] parsed, but the feed carries no entries")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--skill-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--probe", action="store_true", help="connect to each feed URL")
    args = parser.parse_args()

    errors: list[str] = []
    warnings: list[str] = []

    try:
        corpus = corpus_lib.load(args.repo)
    except corpus_lib.CorpusError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        prof = profile_lib.load(args.skill_dir / "profiles", corpus.profile_name)
    except profile_lib.ProfileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    source_list: list[sources_lib.Source] = []
    try:
        source_list = sources_lib.load(
            corpus.sources_file, tuple(collectors.available()), collectors.DEFAULT_TYPE
        )
    except sources_lib.SourceError as exc:
        errors.append(str(exc))

    if source_list:
        enabled = [s for s in source_list if s.enabled]
        if not enabled:
            errors.append(f"{corpus.sources_file}: every source is disabled")
        try:
            filters_lib.load(corpus.filters_file, {s.id for s in source_list})
        except filters_lib.FilterError as exc:
            errors.append(str(exc))

    check_interests(corpus.interests_file, prof, errors, warnings)

    if not corpus.destinations:
        warnings.append(
            f"{corpus.root / corpus_lib.MARKER}: no [[notify]] destination — "
            f"the digest will be written but not sent"
        )

    if args.probe and source_list:
        probe([s for s in source_list if s.enabled], errors, warnings)

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)

    if errors:
        print(f"\n{len(errors)} error(s). Fix them before collecting.", file=sys.stderr)
        return 1

    print(
        f"OK: profile '{prof.name}' ({' -> '.join(prof.lineage)}), "
        f"{len([s for s in source_list if s.enabled])} enabled source(s), "
        f"{len(warnings)} warning(s)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
