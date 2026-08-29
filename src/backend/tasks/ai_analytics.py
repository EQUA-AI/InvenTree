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
from .scope import ScopeError, work_order_scope_filter

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
    / ``window_invalid`` — never free text. Authorization failures do NOT
    raise: they return the unavailable shape, so denial stays silent.
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
    date_field: str, date_from: str | None, date_to: str | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the half-open ``[from, to)`` datetime filter in the plant zone.

    Returns ``(orm_filter_kwargs, applied_filter_echo)``. Calendar dates are
    interpreted as plant-timezone midnights, so "July" means July on the
    plant's clock, not the server's.
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
        orm[f'{date_field}__gte'] = _window_boundary(from_date, tz)
        echo['from'] = from_date.isoformat()
    if to_date:
        orm[f'{date_field}__lt'] = _window_boundary(to_date, tz)
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


__all__ = [
    'HARD_GROUP_CAP',
    'MAX_BUCKETS',
    'MAX_OUTPUT_GROUPS',
    'AnalyticsRequestError',
    'Grouping',
    'PopulationType',
    'TimeBucket',
    'aggregate_work_orders',
    'get_work_order_dataset_profile',
    'plant_timezone',
]
