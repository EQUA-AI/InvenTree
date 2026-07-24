"""Working-time arithmetic for scheduling (S6).

Pure functions over a ``CalendarSpec`` (weekday windows + holidays + IANA
timezone). Every other scheduling rule — duration validation, dependency lag,
planner slot selection, conflict detection — composes from these four
primitives, so a bug here is systemic rather than local. They are unit-tested
directly, and against ``USE_TZ=True`` (this project sets ``USE_TZ = not
TESTING``, so the default test environment cannot exercise timezone behaviour).

Datetimes are timezone-aware throughout. Window boundaries are interpreted in
the calendar's own zone, so DST is handled by ``zoneinfo``: a night shift that
straddles a spring-forward really is one real hour shorter, because the local
window's start and end map to UTC instants only 7 hours apart.

Definitions:

* a *working instant* is one whose local time falls inside a window on a
  non-holiday working day;
* *working minutes between* two instants is the real elapsed time their span
  overlaps with working windows;
* *add working minutes* advances an instant forward, consuming a budget of real
  minutes that elapse only inside working windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# Guard against runaway walks when a calendar has no working time at all (e.g.
# every window empty). No real schedule spans anywhere near this many days.
_MAX_DAYS = 366 * 5


class NoWorkingTime(Exception):  # noqa: N818 - reads better than NoWorkingTimeError
    """Raised when an operation cannot complete within the day horizon.

    Almost always means the calendar has no working windows, or a holiday list
    swallowed the whole horizon.
    """


@dataclass(frozen=True)
class CalendarSpec:
    """A resolved, timezone-aware working-time definition.

    ``windows`` maps a weekday index (0=Monday … 6=Sunday, matching
    ``date.weekday()``) to a tuple of ``(start, end)`` local-time pairs. Multiple
    pairs per day allow split shifts. ``holidays`` are local dates with no
    working time regardless of weekday.
    """

    tzname: str
    windows: dict[int, tuple[tuple[time, time], ...]]
    holidays: frozenset[date]

    @property
    def tz(self) -> ZoneInfo:
        """The calendar's timezone."""
        return ZoneInfo(self.tzname)


# All arithmetic is done on UTC-normalized instants. Aware-datetime subtraction
# and ``+ timedelta`` operate on the *wall clock* when the operands share a
# ZoneInfo, which is wrong across a DST boundary (a spring-forward night would
# count a full 8 hours, and adding minutes could produce a non-existent local
# time). Converting to UTC first makes every duration and advance real-time.


def _utc(instant: datetime) -> datetime:
    return instant.astimezone(timezone.utc)


def _intervals_for_day(
    spec: CalendarSpec, day: date
) -> list[tuple[datetime, datetime]]:
    """Return the UTC (start, end) working intervals for one local date.

    Window times are interpreted in the calendar's zone (so DST is applied when
    localizing), then normalized to UTC so callers can do real-time arithmetic.
    """
    if day in spec.holidays:
        return []

    intervals: list[tuple[datetime, datetime]] = []
    for start_t, end_t in spec.windows.get(day.weekday(), ()):
        start = _utc(datetime.combine(day, start_t, tzinfo=spec.tz))
        end = _utc(datetime.combine(day, end_t, tzinfo=spec.tz))
        if end > start:
            intervals.append((start, end))

    return intervals


def is_working_instant(spec: CalendarSpec, instant: datetime) -> bool:
    """Return whether ``instant`` falls inside a working window.

    The end of a window is exclusive, so an instant exactly at close-of-business
    is not itself working time.
    """
    moment = _utc(instant)
    local = instant.astimezone(spec.tz)
    for start, end in _intervals_for_day(spec, local.date()):
        if start <= moment < end:
            return True
    return False


def next_working_instant(spec: CalendarSpec, instant: datetime) -> datetime:
    """Return the earliest working instant at or after ``instant`` (in UTC)."""
    moment = _utc(instant)
    local_date = instant.astimezone(spec.tz).date()

    for offset in range(_MAX_DAYS):
        day = local_date + timedelta(days=offset)
        for start, end in _intervals_for_day(spec, day):
            if moment < start:
                return start
            if start <= moment < end:
                return moment

    raise NoWorkingTime(
        f'No working time within {_MAX_DAYS} days of {instant.isoformat()}'
    )


def working_minutes_between(
    spec: CalendarSpec, start: datetime, end: datetime
) -> float:
    """Return the real working minutes in the span ``[start, end]``.

    Zero when ``end <= start``. Only time inside working windows counts, so a
    span covering a weekend contributes only its weekday working hours, and one
    crossing a DST boundary counts real elapsed time.
    """
    span_start = _utc(start)
    span_end = _utc(end)
    if span_end <= span_start:
        return 0.0

    total = timedelta()
    day = start.astimezone(spec.tz).date()
    last_day = end.astimezone(spec.tz).date()

    while day <= last_day:
        for win_start, win_end in _intervals_for_day(spec, day):
            overlap_start = max(win_start, span_start)
            overlap_end = min(win_end, span_end)
            if overlap_end > overlap_start:
                total += overlap_end - overlap_start
        day += timedelta(days=1)

    return total.total_seconds() / 60.0


def add_working_minutes(
    spec: CalendarSpec, start: datetime, minutes: float
) -> datetime:
    """Return the UTC instant reached by consuming ``minutes`` of working time.

    Advancing begins at ``start`` (or the next working instant if ``start`` is
    outside a window) and skips nights, weekends and holidays. A zero or negative
    budget returns ``start`` unchanged.
    """
    if minutes <= 0:
        return start

    remaining = timedelta(minutes=minutes)
    cursor = _utc(start)
    local_date = start.astimezone(spec.tz).date()

    for offset in range(_MAX_DAYS):
        day = local_date + timedelta(days=offset)
        for win_start, win_end in _intervals_for_day(spec, day):
            # Only intervals at or after the cursor contribute.
            interval_start = max(win_start, cursor)
            if interval_start >= win_end:
                continue

            available = win_end - interval_start
            if available >= remaining:
                return interval_start + remaining
            remaining -= available
            cursor = win_end

    raise NoWorkingTime(
        f'Could not consume {minutes} working minutes within {_MAX_DAYS} days'
    )


def subtract_working_minutes(
    spec: CalendarSpec, end: datetime, minutes: float
) -> datetime:
    """Return the UTC start such that ``minutes`` of working time reach ``end``.

    The mirror of ``add_working_minutes``, walking backward. Used by the planner
    for finish/start-anchored dependencies (FF/SF), where the
    successor's *end* is constrained and its start must be derived. A zero or
    negative budget returns ``end`` unchanged.
    """
    if minutes <= 0:
        return end

    remaining = timedelta(minutes=minutes)
    cursor = _utc(end)
    local_date = end.astimezone(spec.tz).date()

    for offset in range(_MAX_DAYS):
        day = local_date - timedelta(days=offset)
        # Intervals of this day, latest first, clipped to at or before the cursor.
        for win_start, win_end in reversed(_intervals_for_day(spec, day)):
            interval_end = min(win_end, cursor)
            if interval_end <= win_start:
                continue

            available = interval_end - win_start
            if available >= remaining:
                return interval_end - remaining
            remaining -= available
            cursor = win_start

    raise NoWorkingTime(
        f'Could not reach back {minutes} working minutes within {_MAX_DAYS} days'
    )
