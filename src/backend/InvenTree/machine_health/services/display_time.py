"""Present stored history as though it had just happened.

The migrated estate holds one month of real plant history at its true observation
times, which are over a year behind the wall clock. A dashboard built around
"now" therefore shows nothing: every default window addresses a stretch the
source was never going to have anything in, and a date picker offers months
where no reading exists.

This moves the *presentation* of those times and nothing else. One offset is
derived per station - enough to bring its newest reading up to the present - so
its history reads up to the present. O27 offers the most recent 30 days when
live presentation is on; O23's disclosure remains available when it is off.
A window the operator picks
is shifted back before it reaches the source, and the timestamps that come back
are shifted forward before they reach the browser. What is stored, queried and
reconciled stays at the plant's own clock, which is what keeps the migration
checkable against Cassandra.

Two things are deliberate.

**The offset is derived, never stored.** It is recomputed from the recorded data
range on each request, so the newest reading is always "just now" rather than
drifting a day further into the past every day. The cost is that one sample
shows a slightly different time on successive loads; for a feed presented as
live that is the honest behaviour, since a live feed's newest sample is always
now.

**It is reported, not hidden.** Every response carrying shifted times says so
and by how much. A reader who needs the plant's real clock - an engineer
matching a trace against a logbook, anyone reconciling against the source - can
recover it exactly, and nothing in the API pretends 2026 data exists.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

#: Below this the shift is pointless: the source is already current, and moving
#: times by a few seconds would only make them harder to reason about.
MINIMUM_SHIFT = timedelta(hours=1)

#: The owner-approved presentation horizon. Complete historian scans retain
#: their smaller safety budget; long windows use bounded sampled series reads.
LIVE_WINDOW_SECONDS = 30 * 24 * 3600


def recorded_telemetry_as_live() -> bool:
    """Whether O27's live presentation supersedes O23's history disclosure."""
    return settings.AIMMS_RECORDED_TELEMETRY_AS_LIVE


def presentation_metadata(shift: timedelta) -> dict:
    """Report the presentation policy and the recoverable plant-clock offset."""
    return {
        'recorded_telemetry_as_live': recorded_telemetry_as_live(),
        'telemetry_presentation': 'live'
        if recorded_telemetry_as_live() or not shift
        else 'recorded_history',
        'display_shifted': bool(shift),
        'display_shift_seconds': int(shift.total_seconds()),
    }


def source_read_failed(source, *, error_code='') -> bool:
    """Keep a scoped read failure separate from missing or aged observations."""
    return bool(
        error_code
        or (
            source
            and source.last_error_at
            and (
                source.last_success_at is None
                or source.last_error_at > source.last_success_at
            )
        )
    )


def source_reporting(source, observed_at, *, now=None, error_code=''):
    """Judge reporting from a displayed observation, retaining real failures.

    Poll times remain diagnostic facts. A quiet poller alone does not make a
    current shifted reading unavailable, but an unresolved connector failure,
    disabled source or missing/aged observation still does. ``None`` keeps the
    existing O23 poll-based presentation when the switch is off.
    """
    if not recorded_telemetry_as_live():
        return None
    if source is None or not source.active or observed_at is None:
        return False
    if source_read_failed(source, error_code=error_code):
        return False
    age = ((now or timezone.now()) - observed_at).total_seconds()
    return 0 <= age <= source.freshness_threshold_seconds


def recorded_range(station, source):
    """Return the ``(from, to)`` instants recorded for a station, or None.

    Written by the ``discover_data_range`` command; see that command for why the
    edges cannot be found cheaply at request time.
    """
    if station is None or source is None:
        return None
    ranges = (source.config or {}).get('data_ranges') or {}
    entry = ranges.get(str(station.source_entity_uuid))
    if not entry or not entry.get('from') or not entry.get('to'):
        return None
    try:
        start = timezone.datetime.fromisoformat(entry['from'])
        end = timezone.datetime.fromisoformat(entry['to'])
    except (TypeError, ValueError):
        return None
    return start, end


def display_shift(station, source, *, now=None) -> timedelta:
    """How far forward stored times are moved when presented.

    Zero when the station's range is unknown, or when the data is already recent
    enough that shifting it would be noise rather than help.
    """
    span = recorded_range(station, source)
    if span is None:
        return timedelta(0)
    shift = (now or timezone.now()) - span[1]
    return shift if shift >= MINIMUM_SHIFT else timedelta(0)


def to_source(moment, shift: timedelta):
    """Move an instant the operator chose back onto the plant's own clock."""
    return moment - shift if moment is not None else None


def to_display(moment, shift: timedelta):
    """Move a stored instant forward into the clock the dashboard shows."""
    return moment + shift if moment is not None else None
