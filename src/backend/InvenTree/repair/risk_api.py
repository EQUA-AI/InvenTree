"""REST API for the Risk Radar / Command Center (Features #4 / #16).

Every collection endpoint requires exactly one explicit ``scope`` query
parameter which is decoded and re-authorized server-side; there is no
global or merged view. Scope failures surface as 404 (matching the live
work-order API idiom) so unauthorized scopes are indistinguishable from
absent ones.
"""

from __future__ import annotations

import csv
import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.urls import include, path
from django.utils import timezone

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import risk_services
from .risk_models import RiskFinding, RiskFindingState, RiskRuleDefinition
from .risk_scope import RiskScopeError, authorized_scope_keys, require_scope
from .risk_serializers import (
    RiskAssignSerializer,
    RiskCommandSerializer,
    RiskDismissSerializer,
    RiskFindingDetailSerializer,
    RiskFindingSerializer,
    RiskRuleSerializer,
    RiskRuleUpdateSerializer,
    RiskSnoozeSerializer,
)

MAX_PAGE_SIZE = 200

_CONFLICT_CODES = {
    risk_services.FINDING_STATE_CONFLICT,
    risk_services.IDEMPOTENCY_CONFLICT,
    risk_services.SCAN_LEASE_HELD,
    risk_services.RULE_DISABLED,
}


def _error_body(*, code: str, detail: str, correlation_id: str, current_version=None):
    """Build the stable command error envelope."""
    return {
        'code': code,
        'detail': detail,
        'correlation_id': correlation_id,
        'current_version': current_version,
    }


def _error_response(
    exc: risk_services.RiskCommandError, correlation_id: str, current_version=None
) -> Response:
    """Map a command error onto the envelope with a stable HTTP status."""
    http_status = (
        status.HTTP_409_CONFLICT
        if exc.code in _CONFLICT_CODES
        else status.HTTP_404_NOT_FOUND
        if exc.code == 'FINDING_NOT_FOUND'
        else status.HTTP_400_BAD_REQUEST
    )
    return Response(
        _error_body(
            code=exc.code,
            detail=exc.detail,
            correlation_id=correlation_id,
            current_version=current_version,
        ),
        status=http_status,
    )


class RiskRadarEnabledMixin:
    """Return 404 for every radar endpoint while the master flag is off."""

    def dispatch(self, request, *args, **kwargs):
        """Gate the endpoint on ``AIMMS_RISK_RADAR_ENABLED``."""
        if not getattr(settings, 'AIMMS_RISK_RADAR_ENABLED', False):
            raise Http404
        return super().dispatch(request, *args, **kwargs)


class CommandCenterEnabledMixin(RiskRadarEnabledMixin):
    """Additionally gate on ``AIMMS_COMMAND_CENTER_ENABLED``."""

    def dispatch(self, request, *args, **kwargs):
        """Gate the endpoint on the Command Center flag."""
        if not getattr(settings, 'AIMMS_COMMAND_CENTER_ENABLED', False):
            raise Http404
        return super().dispatch(request, *args, **kwargs)


def _require_scope_or_404(request):
    """Decode and re-authorize the required ``scope`` query parameter."""
    scope_key = request.query_params.get('scope')
    if not scope_key:
        raise risk_services.RiskCommandError(
            risk_services.SCOPE_UNRESOLVED, 'A scope query parameter is required'
        )
    try:
        return require_scope(request.user, scope_key), scope_key
    except RiskScopeError as exc:
        # Unauthorized and nonexistent scopes are indistinguishable.
        raise Http404 from exc


class RiskScopeList(RiskRadarEnabledMixin, APIView):
    """List the requesting actor's authorized scope keys."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Return the actor's scope keys (empty when unresolved)."""
        try:
            scopes = authorized_scope_keys(request.user)
        except RiskScopeError:
            scopes = []
        return Response({
            'scopes': scopes,
            'authorization_fingerprint': risk_services.authorization_fingerprint(
                request.user
            ),
        })


def _filtered_findings(request, scope):
    """Apply documented queue filters to the visible findings queryset."""
    queryset = risk_services.visible_findings(request.user, scope)
    params = request.query_params
    now = timezone.now()
    state = params.get('state')
    if state:
        valid = {choice.value for choice in RiskFindingState}
        if state not in valid:
            raise risk_services.RiskCommandError(
                'FILTER_INVALID', f'Unknown state {state!r}'
            )
        queryset = queryset.filter(state=state)
    else:
        queryset = queryset.filter(risk_services.attention_filter(now))
    category = params.get('category')
    if category:
        queryset = queryset.filter(category=category)
    severity = params.get('severity')
    if severity:
        queryset = queryset.filter(severity=severity)
    rule = params.get('rule')
    if rule:
        queryset = queryset.filter(rule_code=rule)
    source_kind = params.get('source_model')
    if source_kind:
        queryset = queryset.filter(source_model=source_kind)
    owner = params.get('owner')
    if owner:
        try:
            queryset = queryset.filter(owner_id=int(owner))
        except (TypeError, ValueError) as exc:
            raise risk_services.RiskCommandError(
                'FILTER_INVALID', 'owner must be an integer user id'
            ) from exc
    if params.get('due_breached') in ('1', 'true', 'yes'):
        queryset = queryset.filter(due_at__lte=now)
    return queryset


def _ranked_page(request, scope):
    """Return the exact total plus one ranked, paginated findings page.

    The count is computed in the database; the page comes from a bounded
    pool ordered DB-side by the rank-tuple prefix, so a page can never
    silently prefer a lower-severity finding over a higher one.
    """
    now = timezone.now()
    queryset = _filtered_findings(request, scope)
    total = queryset.count()
    try:
        limit = max(min(int(request.query_params.get('limit', 50)), MAX_PAGE_SIZE), 1)
        offset = max(int(request.query_params.get('offset', 0)), 0)
    except (TypeError, ValueError) as exc:
        raise risk_services.RiskCommandError(
            'FILTER_INVALID', 'limit/offset must be integers'
        ) from exc
    page = list(risk_services.rank_ordered(queryset, now)[offset : offset + limit])
    return total, page


class RiskFindingList(RiskRadarEnabledMixin, APIView):
    """One-scope ranked findings list with documented filters."""

    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id='repair_risk_findings_list')
    def get(self, request):
        """Return the ranked findings for the requested scope."""
        correlation_id = str(uuid.uuid4())
        try:
            scope, scope_key = _require_scope_or_404(request)
            total, page = _ranked_page(request, scope)
        except risk_services.RiskCommandError as exc:
            return _error_response(exc, correlation_id)
        return Response({
            'scope': scope_key,
            'as_of': timezone.now().isoformat(),
            'source_freshness': risk_services.source_freshness(),
            'count': total,
            'results': RiskFindingSerializer(page, many=True).data,
        })


class RiskFindingExport(RiskRadarEnabledMixin, APIView):
    """One-scope CSV export of the same visibility-filtered list."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Stream the complete filtered findings list as CSV.

        Export is the whole one-scope, visibility-filtered list in rank
        order — never a silently truncated page.
        """
        correlation_id = str(uuid.uuid4())
        now = timezone.now()
        try:
            scope, scope_key = _require_scope_or_404(request)
            rows = risk_services.rank_ordered(
                _filtered_findings(request, scope), now
            ).iterator(chunk_size=500)
        except risk_services.RiskCommandError as exc:
            return _error_response(exc, correlation_id)
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = (
            f'attachment; filename="risk-findings-{scope_key}.csv"'
        )
        writer = csv.writer(response)
        writer.writerow([
            'id',
            'rule',
            'rule_version',
            'category',
            'severity',
            'state',
            'title',
            'source_model',
            'source_id',
            'condition_started_at',
            'due_at',
            'owner',
            'reopen_count',
        ])
        for finding in rows:
            writer.writerow([
                finding.pk,
                finding.rule_code,
                finding.rule_version,
                finding.category,
                finding.severity,
                finding.state,
                finding.title,
                finding.source_model,
                finding.source_id,
                finding.condition_started_at.isoformat(),
                finding.due_at.isoformat() if finding.due_at else '',
                finding.owner.username if finding.owner else '',
                finding.reopen_count,
            ])
        return response


def _visible_finding_or_404(request, pk: int) -> RiskFinding:
    """Fetch one finding, re-proving scope and category visibility."""
    finding = RiskFinding.objects.filter(pk=pk).first()
    if finding is None:
        raise Http404
    try:
        require_scope(request.user, finding.scope_key)
    except RiskScopeError as exc:
        raise Http404 from exc
    if not request.user.has_perm(risk_services.PERM_VIEW):
        raise PermissionDenied('Missing required permission')
    if not risk_services.VISIBILITY_POLICY.can_view(request.user, finding):
        raise Http404
    return finding


class RiskFindingDetail(RiskRadarEnabledMixin, APIView):
    """Finding detail with evidence, events, and action links."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk: int):
        """Return the detail DTO after re-proving visibility."""
        finding = _visible_finding_or_404(request, pk)
        data = RiskFindingDetailSerializer(finding).data
        if not risk_services.finding_actions_available(finding):
            data['action_links'] = []
        return Response(data)


class RiskFindingCommand(RiskRadarEnabledMixin, APIView):
    """Shared adapter for finding lifecycle commands."""

    permission_classes = [IsAuthenticated]
    command = ''
    serializer_class: type[RiskCommandSerializer] = RiskCommandSerializer

    def post(self, request, pk: int):
        """Validate the envelope and execute the command."""
        correlation_id = str(uuid.uuid4())
        finding = _visible_finding_or_404(request, pk)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = dict(serializer.validated_data)
        expected_version = payload.pop('expected_version')
        idempotency_key = payload.pop('idempotency_key')
        if 'snooze_until' in payload and payload['snooze_until'] is not None:
            payload['snooze_until'] = payload['snooze_until'].isoformat()
        try:
            result = risk_services.execute_finding_command(
                request.user,
                finding.pk,
                self.command,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
                arguments=payload,
            )
        except risk_services.RiskCommandError as exc:
            current = (
                RiskFinding.objects
                .filter(pk=pk)
                .values_list('version', flat=True)
                .first()
            )
            return _error_response(exc, correlation_id, current_version=current)
        except RiskScopeError as exc:
            raise Http404 from exc
        return Response(result)


class RiskFindingAcknowledge(RiskFindingCommand):
    """Acknowledge a finding."""

    command = 'acknowledge'
    serializer_class = RiskCommandSerializer


class RiskFindingAssign(RiskFindingCommand):
    """Assign or clear a finding owner."""

    command = 'assign'
    serializer_class = RiskAssignSerializer


class RiskFindingSnooze(RiskFindingCommand):
    """Snooze a finding until an explicit future expiry."""

    command = 'snooze'
    serializer_class = RiskSnoozeSerializer


class RiskFindingDismiss(RiskFindingCommand):
    """Dismiss a finding with a required reason and recheck policy."""

    command = 'dismiss'
    serializer_class = RiskDismissSerializer


class RiskFindingRecheck(RiskRadarEnabledMixin, APIView):
    """Enqueue the complete current rule+scope scan for one finding."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk: int):
        """Queue a full-snapshot scan; never resolves from a delta."""
        correlation_id = str(uuid.uuid4())
        finding = _visible_finding_or_404(request, pk)
        try:
            risk_services.enqueue_recheck(request.user, finding)
        except risk_services.RiskCommandError as exc:
            return _error_response(exc, correlation_id)
        except RiskScopeError as exc:
            raise Http404 from exc
        return Response(
            {'queued': True, 'correlation_id': correlation_id},
            status=status.HTTP_202_ACCEPTED,
        )


class CommandCenterSummary(CommandCenterEnabledMixin, APIView):
    """One-scope composed Command Center read model (§6)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Return the composed summary for the requested scope."""
        correlation_id = str(uuid.uuid4())
        try:
            scope, _scope_key = _require_scope_or_404(request)
            summary = risk_services.command_center_summary(request.user, scope)
        except risk_services.RiskCommandError as exc:
            return _error_response(exc, correlation_id)
        except RiskScopeError as exc:
            raise Http404 from exc
        return Response(summary)


class RiskRuleList(RiskRadarEnabledMixin, APIView):
    """List current rule revisions (rule-health viewers)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Return every current rule revision."""
        if not request.user.has_perm(risk_services.PERM_HEALTH):
            raise PermissionDenied('Missing required permission')
        risk_services.ensure_rule_definitions()
        rules = RiskRuleDefinition.objects.filter(is_current=True).order_by('code')
        return Response(RiskRuleSerializer(rules, many=True).data)


class RiskRuleDetail(RiskRadarEnabledMixin, APIView):
    """Admin configuration endpoint creating audited revisions."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, code: str):
        """Validate and activate the next immutable revision."""
        correlation_id = str(uuid.uuid4())
        serializer = RiskRuleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = dict(serializer.validated_data)
        reason = payload.pop('reason')
        try:
            revision = risk_services.update_rule_configuration(
                request.user, code, changes=payload, reason=reason
            )
        except risk_services.RiskCommandError as exc:
            return _error_response(exc, correlation_id)
        return Response(RiskRuleSerializer(revision).data)


class RiskRuleHealth(RiskRadarEnabledMixin, APIView):
    """One-scope per-rule scan health for operators."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Return rule health rows for the requested scope."""
        correlation_id = str(uuid.uuid4())
        try:
            scope, scope_key = _require_scope_or_404(request)
            rows = risk_services.rule_health(request.user, scope)
        except risk_services.RiskCommandError as exc:
            return _error_response(exc, correlation_id)
        return Response({
            'scope': scope_key,
            'as_of': timezone.now().isoformat(),
            'rules': rows,
        })


risk_api_urls = [
    path('risk-scopes/', RiskScopeList.as_view(), name='risk-scope-list'),
    path(
        'risk-findings/',
        include([
            path('export/', RiskFindingExport.as_view(), name='risk-finding-export'),
            path(
                '<int:pk>/acknowledge/',
                RiskFindingAcknowledge.as_view(),
                name='risk-finding-acknowledge',
            ),
            path(
                '<int:pk>/assign/',
                RiskFindingAssign.as_view(),
                name='risk-finding-assign',
            ),
            path(
                '<int:pk>/snooze/',
                RiskFindingSnooze.as_view(),
                name='risk-finding-snooze',
            ),
            path(
                '<int:pk>/dismiss/',
                RiskFindingDismiss.as_view(),
                name='risk-finding-dismiss',
            ),
            path(
                '<int:pk>/recheck/',
                RiskFindingRecheck.as_view(),
                name='risk-finding-recheck',
            ),
            path('<int:pk>/', RiskFindingDetail.as_view(), name='risk-finding-detail'),
            path('', RiskFindingList.as_view(), name='risk-finding-list'),
        ]),
    ),
    path(
        'command-center/',
        include([
            path(
                'summary/',
                CommandCenterSummary.as_view(),
                name='command-center-summary',
            )
        ]),
    ),
    path(
        'risk-rules/',
        include([
            path('health/', RiskRuleHealth.as_view(), name='risk-rule-health'),
            path('<str:code>/', RiskRuleDetail.as_view(), name='risk-rule-detail'),
            path('', RiskRuleList.as_view(), name='risk-rule-list'),
        ]),
    ),
]
