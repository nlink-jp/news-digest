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


def rolled_past(oldest_available: str | None, last_seen: str | None) -> bool:
    """True when the feed no longer reaches back to what was last collected.

    Feeds return only their most recent N items. When the oldest item a feed
    still offers was published *after* the newest item already collected from
    it, everything in between has fallen off and cannot be recovered by
    re-running.

    Deliberately independent of the requested window: asking for a narrow
    window is a choice, not a loss. Comparing against the window instead
    reports a gap for every source that simply has not published lately,
    which is the normal case and drowns the real signal.
    """
    if not oldest_available or not last_seen:
        return False
    oldest, seen = _parse(oldest_available), _parse(last_seen)
    if oldest is None or seen is None:
        return False
    return oldest > seen


def days_since(timestamp: str | None, *, now: datetime | None = None) -> float | None:
    """Age of a timestamp in days, or None if it cannot be read.

    Used to notice a feed that has stopped publishing. Conditional GET makes a
    frozen source answer 304 indefinitely and error counters stay at zero, so
    nothing else in the pipeline can tell it apart from a healthy quiet one.
    """
    parsed = _parse(timestamp) if timestamp else None
    if parsed is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - parsed).total_seconds() / 86400.0


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
