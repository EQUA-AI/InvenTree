"""API views for the AI Agent Approval Queue.

Implements all endpoints from spec Sections 7.0 - 7.4.
"""

import uuid as _uuid

from django.db import transaction
from django.urls import include, path
from django.utils import timezone

import django_filters.rest_framework.filters as rest_filters
import structlog
from django_filters.rest_framework.filterset import FilterSet
from rest_framework import status
from rest_framework.response import Response

from InvenTree.filters import SEARCH_ORDER_FILTER
from InvenTree.mixins import CreateAPI, ListAPI, ListCreateAPI, RetrieveAPI

from . import serializers as approval_serializers
from .models import (
    TERMINAL_STATUSES,
    Approval,
    ApprovalEvent,
    ApprovalRevision,
    ApprovalStatus,
    EventType,
    ExecutedEffect,
)
from .permissions import (
    ApprovalDecisionThrottle,
    ApprovalReadThrottle,
    ApprovalReviseThrottle,
    HasApprovalReviewPermission,
)
from .resume import attempt_agent_resume
from .sanitizers import redact_error

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

        # Build JSON containment query for overlapping entity_refs
        return (
            Approval.objects
            .filter(
                status__in=active_statuses, payload__entity_refs__contains=entity_refs
            )
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

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Create."""
        approval = _get_approval_for_update(self.kwargs['pk'])
        if not approval:
            return _error_response(
                'not_found',
                f'Approval {self.kwargs["pk"]} not found',
                status.HTTP_404_NOT_FOUND,
            )

        if not approval.can_transition_to(ApprovalStatus.IN_REVIEW):
            return _error_response(
                'conflict',
                f'Cannot transition from {approval.status} to in_review',
                status.HTTP_409_CONFLICT,
                current_status=approval.status,
            )

        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=request.user)

        return Response(
            approval_serializers.ApprovalDetailSerializer(approval).data,
            status=status.HTTP_200_OK,
        )


class ApprovalConfirmViewedView(CreateAPI):
    """Confirm that the user has reviewed the details (Tier 2-3 gate).

    POST /api/approvals/{id}/confirm-viewed/
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalDecisionThrottle]
    serializer_class = approval_serializers.ConfirmViewedSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Create."""
        approval = _get_approval_for_update(self.kwargs['pk'])
        if not approval:
            return _error_response(
                'not_found',
                f'Approval {self.kwargs["pk"]} not found',
                status.HTTP_404_NOT_FOUND,
            )

        if approval.is_terminal:
            return _error_response(
                'conflict',
                'Approval is in a terminal state',
                status.HTTP_409_CONFLICT,
                current_status=approval.status,
            )

        # Must be in_review or changes_requested to confirm viewed
        allowed_statuses = {ApprovalStatus.IN_REVIEW, ApprovalStatus.CHANGES_REQUESTED}
        if approval.status not in allowed_statuses:
            return _error_response(
                'conflict',
                f'Cannot confirm-viewed when status is {approval.status}. '
                'Must be in_review or changes_requested.',
                status.HTTP_409_CONFLICT,
                current_status=approval.status,
            )

        now = timezone.now()
        approval.viewed_confirmed_at = now
        approval.viewed_confirmed_by_user = request.user
        approval.save(
            update_fields=[
                'viewed_confirmed_at',
                'viewed_confirmed_by_user',
                'updated_at',
            ]
        )

        ApprovalEvent.objects.create(
            approval=approval,
            event_type=EventType.VIEWED_CONFIRMED,
            actor_user=request.user,
            event_payload={'confirmed_at': now.isoformat()},
        )

        return Response(
            approval_serializers.ApprovalDetailSerializer(approval).data,
            status=status.HTTP_200_OK,
        )


class ApprovalRequestChangesView(CreateAPI):
    """Request changes on an approval.

    POST /api/approvals/{id}/request-changes/
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalDecisionThrottle]
    serializer_class = approval_serializers.RequestChangesSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Create."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        approval = _get_approval_for_update(self.kwargs['pk'])
        if not approval:
            return _error_response(
                'not_found',
                f'Approval {self.kwargs["pk"]} not found',
                status.HTTP_404_NOT_FOUND,
            )

        if not approval.can_transition_to(ApprovalStatus.CHANGES_REQUESTED):
            return _error_response(
                'conflict',
                f'Cannot transition from {approval.status} to changes_requested',
                status.HTTP_409_CONFLICT,
                current_status=approval.status,
            )

        approval.transition_to(
            ApprovalStatus.CHANGES_REQUESTED,
            actor_user=request.user,
            event_payload={'instructions': serializer.validated_data['instructions']},
        )

        return Response(
            approval_serializers.ApprovalDetailSerializer(approval).data,
            status=status.HTTP_200_OK,
        )


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
        """Create."""
        pk = self.kwargs['pk']

        # ── Phase 1: Read-only checks (no lock, no transaction) ──
        approval = _get_approval_or_404(pk)
        if not approval:
            return _error_response(
                'not_found', f'Approval {pk} not found', status.HTTP_404_NOT_FOUND
            )

        # Idempotent: if already approved/executing/succeeded, return current
        if approval.status in (
            ApprovalStatus.APPROVED,
            ApprovalStatus.EXECUTING,
            ApprovalStatus.SUCCEEDED,
        ):
            data = approval_serializers.ApprovalDetailSerializer(approval).data
            data['was_already_terminal'] = True  # A-6
            return Response(data, status=status.HTTP_200_OK)

        if approval.is_terminal:
            data = approval_serializers.ApprovalDetailSerializer(approval).data
            data['was_already_terminal'] = True  # A-6
            return Response(data, status=status.HTTP_200_OK)

        try:
            approval.check_lock_allows_action(request.user, 'approve')
        except ValueError as e:
            return _error_response(
                'locked',
                str(e),
                status.HTTP_423_LOCKED,
                holder_user_id=approval.lock_holder_id,
                expires_at=(
                    approval.modification_lock_expires_at.isoformat()
                    if approval.modification_lock_expires_at
                    else None
                ),
            )

        if approval.risk_tier >= 2 and not approval.viewed_confirmed_at:
            return _error_response(
                'forbidden',
                'Tier 2-3 approvals require confirm-viewed before approve',
                status.HTTP_403_FORBIDDEN,
            )

        if not approval.can_transition_to(ApprovalStatus.APPROVED):
            return _error_response(
                'conflict',
                f'Cannot transition from {approval.status} to approved',
                status.HTTP_409_CONFLICT,
                current_status=approval.status,
            )

        # ── Phase 2: Revalidation (no lock, may do network I/O) ──
        from .executors import is_executor_required
        from .executors import registry as executor_registry

        executor = None
        if executor_registry.has(approval.action_type):
            executor = executor_registry.get(approval.action_type)
            try:  # noqa: PLW0717 - established drift-check block
                drift_report = executor.check_preconditions(
                    approval.payload, approval.baseline_context
                )
                if drift_report and getattr(drift_report, 'has_drift', False):
                    with transaction.atomic():
                        locked = _get_approval_for_update(pk)
                        if locked and not locked.is_terminal:
                            ApprovalEvent.objects.create(
                                approval=locked,
                                event_type=EventType.REVALIDATION_FAILED,
                                actor_user=request.user,
                                event_payload={'drift_report': str(drift_report)},
                            )
                            if locked.can_transition_to(
                                ApprovalStatus.CHANGES_REQUESTED
                            ):
                                locked.transition_to(
                                    ApprovalStatus.CHANGES_REQUESTED,
                                    actor_user=request.user,
                                    event_payload={'reason': 'revalidation_failed'},
                                )
                    return _error_response(
                        'conflict',
                        'Approve-time revalidation detected drift',
                        status.HTTP_409_CONFLICT,
                    )
            except Exception:
                logger.warning('revalidation_error', approval_id=str(pk), exc_info=True)

        # ── Phase 3: Lock + transition to approved → executing ──
        with transaction.atomic():
            locked = _get_approval_for_update(pk)
            if not locked:
                return _error_response(
                    'not_found', f'Approval {pk} not found', status.HTTP_404_NOT_FOUND
                )

            # Re-verify state after acquiring lock
            if locked.status in (
                ApprovalStatus.APPROVED,
                ApprovalStatus.EXECUTING,
                ApprovalStatus.SUCCEEDED,
            ):
                data = approval_serializers.ApprovalDetailSerializer(locked).data
                data['was_already_terminal'] = True
                return Response(data, status=status.HTTP_200_OK)

            if locked.is_terminal:
                data = approval_serializers.ApprovalDetailSerializer(locked).data
                data['was_already_terminal'] = True
                return Response(data, status=status.HTTP_200_OK)

            if not locked.can_transition_to(ApprovalStatus.APPROVED):
                return _error_response(
                    'conflict',
                    f'Cannot transition from {locked.status} to approved',
                    status.HTTP_409_CONFLICT,
                    current_status=locked.status,
                )

            missing_required_executor = is_executor_required(
                locked.action_type
            ) and not executor_registry.has(locked.action_type)
            locked.transition_to(ApprovalStatus.APPROVED, actor_user=request.user)
            if missing_required_executor:
                locked.execution_error = redact_error(
                    'No executor registered for required action'
                )
                locked.transition_to(
                    ApprovalStatus.FAILED,
                    actor_user=request.user,
                    extra_update_fields=['execution_error'],
                )
            elif locked.can_transition_to(ApprovalStatus.EXECUTING):
                locked.transition_to(ApprovalStatus.EXECUTING, actor_user=request.user)

        # ── Phase 4: Execute via executor (outside transaction) ──
        effect_result = None
        if executor:
            try:
                effect_result = executor.execute(locked.payload, locked.idempotency_key)
            except Exception as exc:
                logger.error('executor_failed', approval_id=str(pk), exc_info=True)
                effect_result = type(
                    'EffectResult',
                    (),
                    {
                        'success': False,
                        'error_message': str(exc),
                        'result_payload': None,
                        'effect_ref': None,
                    },
                )()

        # ── Phase 5: Record result (new transaction) ──
        with transaction.atomic():
            locked = _get_approval_for_update(pk)
            if locked and locked.status == ApprovalStatus.EXECUTING:
                if effect_result and effect_result.success:
                    from .executors import compute_effect_idempotency_key

                    effect_key = compute_effect_idempotency_key(
                        locked.idempotency_key, locked.action_type
                    )
                    ExecutedEffect.objects.get_or_create(
                        idempotency_key=effect_key,
                        defaults={
                            'approval': locked,
                            'effect_type': locked.action_type,
                            'effect_ref': getattr(effect_result, 'effect_ref', '')
                            or '',
                        },
                    )
                    locked.execution_result = (
                        getattr(effect_result, 'result_payload', None) or {}
                    )
                    locked.transition_to(
                        ApprovalStatus.SUCCEEDED,
                        actor_user=request.user,
                        extra_update_fields=['execution_result'],
                    )
                elif effect_result and not effect_result.success:
                    locked.execution_error = redact_error(effect_result.error_message)
                    locked.transition_to(
                        ApprovalStatus.FAILED,
                        actor_user=request.user,
                        extra_update_fields=['execution_error'],
                    )
                else:
                    if is_executor_required(locked.action_type):
                        locked.execution_error = redact_error(
                            'No executor registered for required action'
                        )
                        locked.transition_to(
                            ApprovalStatus.FAILED,
                            actor_user=request.user,
                            extra_update_fields=['execution_error'],
                        )
                    else:
                        locked.transition_to(
                            ApprovalStatus.SUCCEEDED, actor_user=request.user
                        )

        # ── Phase 6: Agent resume (outside transaction) ──
        attempt_agent_resume(locked, 'approved', request.user)

        return Response(
            approval_serializers.ApprovalDetailSerializer(locked).data,
            status=status.HTTP_200_OK,
        )


class ApprovalDenyView(CreateAPI):
    """Deny an approval.

    POST /api/approvals/{id}/deny/
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalDecisionThrottle]
    serializer_class = approval_serializers.DenySerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Create."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        approval = _get_approval_for_update(self.kwargs['pk'])
        if not approval:
            return _error_response(
                'not_found',
                f'Approval {self.kwargs["pk"]} not found',
                status.HTTP_404_NOT_FOUND,
            )

        # Idempotent: if already terminal, return
        if approval.is_terminal:
            return Response(
                approval_serializers.ApprovalDetailSerializer(approval).data,
                status=status.HTTP_200_OK,
            )

        # Check lock blocks deny for non-holder
        try:
            approval.check_lock_allows_action(request.user, 'deny')
        except ValueError as e:
            return _error_response(
                'locked',
                str(e),
                status.HTTP_423_LOCKED,
                holder_user_id=approval.lock_holder_id,
                expires_at=(
                    approval.modification_lock_expires_at.isoformat()
                    if approval.modification_lock_expires_at
                    else None
                ),
            )

        if not approval.can_transition_to(ApprovalStatus.DENIED):
            return _error_response(
                'conflict',
                f'Cannot transition from {approval.status} to denied',
                status.HTTP_409_CONFLICT,
                current_status=approval.status,
            )

        reason = serializer.validated_data['reason']
        approval.deny_reason = reason
        # D-3: Save deny_reason atomically with transition
        approval.transition_to(
            ApprovalStatus.DENIED,
            actor_user=request.user,
            event_payload={'reason': reason},
            extra_update_fields=['deny_reason'],
        )

        # A-3: Resume agent runtime with denial
        attempt_agent_resume(approval, 'denied', request.user)

        return Response(
            approval_serializers.ApprovalDetailSerializer(approval).data,
            status=status.HTTP_200_OK,
        )


class ApprovalCancelView(CreateAPI):
    """Cancel an approval.

    POST /api/approvals/{id}/cancel/
    Cancel semantics: reverts to previous revision, then terminates.
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalDecisionThrottle]
    serializer_class = approval_serializers.CancelSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Create."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        approval = _get_approval_for_update(self.kwargs['pk'])
        if not approval:
            return _error_response(
                'not_found',
                f'Approval {self.kwargs["pk"]} not found',
                status.HTTP_404_NOT_FOUND,
            )

        # Idempotent: if already terminal, return
        if approval.is_terminal:
            return Response(
                approval_serializers.ApprovalDetailSerializer(approval).data,
                status=status.HTTP_200_OK,
            )

        if not approval.can_transition_to(ApprovalStatus.CANCELED):
            return _error_response(
                'conflict',
                f'Cannot transition from {approval.status} to canceled',
                status.HTTP_409_CONFLICT,
                current_status=approval.status,
            )

        reason = serializer.validated_data.get('reason', '')

        # If there are revisions beyond 0, revert to previous
        if approval.current_revision_number > 0:
            prev_revision = ApprovalRevision.objects.filter(
                approval=approval, revision_number=approval.current_revision_number - 1
            ).first()
            if prev_revision:
                old_revision = approval.current_revision_number
                approval.payload = prev_revision.payload_snapshot
                # A-7: Update current_revision_number on revert
                approval.current_revision_number = prev_revision.revision_number
                approval.save(
                    update_fields=['payload', 'current_revision_number', 'updated_at']
                )

                ApprovalEvent.objects.create(
                    approval=approval,
                    event_type=EventType.CANCEL_REVERTED,
                    actor_user=request.user,
                    event_payload={
                        'reverted_from_revision': old_revision,
                        'reverted_to_revision': prev_revision.revision_number,
                    },
                )

        # D-3: Store cancel reason and transition atomically
        approval.canceled_reason = reason
        approval.transition_to(
            ApprovalStatus.CANCELED,
            actor_user=request.user,
            event_payload={'reason': reason},
            extra_update_fields=['canceled_reason'],
        )

        # A-3: Resume agent with cancellation
        attempt_agent_resume(approval, 'canceled', request.user)

        return Response(
            approval_serializers.ApprovalDetailSerializer(approval).data,
            status=status.HTTP_200_OK,
        )


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

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Create."""
        approval = _get_approval_for_update(self.kwargs['pk'])
        if not approval:
            return _error_response(
                'not_found',
                f'Approval {self.kwargs["pk"]} not found',
                status.HTTP_404_NOT_FOUND,
            )

        if approval.is_terminal:
            return _error_response(
                'conflict',
                'Cannot modify a terminal approval',
                status.HTTP_409_CONFLICT,
                current_status=approval.status,
            )

        try:
            lock_meta = approval.acquire_lock(request.user)
        except ValueError as e:
            # A-11: Return 423 Locked for lock conflicts
            return _error_response(
                'locked',
                str(e),
                status.HTTP_423_LOCKED,
                holder_user_id=approval.lock_holder_id,
                expires_at=(
                    approval.modification_lock_expires_at.isoformat()
                    if approval.modification_lock_expires_at
                    else None
                ),
            )

        return Response(lock_meta, status=status.HTTP_200_OK)


class ApprovalReleaseModifyLockView(CreateAPI):
    """Release the modification lock.

    POST /api/approvals/{id}/release-modify-lock/
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalDecisionThrottle]
    serializer_class = approval_serializers.ReleaseModifyLockSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Create."""
        approval = _get_approval_for_update(self.kwargs['pk'])
        if not approval:
            return _error_response(
                'not_found',
                f'Approval {self.kwargs["pk"]} not found',
                status.HTTP_404_NOT_FOUND,
            )

        try:
            approval.release_lock(request.user)
        except ValueError as e:
            return _error_response('forbidden', str(e), status.HTTP_403_FORBIDDEN)

        return Response({'detail': 'Lock released'}, status=status.HTTP_200_OK)


class ApprovalReviseView(CreateAPI):
    """Submit a new revision.

    POST /api/approvals/{id}/revise/
    """

    permission_classes = [HasApprovalReviewPermission]
    throttle_classes = [ApprovalReviseThrottle]
    serializer_class = approval_serializers.ReviseSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Create."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        approval = _get_approval_for_update(self.kwargs['pk'])
        if not approval:
            return _error_response(
                'not_found',
                f'Approval {self.kwargs["pk"]} not found',
                status.HTTP_404_NOT_FOUND,
            )

        # Status restriction: only in_review or changes_requested
        allowed_statuses = {ApprovalStatus.IN_REVIEW, ApprovalStatus.CHANGES_REQUESTED}
        if approval.status not in allowed_statuses:
            return _error_response(
                'invalid_status',
                'Revisions are only allowed when status is in_review or changes_requested',
                status.HTTP_409_CONFLICT,
                current_status=approval.status,
            )

        # Lock enforcement
        if (
            approval.is_lock_active
            and approval.modification_lock_user_id != request.user.pk
        ):
            return _error_response(
                'locked',
                'Approval is being modified by another user',
                status.HTTP_423_LOCKED,
                holder_user_id=approval.modification_lock_user_id,
                expires_at=(
                    approval.modification_lock_expires_at.isoformat()
                    if approval.modification_lock_expires_at
                    else None
                ),
            )

        # Optimistic concurrency check
        expected_rev = serializer.validated_data['expected_revision']
        if expected_rev != approval.current_revision_number:
            return _error_response(
                'conflict',
                f'Expected revision {expected_rev} but current is {approval.current_revision_number}',
                status.HTTP_409_CONFLICT,
                current_revision=approval.current_revision_number,
            )

        data = serializer.validated_data
        new_revision_number = approval.current_revision_number + 1

        # A-9: Payload size check moved to ReviseSerializer.validate_payload()

        # Create new revision
        ApprovalRevision.objects.create(
            approval=approval,
            revision_number=new_revision_number,
            payload_snapshot=data['payload'],
            diff_summary=data.get('diff_summary'),
            created_by_user=request.user,
        )

        # Update approval
        approval.payload = data['payload']
        approval.current_revision_number = new_revision_number
        approval.save(
            update_fields=['payload', 'current_revision_number', 'updated_at']
        )

        # Emit revised event
        ApprovalEvent.objects.create(
            approval=approval,
            event_type=EventType.REVISED,
            actor_user=request.user,
            event_payload={
                'revision_number': new_revision_number,
                'diff_summary': data.get('diff_summary'),
                'note': data.get('note', ''),
            },
        )

        return Response(
            approval_serializers.ApprovalDetailSerializer(approval).data,
            status=status.HTTP_200_OK,
        )


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
