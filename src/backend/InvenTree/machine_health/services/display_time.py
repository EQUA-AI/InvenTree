"""Present stored history as though it had just happened.

The migrated estate holds ten days of real plant history at its true observation
times, which are over a year behind the wall clock. A dashboard built around
"now" therefore shows nothing: every default window addresses a stretch the
source was never going to have anything in, and a date picker offers months
where no reading exists.

This moves the *presentation* of those times and nothing else. One offset is
derived per station - enough to bring its newest reading up to the present - so
its ten days of history read as the last ten days. A window the operator picks
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

from django.utils import timezone

#: Below this the shift is pointless: the source is already current, and moving
#: times by a few seconds would only make them harder to reason about.
MINIMUM_SHIFT = timedelta(hours=1)


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
