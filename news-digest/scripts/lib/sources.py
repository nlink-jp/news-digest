"""The source list — `config/sources.toml` in a corpus.

What is written here is burned into every article's `origin` at collection
time and never rewritten afterwards. Editing the list changes what is
collected next; it does not change history, and that is what makes the corpus
traceable.

Standard library only.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# How a source stands in relation to the facts it reports. This is the input
# to `credibility`, which a decision table may consult and which the agent
# never scores — the relationship is a property of the source, not a judgement
# about an article.
TIERS = ("primary", "secondary", "analysis", "community")

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

DEFAULT_ACCEPT_LANGUAGE = {"ja": "ja,en-US;q=0.8,en;q=0.6", "en": "en-US,en;q=0.9"}


class SourceError(Exception):
    """A source list that cannot be collected from. The message names the fix."""


@dataclass(frozen=True)
class Auth:
    token_env: str
    header: str = "Authorization: Bearer {token}"

    def resolve(self) -> dict[str, str]:
        """Build the header, or raise if the environment does not carry the token.

        The token itself never appears in the corpus: `sources.toml` names an
        environment variable, so a private repository still holds no secret.
        """
        token = os.environ.get(self.token_env, "")
        if not token:
            raise SourceError(
                f"environment variable {self.token_env} is unset or empty, "
                f"but a source requires it"
            )
        name, _, template = self.header.partition(":")
        if not template:
            raise SourceError(f"auth header must look like 'Name: value', got {self.header!r}")
        return {name.strip(): template.strip().replace("{token}", token)}


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    type: str
    category: str
    lang: str
    tier: str
    weight: float = 1.0
    enabled: bool = True
    accept_language: str | None = None
    note: str = ""
    auth: Auth | None = None
    options: dict[str, Any] = field(default_factory=dict)

    def language_header(self) -> str:
        return self.accept_language or DEFAULT_ACCEPT_LANGUAGE.get(self.lang, "en-US,en;q=0.9")

    def origin(self) -> dict[str, Any]:
        """The provenance stamped into every article from this source.

        Copied by value at collection time. A later edit to `sources.toml`
        leaves records already written untouched.
        """
        return {
            "source_id": self.id,
            "source_name": self.name,
            "feed_url": self.url,
            "collector": self.type,
            "category": self.category,
            "lang": self.lang,
            "tier": self.tier,
            "weight": self.weight,
        }


def _fail(where: str, message: str) -> None:
    raise SourceError(f"{where}: {message}")


def _parse_one(entry: Any, where: str, known_types: tuple[str, ...], default_type: str) -> Source:
    if not isinstance(entry, dict):
        _fail(where, "not a table")
    for key in ("id", "name", "url", "category", "lang", "tier"):
        if not str(entry.get(key, "")).strip():
            _fail(where, f"missing or empty '{key}'")

    source_id = str(entry["id"]).strip()
    if not ID_RE.match(source_id):
        _fail(where, f"id '{source_id}' must be lowercase letters, digits, and hyphens")

    url = str(entry["url"]).strip()
    if not url.startswith(("http://", "https://")):
        _fail(where, f"url must be http(s), got {url!r}")

    source_type = str(entry.get("type", default_type)).strip() or default_type
    if source_type not in known_types:
        _fail(where, f"type '{source_type}' is unknown (available: {', '.join(known_types)})")

    tier = str(entry["tier"]).strip()
    if tier not in TIERS:
        _fail(where, f"tier '{tier}' must be one of {', '.join(TIERS)}")

    try:
        weight = float(entry.get("weight", 1.0))
    except (TypeError, ValueError):
        _fail(where, "weight must be a number")
    if weight <= 0:
        _fail(where, "weight must be positive (use enabled = false to switch a source off)")

    auth = None
    raw_auth = entry.get("auth")
    if raw_auth is not None:
        if not isinstance(raw_auth, dict) or not str(raw_auth.get("token_env", "")).strip():
            _fail(where, "[source.auth] needs 'token_env' naming an environment variable")
        if "token" in raw_auth or "value" in raw_auth:
            _fail(where, "[source.auth] must not carry a literal token — name an environment variable")
        auth = Auth(
            token_env=str(raw_auth["token_env"]).strip(),
            header=str(raw_auth.get("header", "Authorization: Bearer {token}")),
        )

    known_keys = {
        "id", "name", "url", "type", "category", "lang", "tier",
        "weight", "enabled", "accept_language", "note", "auth", "options",
    }
    unknown = sorted(set(entry) - known_keys)
    if unknown:
        _fail(where, f"unknown key(s): {', '.join(unknown)}")

    return Source(
        id=source_id,
        name=str(entry["name"]).strip(),
        url=url,
        type=source_type,
        category=str(entry["category"]).strip(),
        lang=str(entry["lang"]).strip(),
        tier=tier,
        weight=weight,
        enabled=bool(entry.get("enabled", True)),
        accept_language=(str(entry["accept_language"]) if entry.get("accept_language") else None),
        note=str(entry.get("note", "")),
        auth=auth,
        options=dict(entry.get("options") or {}),
    )


def load(path: Path, known_types: tuple[str, ...], default_type: str = "rss") -> list[Source]:
    """Read and validate a source list.

    Every problem is raised rather than skipped. A source list that half-loads
    produces a short collection that looks like a quiet day, which is the
    failure mode this tool exists to remove.
    """
    path = Path(path)
    if not path.is_file():
        raise SourceError(f"{path}: no source list")
    try:
        with path.open("rb") as fh:
            doc = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise SourceError(f"{path}: invalid TOML — {exc}") from exc

    raw = doc.get("source")
    if not isinstance(raw, list) or not raw:
        raise SourceError(f"{path}: expected at least one [[source]] table")

    out: list[Source] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        source = _parse_one(entry, f"{path}: [[source]] #{i + 1}", known_types, default_type)
        if source.id in seen:
            raise SourceError(f"{path}: duplicate source id '{source.id}'")
        seen.add(source.id)
        out.append(source)
    return out


def select(sources: list[Source], only: list[str] | None) -> tuple[list[Source], list[Source]]:
    """Split into what will be collected and what will not.

    `--source` names sources explicitly and overrides `enabled`: asking for a
    disabled source by name and silently collecting nothing would be a lie.
    """
    if only:
        wanted = set(only)
        unknown = sorted(wanted - {s.id for s in sources})
        if unknown:
            raise SourceError(f"no such source id: {', '.join(unknown)}")
        chosen = [s for s in sources if s.id in wanted]
        return chosen, [s for s in sources if s.id not in wanted]
    return [s for s in sources if s.enabled], [s for s in sources if not s.enabled]
