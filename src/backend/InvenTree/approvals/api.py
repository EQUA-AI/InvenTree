"""API views for the AI Agent Approval Queue.

Implements all endpoints from spec Sections 7.0 - 7.4.
"""

import uuid as _uuid

from django.db.models import Q
from django.urls import include, path

import django_filters.rest_framework.filters as rest_filters
import structlog
from django_filters.rest_framework.filterset import FilterSet
from rest_framework import status
from rest_framework.response import Response

from InvenTree.filters import SEARCH_ORDER_FILTER
from InvenTree.mixins import CreateAPI, ListAPI, ListCreateAPI, RetrieveAPI

from . import serializers as approval_serializers
from . import services as approval_services
from .models import (
    TERMINAL_STATUSES,
    Approval,
    ApprovalEvent,
    ApprovalRevision,
    ApprovalStatus,
)
from .permissions import (
    ApprovalDecisionThrottle,
    ApprovalReadThrottle,
    ApprovalReviseThrottle,
    HasApprovalReviewPermission,
)

logger = structlog.get_logger('approvals.api')


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class ApprovalFilter(FilterSet):
    """Filter for the approval list endpoint."""

    status = rest_filters.CharFilter(method='filter_status')
    risk_tier = rest_filters.NumberFilter(field_name='risk_tier')
    action_type = rest_filters.CharFilter(field_name='action_type')
    assigned_to = rest_filters.NumberFilter(field_name='assigned_to_user_id')
    created_after = rest_filters.IsoDateTimeFilter(
        field_name='created_at', lookup_expr='gte'
    )
    created_before = rest_filters.IsoDateTimeFilter(
        field_name='created_at', lookup_expr='lte'
    )

    class Meta:
        """Metadata options."""

        model = Approval
        fields = ['status', 'risk_tier', 'action_type', 'assigned_to']

    def filter_status(self, queryset, name, value):
        """Filter by comma-separated status values."""
        statuses = [s.strip() for s in value.split(',') if s.strip()]
        return queryset.filter(status__in=statuses)


# ---------------------------------------------------------------------------
# Read endpoints (Section 7.1)
# ---------------------------------------------------------------------------


class ApprovalList(ListCreateAPI):
    """List approvals with filters, or create a new approval.

    GET /api/approvals/ — list with filters
    POST /api/approvals/ — create (agent/service-internal)
    """

    permission_classes = [HasApprovalReviewPermission]
    filter_backends = SEARCH_ORDER_FILTER
    filterset_class = ApprovalFilter
    ordering_fields = ['created_at', 'updated_at', 'status', 'risk_tier', 'action_type']
    ordering = '-created_at'
    search_fields = ['summary', 'action_type', 'agent_run_id', 'tool_call_id']

    def get_queryset(self):
        """Return the queryset for this endpoint."""
        return Approval.objects.all()

    def get_serializer_class(self):
        """Return appropriate serializer based on request method."""
        if self.request.method == 'POST':
            return approval_serializers.ApprovalCreateSerializer
        return approval_serializers.ApprovalListSerializer

    def create(self, request, *args, **kwargs):
        """Create a new approval (Section 7.0).

        Handles idempotency: returns existing record if idempotency_key matches.
        Handles entity conflict detection (advisory 409).
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        approval = serializer.save()

        # Check if this was an idempotent return (existing record)
        was_existing = getattr(approval, '_was_existing', False)

        # Entity conflict detection (advisory)
        response_status = status.HTTP_201_CREATED
        headers = {}

        if not was_existing:
            entity_refs = request.data.get('payload', {}).get('entity_refs', {})
            if (
                entity_refs
                and isinstance(entity_refs, dict)
                and all(
                    isinstance(v, (str, int, float, bool, type(None)))
                    for v in entity_refs.values()
                )
            ):
                conflicting = self._check_entity_conflicts(approval.pk, entity_refs)
                if conflicting:
                    response_status = status.HTTP_409_CONFLICT
                    headers['X-Approval-Conflict'] = (
                        f'existing_approval_id={conflicting.pk}'
                    )
        else:
            response_status = status.HTTP_200_OK

        detail_serializer = approval_serializers.ApprovalDetailSerializer(approval)
        return Response(detail_serializer.data, status=response_status, headers=headers)

    def _check_entity_conflicts(self, exclude_pk, entity_refs):
        """Check for active approvals targeting the same entities."""
        active_statuses = [
            s for s in ApprovalStatus.values if s not in TERMINAL_STATUSES
        ]

        # Superset match on the (flat) entity_refs dict. JSONField __contains is
        # unsupported on SQLite, so AND key-transform lookups emulate containment
        # portably with identical semantics for flat scalar dicts.
        refs_query = Q()
        for key, ref_value in entity_refs.items():
            refs_query &= Q(**{f'payload__entity_refs__{key}': ref_value})

        return (
            Approval.objects
            .filter(refs_query, status__in=active_statuses)
            .exclude(pk=exclude_pk)
            .first()
        )


class ApprovalDetail(RetrieveAPI):
    """Retrieve a single approval with full detail.

    GET /api/approvals/{id}/
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalReadThrottle]
    serializer_class = approval_serializers.ApprovalDetailSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        """Return the scoped queryset."""
        return Approval.objects.all()


class ApprovalCardPackage(RetrieveAPI):
    """Return the card package for Modify-in-chat (Section 7.1).

    GET /api/approvals/{id}/card-package/
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalReadThrottle]
    serializer_class = approval_serializers.CardPackageSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        """Return the scoped queryset."""
        return Approval.objects.all()


class ApprovalCount(ListAPI):
    """Return the count of approvals matching filters.

    GET /api/approvals/count/?status=pending
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalReadThrottle]
    serializer_class = approval_serializers.ApprovalCountSerializer
    filterset_class = ApprovalFilter
    filter_backends = SEARCH_ORDER_FILTER

    def get_queryset(self):
        """Return the scoped queryset."""
        return Approval.objects.all()

    def list(self, request, *args, **kwargs):
        """Return just the count."""
        queryset = self.filter_queryset(self.get_queryset())
        return Response({'count': queryset.count()})


class ApprovalRevisionList(ListAPI):
    """List revisions for an approval (Section 7.1).

    GET /api/approvals/{id}/revisions/
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalReadThrottle]
    serializer_class = approval_serializers.ApprovalRevisionSerializer

    def get_queryset(self):
        """Return the scoped queryset."""
        return ApprovalRevision.objects.filter(approval_id=self.kwargs['pk']).order_by(
            'revision_number'
        )


class ApprovalEventList(ListAPI):
    """List events for an approval (Section 7.1).

    GET /api/approvals/{id}/events/
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalReadThrottle]
    serializer_class = approval_serializers.ApprovalEventSerializer

    def get_queryset(self):
        """Return the scoped queryset."""
        return ApprovalEvent.objects.filter(approval_id=self.kwargs['pk']).order_by(
            'timestamp'
        )


# ---------------------------------------------------------------------------
# Write endpoints (Section 7.2) — state machine actions
# ---------------------------------------------------------------------------


def _get_approval_or_404(pk):
    """Get an approval by PK without row lock (read-only use)."""
    try:
        return Approval.objects.get(pk=pk)
    except Approval.DoesNotExist:
        return None


def _get_approval_for_update(pk):
    """Get an approval by PK with row lock for mutation."""
    try:
        return Approval.objects.select_for_update().get(pk=pk)
    except Approval.DoesNotExist:
        return None


def _error_response(error_code, detail, http_status, **extra):
    """Build a standardized error response with request_id."""
    body = {'error': error_code, 'detail': detail, 'request_id': str(_uuid.uuid4())}
    body.update(extra)
    return Response(body, status=http_status)


class ApprovalOpenView(CreateAPI):
    """Open an approval for review.

    POST /api/approvals/{id}/open/
    Transition: pending → in_review, or changes_requested → in_review
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalDecisionThrottle]
    serializer_class = approval_serializers.OpenApprovalSerializer

    def create(self, request, *args, **kwargs):
        """Delegate the action to the shared approval service."""
        try:
            result = approval_services.open_approval(
                self.kwargs['pk'], actor=request.user, data=request.data
            )
        except approval_services.ApprovalServiceError as exc:
            return _error_response(exc.code, exc.detail, exc.http_status, **exc.extra)
        return Response(result.data, status=result.status)


class ApprovalConfirmViewedView(CreateAPI):
    """Confirm that the user has reviewed the details (Tier 2-3 gate).

    POST /api/approvals/{id}/confirm-viewed/
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalDecisionThrottle]
    serializer_class = approval_serializers.ConfirmViewedSerializer

    def create(self, request, *args, **kwargs):
        """Delegate the action to the shared approval service."""
        try:
            result = approval_services.confirm_viewed(
                self.kwargs['pk'], actor=request.user, data=request.data
            )
        except approval_services.ApprovalServiceError as exc:
            return _error_response(exc.code, exc.detail, exc.http_status, **exc.extra)
        return Response(result.data, status=result.status)


class ApprovalRequestChangesView(CreateAPI):
    """Request changes on an approval.

    POST /api/approvals/{id}/request-changes/
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalDecisionThrottle]
    serializer_class = approval_serializers.RequestChangesSerializer

    def create(self, request, *args, **kwargs):
        """Delegate the action to the shared approval service."""
        try:
            result = approval_services.request_changes(
                self.kwargs['pk'], actor=request.user, data=request.data
            )
        except approval_services.ApprovalServiceError as exc:
            return _error_response(exc.code, exc.detail, exc.http_status, **exc.extra)
        return Response(result.data, status=result.status)


class ApprovalApproveView(CreateAPI):
    """Approve an approval with executor wiring and revalidation.

    POST /api/approvals/{id}/approve/
    Multi-phase pattern (A-1, A-2, A-3, A-8):
      Phase 1: Read-only validation (no lock)
      Phase 2: Revalidation via executor.check_preconditions()
      Phase 3: Lock + transition approved → executing
      Phase 4: Execute via executor (outside transaction)
      Phase 5: Record result + transition to succeeded/failed
      Phase 6: Agent resume (outside transaction)
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalDecisionThrottle]
    serializer_class = approval_serializers.ApproveSerializer

    def create(self, request, *args, **kwargs):
        """Delegate the action to the shared approval service."""
        try:
            result = approval_services.approve(
                self.kwargs['pk'], actor=request.user, data=request.data
            )
        except approval_services.ApprovalServiceError as exc:
            return _error_response(exc.code, exc.detail, exc.http_status, **exc.extra)
        return Response(result.data, status=result.status)


class ApprovalDenyView(CreateAPI):
    """Deny an approval.

    POST /api/approvals/{id}/deny/
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalDecisionThrottle]
    serializer_class = approval_serializers.DenySerializer

    def create(self, request, *args, **kwargs):
        """Delegate the action to the shared approval service."""
        try:
            result = approval_services.deny(
                self.kwargs['pk'], actor=request.user, data=request.data
            )
        except approval_services.ApprovalServiceError as exc:
            return _error_response(exc.code, exc.detail, exc.http_status, **exc.extra)
        return Response(result.data, status=result.status)


class ApprovalCancelView(CreateAPI):
    """Cancel an approval.

    POST /api/approvals/{id}/cancel/
    Cancel semantics: reverts to previous revision, then terminates.
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalDecisionThrottle]
    serializer_class = approval_serializers.CancelSerializer

    def create(self, request, *args, **kwargs):
        """Delegate the action to the shared approval service."""
        try:
            result = approval_services.cancel(
                self.kwargs['pk'], actor=request.user, data=request.data
            )
        except approval_services.ApprovalServiceError as exc:
            return _error_response(exc.code, exc.detail, exc.http_status, **exc.extra)
        return Response(result.data, status=result.status)


# ---------------------------------------------------------------------------
# Modify endpoints (Section 7.3)
# ---------------------------------------------------------------------------


class ApprovalAcquireModifyLockView(CreateAPI):
    """Acquire the modification lock.

    POST /api/approvals/{id}/acquire-modify-lock/
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalDecisionThrottle]
    serializer_class = approval_serializers.AcquireModifyLockSerializer

    def create(self, request, *args, **kwargs):
        """Delegate the action to the shared approval service."""
        try:
            result = approval_services.acquire_modify_lock(
                self.kwargs['pk'], actor=request.user, data=request.data
            )
        except approval_services.ApprovalServiceError as exc:
            return _error_response(exc.code, exc.detail, exc.http_status, **exc.extra)
        return Response(result.data, status=result.status)


class ApprovalReleaseModifyLockView(CreateAPI):
    """Release the modification lock.

    POST /api/approvals/{id}/release-modify-lock/
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalDecisionThrottle]
    serializer_class = approval_serializers.ReleaseModifyLockSerializer

    def create(self, request, *args, **kwargs):
        """Delegate the action to the shared approval service."""
        try:
            result = approval_services.release_modify_lock(
                self.kwargs['pk'], actor=request.user, data=request.data
            )
        except approval_services.ApprovalServiceError as exc:
            return _error_response(exc.code, exc.detail, exc.http_status, **exc.extra)
        return Response(result.data, status=result.status)


class ApprovalReviseView(CreateAPI):
    """Submit a new revision.

    POST /api/approvals/{id}/revise/
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalReviseThrottle]
    serializer_class = approval_serializers.ReviseSerializer

    def create(self, request, *args, **kwargs):
        """Delegate the action to the shared approval service."""
        try:
            result = approval_services.revise(
                self.kwargs['pk'], actor=request.user, data=request.data
            )
        except approval_services.ApprovalServiceError as exc:
            return _error_response(exc.code, exc.detail, exc.http_status, **exc.extra)
        return Response(result.data, status=result.status)


# ---------------------------------------------------------------------------
# URL patterns
# ---------------------------------------------------------------------------

approvals_api_urls = [
    # Detail endpoints with sub-actions
    path(
        '<uuid:pk>/',
        include([
            # Sub-action endpoints
            path('open/', ApprovalOpenView.as_view(), name='api-approval-open'),
            path(
                'confirm-viewed/',
                ApprovalConfirmViewedView.as_view(),
                name='api-approval-confirm-viewed',
            ),
            path(
                'request-changes/',
                ApprovalRequestChangesView.as_view(),
                name='api-approval-request-changes',
            ),
            path(
                'approve/', ApprovalApproveView.as_view(), name='api-approval-approve'
            ),
            path('deny/', ApprovalDenyView.as_view(), name='api-approval-deny'),
            path('cancel/', ApprovalCancelView.as_view(), name='api-approval-cancel'),
            path(
                'acquire-modify-lock/',
                ApprovalAcquireModifyLockView.as_view(),
                name='api-approval-acquire-lock',
            ),
            path(
                'release-modify-lock/',
                ApprovalReleaseModifyLockView.as_view(),
                name='api-approval-release-lock',
            ),
            path('revise/', ApprovalReviseView.as_view(), name='api-approval-revise'),
            path(
                'card-package/',
                ApprovalCardPackage.as_view(),
                name='api-approval-card-package',
            ),
            path(
                'revisions/',
                ApprovalRevisionList.as_view(),
                name='api-approval-revisions',
            ),
            path('events/', ApprovalEventList.as_view(), name='api-approval-events'),
            # Detail view (must be last to avoid matching sub-paths)
            path('', ApprovalDetail.as_view(), name='api-approval-detail'),
        ]),
    ),
    # Count endpoint (before list to avoid UUID matching)
    path('count/', ApprovalCount.as_view(), name='api-approval-count'),
    # List + create endpoint
    path('', ApprovalList.as_view(), name='api-approval-list'),
]
