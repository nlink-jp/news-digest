"""The corpus repository and the `.newsrc.toml` contract.

The engine is published; the corpus is not, and the engine cannot see the
corpora it will be run against. `config_version` and `schema_version` are how
an engine update declines to corrupt data it has never met: an unsupported
version stops the run before anything is read or written.

Standard library only.
"""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Corpus formats this engine understands. Widening either set means the code
# can actually read every version listed — not that it is willing to try.
CONFIG_VERSIONS = frozenset({1})
SCHEMA_VERSIONS = frozenset({1})

# The version stamped into records written now.
SCHEMA_WRITE_VERSION = 1

MARKER = ".newsrc.toml"

# Scratch for one run, inside the corpus and gitignored there.
WORK_DIRNAME = ".news-digest-work"


class CorpusError(Exception):
    """A corpus that cannot be operated on. The message names the fix."""


@dataclass(frozen=True)
class Destination:
    """Where a digest goes. Names a destination, never a transport."""

    id: str
    kind: str
    channel: str
    thread: bool = False
    # Whether a threaded continuation is also shown in the channel. Off, the
    # second half of a long digest is only visible to someone who opens the
    # thread — which is most of the digest, and none of the reader's habit.
    broadcast: bool = True


@dataclass(frozen=True)
class Corpus:
    root: Path
    config_version: int
    schema_version: int
    profile_name: str
    config_dir: Path
    data_dir: Path
    digests_dir: Path
    destinations: tuple[Destination, ...] = field(default=())

    # --- config files ---

    @property
    def sources_file(self) -> Path:
        return self.config_dir / "sources.toml"

    @property
    def filters_file(self) -> Path:
        return self.config_dir / "filters.toml"

    @property
    def interests_file(self) -> Path:
        return self.config_dir / "interests.toml"

    # --- data areas ---

    @property
    def articles_dir(self) -> Path:
        return self.data_dir / "articles"

    @property
    def stories_dir(self) -> Path:
        return self.data_dir / "stories"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state" / "sources.json"

    def seen_file(self, year: int | str) -> Path:
        """The seen index is split by year so a run appends instead of
        rewriting every line it has ever written."""
        return self.index_dir / f"seen-{year}.tsv"

    def seen_files(self) -> list[Path]:
        return sorted(self.index_dir.glob("seen-*.tsv"))

    @property
    def work_dir(self) -> Path:
        return self.root / WORK_DIRNAME

    def reset_work_dir(self) -> Path:
        """Empty the scratch directory and return it.

        Called at the *start* of a run rather than the end. A run that dies
        halfway leaves its files to be read, and the next run clears them —
        which keeps the debugging value while making it impossible to pick up
        a previous run's artefact. Removing them at the end instead was an
        instruction in prose, and prose instructions get skipped.
        """
        work = self.work_dir
        # This deletes a directory tree. `work_dir` is derived from `root`, so
        # checking them against each other proves nothing — the pair is
        # consistent for any root at all, including "/". What is worth
        # checking is that the root is still a corpus: the same evidence that
        # justified operating here in the first place must be on disk now.
        if not (self.root / MARKER).is_file():
            raise CorpusError(
                f"refusing to clear {work}: {self.root} is not a corpus (no {MARKER})"
            )
        if work.exists():
            if not work.is_dir():
                raise CorpusError(f"{work} exists and is not a directory")
            shutil.rmtree(work)
        work.mkdir(parents=True)
        return work

    def article_file(self, published_date: str) -> Path:
        """`published_date` is `YYYY-MM-DD`."""
        year, month, day = published_date.split("-")
        return self.articles_dir / year / month / f"{day}.jsonl"


def _require(table: dict, key: str, where: str) -> object:
    if key not in table:
        raise CorpusError(f"{where}: missing required key '{key}'")
    return table[key]


def _check_version(kind: str, value: object, supported: frozenset[int], root: Path) -> int:
    if not isinstance(value, int):
        raise CorpusError(f"{root / MARKER}: [repo] {kind} must be an integer")
    if value not in supported:
        newest = max(supported)
        direction = (
            "upgrade this skill" if value > newest
            else "this corpus predates the versions this skill supports"
        )
        raise CorpusError(
            f"{root / MARKER}: {kind} {value} is not supported by this skill "
            f"(supported: {sorted(supported)}). {direction.capitalize()}. "
            f"Running anyway risks writing records the corpus cannot read back."
        )
    return value


def _parse_destinations(raw: object, root: Path) -> tuple[Destination, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise CorpusError(f"{root / MARKER}: [[notify]] must be an array of tables")
    out: list[Destination] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        where = f"{root / MARKER}: [[notify]] #{i + 1}"
        if not isinstance(entry, dict):
            raise CorpusError(f"{where}: not a table")
        dest_id = str(_require(entry, "id", where))
        if dest_id in seen:
            raise CorpusError(f"{where}: duplicate id '{dest_id}'")
        seen.add(dest_id)
        channel = str(_require(entry, "channel", where)).strip()
        if not channel:
            raise CorpusError(f"{where}: 'channel' is empty (remove the entry to disable it)")
        out.append(
            Destination(
                id=dest_id,
                kind=str(_require(entry, "kind", where)),
                channel=channel,
                thread=bool(entry.get("thread", False)),
                broadcast=bool(entry.get("broadcast", True)),
            )
        )
    return tuple(out)


def load(root: Path) -> Corpus:
    """Read and validate `<root>/.newsrc.toml`.

    Raises `CorpusError` rather than guessing: a wrong corpus is worse than no
    corpus, because the run would write real records into it.
    """
    root = Path(root).expanduser().resolve()
    marker = root / MARKER
    if not marker.is_file():
        raise CorpusError(
            f"{root}: not a corpus repository (no {MARKER}). "
            f"Pass --repo pointing at one."
        )
    try:
        with marker.open("rb") as fh:
            doc = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise CorpusError(f"{marker}: invalid TOML — {exc}") from exc

    repo = doc.get("repo")
    if not isinstance(repo, dict):
        raise CorpusError(f"{marker}: missing [repo] table")

    config_version = _check_version(
        "config_version", _require(repo, "config_version", f"{marker}: [repo]"),
        CONFIG_VERSIONS, root,
    )
    schema_version = _check_version(
        "schema_version", _require(repo, "schema_version", f"{marker}: [repo]"),
        SCHEMA_VERSIONS, root,
    )
    profile_name = str(_require(repo, "profile", f"{marker}: [repo]"))

    paths = doc.get("paths") or {}
    if not isinstance(paths, dict):
        raise CorpusError(f"{marker}: [paths] must be a table")

    def area(key: str, default: str) -> Path:
        rel = Path(str(paths.get(key, default)))
        if rel.is_absolute() or ".." in rel.parts:
            raise CorpusError(
                f"{marker}: [paths] {key} must be a relative path inside the corpus"
            )
        return root / rel

    return Corpus(
        root=root,
        config_version=config_version,
        schema_version=schema_version,
        profile_name=profile_name,
        config_dir=area("config", "config"),
        data_dir=area("data", "data"),
        digests_dir=area("digests", "digests"),
        destinations=_parse_destinations(doc.get("notify"), root),
    )


def discover(start: Path) -> Path:
    """Return `start` if it is a corpus, else raise.

    Deliberately does not walk upwards: a run that silently found a corpus
    two directories above the one the user meant would write into the wrong
    place, and the mistake would only be visible after the commit.
    """
    start = Path(start).expanduser().resolve()
    if (start / MARKER).is_file():
        return start
    raise CorpusError(
        f"{start}: not a corpus repository (no {MARKER}). "
        f"Pass --repo pointing at one."
    )
