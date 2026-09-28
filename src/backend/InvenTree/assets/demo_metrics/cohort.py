"""Shared authorized cohort selector and coverage projection (work package D).

The cohort is the intersection of actor-authorized machines, session
membership and current physical location scope. Work-order counts additionally
select session-owned/referenced order membership, so other pre-existing demo
orders on the same machines never contaminate the session count.

Coverage is deliberately distinct from health condition
(:func:`machine_health.services.summary.health_summary` keeps its own
``offline`` semantics and anomaly influence untouched):

* ``not_configured``: no required active demo bindings,
* ``never_seen``: configured, no states received,
* ``stale``: observations exist but none are fresh,
* ``partial``: some required values are missing, stale, invalid or unsuitable
  quality,
* ``fresh``: every required signal is present, good quality and fresh.

Condition (normal/warning/critical) is computed only from fresh, good-quality
values with the backend's strict ``>`` threshold semantics and stays
independent of anomaly severity. A partial/poor-quality machine is never
presented as fully healthy because one numeric reading is normal.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils import timezone

from tasks.dashboard_metrics import OPEN

COVERAGE_STATES = ('not_configured', 'never_seen', 'stale', 'partial', 'fresh')
CONDITIONS = ('normal', 'warning', 'critical')

#: Open lifecycle set used everywhere in this module (existing application set).
OPEN_STATES = tuple(OPEN)


class CohortError(Exception):
    """The requested cohort cannot be authorized or resolved."""

    def __init__(self, code: str, message: str):
        """Carry a stable machine-readable code plus a human message."""
        self.code = code
        super().__init__(f'{code}: {message}')


def session_cohort(session, actor, *, location_id=None, descendants=True):
    """Return the authorized session machine rows for this actor.

    Intersection of: session membership (``DemoMetricsMachine``), the actor's
    machine scope filter, and — when ``location_id`` is given — the current
    physical location scope (direct or with descendants).

    Authority is judged against ALL retained memberships before the
    active-only projection: a cleaned session retains its memberships as
    inactive claims, and an actor authorized for none of them is an outsider
    exactly as on an active session. An actor authorized for retained
    membership keeps an explicit empty active projection (zeros plus session
    metadata is never handed to a foreign scoped actor). A session retaining
    no membership proves nobody's authority: ownership is never invented
    from the caller or the demo-owner evidence, so every actor is denied.
    """
    from tasks.scope import ScopeError, machine_scope_filter

    from assets import locations as location_tools
    from assets.models import AssetMachine

    rows = session.machines.select_related(
        'machine', 'machine__physical_location'
    ).filter(claim_active=True)
    try:
        # The scope predicate speaks AssetMachine fields; translate it through
        # the machine table instead of applying it to the membership rows.
        allowed = AssetMachine.objects.filter(machine_scope_filter(actor)).values('pk')
        rows = rows.filter(machine_id__in=allowed)
    except ScopeError as exc:
        raise CohortError('SCOPE_UNRESOLVED', str(exc)) from exc

    # Denied is reported explicitly, never as a numeric zero: an actor whose
    # scope excludes every retained session member must not receive zeroed
    # metrics — inactive claims included (``claim_active`` is a lifecycle
    # projection, never an authorization input).
    if not session.machines.filter(machine_id__in=allowed).exists():
        raise CohortError(
            'SCOPE_DENIED', 'No session machine is authorized for this actor'
        )

    if location_id is not None:
        graph = location_tools.graph_for(actor)
        if location_id not in graph:
            raise CohortError(
                'LOCATION_DENIED', 'Location is not authorized for this actor'
            )
        ids = (
            location_tools.descendants(graph, location_id)
            if descendants
            else {location_id}
        )
        rows = rows.filter(machine__physical_location_id__in=ids)
    return rows.order_by('alias')


@dataclass
class MachineCoverage:
    """Coverage projection for one machine's session-owned bindings."""

    alias: str
    machine_id: int
    coverage_state: str
    required_signals: int
    present_signals: int
    fresh_signals: int
    last_observed_at: object
    condition: str | None


def machine_coverage(membership, *, now=None) -> MachineCoverage:
    """Project coverage/condition for one session machine row.

    Only the session's own active bindings count as required signals: other
    demo records on the machine are preserved and excluded.
    """
    from assets.health_models import SignalQuality

    now = now or timezone.now()
    machine = membership.machine
    bindings = list(
        machine.signal_bindings.filter(
            active=True, source__in=_session_sources(membership.session)
        )
    )
    if not bindings:
        return MachineCoverage(
            alias=membership.alias,
            machine_id=machine.pk,
            coverage_state='not_configured',
            required_signals=0,
            present_signals=0,
            fresh_signals=0,
            last_observed_at=None,
            condition=None,
        )

    present = 0
    fresh = 0
    usable = 0
    last_observed_at = None
    rank = 0
    for binding in bindings:
        state = getattr(binding, 'state', None)
        if state is None:
            continue
        present += 1
        last_observed_at = max(filter(None, [last_observed_at, state.observed_at]))
        threshold = binding.source.freshness_threshold_seconds
        if state.is_stale(threshold, now=now):
            continue
        fresh += 1
        if state.quality != SignalQuality.GOOD:
            continue
        value = (state.value or {}).get('value')
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        usable += 1
        verdict = binding.classify(value)
        rank = max(rank, {'normal': 0, 'warning': 1, 'critical': 2}.get(verdict, 0))

    required = len(bindings)
    if present == 0:
        coverage_state = 'never_seen'
    elif fresh == 0:
        coverage_state = 'stale'
    elif usable < required:
        coverage_state = 'partial'
    else:
        coverage_state = 'fresh'

    condition = None
    if coverage_state == 'fresh':
        condition = CONDITIONS[rank]
    return MachineCoverage(
        alias=membership.alias,
        machine_id=machine.pk,
        coverage_state=coverage_state,
        required_signals=required,
        present_signals=present,
        fresh_signals=fresh,
        last_observed_at=last_observed_at,
        condition=condition,
    )


def _session_sources(session):
    """The session-owned source ids (bindings belonging to this session)."""
    from assets.demo_metrics_models import DemoMetricsObject

    return [
        row.source_id
        for row in session.ledger_objects.filter(
            kind=DemoMetricsObject.Kind.SOURCE, source__isnull=False
        )
    ]


def session_membership_ids(session, actor) -> set[int]:
    """Machine ids at the session/actor intersection (no location filter).

    The shared primitive behind every scoped surface — current metrics,
    history aggregation and the contributing work list — so all of them see
    exactly the same authorized membership. Raises when the actor's scope
    excludes every retained session member (inactive claims included);
    arbitrary ids are never authority.
    """
    return {row.machine_id for row in session_cohort(session, actor)}


def session_orders(session, actor, *, location_id=None, descendants=True):
    """Session-owned/referenced work orders on the authorized cohort.

    Work-order counts and the contributing list are the same queryset, so a
    count can never disagree with its drill-down list. Only orders with
    session membership (``DemoMetricsObject`` work-order rows) are selected:
    other pre-existing demo orders on the same machines never contaminate the
    session count. The cohort intersection applies unchanged.
    """
    rows = session_cohort(
        session, actor, location_id=location_id, descendants=descendants
    )
    return _orders_for_machines(session, [membership.machine_id for membership in rows])


def _orders_for_machines(session, machine_ids):
    """Session-member work orders restricted to the given cohort machines."""
    from tasks.models import WorkOrder

    return WorkOrder.objects.filter(
        pk__in=_session_order_ids(session), machine_id__in=machine_ids
    ).order_by('reference', 'pk')


def session_current_metrics(
    session, actor, *, location_id=None, descendants=True, now=None
):
    """Return the session-scoped current metrics for the authorized cohort.

    Includes mode, synthetic flag, session identity, cohort size, applied
    filters, coverage and condition breakdowns, open work-order counts (by
    session order membership), distinct affected machines, overdue counts under
    the application date rule, and capability flags. Unavailable capability is
    reported explicitly and never as numeric zero.
    """
    from assets.demo_metrics_models import DemoMetricsSession

    now = now or timezone.now()
    rows = list(
        session_cohort(session, actor, location_id=location_id, descendants=descendants)
    )

    coverage = dict.fromkeys(COVERAGE_STATES, 0)
    condition = dict.fromkeys(CONDITIONS, 0)
    machines = []
    for membership in rows:
        projection = machine_coverage(membership, now=now)
        coverage[projection.coverage_state] += 1
        if projection.condition is not None:
            condition[projection.condition] += 1
        machines.append({
            'alias': projection.alias,
            'machine_id': projection.machine_id,
            'coverage_state': projection.coverage_state,
            'condition': projection.condition,
            'required_signals': projection.required_signals,
            'present_signals': projection.present_signals,
            'fresh_signals': projection.fresh_signals,
            'last_observed_at': projection.last_observed_at,
        })

    cohort_machine_ids = [membership.machine_id for membership in rows]
    # Same selection as the contributing list (``session_orders``), with the
    # open-state filter on top: counts and drill-down lists cannot disagree.
    orders = _orders_for_machines(session, cohort_machine_ids).filter(
        is_active=True, lifecycle_status__in=OPEN_STATES
    )
    today = _today(session.timezone_name, now)
    overdue = [
        order
        for order in orders
        if order.due_date is not None and order.due_date < today
    ]

    from assets.demo_metrics_models import DemoMetricsCoverageInterval

    history_available = DemoMetricsCoverageInterval.objects.filter(
        session=session
    ).exists()
    replay_available = session.status == DemoMetricsSession.Status.ACTIVE

    return {
        'mode': session.get_mode_display(),
        'synthetic': True,
        'session': {
            'id': str(session.pk),
            'dataset_key': session.dataset_key,
            'session_key': session.session_key,
            'status': session.status,
            'anchor_at': session.anchor_at,
            'expires_at': session.expires_at,
        },
        'generated_at': now,
        'filters': {'location_id': location_id, 'include_descendants': descendants},
        'cohort_size': len(rows),
        'machines': machines,
        'observation_coverage': coverage,
        'fresh_condition': condition,
        'open_work_orders': orders.count(),
        'machines_with_open_work': orders.values('machine_id').distinct().count(),
        'overdue_open_work_orders': len(overdue),
        'open_by_state': {
            state: sum(1 for order in orders if order.lifecycle_status == state)
            for state in sorted(OPEN_STATES)
        },
        'distinct_affected_machines': orders.values('machine_id').distinct().count(),
        'capabilities': {
            'history_available': history_available,
            'replay_available': replay_available,
            'anomaly_creation': False,
            'oee': False,
        },
    }


def _session_order_ids(session):
    """Work-order ids owned or referenced by this session (membership only)."""
    from assets.demo_metrics_models import DemoMetricsObject

    return [
        row.work_order_id
        for row in session.ledger_objects.filter(
            kind=DemoMetricsObject.Kind.WORK_ORDER, work_order__isnull=False
        )
    ]


def _today(timezone_name: str, now):
    """The application date in the session's reporting timezone."""
    from zoneinfo import ZoneInfo

    try:
        zone = ZoneInfo(timezone_name or 'UTC')
    except Exception:
        zone = ZoneInfo('UTC')
    value = now
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo('UTC'))
    return value.astimezone(zone).date()
