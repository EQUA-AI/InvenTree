"""Read-only maintenance dashboard projections, independent of the AI read gate.

Every calculation starts from the current actor's scoped population. Board cards
and maintenance-history rows never participate in work-order counts.
"""

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from statistics import mean, median

from django.db.models import Exists, F, OuterRef, Prefetch, Subquery
from django.utils import timezone

from .ai_analytics import plant_timezone
from .models import WorkOrder, WorkOrderPart
from .scope import machine_scope_filter, work_order_scope_filter

OPEN = frozenset({'planned', 'ready', 'in_progress', 'on_hold', 'verifying'})
METRICS = frozenset({
    'open',
    'completed',
    'overdue',
    'preventive',
    'assignment',
    'age',
    'alerts',
    'holds',
    'verification',
    'pm-on-time',
    'elapsed',
    'mix',
    'repeat',
    'downtime',
    'data-health',
})
PERIOD_METRICS = frozenset({
    'completed',
    'pm-on-time',
    'elapsed',
    'mix',
    'repeat',
    'downtime',
})
MACHINE_METRICS = frozenset({'alerts', 'data-health'})


def local_datetime(value, zone):
    """Interpret naive test/storage values in the server timezone."""
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_default_timezone())
    return value.astimezone(zone)


class MetricOptions:
    """Validate public filters and resolve the plant's reporting clock."""

    def __init__(self, params, *, now=None):
        """Resolve validated filters and inclusive UI dates to half-open clocks."""
        self.zone, self.zone_name = plant_timezone()
        self.now = local_datetime(now or timezone.now(), self.zone)
        self.today = self.now.date()
        self.metric = params.get('metric', 'open')
        if self.metric not in METRICS:
            raise ValueError('Unknown metric')
        self.offset = int(params.get('offset', 0))
        if not 0 <= self.offset <= 1000000:
            raise ValueError('Invalid offset')
        self.group = params.get('group', '')
        self.period = params.get('period', 'month')
        self.horizon = int(params.get('horizon', 7))
        if self.horizon not in {7, 14, 30}:
            raise ValueError('Invalid due-soon window')
        starts = {
            'today': self.today,
            'week': self.today - timedelta(days=self.today.weekday()),
            'month': self.today.replace(day=1),
            '30': self.today - timedelta(days=29),
            '90': self.today - timedelta(days=89),
        }
        if self.period == 'custom':
            self.start_date = date.fromisoformat(params.get('from', ''))
            self.end_date = date.fromisoformat(params.get('to', ''))
        elif self.period in starts:
            self.start_date, self.end_date = starts[self.period], self.today
        else:
            raise ValueError('Invalid period')
        if (
            self.end_date < self.start_date
            or (self.end_date - self.start_date).days > 3660
        ):
            raise ValueError('Choose a date range of at most ten years')
        self.start = datetime.combine(self.start_date, time.min, self.zone)
        self.end = min(
            datetime.combine(self.end_date + timedelta(days=1), time.min, self.zone),
            self.now,
        )
        self.filters = {}
        for key in ('machine', 'client', 'assigned_to'):
            if params.get(key):
                value = int(params[key])
                if value <= 0:
                    raise ValueError('Invalid identity filter')
                self.filters[key] = value
        for key, choices in (
            ('priority', {'low', 'medium', 'high'}),
            (
                'type',
                {'corrective', 'preventive', 'inspection', 'calibration', 'other'},
            ),
            ('criticality', {'critical', 'high', 'medium', 'low'}),
            ('mine', {'true'}),
        ):
            if params.get(key):
                if params[key] not in choices:
                    raise ValueError('Invalid filter')
                self.filters[key] = params[key]
        if self.metric in MACHINE_METRICS and any(
            k in self.filters for k in ('assigned_to', 'mine', 'priority', 'type')
        ):
            raise ValueError('Work-order filters do not apply to machine metrics')

    def in_period(self, value):
        """Completion dates are half-open; missing timestamps never qualify."""
        return (
            value is not None
            and self.start <= local_datetime(value, self.zone) < self.end
        )


def _machines(actor, options, *, apply_filters=True):
    from assets.models import AssetMachine

    rows = AssetMachine.objects.filter(machine_scope_filter(actor))
    if apply_filters and 'machine' in options.filters:
        rows = rows.filter(pk=options.filters['machine'])
    if apply_filters and 'client' in options.filters:
        rows = rows.filter(client_id=options.filters['client'])
    # Validate the profile in Python, matching the canonical profile reader.
    from assets.machine_profile import declared_profile

    machines = {}
    for row in rows.select_related('client').iterator():
        profile = declared_profile(row)
        criticality = (profile or {}).get('criticality')
        if (
            apply_filters
            and options.filters.get('criticality')
            and criticality != options.filters['criticality']
        ):
            continue
        machines[row.pk] = {
            'pk': row.pk,
            'label': row.name,
            'active': row.active,
            'criticality': criticality,
            'client_id': row.client_id,
            'client_label': row.client.name if row.client else None,
        }
    return machines


def _orders(actor, options, machines):
    from .workorder_models import WorkOrderEvent

    rows = WorkOrder.objects.filter(work_order_scope_filter(actor))
    f = options.filters
    if any(k in f for k in ('machine', 'client', 'criticality')):
        rows = rows.filter(machine_id__in=machines)
    for key, field in (
        ('priority', 'priority'),
        ('type', 'work_order_type'),
        ('assigned_to', 'assigned_to_id'),
    ):
        if key in f:
            rows = rows.filter(**{field: f[key]})
    if f.get('mine'):
        rows = rows.filter(assigned_to_id=actor.pk)
    rows = rows.annotate(
        parts_gap=Exists(
            WorkOrderPart.objects.filter(
                work_order_id=OuterRef('pk'), allocated_quantity__lt=F('quantity')
            )
        ),
        verifying_since=Subquery(
            WorkOrderEvent.objects
            .filter(work_order_id=OuterRef('pk'), to_status='verifying')
            .order_by('-created_at')
            .values('created_at')[:1]
        ),
    )
    fields = [
        'pk',
        'reference',
        'title',
        'lifecycle_status',
        'work_order_type',
        'priority',
        'is_active',
        'created_at',
        'actual_completed_at',
        'actual_started_at',
        'due_date',
        'assigned_to_id',
        'assigned_to__username',
        'assignee',
        'estimated_minutes',
        'machine_id',
        'hold_reason',
        'parts_gap',
        'verifying_since',
    ]
    result = list(rows.values(*fields).order_by('pk').iterator())
    for row in result:
        machine = machines.get(row['machine_id'])
        row.update(
            model='workorder',
            label=row['reference'] or row['title'],
            machine_label=machine['label'] if machine else None,
            visible_machine=row['machine_id'] if machine else None,
            groups=[],
            assigned_user=row['assigned_to__username'],
        )
    return result


def _group(row, key, label=None, value=None):
    row['groups'].append((str(key), str(label if label is not None else key), value))


def _work_metric(actor, o, machines):
    rows = _orders(actor, o, machines)
    opened = [r for r in rows if r['is_active'] and r['lifecycle_status'] in OPEN]
    completed = [
        r
        for r in rows
        if r['lifecycle_status'] == 'completed'
        and o.in_period(r['actual_completed_at'])
    ]
    missing = Counter()
    stats = {}
    value = None
    unit = 'work_orders'
    metric = o.metric
    selected = opened
    if metric in PERIOD_METRICS:
        selected = completed
        missing['completion_date_unknown_period'] = sum(
            r['lifecycle_status'] == 'completed' and r['actual_completed_at'] is None
            for r in rows
        )
    if metric == 'open':
        for r in selected:
            _group(r, r['lifecycle_status'])
        stats['drafts'] = sum(
            r['is_active'] and r['lifecycle_status'] == 'draft' for r in rows
        )
    elif metric == 'completed':
        span = max(timedelta(0), o.end.astimezone(UTC) - o.start.astimezone(UTC))
        previous_start = (o.start.astimezone(UTC) - span).astimezone(o.zone)
        previous = sum(
            r['lifecycle_status'] == 'completed'
            and r['actual_completed_at'] is not None
            and previous_start
            <= local_datetime(r['actual_completed_at'], o.zone)
            < o.start
            for r in rows
        )
        stats.update(
            previous=previous,
            change=len(completed) - previous,
            comparison_from=previous_start.isoformat(),
            comparison_to=o.start.isoformat(),
        )
        for r in selected:
            day = local_datetime(r['actual_completed_at'], o.zone).date()
            if (o.end_date - o.start_date).days > 90:
                day = day.replace(day=1)
            elif (o.end_date - o.start_date).days > 31:
                day -= timedelta(days=day.weekday())
            _group(r, day.isoformat())
    elif metric in {'overdue', 'preventive'}:
        population = [
            r
            for r in opened
            if metric != 'preventive' or r['work_order_type'] == 'preventive'
        ]
        missing['due_date'] = sum(r['due_date'] is None for r in population)
        selected = []
        for r in population:
            due = r['due_date']
            if due is None:
                continue
            if due < o.today:
                _group(r, 'overdue')
                selected.append(r)
            elif metric == 'preventive' and due <= o.today + timedelta(days=o.horizon):
                _group(r, 'today' if due == o.today else 'upcoming')
                selected.append(r)
        selected.sort(key=lambda r: (r['priority'] != 'high', r['due_date'], r['pk']))
    elif metric == 'assignment':
        for r in selected:
            _group(
                r,
                r['assigned_to_id'] or 'unassigned',
                r['assigned_to__username'] or 'unassigned',
            )
        stats['unassigned'] = sum(r['assigned_to_id'] is None for r in selected)
        stats['legacy_assignment'] = sum(
            r['assigned_to_id'] is None and bool(r['assignee']) for r in selected
        )
        stats['estimated_minutes'] = sum(r['estimated_minutes'] or 0 for r in selected)
        missing['estimate'] = sum(r['estimated_minutes'] is None for r in selected)
    elif metric == 'age':
        for r in selected:
            age = (o.today - local_datetime(r['created_at'], o.zone).date()).days
            key = (
                'invalid'
                if age < 0
                else next(
                    (
                        label
                        for limit, label in [
                            (7, '0-7'),
                            (14, '8-14'),
                            (30, '15-30'),
                            (60, '31-60'),
                        ]
                        if age <= limit
                    ),
                    '61+',
                )
            )
            _group(r, key)
        selected.sort(key=lambda r: (r['created_at'], r['pk']))
    elif metric == 'holds':
        selected = [
            r for r in opened if r['lifecycle_status'] == 'on_hold' or r['parts_gap']
        ]
        for r in selected:
            if r['lifecycle_status'] == 'on_hold':
                _group(r, 'on_hold')
            if r['parts_gap']:
                _group(r, 'parts_gap')
        stats['overlap'] = sum(
            r['lifecycle_status'] == 'on_hold' and r['parts_gap'] for r in selected
        )
    elif metric == 'verification':
        selected = [r for r in opened if r['lifecycle_status'] == 'verifying']
        missing['verification_start'] = sum(
            r['verifying_since'] is None for r in selected
        )
        for r in selected:
            _group(r, 'verifying')
        selected.sort(
            key=lambda r: (
                r['verifying_since'] is None,
                r['verifying_since'] or o.now,
                r['pk'],
            )
        )
    elif metric == 'pm-on-time':
        eligible = [
            r
            for r in rows
            if r['work_order_type'] == 'preventive'
            and r['lifecycle_status'] not in {'draft', 'canceled'}
        ]
        missing['due_date'] = sum(r['due_date'] is None for r in eligible)
        selected = [
            r
            for r in eligible
            if r['due_date'] is not None
            and o.start_date <= r['due_date'] <= min(o.end_date, o.today)
        ]
        for r in selected:
            completion = r['actual_completed_at']
            key = 'unfinished'
            if r['lifecycle_status'] == 'completed':
                key = (
                    'unknown'
                    if completion is None
                    else 'on_time'
                    if local_datetime(completion, o.zone).date() <= r['due_date']
                    else 'late'
                )
            _group(r, key)
        numerator = sum(r['groups'][0][0] == 'on_time' for r in selected)
        stats.update(numerator=numerator, denominator=len(selected))
        value = round(100 * numerator / len(selected), 1) if selected else None
        unit = 'percent'
    elif metric == 'elapsed':
        selected = []
        durations = []
        for r in completed:
            if r['actual_started_at'] is None:
                missing['actual_start'] += 1
                continue
            minutes = (
                r['actual_completed_at'] - r['actual_started_at']
            ).total_seconds() / 60
            if minutes < 0:
                missing['invalid_duration'] += 1
                continue
            durations.append(minutes)
            r['minutes'] = round(minutes, 1)
            selected.append(r)
        value = round(median(durations), 1) if durations else None
        stats['mean_minutes'] = round(mean(durations), 1) if durations else None
        unit = 'minutes'
        selected.sort(key=lambda r: (-r['minutes'], r['pk']))
    elif metric == 'mix':
        for r in selected:
            _group(r, r['work_order_type'])
    elif metric == 'repeat':
        candidates = [
            r
            for r in completed
            if r['work_order_type'] == 'corrective' and r['visible_machine'] is not None
        ]
        counts = Counter(r['machine_id'] for r in candidates)
        selected = [r for r in candidates if counts[r['machine_id']] >= 2]
        for r in selected:
            _group(r, r['machine_id'], r['machine_label'])
        value = sum(n >= 2 for n in counts.values())
        missing['machine_not_visible'] = sum(
            r['work_order_type'] == 'corrective' and r['visible_machine'] is None
            for r in completed
        )
        unit = 'machines'
    elif metric == 'downtime':
        from .closeout_models import CloseoutAmendment, CloseoutAmendmentStatus
        from .services.closeout_amend import effective_closeout_overview
        from .workorder_models import WorkOrderCloseout

        closeouts = WorkOrderCloseout.objects.filter(
            work_order_id__in=[r['pk'] for r in completed]
        ).prefetch_related(
            Prefetch(
                'amendments',
                queryset=CloseoutAmendment.objects.filter(
                    status=CloseoutAmendmentStatus.APPLIED
                ).order_by('-applied_at', '-pk'),
                to_attr='applied_amendments',
            )
        )
        fields = {c.work_order_id: effective_closeout_overview(c) for c in closeouts}
        selected = []
        for r in completed:
            projection = fields.get(r['pk'], {})
            minutes = projection.get('downtime_minutes')
            if type(minutes) is not int or minutes < 0:
                missing['downtime'] += 1
                continue
            r.update(minutes=minutes, amended=projection['amended'])
            _group(
                r,
                r['visible_machine'] or 'unknown',
                r['machine_label'] or 'unknown',
                minutes,
            )
            selected.append(r)
        value = sum(r['minutes'] for r in selected) if selected else None
        stats['population'] = len(completed)
        unit = 'minutes'
    stats['high_priority'] = sum(r['priority'] == 'high' for r in selected)
    return selected, value, unit, dict(missing), stats


def _signal_health(o, machines):
    from assets.health_models import MachineSignalBinding

    bindings = MachineSignalBinding.objects.filter(
        machine_id__in=machines, active=True
    ).select_related('source', 'state')
    by_machine = defaultdict(list)
    connector_issues = set()
    for binding in bindings.iterator():
        state = getattr(binding, 'state', None)
        source = binding.source
        stale = (
            state is None
            or (o.now - local_datetime(state.observed_at, o.zone)).total_seconds()
            > source.freshness_threshold_seconds
        )
        bad = (
            state is None
            or state.quality != 'good'
            or not source.active
            or not source.connection_healthy
        )
        by_machine[binding.machine_id].append((stale, bad))
        if not source.active or not source.connection_healthy:
            connector_issues.add(binding.machine_id)
    states = {}
    for pk in machines:
        signals = by_machine[pk]
        states[pk] = (
            'unmapped'
            if not signals
            else 'stale'
            if all(stale for stale, _ in signals)
            else 'degraded'
            if any(stale or bad for stale, bad in signals)
            else 'current'
        )
    return states, connector_issues


def _machine_metric(actor, o, machines):
    from assets.health_models import MachineAnomaly

    active = {pk: r for pk, r in machines.items() if r['active']}
    states, connector_issues = _signal_health(o, active)
    selected = []
    if o.metric == 'alerts':
        anomalies = MachineAnomaly.objects.filter(
            machine_id__in=active,
            status__in=['open', 'acknowledged'],
            severity__in=['warning', 'critical'],
        ).order_by('first_observed_at')
        by_machine = defaultdict(list)
        alerts = list(
            anomalies.values(
                'machine_id',
                'severity',
                'first_observed_at',
                'last_observed_at',
                'work_order_id',
            ).iterator()
        )
        visible_orders = {
            r['pk']: r['reference'] or r['title']
            for r in WorkOrder.objects.filter(
                work_order_scope_filter(actor),
                pk__in=[a['work_order_id'] for a in alerts if a['work_order_id']],
            ).values('pk', 'reference', 'title')
        }
        for alert in alerts:
            by_machine[alert['machine_id']].append(alert)
        for pk, machine_alerts in by_machine.items():
            row = dict(
                active[pk], model='assetmachine', groups=[], data_health=states[pk]
            )
            severity = (
                'critical'
                if any(a['severity'] == 'critical' for a in machine_alerts)
                else 'warning'
            )
            row.update(
                alert_count=len(machine_alerts),
                first_observed_at=machine_alerts[0]['first_observed_at'],
                last_observed_at=max(a['last_observed_at'] for a in machine_alerts),
                linked_orders=[
                    {'pk': key, 'label': visible_orders[key]}
                    for key in sorted({
                        a['work_order_id']
                        for a in machine_alerts
                        if a['work_order_id'] in visible_orders
                    })
                ],
            )
            _group(row, severity)
            selected.append(row)
        selected.sort(
            key=lambda r: (
                r['groups'][0][0] != 'critical',
                r['first_observed_at'],
                r['pk'],
            )
        )
    else:
        for pk, machine in active.items():
            row = dict(machine, model='assetmachine', groups=[], data_health=states[pk])
            _group(row, states[pk])
            selected.append(row)
    return (
        selected,
        None,
        'machines',
        {},
        {'population': len(active), 'connector_issues': len(connector_issues)},
    )


def dashboard_metric(actor, options):
    """Calculate a complete authorized metric and its matching paginated rows."""
    machines = _machines(actor, options)
    rows, value, unit, missing, stats = (
        _machine_metric(actor, options, machines)
        if options.metric in MACHINE_METRICS
        else _work_metric(actor, options, machines)
    )
    groups = {}
    for row in rows:
        for key, label, amount in row['groups']:
            group = groups.setdefault(
                key,
                {
                    'key': key,
                    'label': label,
                    'count': 0,
                    'value': 0,
                    'high_priority': 0,
                    'overdue': 0,
                },
            )
            group['high_priority'] += int(row.get('priority') == 'high')
            group['overdue'] += int(
                row.get('due_date') is not None and row['due_date'] < options.today
            )
            group['count'] += 1
            group['value'] += amount if amount is not None else 1
    if value is None and unit in {'work_orders', 'machines'}:
        value = len(rows)
    population_count = len(rows)
    if options.group == '_drafts' and options.metric == 'open':
        rows = [
            r
            for r in _orders(actor, options, machines)
            if r['is_active'] and r['lifecycle_status'] == 'draft'
        ]
    elif options.group:
        rows = [r for r in rows if any(g[0] == options.group for g in r['groups'])]
    # Serialize only this page. Private assignment text and inaccessible asset IDs
    # never leave the service. All selected rows derive from the same predicates.
    public = {
        'pk',
        'model',
        'label',
        'machine_label',
        'visible_machine',
        'due_date',
        'lifecycle_status',
        'priority',
        'work_order_type',
        'actual_completed_at',
        'created_at',
        'verifying_since',
        'hold_reason',
        'minutes',
        'amended',
        'alert_count',
        'first_observed_at',
        'last_observed_at',
        'criticality',
        'assigned_user',
        'data_health',
        'linked_orders',
        'parts_gap',
    }
    page = [
        {k: v for k, v in row.items() if k in public}
        for row in rows[options.offset : options.offset + 25]
    ]
    next_offset = options.offset + len(page)
    available_machines = _machines(actor, options, apply_filters=False)
    clients = {
        str(m['client_id']): m['client_label']
        for m in available_machines.values()
        if m['client_id']
    }
    assignees = []
    if options.metric not in MACHINE_METRICS:
        assignees = [
            {'value': str(pk), 'label': name}
            for pk, name in WorkOrder.objects
            .filter(work_order_scope_filter(actor), assigned_to__isnull=False)
            .order_by('assigned_to_id')
            .values_list('assigned_to_id', 'assigned_to__username')
            .distinct()
        ]
    if options.metric == 'completed':
        groups = dict(sorted(groups.items()))
    elif options.metric in {'assignment', 'repeat'}:
        groups = dict(
            sorted(
                groups.items(), key=lambda item: (-item[1]['count'], item[1]['label'])
            )
        )
    return {
        'id': options.metric,
        'version': 1,
        'state': 'ready',
        'complete': True,
        'value': value,
        'unit': unit,
        'record_count': population_count,
        'detail_count': len(rows),
        'groups': list(groups.values()),
        'missing': missing,
        'stats': stats,
        'records': page,
        'next_offset': next_offset if next_offset < len(rows) else None,
        'observed_at': options.now.isoformat(),
        'timezone': options.zone_name,
        'reporting_date': options.today.isoformat(),
        'snapshot': 'live',
        'clock': 'due_date'
        if options.metric == 'pm-on-time'
        else 'actual_completed_at'
        if options.metric in PERIOD_METRICS
        else 'now',
        'from': options.start.isoformat(),
        'to': options.end.isoformat(),
        'partial_period': options.end_date >= options.today,
        'filters': options.filters,
        'clients': [{'value': pk, 'label': name} for pk, name in clients.items()],
        'assignees': assignees,
        'machines': [
            {'value': str(pk), 'label': m['label']}
            for pk, m in available_machines.items()
        ],
    }
