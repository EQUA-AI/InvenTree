"""Promoted reference contract logic from the EQUA demo metrics companion.

This is a bounded, reviewed copy of ``LocalDocs/UiUpgrades/
EQUA_Demo_Metrics_Data_Companion/demo_dataset.py`` promoted into tracked
runtime/test resources (work package A). The LocalDocs originals stay
unchanged; this copy exists so the backend can validate and cross-check the
frozen fixture without importing ignored files.

Pure standard library only: no Django, no database, no network. Nothing here
executes automatically.

Semantic notes preserved from the reference contract:

* the fixture reference calculator uses ``>=`` threshold semantics
  (``machine_state``); the application backend uses strict ``>`` via
  ``MachineSignalBinding.classify``. Both are documented and tested in
  ``contract.py`` / ``test_demo_metrics_contract.py`` — do not silently change
  either side;
* ``quality: valid`` is a fixture vocabulary value; the backend equivalent is
  ``good`` (translated in ``contract.py``, never here);
* the minimal history calculator requires non-overlapping fully observed
  planned windows and unions overlapping downtime per machine.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc
OPEN_STATES = frozenset({'planned', 'ready', 'in_progress', 'on_hold', 'verifying'})
FIXTURE_SCHEMA_VERSION = 'equa.demo-fixture/1'
DATASET_KEY = 'equa-demo-metrics-v1'


def timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp that must carry a timezone."""
    if not isinstance(value, str):
        raise ValueError('Timestamp must be an ISO 8601 string with timezone')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError(f'Invalid timestamp: {value}') from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Naive timestamps are not allowed')
    return result.astimezone(UTC)


def iso(value: datetime) -> str:
    """Render a timezone-aware datetime as UTC ISO-8601."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Timezone-aware datetime required')
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def aware_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    The deployment stores datetimes in the project's ``USE_TZ`` convention,
    where naive means UTC wall time; artifact arithmetic always runs on aware
    UTC. This converts without changing the represented instant.
    """
    if not isinstance(value, datetime):
        raise ValueError('A datetime is required')
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def finite(value: Any, name: str) -> float:
    """Return a finite float or reject the value."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f'{name} must be a finite number')
    return float(value)


def content_hash(value: Any) -> str:
    """Canonical JSON content hash (sorted keys, no NaN)."""
    body = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def file_byte_hash(path) -> str:
    """SHA-256 of the raw file bytes (distinct from the canonical content hash)."""
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def merge_seconds(
    intervals: Iterable[tuple[datetime, datetime]], start: datetime, end: datetime
) -> float:
    """Union duration of intervals clipped to ``[start, end)``; no double count."""
    if start >= end:
        raise ValueError('Window must have positive duration')
    clipped = []
    for a, b in intervals:
        if a >= b:
            raise ValueError('Interval must have positive duration')
        left, right = max(a, start), min(b, end)
        if left < right:
            clipped.append((left, right))
    clipped.sort()
    merged: list[tuple[datetime, datetime]] = []
    for a, b in clipped:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return sum((b - a).total_seconds() for a, b in merged)


def location_scope(data: dict, key: str | None, descendants: bool = True) -> set[str]:
    """Return the location keys inside a fixture location scope."""
    parents = {n['key']: n['parent_key'] for n in data['locations']}
    if len(parents) != len(data['locations']):
        raise ValueError('Duplicate location key')
    for node in parents:
        seen = set()
        current = node
        while current is not None:
            if current not in parents:
                raise ValueError('Missing location parent')
            if current in seen:
                raise ValueError('Location cycle')
            seen.add(current)
            current = parents[current]
    if key is None:
        return set(parents)
    if key not in parents:
        raise ValueError('Unknown location scope')
    selected = {key}
    if descendants:
        changed = True
        while changed:
            previous = len(selected)
            selected.update(n for n, parent in parents.items() if parent in selected)
            changed = len(selected) != previous
    return selected


def validate(data: dict) -> None:
    """Validate the bounded fixture contract (promoted from the companion)."""
    if data.get('synthetic') is not True or data.get('allow_production') is not False:
        raise ValueError('Fixture must be synthetic and disallow production')
    if data.get('schema_version') != FIXTURE_SCHEMA_VERSION:
        raise ValueError('Unsupported fixture schema')
    t = timestamp(data['as_of'])
    locations = location_scope(data, None)
    machines = {m['key']: m for m in data['machines']}
    if len(machines) != len(data['machines']):
        raise ValueError('Duplicate machine key')
    for m in machines.values():
        if m['location_key'] not in locations or m.get('synthetic') is not True:
            raise ValueError('Invalid machine/location or missing synthetic flag')
    signals = {s['key']: s for s in data['signal_definitions']}
    if len(signals) != len(data['signal_definitions']):
        raise ValueError('Duplicate signal definition')
    for s in signals.values():
        if finite(s['warning_at'], 'warning') >= finite(s['critical_at'], 'critical'):
            raise ValueError('Invalid threshold order')
    source_keys = {s['key'] for s in data['sources']}
    seen_bindings = set()
    for binding in data['bindings']:
        pair = (binding['machine_key'], binding['signal_key'])
        if pair in seen_bindings:
            raise ValueError('Duplicate binding')
        seen_bindings.add(pair)
        if (
            pair[0] not in machines
            or pair[1] not in signals
            or binding['source_key'] not in source_keys
        ):
            raise ValueError('Unknown binding reference')
        if not machines[pair[0]]['configured']:
            raise ValueError('Unconfigured machine has binding')
    seen = set()
    for reading in data['readings']:
        if reading['key'] in seen:
            raise ValueError('Duplicate reading key')
        seen.add(reading['key'])
        pair = (reading['machine_key'], reading['signal_key'])
        if pair not in seen_bindings:
            raise ValueError('Unbound reading')
        if reading['unit'] != signals[pair[1]]['unit']:
            raise ValueError('Unit mismatch')
        finite(reading['value'], 'reading value')
        if reading.get('synthetic') is not True or reading['quality'] != 'valid':
            raise ValueError(
                'Only explicit synthetic valid readings supported by this fixture'
            )
        matching = next(
            b for b in data['bindings'] if (b['machine_key'], b['signal_key']) == pair
        )
        if matching['source_key'] != reading['source_key']:
            raise ValueError('Reading source does not match binding')
        if timestamp(reading['observed_at']) > t:
            raise ValueError('Future observation')
    seen = set()
    for order in data['work_orders']:
        if order['key'] in seen or order['machine_key'] not in machines:
            raise ValueError('Duplicate order key or unknown machine')
        seen.add(order['key'])
        if order['requested_state'] not in OPEN_STATES | {'completed'}:
            raise ValueError('Unknown fixture work-order state')
        if order['active'] != (order['requested_state'] in OPEN_STATES):
            raise ValueError('Active flag conflicts with fixture state')
        if order.get('synthetic') is not True:
            raise ValueError('Missing work-order provenance')
        timestamp(order['due_at'])
    history = data['history']
    if timestamp(history['start_at']) >= timestamp(history['end_at']):
        raise ValueError('Invalid history window')
    for collection in ('coverage_intervals', 'downtime_intervals'):
        seen = set()
        for event in history[collection]:
            if event['key'] in seen or event['machine_key'] not in machines:
                raise ValueError('Duplicate event or unknown machine')
            seen.add(event['key'])
            if (
                event['location_at_event_key'] not in locations
                or event.get('synthetic') is not True
            ):
                raise ValueError('Invalid event attribution/provenance')
            if timestamp(event['start_at']) >= timestamp(event['end_at']):
                raise ValueError('Invalid event interval')
    # This minimal calculator requires non-overlapping, fully observed planned windows.
    for key in machines:
        ranges = sorted(
            (timestamp(c['start_at']), timestamp(c['end_at']))
            for c in history['coverage_intervals']
            if c['machine_key'] == key
        )
        if any(ranges[i][0] < ranges[i - 1][1] for i in range(1, len(ranges))):
            raise ValueError('Overlapping planned windows')
    calculate_oee(data['oee_lab'])


def calculate_oee(case: dict) -> dict[str, float | None]:
    """Disabled OEE lab calculation; kept for contract completeness only."""
    planned = finite(case['planned_seconds'], 'planned_seconds')
    run = finite(case['run_seconds'], 'run_seconds')
    ideal = finite(case['ideal_cycle_seconds'], 'ideal_cycle_seconds')
    total, good = case['total_count'], case['first_pass_good_count']
    if any(
        isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in (total, good)
    ):
        raise ValueError('Counts must be nonnegative integers')
    if planned < 0 or run < 0 or run > planned or ideal <= 0 or good > total:
        raise ValueError('Invalid OEE inputs')
    if total * ideal > run + 1e-9:
        raise ValueError('Performance exceeds 100%; investigate inputs, do not clamp')
    return {
        'availability': run / planned if planned else None,
        'performance': ideal * total / run if run else None,
        'quality': good / total if total else None,
        'oee': ideal * good / planned if planned else None,
    }


def machine_state(data: dict, machine_key: str) -> dict:
    """Reference coverage/condition projection (fixture ``>=`` semantics)."""
    m = next(m for m in data['machines'] if m['key'] == machine_key)
    if not m['configured']:
        return {'coverage': 'not_configured', 'condition': None}
    required = {
        b['signal_key'] for b in data['bindings'] if b['machine_key'] == machine_key
    }
    latest = {}
    for r in data['readings']:
        if r['machine_key'] == machine_key and (
            r['signal_key'] not in latest
            or timestamp(r['observed_at'])
            > timestamp(latest[r['signal_key']]['observed_at'])
        ):
            latest[r['signal_key']] = r
    if not latest:
        return {'coverage': 'never_seen', 'condition': None}
    if not required or not required.issubset(latest):
        return {'coverage': 'partial', 'condition': None}
    t = timestamp(data['as_of'])
    if any(
        (t - timestamp(r['observed_at'])).total_seconds()
        > data['clock_policy']['stale_after_seconds']
        for r in latest.values()
    ):
        return {'coverage': 'stale', 'condition': None}
    signals = {s['key']: s for s in data['signal_definitions']}
    rank = 0
    for key, reading in latest.items():
        value, signal = reading['value'], signals[key]
        rank = max(
            rank,
            2
            if value >= signal['critical_at']
            else 1
            if value >= signal['warning_at']
            else 0,
        )
    return {'coverage': 'fresh', 'condition': ['normal', 'warning', 'critical'][rank]}
