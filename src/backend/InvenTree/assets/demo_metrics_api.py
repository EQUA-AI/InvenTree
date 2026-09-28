"""Scoped read APIs for the EQUA demo metrics session (work packages D/G).

Read-only, explicitly permissioned and client-scoped through the shared
authorized cohort selector; responses are ``private, no-store`` like the
location APIs. Denied or unavailable is reported explicitly and never as a
numeric zero, and arbitrary ids are never authority.

Fail closed everywhere: a scope that cannot be resolved is an explicit
denial (403), never a fallback to a wider list. Current metrics, historical
aggregation and the contributing work list all run through the same
authorized intersection (``demo_metrics.cohort.session_cohort``) with the
same ``location``/``include_descendants`` filters, so counts and lists can
never disagree about which machines are in scope.
"""

from datetime import datetime
from datetime import timezone as dt_timezone

from django.http import Http404
from django.urls import path
from django.utils import timezone

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from tasks.scope import ScopeError

from InvenTree.permissions import IsAuthenticatedOrReadScope, RolePermission

from . import locations
from .demo_metrics import cohort, history
from .demo_metrics_models import DemoMetricsCoverageInterval, DemoMetricsSession
from .location_api import LocationView

#: The contributing list is bounded; the metrics cards count the full set.
MAX_WORK_ROWS = 100


class DemoMetricsView(LocationView):
    """Same permission/cache wiring as the location APIs."""

    permission_classes = [IsAuthenticatedOrReadScope, RolePermission]
    role_required = 'work_order'


def _boolean(value, field, default=False):
    if value is None:
        return default
    if value not in ('true', 'false'):
        raise ValidationError({field: 'Use true or false.'})
    return value == 'true'


def _integer(value, field):
    try:
        result = int(value)
        if result <= 0:
            raise ValueError
        return result
    except (ValueError, TypeError) as exc:
        raise ValidationError({field: 'Enter a valid positive identifier.'}) from exc


def _instant(value, field):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValidationError({field: 'Enter an ISO-8601 timestamp.'}) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def _session_or_404(pk):
    """Resolve the session; an unknown id is never authority."""
    session = DemoMetricsSession.objects.filter(pk=pk).first()
    if session is None:
        raise Http404
    return session


def _scope_params(request):
    """Shared ``location``/``include_descendants`` parsing for every surface.

    The location must be inside the actor's authorized tree (404 otherwise,
    so an arbitrary id never discloses existence). The same values feed
    current metrics, history and the contributing list — one intersection,
    three views.
    """
    location = request.query_params.get('location')
    location_id = _integer(location, 'location') if location else None
    include = _boolean(
        request.query_params.get('include_descendants'),
        'include_descendants',
        default=True,
    )
    if location_id is not None:
        graph = locations.graph_for(request.user)
        if location_id not in graph:
            raise Http404
    return location_id, include


def _denied(exc):
    """A scope denial is explicit and machine-readable, never a zero."""
    code = exc.code if isinstance(exc, cohort.CohortError) else 'SCOPE_DENIED'
    return Response({'error': code, 'detail': str(exc)}, status=403)


class DemoSessionList(DemoMetricsView):
    """Bounded list of demo sessions visible to this actor."""

    @extend_schema(responses={200: dict, 403: dict})
    def get(self, request):
        """Return session identity and status only, scoped to member machines.

        Fail closed: the actor's machine scope must resolve. An unresolvable
        scope is an explicit 403 — never the unscoped session list. This
        deliberately has no ``except Exception`` legacy fallback: the only
        caught error is ``ScopeError``, and anything unexpected propagates as
        a server error rather than disclosing sessions.
        """
        from tasks.scope import machine_scope_filter

        from assets.models import AssetMachine

        from .demo_metrics_models import DemoMetricsMachine

        try:
            allowed = AssetMachine.objects.filter(
                machine_scope_filter(request.user)
            ).values('pk')
        except ScopeError as exc:
            return _denied(exc)
        visible = set(
            DemoMetricsMachine.objects.filter(
                machine_id__in=allowed, claim_active=True
            ).values_list('session_id', flat=True)
        )
        sessions = list(
            DemoMetricsSession.objects.filter(pk__in=visible).order_by('-created_at')[
                :25
            ]
        )
        return Response({
            'count': len(sessions),
            'results': [
                {
                    'id': str(session.pk),
                    'dataset_key': session.dataset_key,
                    'session_key': session.session_key,
                    'mode': session.mode,
                    'status': session.status,
                    'synthetic': True,
                    'anchor_at': session.anchor_at,
                    'expires_at': session.expires_at,
                }
                for session in sessions
            ],
        })


class DemoSessionMetrics(DemoMetricsView):
    """Session current metrics for the authorized cohort."""

    @extend_schema(responses={200: dict, 403: dict})
    def get(self, request, pk):
        """Return coverage/condition breakdown and scoped open-work counts."""
        session = _session_or_404(pk)
        location_id, include = _scope_params(request)
        try:
            data = cohort.session_current_metrics(
                session, request.user, location_id=location_id, descendants=include
            )
        except cohort.CohortError as exc:
            return _denied(exc)
        except ScopeError as exc:
            return _denied(exc)
        return Response(data)


class DemoSessionWork(DemoMetricsView):
    """The contributing session work list behind the metrics counts."""

    @extend_schema(responses={200: dict, 403: dict})
    def get(self, request, pk):
        """Return session-owned work orders on the same authorized cohort.

        This is the drill-down list for the open-work counts: the same
        cohort intersection and the same session membership selection, so a
        count always reconciles with its contributing rows. Open and
        completed/inactive control rows are both listed and labelled; the
        ``open_count`` equals the metrics endpoint's ``open_work_orders``.
        """
        from tasks.dashboard_metrics import OPEN

        session = _session_or_404(pk)
        location_id, include = _scope_params(request)
        try:
            orders = cohort.session_orders(
                session, request.user, location_id=location_id, descendants=include
            )
            rows = list(orders[:MAX_WORK_ROWS])
            today = cohort._today(session.timezone_name or 'UTC', timezone.now())
        except cohort.CohortError as exc:
            return _denied(exc)
        except ScopeError as exc:
            return _denied(exc)

        results = []
        for order in rows:
            open_row = order.is_active and order.lifecycle_status in OPEN
            results.append({
                'id': order.pk,
                'reference': order.reference,
                'title': order.title,
                'machine_id': order.machine_id,
                'priority': order.priority,
                'lifecycle_status': order.lifecycle_status,
                'is_active': order.is_active,
                'open': open_row,
                'due_date': order.due_date,
                'overdue': bool(
                    open_row and order.due_date is not None and order.due_date < today
                ),
            })
        count = orders.count()
        return Response({
            'count': count,
            'open_count': orders.filter(
                is_active=True, lifecycle_status__in=OPEN
            ).count(),
            'results_returned': len(results),
            'has_more': count > len(results),
            'filters': {'location_id': location_id, 'include_descendants': include},
            'session': {
                'id': str(session.pk),
                'session_key': session.session_key,
                'synthetic': True,
            },
            'results': results,
        })


class DemoSessionHistory(DemoMetricsView):
    """Scoped historical aggregation; unavailable stays honestly unavailable."""

    @extend_schema(responses={200: dict, 400: dict, 403: dict})
    def get(self, request, pk):
        """Return planned/lost machine-minutes for the requested window.

        The machine selection is the same authorized intersection as the
        current metrics (``location``/``include_descendants`` included), so
        history never aggregates machines the current view would not show.
        Missing coverage reports ``null``; capability and error states are
        distinct from numeric zero.
        """
        session = _session_or_404(pk)
        location_id, include = _scope_params(request)
        try:
            rows = list(
                cohort.session_cohort(
                    session, request.user, location_id=location_id, descendants=include
                )
            )
        except cohort.CohortError as exc:
            return _denied(exc)
        except ScopeError as exc:
            return _denied(exc)

        filters = {'location_id': location_id, 'include_descendants': include}
        if not DemoMetricsCoverageInterval.objects.filter(session=session).exists():
            return Response({
                'available': False,
                'synthetic': True,
                'reason': 'no_imported_history_for_session',
                'filters': filters,
            })

        start = _instant(request.query_params.get('start'), 'start')
        end = _instant(request.query_params.get('end'), 'end')
        if start is None or end is None:
            raise ValidationError({'start': 'Both start and end are required.'})
        machine_ids = [row.machine_id for row in rows]
        if not machine_ids:
            # Authorized but empty selection: honest empty result, with null
            # downtime/availability — not a zero that implies "no losses".
            return Response({
                'available': True,
                'synthetic': True,
                'attribution_mode': history.ATTRIBUTION_MODE,
                'session_key': session.session_key,
                'filters': filters,
                'selected_machines': 0,
                'measured_machines': 0,
                'completeness': 'no_machines_in_scope',
                'daily': [],
                'planned_machine_minutes': 0,
                'downtime_machine_minutes': None,
                'measured_cohort_availability': None,
            })
        try:
            result = history.aggregate_history(
                session=session, start=start, end=end, machine_ids=machine_ids
            )
        except history.HistoryError as exc:
            return Response({'error': exc.code, 'detail': str(exc)}, status=400)
        result['available'] = True
        result['selected_machines'] = len(machine_ids)
        result['filters'] = filters
        return Response(result)


demo_metrics_api_urls = [
    path('sessions/', DemoSessionList.as_view(), name='asset-demo-session-list'),
    path(
        'sessions/<uuid:pk>/metrics/',
        DemoSessionMetrics.as_view(),
        name='asset-demo-session-metrics',
    ),
    path(
        'sessions/<uuid:pk>/work-orders/',
        DemoSessionWork.as_view(),
        name='asset-demo-session-work-orders',
    ),
    path(
        'sessions/<uuid:pk>/history/',
        DemoSessionHistory.as_view(),
        name='asset-demo-session-history',
    ),
]
