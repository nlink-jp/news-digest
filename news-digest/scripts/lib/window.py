"""The collection window.

Feeds return only their most recent N items, so a window that opens after the
newest article a source has already shown means articles were published in
between and are gone. Detecting that is the difference between a quiet day and
a day whose news was missed.

Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone


class WindowError(Exception):
    """A window that cannot be resolved. The message names the fix."""


def local_timezone() -> timezone:
    """The machine's current UTC offset.

    A bare date on the command line means midnight where the operator is, not
    midnight UTC. The resolved bounds are reported in the collection stats so
    the interpretation is visible rather than assumed.
    """
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    return timezone(offset)


@dataclass(frozen=True)
class Window:
    since: datetime | None
    until: datetime

    def contains(self, when: datetime | None) -> bool:
        """Half-open: `[since, until)`.

        An article with no usable date is included. Feeds omit and mangle
        dates often enough that excluding them would drop real articles
        silently, and a wrong date is visible downstream while a missing
        article is not.
        """
        if when is None:
            return True
        if self.since is not None and when < self.since:
            return False
        return when < self.until

    def as_dict(self) -> dict[str, str | None]:
        return {
            "since": self.since.isoformat() if self.since else None,
            "until": self.until.isoformat(),
        }


def parse_bound(raw: str | None, tz: timezone) -> datetime | None:
    """Parse one bound. `all` means no lower bound.

    A bare date is midnight in `tz`; a naive datetime is read in `tz` as well.
    An explicit offset in the input is honoured as given.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text or text.lower() == "all":
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise WindowError(
            f"cannot read '{raw}' as a date or time — use YYYY-MM-DD or an ISO 8601 timestamp"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed


def resolve(
    since_raw: str | None,
    until_raw: str | None,
    *,
    tz: timezone | None = None,
    now: datetime | None = None,
) -> Window:
    """Resolve the window, defaulting to "yesterday from midnight until now".

    `tz` and `now` are injected so the result does not depend on the machine
    the tests run on.
    """
    tz = tz or local_timezone()
    now = now.astimezone(tz) if now else datetime.now(tz)

    until = parse_bound(until_raw, tz) or now
    if since_raw is None:
        yesterday = (until.astimezone(tz) - timedelta(days=1)).date()
        since: datetime | None = datetime.combine(yesterday, time.min, tzinfo=tz)
    else:
        since = parse_bound(since_raw, tz)

    if since is not None and since >= until:
        raise WindowError(
            f"window is empty: since ({since.isoformat()}) is not before until ({until.isoformat()})"
        )
    return Window(since=since, until=until)


def gap_before(window: Window, last_seen: str | None) -> bool:
    """True when the window opens after the newest article already collected.

    Anything published between the two is no longer in the feed, so re-running
    will not recover it. The run reports this rather than treating the absence
    as news that did not happen.
    """
    if window.since is None or not last_seen:
        return False
    try:
        seen = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
    except ValueError:
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return window.since > seen
