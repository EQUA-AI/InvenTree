"""Historical coverage/downtime import and aggregation (work package G).

The fixture's 28 planned-coverage intervals and 13 downtime intervals are
imported as immutable synthetic historical evidence with explicit
``synthetic_scenario`` attribution: current placements never prove where an
asset was two weeks ago, so no placement is backdated and no chart claims
placement-at-event. Aggregation uses UTC half-open intervals ``[start, end)``,
clips complete planned coverage to the window, clips downtime to covered
planned intervals and unions overlapping losses per machine before summing.

Missing observation coverage yields ``null`` (never zero); availability is
``(planned - downtime) / planned`` for the measured cohort with ``null`` for a
zero denominator. Machine percentages are never averaged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from assets.demo_metrics_models import (
    DemoMetricsCoverageInterval,
    DemoMetricsDowntimeInterval,
    DemoMetricsObject,
)

from . import contract, reference

UTC = timezone.utc

#: Accepted loss categories for the bounded synthetic history.
LOSS_CATEGORIES = frozenset({'unplanned_loss_of_function'})

#: Attribution vocabulary; the only supported mode is explicit synthetic data.
ATTRIBUTION_MODE = 'synthetic_scenario'


class HistoryError(Exception):
    """The history import or aggregation request is invalid."""

    def __init__(self, code: str, message: str):
        """Carry a stable machine-readable code plus a human message."""
        self.code = code
        super().__init__(f'{code}: {message}')


def _aware(value: datetime) -> datetime:
    from django.conf import settings
    from django.utils import timezone as dj_tz

    if settings.USE_TZ:
        return dj_tz.make_aware(value) if dj_tz.is_naive(value) else value
    return dj_tz.make_naive(value) if dj_tz.is_aware(value) else value


def import_history(
    *, fixture: contract.Fixture, mapping: contract.ResolvedMapping, session
):
    """Import the fixture's coverage/downtime intervals for one session.

    Mapped machine membership is required; intervals are translated with the
    same session-anchor shift as current data, bounded in count and window,
    and stored with explicit synthetic attribution.
    """
    data = fixture.data['history']
    coverage = data['coverage_intervals']
    downtime = data['downtime_intervals']
    if len(coverage) + len(downtime) > contract.MAX_HISTORY_INTERVALS:
        raise HistoryError(
            'BATCH_TOO_LARGE', 'History import exceeds the bounded interval count'
        )

    membership = {row.alias: row.machine for row in session.machines.all()}
    anchor = session.anchor_at
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    else:
        anchor = anchor.astimezone(UTC)

    window_start = contract.translate_instant(
        reference.timestamp(data['start_at']), fixture.as_of, anchor
    )
    window_end = contract.translate_instant(
        reference.timestamp(data['end_at']), fixture.as_of, anchor
    )
    if (window_end - window_start) > timedelta(days=contract.MAX_INTERVAL_DAYS):
        raise HistoryError(
            'WINDOW_TOO_LARGE', 'History window exceeds the bounded length'
        )

    created = {'coverage': 0, 'downtime': 0}
    seen_ranges: dict[str, list[tuple[datetime, datetime]]] = {}

    for event in sorted(coverage, key=lambda e: e['key']):
        alias = event['machine_key']
        machine = membership.get(alias)
        if machine is None:
            raise HistoryError(
                'OUT_OF_SESSION', f'{event["key"]} names a non-session machine'
            )
        if (
            event.get('synthetic') is not True
            or event.get('observation_complete') is not True
        ):
            raise HistoryError(
                'NOT_SYNTHETIC', f'{event["key"]} lacks synthetic provenance'
            )
        start = contract.translate_instant(
            reference.timestamp(event['start_at']), fixture.as_of, anchor
        )
        end = contract.translate_instant(
            reference.timestamp(event['end_at']), fixture.as_of, anchor
        )
        if end <= start:
            raise HistoryError(
                'INVALID_INTERVAL', f'{event["key"]} is not a positive interval'
            )
        for other_start, other_end in seen_ranges.get(alias, []):
            if start < other_end and other_start < end:
                raise HistoryError(
                    'OVERLAPPING_COVERAGE',
                    f'{event["key"]} overlaps planned coverage for {alias}',
                )
        seen_ranges.setdefault(alias, []).append((start, end))
        DemoMetricsCoverageInterval.objects.create(
            session=session,
            event_key=event['key'],
            machine=machine,
            start_at=_aware(start),
            end_at=_aware(end),
            fully_observed=True,
            planned=True,
            location_alias=event.get('location_at_event_key', ''),
            attribution_mode=ATTRIBUTION_MODE,
        )
        _seed_history_ledger(
            DemoMetricsObject.objects.create(
                session=session,
                kind=DemoMetricsObject.Kind.COVERAGE_INTERVAL,
                fixture_key=event['key'],
                origin=DemoMetricsObject.Origin.CREATED,
            )
        )
        created['coverage'] += 1

    for event in sorted(downtime, key=lambda e: e['key']):
        alias = event['machine_key']
        machine = membership.get(alias)
        if machine is None:
            raise HistoryError(
                'OUT_OF_SESSION', f'{event["key"]} names a non-session machine'
            )
        if event.get('synthetic') is not True:
            raise HistoryError(
                'NOT_SYNTHETIC', f'{event["key"]} lacks synthetic provenance'
            )
        category = event.get('classification')
        if category not in LOSS_CATEGORIES:
            raise HistoryError(
                'BAD_CATEGORY', f'{event["key"]} has an unsupported loss category'
            )
        start = contract.translate_instant(
            reference.timestamp(event['start_at']), fixture.as_of, anchor
        )
        end = contract.translate_instant(
            reference.timestamp(event['end_at']), fixture.as_of, anchor
        )
        if end <= start:
            raise HistoryError(
                'INVALID_INTERVAL', f'{event["key"]} is not a positive interval'
            )
        DemoMetricsDowntimeInterval.objects.create(
            session=session,
            event_key=event['key'],
            machine=machine,
            start_at=_aware(start),
            end_at=_aware(end),
            loss_category=category,
            failure_key=event.get('failure_key', ''),
            location_alias=event.get('location_at_event_key', ''),
            attribution_mode=ATTRIBUTION_MODE,
        )
        _seed_history_ledger(
            DemoMetricsObject.objects.create(
                session=session,
                kind=DemoMetricsObject.Kind.DOWNTIME_INTERVAL,
                fixture_key=event['key'],
                origin=DemoMetricsObject.Origin.CREATED,
            )
        )
        created['downtime'] += 1

    return created


def _seed_history_ledger(row: DemoMetricsObject) -> DemoMetricsObject:
    """Seed one history ledger row in the shared cleanup fingerprint space.

    The seed is the full fingerprint of the *persisted* interval row - the
    same schema ``cleanup.object_fingerprint`` re-reads for every drift check
    - so a clean, untouched import is never reported as an operator edit. The
    fixture-event digest (``planner_hash``) is input provenance and is never
    the row's ownership fingerprint.
    """
    from . import cleanup

    fingerprint = cleanup.object_fingerprint(row)
    row.seed_fingerprint = fingerprint
    row.last_fingerprint = fingerprint
    row.save(update_fields=['seed_fingerprint', 'last_fingerprint'])
    return row


def planner_hash(event: dict) -> str:
    """Stable digest of a raw fixture history event (input provenance only).

    This hashes the pre-translation fixture event and is deliberately *not*
    the ledger row fingerprint: row ownership is judged exclusively in the
    shared cleanup fingerprint space of the persisted, translated rows.
    """
    from .planner import canonical_hash

    return canonical_hash({'event': event})


@dataclass(frozen=True)
class HistoryWindow:
    """A requested aggregation window in UTC."""

    start: datetime
    end: datetime

    def __post_init__(self):
        """Reject empty or naive windows."""
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise HistoryError('BAD_WINDOW', 'History windows must be timezone-aware')
        if self.end <= self.start:
            raise HistoryError(
                'BAD_WINDOW', 'History window must have positive duration'
            )


def aggregate_history(
    *, session, start: datetime, end: datetime, machine_ids=None
) -> dict:
    """Aggregate planned/lost machine-minutes for the session's history.

    Returns daily buckets plus totals, the measured/selected cohort sizes,
    completeness and bounded contributing interval ids with synthetic
    provenance. Buckets without planned coverage report ``null`` downtime.
    """
    window = HistoryWindow(start=start.astimezone(UTC), end=end.astimezone(UTC))
    if (window.end - window.start) > timedelta(days=contract.MAX_INTERVAL_DAYS):
        raise HistoryError(
            'WINDOW_TOO_LARGE', 'History window exceeds the bounded length'
        )

    memberships = {row.machine_id: row for row in session.machines.all()}
    selected = sorted(memberships)
    if machine_ids is not None:
        requested = set(machine_ids)
        unknown = requested - set(selected)
        if unknown:
            raise HistoryError(
                'OUT_OF_SESSION', 'Requested machines are not session members'
            )
        selected = sorted(requested)
    if not selected:
        raise HistoryError('EMPTY_COHORT', 'No machines selected for aggregation')

    coverage_rows = list(
        DemoMetricsCoverageInterval.objects.filter(
            session=session, machine_id__in=selected, fully_observed=True, planned=True
        ).order_by('machine_id', 'start_at')
    )
    downtime_rows = list(
        DemoMetricsDowntimeInterval.objects.filter(
            session=session, machine_id__in=selected
        ).order_by('machine_id', 'start_at')
    )

    def to_utc(row_time):
        return (
            row_time.astimezone(UTC)
            if row_time.tzinfo
            else row_time.replace(tzinfo=UTC)
        )

    buckets = []
    measured = set()
    # Buckets are UTC calendar days ``[00:00, 24:00)``, clipped to the
    # requested window: a partial first/last day reports only the window's
    # share of that day, never a full day of machine-minutes.
    day = window.start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < window.end:
        day_end = day + timedelta(days=1)
        bucket_start = max(day, window.start)
        bucket_end = min(day_end, window.end)
        planned_seconds = 0.0
        down_seconds = 0.0
        contributing = []
        for machine_id in selected:
            windows = []
            for row in coverage_rows:
                if row.machine_id != machine_id:
                    continue
                left = max(to_utc(row.start_at), bucket_start)
                right = min(to_utc(row.end_at), bucket_end)
                if left < right:
                    windows.append((left, right))
                    measured.add(machine_id)
                    contributing.append(row.event_key)
            if not windows:
                continue
            for left, right in windows:
                planned_seconds += (right - left).total_seconds()
            events = []
            for row in downtime_rows:
                if row.machine_id != machine_id:
                    continue
                left = max(to_utc(row.start_at), bucket_start)
                right = min(to_utc(row.end_at), bucket_end)
                if left < right:
                    events.append((left, right))
                    contributing.append(row.event_key)
            for left, right in windows:
                down_seconds += reference.merge_seconds(events, left, right)
        buckets.append({
            'date': day.date().isoformat(),
            'planned_machine_minutes': planned_seconds / 60,
            'downtime_machine_minutes': (down_seconds / 60)
            if planned_seconds
            else None,
            'contributing_interval_ids': sorted(set(contributing)),
        })
        day = day_end

    planned = sum(bucket['planned_machine_minutes'] for bucket in buckets)
    down = sum(bucket['downtime_machine_minutes'] or 0 for bucket in buckets)
    return {
        'synthetic': True,
        'attribution_mode': ATTRIBUTION_MODE,
        'session_key': session.session_key,
        'window': {
            'start': reference.iso(window.start),
            'end': reference.iso(window.end),
            'timezone': 'UTC',
        },
        'selected_machines': len(selected),
        'measured_machines': len(measured),
        'completeness': 'partial_measured_cohort'
        if len(measured) < len(selected)
        else 'complete',
        'daily': buckets,
        'planned_machine_minutes': planned,
        'downtime_machine_minutes': down if planned else None,
        'measured_cohort_availability': ((planned - down) / planned)
        if planned
        else None,
    }
