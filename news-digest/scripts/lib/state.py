"""Per-source state, carried between runs.

Small enough to live in git beside the corpus, and worth having there: it is
what lets the next run revalidate instead of re-downloading, back off from a
source that keeps failing, and notice that a gap opened while nobody was
running the collector.

Standard library only.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

# After this many consecutive failures a source is reported as probably dead
# rather than as today's transient error.
DEAD_AFTER = 5


@dataclass
class SourceState:
    etag: str | None = None
    last_modified: str | None = None
    last_seen_published_at: str | None = None
    last_fetch_at: str | None = None
    last_status: str = "unknown"
    consecutive_errors: int = 0

    @property
    def probably_dead(self) -> bool:
        return self.consecutive_errors >= DEAD_AFTER

    def record_success(self, *, fetched_at: str, newest_published: str | None) -> None:
        self.last_fetch_at = fetched_at
        self.last_status = "ok"
        self.consecutive_errors = 0
        # Only ever moves forward: a feed that briefly serves an older page
        # must not rewind the marker that gap detection depends on.
        if newest_published and (
            self.last_seen_published_at is None
            or newest_published > self.last_seen_published_at
        ):
            self.last_seen_published_at = newest_published

    def record_not_modified(self, *, fetched_at: str) -> None:
        self.last_fetch_at = fetched_at
        self.last_status = "not_modified"
        self.consecutive_errors = 0

    def record_error(self, *, fetched_at: str, kind: str) -> None:
        self.last_fetch_at = fetched_at
        self.last_status = f"error:{kind}"
        self.consecutive_errors += 1
        # Validators are dropped on failure: a 4xx may mean the resource
        # moved, and replaying a stale ETag against a new document would keep
        # answering 304 forever.
        self.etag = None
        self.last_modified = None


class Store:
    """The whole state file, keyed by source id."""

    def __init__(self, path: Path, entries: dict[str, SourceState] | None = None):
        self.path = Path(path)
        self.entries: dict[str, SourceState] = entries or {}

    @classmethod
    def load(cls, path: Path) -> "Store":
        path = Path(path)
        if not path.is_file():
            return cls(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # State is a cache of facts the next fetch can rediscover. A
            # corrupt file costs one unconditional re-fetch; refusing to run
            # over it would cost the whole digest.
            return cls(path)
        if not isinstance(raw, dict):
            return cls(path)
        entries = {}
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            known = {f: value.get(f) for f in SourceState.__annotations__ if f in value}
            entries[str(key)] = SourceState(**known)
        return cls(path, entries)

    def get(self, source_id: str) -> SourceState:
        return self.entries.setdefault(source_id, SourceState())

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: asdict(v) for k, v in sorted(self.entries.items())}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
