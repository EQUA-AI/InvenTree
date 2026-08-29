"""Complete-population maintenance analytics for the AI rails (S7, §8.3).

The sole ORM-level implementation for maintenance aggregates. Sits beside
``tasks.ai_read`` and inherits its three rules (the page is not the source,
the REST layer is not the source, projections are allow-lists) plus four of
its own:

**Populations are named, never unioned.** ``work_orders`` and
``maintenance_records`` are distinct event populations; a linked
``AssetMaintenanceRecord.work_order`` row enriches its work order and is
never a second event. No operation here accepts "both".

**Groupings are enums, not columns.** The grouping vocabulary maps to an
allow-listed column table; free-text narratives and performer identities are
structurally absent from that table, so they cannot be grouped no matter
what a caller sends. An unmapped grouping raises the typed
``grouping_unavailable`` — the rail renders a limitation, never a guess.

**Every result names its clock.** Aggregations echo ``date_field`` and the
IANA ``timezone`` actually applied (``AIMMS_PLANT_TIMEZONE``, falling back
to the server ``TIME_ZONE``); windows are half-open ``[from, to)`` after
conversion from that zone.

**An unavailable aggregate never claims an empty population.** Unlike the
conversational page, the fail-closed shape here reports
``complete_population=False`` with ``available=False`` — a switched-off flag
or an unresolved actor must abstain downstream, not validate a false zero.

This module never imports ``ai.*``; the analysis executor calls it through
its sync seam with plain values.
"""

from __future__ import annotations

import datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings

from .ai_read import PAGE_DATE_FIELDS, fence, maintenance_ai_read_enabled
from .models import WorkOrder
from .scope import (
    ScopeError,
    maintenance_record_scope_filter,
    require_maintenance_record_scope,
    work_order_scope_filter,
)

#: Display-group policy (owner-flagged defaults, 2026-08-29): a readable
#: answer shows at most this many groups; the hard cap bounds the query and
#: everything beyond it collapses into one server-counted remainder row.
MAX_OUTPUT_GROUPS = 12
HARD_GROUP_CAP = 24

#: Longest zero-filled bucket series any timeline may span.
MAX_BUCKETS = 36


class AnalyticsRequestError(Exception):
    """A typed vocabulary failure the analysis rail renders as a limitation.

    ``code`` is one of ``grouping_unavailable`` / ``date_field_unavailable``
    / ``window_invalid`` / ``bucket_range_exceeded`` /
    ``population_unavailable`` / ``selection_rule_unavailable`` — never free
    text. Authorization failures do NOT raise: they return the unavailable
    shape, so denial stays silent.
    """

    def __init__(self, code: str, message: str) -> None:
        """Store the typed code alongside the human message."""
        super().__init__(message)
        self.code = code


class PopulationType(StrEnum):
    """The two event populations. Never unioned (A7)."""

    WORK_ORDERS = 'work_orders'
    MAINTENANCE_RECORDS = 'maintenance_records'


class Grouping(StrEnum):
    """Approved grouping dimensions (§8.3): enums, not raw SQL columns."""

    MACHINE = 'machine'
    LIFECYCLE_STATUS = 'lifecycle_status'
    WORK_ORDER_TYPE = 'work_order_type'
    PRIORITY = 'priority'
    COMPONENT_REF = 'component_ref'
    COMPONENT_LABEL = 'component_label'


class TimeBucket(StrEnum):
    """Approved calendar buckets, computed in the plant timezone."""

    WEEK = 'week'
    MONTH = 'month'
    QUARTER = 'quarter'


#: Grouping → (key column, label column) for the work-order population.
#: This table IS the control: free-text narratives (``description``, closeout
#: prose) and performer identities (``assigned_to``, ``requested_by``) do not
#: appear here and therefore cannot be grouped. ``component_label`` is the
#: §8.3 "exact recorded structured label" grouping — exact stored values, no
#: invented fault taxonomy; its labels are operator text and get fenced.
_WORK_ORDER_GROUP_COLUMNS: dict[Grouping, tuple[str, str | None]] = {
    Grouping.MACHINE: ('machine_id', 'machine__name'),
    Grouping.LIFECYCLE_STATUS: ('lifecycle_status', None),
    Grouping.WORK_ORDER_TYPE: ('work_order_type', None),
    Grouping.PRIORITY: ('priority', None),
    Grouping.COMPONENT_REF: ('affected_component_ref', None),
    Grouping.COMPONENT_LABEL: ('affected_component', None),
}

#: The maintenance-record population groups by its machine only: `summary`
#: and `details` are narratives, `performed_by` is an identity — absent.
_MAINTENANCE_RECORD_GROUP_COLUMNS: dict[Grouping, tuple[str, str | None]] = {
    Grouping.MACHINE: ('machine_id', 'machine__name')
}

#: Repeat-interval analysis groups by stable event identity, nothing else.
_INTERVAL_GROUPINGS = frozenset({Grouping.MACHINE, Grouping.COMPONENT_REF})

#: Deterministic comparison candidate-selection rules (S9 entry point):
#: rule name → extra ORM filters on the completed-work-order base. The rule
#: table is the control — a model never orders or picks candidates.
_COMPARISON_RULES: dict[str, dict[str, Any]] = {
    'most_recent_completed_corrective': {'work_order_type': 'corrective'},
    'most_recent_completed': {},
}

#: How many ordered candidates the S9 gate may iterate before abstaining.
MAX_COMPARISON_CANDIDATES = 5


def plant_timezone() -> tuple[ZoneInfo, str]:
    """The deployment's analytics clock, as ``(tzinfo, IANA name)``.

    ``AIMMS_PLANT_TIMEZONE`` when set and valid, else the server
    ``TIME_ZONE`` (boot-validated). A misconfigured knob falls back rather
    than failing turns — every result echoes the zone actually applied, so
    the fallback is visible, never silent.
    """
    configured = str(getattr(settings, 'AIMMS_PLANT_TIMEZONE', '') or '').strip()
    if configured:
        try:
            return ZoneInfo(configured), configured
        except (KeyError, ValueError):
            pass
    name = str(settings.TIME_ZONE)
    return ZoneInfo(name), name


def _parse_window_date(value: str | None, *, edge: str) -> datetime.date | None:
    """Parse one ISO calendar-date window edge; typed error on garbage."""
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError as exc:
        raise AnalyticsRequestError(
            'window_invalid', f'{edge} is not an ISO calendar date: {value!r}'
        ) from exc


def _window_boundary(day: datetime.date, tz: ZoneInfo) -> datetime.datetime:
    """One window edge: the plant-zone midnight, in the storage convention.

    Production runs ``USE_TZ=True`` and takes the aware value as-is. The
    InvenTree test runner flips ``USE_TZ`` off (naive-local storage, and
    SQLite refuses aware values there), so the same instant is re-expressed
    on the server's naive clock instead of being silently reinterpreted.
    """
    value = datetime.datetime.combine(day, datetime.time.min, tzinfo=tz)
    if not getattr(settings, 'USE_TZ', True):
        server = ZoneInfo(str(settings.TIME_ZONE))
        value = value.astimezone(server).replace(tzinfo=None)
    return value


def _window_filter(
    date_field: str,
    date_from: str | None,
    date_to: str | None,
    *,
    date_only: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the half-open ``[from, to)`` filter in the plant zone.

    Returns ``(orm_filter_kwargs, applied_filter_echo)``. Calendar dates are
    interpreted as plant-timezone midnights, so "July" means July on the
    plant's clock, not the server's. ``date_only`` targets a ``DateField``
    (maintenance-record dates): calendar values compare directly and no
    clock conversion applies.
    """
    tz, tzname = plant_timezone()
    from_date = _parse_window_date(date_from, edge='date_from')
    to_date = _parse_window_date(date_to, edge='date_to')
    if from_date and to_date and from_date >= to_date:
        raise AnalyticsRequestError(
            'window_invalid', f'empty half-open window: [{from_date}, {to_date})'
        )

    orm: dict[str, Any] = {}
    echo: dict[str, Any] = {'date_field': date_field, 'timezone': tzname}
    if from_date:
        orm[f'{date_field}__gte'] = (
            from_date if date_only else _window_boundary(from_date, tz)
        )
        echo['from'] = from_date.isoformat()
    if to_date:
        orm[f'{date_field}__lt'] = (
            to_date if date_only else _window_boundary(to_date, tz)
        )
        echo['to'] = to_date.isoformat()
    return orm, echo


def _require_date_field(date_field: str) -> str:
    """Validate a work-order date-field selector against the allow-list."""
    if date_field not in PAGE_DATE_FIELDS:
        raise AnalyticsRequestError(
            'date_field_unavailable',
            f'{date_field!r} is not an approved work-order date field',
        )
    return date_field


def _unavailable(population_type: PopulationType, operation: str) -> dict[str, Any]:
    """The fail-closed shape: silent, and honest about knowing nothing."""
    return {
        'operation': operation,
        'population_type': str(population_type),
        'available': False,
        'population_count': 0,
        'complete_population': False,
        'applied_filters': {},
        'high_watermark': None,
    }


def _authorized_work_orders(user):
    """The actor's authorized work-order queryset, or ``None`` fail-closed."""
    if not maintenance_ai_read_enabled():
        return None
    if not getattr(user, 'is_authenticated', False):
        return None
    try:
        predicate = work_order_scope_filter(user)
    except ScopeError:
        return None
    return WorkOrder.objects.filter(predicate)


def get_work_order_dataset_profile(
    user,
    *,
    date_field: str = 'created_at',
    date_from: str | None = None,
    date_to: str | None = None,
    scope_machine_ids=None,
) -> dict[str, Any]:
    """Profile the complete authorized work-order population (§8.3 op 1).

    Total, date range by the selected field, null-date count, status/type
    counts, and machine-assignment coverage. ``unassigned_machine_count``
    (Q17) is computed BEFORE any machine narrowing — those orders are
    excluded from every per-asset population, and the note must still be
    able to say they exist.
    """
    from django.db.models import Count, Max, Min

    operation = 'work_order_dataset_profile'
    rows = _authorized_work_orders(user)
    if rows is None:
        return _unavailable(PopulationType.WORK_ORDERS, operation)

    _require_date_field(date_field)
    window, applied_filters = _window_filter(date_field, date_from, date_to)
    if window:
        rows = rows.filter(**window)

    unassigned = rows.filter(machine_id__isnull=True).count()
    scope_ids = (
        None if scope_machine_ids is None else {int(pk) for pk in scope_machine_ids}
    )
    if scope_ids is not None:
        rows = rows.filter(machine_id__in=scope_ids)
        applied_filters['machine_ids'] = sorted(scope_ids)

    bounds = rows.aggregate(
        population_count=Count('pk'),
        date_min=Min(date_field),
        date_max=Max(date_field),
        high_watermark=Max('updated_at'),
        machine_count=Count('machine_id', distinct=True),
    )
    null_dates = rows.filter(**{f'{date_field}__isnull': True}).count()

    def _value_counts(column: str) -> dict[str, int]:
        return {
            str(entry[column] or ''): entry['n']
            for entry in rows.values(column).annotate(n=Count('pk')).order_by(column)
        }

    return {
        'operation': operation,
        'population_type': str(PopulationType.WORK_ORDERS),
        'available': True,
        'population_count': bounds['population_count'],
        'complete_population': True,
        'date_field': date_field,
        'timezone': applied_filters['timezone'],
        'date_min': bounds['date_min'].isoformat() if bounds['date_min'] else None,
        'date_max': bounds['date_max'].isoformat() if bounds['date_max'] else None,
        'null_date_count': null_dates,
        'unassigned_machine_count': unassigned,
        'distinct_machine_count': bounds['machine_count'],
        'lifecycle_status_counts': _value_counts('lifecycle_status'),
        'work_order_type_counts': _value_counts('work_order_type'),
        'applied_filters': applied_filters,
        'high_watermark': (
            bounds['high_watermark'].isoformat() if bounds['high_watermark'] else None
        ),
    }


def aggregate_work_orders(
    user,
    *,
    grouping: str,
    date_field: str = 'created_at',
    date_from: str | None = None,
    date_to: str | None = None,
    scope_machine_ids=None,
) -> dict[str, Any]:
    """Server-side count by one approved dimension (§8.3 op 2).

    One grouping per call; time-bucketed series belong to the timeline
    operation, and a combined grouping-by-bucket matrix is deliberately not
    offered in v1. Group rows beyond ``HARD_GROUP_CAP`` collapse into a
    server-counted remainder — the model never sees an open-ended table.

    For the ``machine`` grouping, unassigned-machine orders are excluded
    from every group (Q17) and surfaced as ``unassigned_machine_count``.
    """
    from django.db.models import Count, Max

    operation = 'aggregate_work_orders'
    rows = _authorized_work_orders(user)
    if rows is None:
        return _unavailable(PopulationType.WORK_ORDERS, operation)

    try:
        grouping_key = Grouping(str(grouping))
    except ValueError:
        raise AnalyticsRequestError(
            'grouping_unavailable',
            f'{grouping!r} is not an approved grouping dimension',
        ) from None
    key_column, label_column = _WORK_ORDER_GROUP_COLUMNS[grouping_key]

    _require_date_field(date_field)
    window, applied_filters = _window_filter(date_field, date_from, date_to)
    if window:
        rows = rows.filter(**window)
    applied_filters['grouping'] = str(grouping_key)

    unassigned = 0
    if grouping_key is Grouping.MACHINE:
        unassigned = rows.filter(machine_id__isnull=True).count()
        rows = rows.filter(machine_id__isnull=False)

    scope_ids = (
        None if scope_machine_ids is None else {int(pk) for pk in scope_machine_ids}
    )
    if scope_ids is not None:
        rows = rows.filter(machine_id__in=scope_ids)
        applied_filters['machine_ids'] = sorted(scope_ids)

    population_count = rows.count()
    high_watermark = rows.aggregate(hw=Max('updated_at'))['hw']

    columns = [key_column] if label_column is None else [key_column, label_column]
    grouped = (
        rows
        .values(*columns)
        .annotate(group_count=Count('pk'))
        .order_by('-group_count', key_column)
    )
    total_groups = grouped.count()
    top = list(grouped[:HARD_GROUP_CAP])

    groups: list[dict[str, Any]] = []
    for entry in top:
        key = entry[key_column]
        if label_column is not None:
            label = fence(str(entry[label_column] or ''), limit=255) or None
        elif grouping_key is Grouping.COMPONENT_LABEL:
            label = fence(str(key or ''), limit=255) or None
        else:
            label = str(key or '')
        groups.append({
            'key': key if key is not None else '',
            'label': label,
            'group_count': entry['group_count'],
        })

    shown = sum(row['group_count'] for row in groups)
    return {
        'operation': operation,
        'population_type': str(PopulationType.WORK_ORDERS),
        'available': True,
        'grouping': str(grouping_key),
        'population_count': population_count,
        'evaluated_count': population_count,
        'complete_population': True,
        'date_field': date_field,
        'timezone': applied_filters['timezone'],
        'groups': groups,
        'total_group_count': total_groups,
        'groups_truncated': total_groups > len(groups),
        'remainder_group_count': max(0, total_groups - len(groups)),
        'remainder_count': max(0, population_count - shown),
        'unassigned_machine_count': unassigned,
        'applied_filters': applied_filters,
        'high_watermark': high_watermark.isoformat() if high_watermark else None,
    }


def _require_population(population: str) -> PopulationType:
    """Coerce a population selector; typed error keeps 'both' unsayable."""
    try:
        return PopulationType(str(population))
    except ValueError:
        raise AnalyticsRequestError(
            'population_unavailable', f'{population!r} is not a named event population'
        ) from None


def _authorized_maintenance_records(user):
    """The actor's authorized record queryset, or ``None`` fail-closed."""
    if not maintenance_ai_read_enabled():
        return None
    if not getattr(user, 'is_authenticated', False):
        return None

    from assets.models import AssetMaintenanceRecord

    try:
        predicate = maintenance_record_scope_filter(user)
    except ScopeError:
        return None
    return AssetMaintenanceRecord.objects.filter(predicate)


def _population_queryset(user, population: PopulationType):
    """Authorized rows for one named population, or ``None`` fail-closed."""
    if population is PopulationType.WORK_ORDERS:
        return _authorized_work_orders(user)
    return _authorized_maintenance_records(user)


def _population_date_field(population: PopulationType, date_field: str | None) -> str:
    """Resolve and validate the event clock for one population."""
    if population is PopulationType.WORK_ORDERS:
        return _require_date_field(date_field or 'created_at')
    resolved = date_field or 'date'
    if resolved != 'date':
        raise AnalyticsRequestError(
            'date_field_unavailable',
            f'{resolved!r} is not the maintenance-record event date',
        )
    return resolved


# --- calendar buckets -------------------------------------------------------


def _truncate_day(day: datetime.date, bucket: TimeBucket) -> datetime.date:
    """The bucket-start date containing ``day`` (ISO weeks; calendar units)."""
    if bucket is TimeBucket.WEEK:
        return day - datetime.timedelta(days=day.weekday())
    if bucket is TimeBucket.MONTH:
        return day.replace(day=1)
    quarter_month = ((day.month - 1) // 3) * 3 + 1
    return day.replace(month=quarter_month, day=1)


def _advance_day(day: datetime.date, bucket: TimeBucket) -> datetime.date:
    """The next bucket-start after ``day`` (itself a bucket start)."""
    if bucket is TimeBucket.WEEK:
        return day + datetime.timedelta(days=7)
    months = 1 if bucket is TimeBucket.MONTH else 3
    month_index = day.month - 1 + months
    return day.replace(
        year=day.year + month_index // 12, month=month_index % 12 + 1, day=1
    )


def _bucket_expression(bucket: TimeBucket, date_field: str, tz: ZoneInfo):
    """The ORM Trunc expression matching :func:`_truncate_day` exactly.

    ``tzinfo`` only applies under ``USE_TZ`` (Django rejects it otherwise);
    the naive test convention truncates on the stored clock, which is what
    :func:`_window_boundary` stored.
    """
    from django.db.models.functions import TruncMonth, TruncQuarter, TruncWeek

    trunc = {
        TimeBucket.WEEK: TruncWeek,
        TimeBucket.MONTH: TruncMonth,
        TimeBucket.QUARTER: TruncQuarter,
    }[bucket]
    if getattr(settings, 'USE_TZ', True):
        return trunc(date_field, tzinfo=tz)
    return trunc(date_field)


def _bucket_date(value, tz: ZoneInfo) -> datetime.date:
    """Normalize a Trunc result (datetime or date, aware or naive) to a date."""
    if isinstance(value, datetime.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(tz)
        return value.date()
    return value


def get_work_order_timeline(
    user,
    *,
    bucket: str,
    population: str = 'work_orders',
    date_field: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    scope_machine_ids=None,
) -> dict[str, Any]:
    """Zero-filled calendar series over one population (§8.3 op 3).

    Every bucket between the first and last is present — a silent gap and a
    zero month must be distinguishable. Series longer than ``MAX_BUCKETS``
    are a typed refusal, never a silently clipped chart. Null event dates
    are excluded from the series and counted.
    """
    operation = 'timeline'
    population_type = _require_population(population)
    rows = _population_queryset(user, population_type)
    if rows is None:
        return _unavailable(population_type, operation)

    try:
        bucket_key = TimeBucket(str(bucket))
    except ValueError:
        raise AnalyticsRequestError(
            'window_invalid', f'{bucket!r} is not an approved time bucket'
        ) from None
    resolved_field = _population_date_field(population_type, date_field)
    date_only = population_type is PopulationType.MAINTENANCE_RECORDS

    from django.db.models import Count, Max

    window, applied_filters = _window_filter(
        resolved_field, date_from, date_to, date_only=date_only
    )
    if window:
        rows = rows.filter(**window)
    applied_filters['bucket'] = str(bucket_key)

    scope_ids = (
        None if scope_machine_ids is None else {int(pk) for pk in scope_machine_ids}
    )
    if scope_ids is not None:
        rows = rows.filter(machine_id__in=scope_ids)
        applied_filters['machine_ids'] = sorted(scope_ids)

    population_count = rows.count()
    null_dates = rows.filter(**{f'{resolved_field}__isnull': True}).count()
    high_watermark = rows.aggregate(hw=Max('updated_at'))['hw']

    tz, _tzname = plant_timezone()
    dated = rows.exclude(**{f'{resolved_field}__isnull': True})
    counted = (
        dated
        .annotate(bucket_start=_bucket_expression(bucket_key, resolved_field, tz))
        .values('bucket_start')
        .annotate(n=Count('pk'))
        .order_by('bucket_start')
    )
    by_start: dict[datetime.date, int] = {
        _bucket_date(entry['bucket_start'], tz): entry['n']
        for entry in counted
        if entry['bucket_start'] is not None
    }

    from_date = _parse_window_date(date_from, edge='date_from')
    to_date = _parse_window_date(date_to, edge='date_to')
    starts = sorted(by_start)
    first = _truncate_day(from_date, bucket_key) if from_date else None
    if first is None and starts:
        first = starts[0]
    last = (
        _truncate_day(to_date - datetime.timedelta(days=1), bucket_key)
        if to_date
        else None
    )
    if last is None and starts:
        last = starts[-1]

    buckets: list[dict[str, Any]] = []
    if first is not None and last is not None and first <= last:
        cursor = first
        while cursor <= last:
            if len(buckets) >= MAX_BUCKETS:
                raise AnalyticsRequestError(
                    'bucket_range_exceeded',
                    f'series exceeds {MAX_BUCKETS} {bucket_key} buckets; '
                    'narrow the window or widen the bucket',
                )
            buckets.append({
                'bucket': cursor.isoformat(),
                'group_count': by_start.get(cursor, 0),
            })
            cursor = _advance_day(cursor, bucket_key)

    return {
        'operation': operation,
        'population_type': str(population_type),
        'available': True,
        'bucket': str(bucket_key),
        'population_count': population_count,
        'evaluated_count': population_count,
        'complete_population': True,
        'date_field': resolved_field,
        'timezone': applied_filters['timezone'],
        'buckets': buckets,
        'bucket_count': len(buckets),
        'null_date_count': null_dates,
        'applied_filters': applied_filters,
        'high_watermark': high_watermark.isoformat() if high_watermark else None,
    }


def get_repeat_intervals(
    user,
    *,
    grouping: str = 'machine',
    population: str = 'work_orders',
    date_field: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    scope_machine_ids=None,
) -> dict[str, Any]:
    """Consecutive-event intervals per group (§8.3 op 4).

    The grouping rule and the event clock are explicit in the result; an
    interval is the gap between successive events of the SAME group under
    that rule, nothing more — recurrence of a fault is a human conclusion.
    Groups are ranked by event count and capped like the aggregate.
    """
    import itertools
    import statistics

    operation = 'repeat_intervals'
    population_type = _require_population(population)
    rows = _population_queryset(user, population_type)
    if rows is None:
        return _unavailable(population_type, operation)

    columns = (
        _WORK_ORDER_GROUP_COLUMNS
        if population_type is PopulationType.WORK_ORDERS
        else _MAINTENANCE_RECORD_GROUP_COLUMNS
    )
    try:
        grouping_key = Grouping(str(grouping))
    except ValueError:
        grouping_key = None
    if (
        grouping_key is None
        or grouping_key not in _INTERVAL_GROUPINGS
        or grouping_key not in columns
    ):
        raise AnalyticsRequestError(
            'grouping_unavailable',
            f'{grouping!r} is not an approved interval grouping for {population_type}',
        )
    key_column, label_column = columns[grouping_key]
    resolved_field = _population_date_field(population_type, date_field)
    date_only = population_type is PopulationType.MAINTENANCE_RECORDS

    from django.db.models import Max

    window, applied_filters = _window_filter(
        resolved_field, date_from, date_to, date_only=date_only
    )
    if window:
        rows = rows.filter(**window)
    applied_filters['grouping'] = str(grouping_key)

    unassigned = 0
    if grouping_key is Grouping.MACHINE:
        unassigned = rows.filter(machine_id__isnull=True).count()
        rows = rows.filter(machine_id__isnull=False)

    scope_ids = (
        None if scope_machine_ids is None else {int(pk) for pk in scope_machine_ids}
    )
    if scope_ids is not None:
        rows = rows.filter(machine_id__in=scope_ids)
        applied_filters['machine_ids'] = sorted(scope_ids)

    null_dates = rows.filter(**{f'{resolved_field}__isnull': True}).count()
    dated = rows.exclude(**{f'{resolved_field}__isnull': True})
    population_count = dated.count()
    high_watermark = rows.aggregate(hw=Max('updated_at'))['hw']

    label_by_key: dict[Any, str | None] = {}
    events_by_key: dict[Any, list[Any]] = {}
    value_columns = (
        (key_column, resolved_field)
        if label_column is None
        else (key_column, label_column, resolved_field)
    )
    for entry in dated.values_list(*value_columns).order_by(key_column, resolved_field):
        key = entry[0]
        when = entry[-1]
        events_by_key.setdefault(key, []).append(when)
        if label_column is not None and key not in label_by_key:
            label_by_key[key] = fence(str(entry[1] or ''), limit=255) or None

    def _days_between(earlier, later) -> float:
        if isinstance(earlier, datetime.datetime):
            return round((later - earlier).total_seconds() / 86400.0, 1)
        return float((later - earlier).days)

    ranked = sorted(
        events_by_key.items(), key=lambda item: (-len(item[1]), str(item[0]))
    )
    groups: list[dict[str, Any]] = []
    for key, events in ranked[:HARD_GROUP_CAP]:
        gaps = [
            _days_between(earlier, later)
            for earlier, later in itertools.pairwise(events)
        ]
        groups.append({
            'key': key if key is not None else '',
            'label': label_by_key.get(key)
            if label_column is not None
            else str(key or ''),
            'event_count': len(events),
            'interval_count': len(gaps),
            'min_days': min(gaps) if gaps else None,
            'median_days': round(statistics.median(gaps), 1) if gaps else None,
            'mean_days': round(statistics.fmean(gaps), 1) if gaps else None,
            'max_days': max(gaps) if gaps else None,
        })

    shown_events = sum(row['event_count'] for row in groups)
    return {
        'operation': operation,
        'population_type': str(population_type),
        'available': True,
        'grouping': str(grouping_key),
        'population_count': population_count,
        'evaluated_count': population_count,
        'complete_population': True,
        'date_field': resolved_field,
        'timezone': applied_filters['timezone'],
        'groups': groups,
        'total_group_count': len(events_by_key),
        'groups_truncated': len(events_by_key) > len(groups),
        'remainder_group_count': max(0, len(events_by_key) - len(groups)),
        'remainder_count': max(0, population_count - shown_events),
        'unassigned_machine_count': unassigned,
        'null_date_count': null_dates,
        'applied_filters': applied_filters,
        'high_watermark': high_watermark.isoformat() if high_watermark else None,
    }


def get_work_order_durations(
    user,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    scope_machine_ids=None,
) -> dict[str, Any]:
    """Execution-duration statistics (§8.3 op 5): both actuals, or excluded.

    Only rows carrying BOTH ``actual_started_at`` and ``actual_completed_at``
    qualify; the missing and the impossible (completed before started) are
    counted, never imputed. The window means the completion clock.
    """
    import statistics

    operation = 'work_order_durations'
    rows = _authorized_work_orders(user)
    if rows is None:
        return _unavailable(PopulationType.WORK_ORDERS, operation)

    from django.db.models import Max

    window, applied_filters = _window_filter('actual_completed_at', date_from, date_to)
    if window:
        rows = rows.filter(**window)

    scope_ids = (
        None if scope_machine_ids is None else {int(pk) for pk in scope_machine_ids}
    )
    if scope_ids is not None:
        rows = rows.filter(machine_id__in=scope_ids)
        applied_filters['machine_ids'] = sorted(scope_ids)

    population_count = rows.count()
    high_watermark = rows.aggregate(hw=Max('updated_at'))['hw']

    paired = rows.exclude(actual_started_at__isnull=True).exclude(
        actual_completed_at__isnull=True
    )
    durations: list[float] = []
    invalid = 0
    for started, completed in paired.values_list(
        'actual_started_at', 'actual_completed_at'
    ):
        minutes = (completed - started).total_seconds() / 60.0
        if minutes < 0:
            invalid += 1
            continue
        durations.append(minutes)

    qualifying = len(durations)
    return {
        'operation': operation,
        'population_type': str(PopulationType.WORK_ORDERS),
        'available': True,
        'population_count': population_count,
        'evaluated_count': population_count,
        'complete_population': True,
        'date_field': 'actual_completed_at',
        'timezone': applied_filters['timezone'],
        'qualifying_count': qualifying,
        'excluded_missing_count': max(0, population_count - qualifying - invalid),
        'excluded_invalid_count': invalid,
        'min_minutes': round(min(durations), 1) if durations else None,
        'median_minutes': round(statistics.median(durations), 1) if durations else None,
        'mean_minutes': round(statistics.fmean(durations), 1) if durations else None,
        'max_minutes': round(max(durations), 1) if durations else None,
        'applied_filters': applied_filters,
        'high_watermark': high_watermark.isoformat() if high_watermark else None,
    }


def select_comparison_candidate(
    user,
    *,
    rule: str = 'most_recent_completed_corrective',
    machine_id=None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Deterministically ordered comparison candidates (§8.3 op 6).

    The rule table picks and orders — never a model. Returns an ordered
    candidate id list so the S9 gate can try the next candidate when one
    lacks required facets; a known-insufficient record is never "used
    anyway". Candidates are completed orders with a real completion time.
    """
    operation = 'select_comparison_candidate'
    rows = _authorized_work_orders(user)
    if rows is None:
        return _unavailable(PopulationType.WORK_ORDERS, operation)

    rule_name = str(rule)
    rule_filters = _COMPARISON_RULES.get(rule_name)
    if rule_filters is None:
        raise AnalyticsRequestError(
            'selection_rule_unavailable',
            f'{rule!r} is not a deterministic selection rule',
        )

    window, applied_filters = _window_filter('actual_completed_at', date_from, date_to)
    rows = rows.filter(
        lifecycle_status='completed', actual_completed_at__isnull=False, **rule_filters
    )
    if window:
        rows = rows.filter(**window)
    applied_filters['rule'] = rule_name
    if machine_id is not None:
        rows = rows.filter(machine_id=int(machine_id))
        applied_filters['machine_ids'] = [int(machine_id)]

    population_count = rows.count()
    candidates = list(
        rows.order_by('-actual_completed_at', '-pk').values_list('pk', flat=True)[
            :MAX_COMPARISON_CANDIDATES
        ]
    )
    return {
        'operation': operation,
        'population_type': str(PopulationType.WORK_ORDERS),
        'available': True,
        'rule': rule_name,
        'population_count': population_count,
        'complete_population': True,
        'candidates': candidates,
        'returned_count': len(candidates),
        'date_field': 'actual_completed_at',
        'timezone': applied_filters['timezone'],
        'applied_filters': applied_filters,
    }


def authorized_maintenance_record(user, record_id):
    """Load one record scope-safely; denial never discloses existence."""
    if not maintenance_ai_read_enabled():
        return None
    if not getattr(user, 'is_authenticated', False):
        return None

    from assets.models import AssetMaintenanceRecord

    try:
        pk = int(record_id)
    except (TypeError, ValueError):
        return None
    record = (
        AssetMaintenanceRecord.objects.select_related('machine').filter(pk=pk).first()
    )
    if record is None:
        return None
    try:
        require_maintenance_record_scope(user, record)
    except ScopeError:
        return None
    return record


def _operand_versions(
    rows,
    *,
    date_field: str,
    date_from: str | None,
    date_to: str | None,
    date_only: bool,
    scope_machine_ids,
    require_machine: bool,
    limit: int | None,
) -> dict[str, Any]:
    """The shared operand scan: ordered ``(pk, updated_at)`` version rows.

    The snapshot hash is computed over this list (see
    ``ai/core/analysis/snapshot.py`` — the vocabulary is ``pk:updated_at``
    for both populations). ``limit`` fetches one extra row so overflow is
    a fact, never a guess.
    """
    window, _echo = _window_filter(date_field, date_from, date_to, date_only=date_only)
    if window:
        rows = rows.filter(**window)
    if require_machine:
        rows = rows.filter(machine_id__isnull=False)
    scope_ids = (
        None if scope_machine_ids is None else {int(pk) for pk in scope_machine_ids}
    )
    if scope_ids is not None:
        rows = rows.filter(machine_id__in=scope_ids)

    ordered = rows.order_by('pk').values_list('pk', 'updated_at')
    fetched = list(ordered[: limit + 1] if limit is not None else ordered)
    overflow = limit is not None and len(fetched) > limit
    if overflow:
        fetched = fetched[:limit]
    return {
        'available': True,
        'rows': [
            (pk, updated.isoformat() if updated else '') for pk, updated in fetched
        ],
        'overflow': overflow,
    }


def work_order_operand_versions(
    user,
    *,
    date_field: str = 'created_at',
    date_from: str | None = None,
    date_to: str | None = None,
    scope_machine_ids=None,
    require_machine: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Version rows for the work-order population under the same filters.

    ``require_machine`` mirrors the machine-grouped aggregate (Q17:
    unassigned orders are outside every per-asset population, so they are
    outside its membership too).
    """
    rows = _authorized_work_orders(user)
    if rows is None:
        return {'available': False, 'rows': [], 'overflow': False}
    _require_date_field(date_field)
    return _operand_versions(
        rows,
        date_field=date_field,
        date_from=date_from,
        date_to=date_to,
        date_only=False,
        scope_machine_ids=scope_machine_ids,
        require_machine=require_machine,
        limit=limit,
    )


def maintenance_record_operand_versions(
    user,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    scope_machine_ids=None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Version rows for the maintenance-record population (``date`` clock)."""
    rows = _authorized_maintenance_records(user)
    if rows is None:
        return {'available': False, 'rows': [], 'overflow': False}
    return _operand_versions(
        rows,
        date_field='date',
        date_from=date_from,
        date_to=date_to,
        date_only=True,
        scope_machine_ids=scope_machine_ids,
        require_machine=False,
        limit=limit,
    )


def get_maintenance_evidence(user, work_order_id, *, identity=None) -> dict[str, Any]:
    """The S9 evidence bundle (§8.3 op 7): distinct stages, never blended.

    One authorized work order with its effective closeout, its linked
    maintenance record (enrichment — withheld when the record's own machine
    scope denies the actor, without hiding the work order), and structured
    procedure/deviation presence counts. Symptom, cause, action, outcome and
    administrative status arrive as separate fields; none is ever filled
    from another.
    """
    from .ai_read import authorized_work_order, work_order_closeout, work_order_row

    operation = 'maintenance_evidence'
    work_order = authorized_work_order(user, work_order_id)
    if work_order is None:
        return _unavailable(PopulationType.WORK_ORDERS, operation)

    record = getattr(work_order, 'maintenance_record', None)
    record_row = None
    record_withheld = False
    if record is not None:
        try:
            require_maintenance_record_scope(user, record)
        except ScopeError:
            record_withheld = True
        else:
            record_row = {
                'record_id': record.pk,
                'machine_id': record.machine_id,
                'date': record.date.isoformat(),
                'summary': fence(record.summary, limit=255),
                'details': fence(record.details) or None,
                'created_at': record.created_at.isoformat(),
                'updated_at': record.updated_at.isoformat(),
            }

    return {
        'operation': operation,
        'population_type': str(PopulationType.WORK_ORDERS),
        'available': True,
        'work_order': work_order_row(work_order, identity=identity),
        'closeout': work_order_closeout(work_order),
        'maintenance_record': record_row,
        'maintenance_record_withheld': record_withheld,
        'procedure_application_count': work_order.procedure_applications.count(),
        'deviation_count': work_order.deviations.count(),
    }


__all__ = [
    'HARD_GROUP_CAP',
    'MAX_BUCKETS',
    'MAX_COMPARISON_CANDIDATES',
    'MAX_OUTPUT_GROUPS',
    'AnalyticsRequestError',
    'Grouping',
    'PopulationType',
    'TimeBucket',
    'aggregate_work_orders',
    'authorized_maintenance_record',
    'get_maintenance_evidence',
    'get_repeat_intervals',
    'get_work_order_dataset_profile',
    'get_work_order_durations',
    'get_work_order_timeline',
    'maintenance_record_operand_versions',
    'plant_timezone',
    'select_comparison_candidate',
    'work_order_operand_versions',
]
