"""REST API for Closeout Automation (Feature #15).

Every route resolves its parent work order scope-first through the same
bounded queryset the canonical work-order API uses; the closeout surface is
additionally gated by ``AIMMS_CLOSEOUT_WIZARD_ENABLED`` and returns a normal
404 while disabled. Services re-check permission, scope, version, and
idempotency on every command — view-level checks are convenience only.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404

from rest_framework import status
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .closeout_models import (
    CloseoutAmendment,
    CloseoutEffect,
    CloseoutPartUsage,
    CloseoutReading,
)
from .closeout_serializers import (
    AmendmentDecideSerializer,
    AmendmentProposeSerializer,
    CaptureCreateCommandSerializer,
    CaptureReviseCommandSerializer,
    CloseoutAmendmentSerializer,
    CloseoutCaptureSerializer,
    CloseoutEffectSerializer,
    CloseoutPartUsageSerializer,
    CloseoutProposalSerializer,
    CloseoutReadingSerializer,
    DecisionBatchCommandSerializer,
    EffectRetrySerializer,
    PartUsageCreateSerializer,
    PartUsageResolveSerializer,
    ReadingCreateSerializer,
    ReadingDispositionSerializer,
)
from .scope import ScopeError
from .services.closeout_amend import (
    decide_amendment,
    propose_amendment,
    verify_closeout,
)
from .services.closeout_capture import (
    _live_proposal,
    abandon_capture,
    create_capture,
    record_decisions,
    request_extraction,
    revise_capture,
)
from .services.closeout_effects import retry_effect
from .services.closeout_reconcile import (
    add_narrative_candidate,
    add_walkup_usage,
    disposition_reading,
    record_reading,
    refresh_closeout_reconciliation,
    resolve_part_usage,
)
from .services.work_orders import (
    ReadinessBlocked,
    WorkOrderCommandError,
    WorkOrderScopeError,
)
from .workorder_api import (
    WorkOrderEnabledMixin,
    WorkOrderPagination,
    _current_version,
    _error_body,
    _work_order_queryset,
)
from .workorder_serializers import WorkOrderReadinessSerializer


class CloseoutEnabledMixin(WorkOrderEnabledMixin):
    """Hide the closeout surface unless its deployment flag is enabled."""

    def dispatch(self, request, *args, **kwargs):
        """Return a normal 404 while the additive closeout API is disabled."""
        if not getattr(settings, 'AIMMS_CLOSEOUT_WIZARD_ENABLED', False):
            raise Http404
        return super().dispatch(request, *args, **kwargs)


class CloseoutCommandView(CloseoutEnabledMixin, APIView):
    """Shared scope-first parent lookup and stable error translation."""

    permission_classes = [IsAuthenticated]

    def get_work_order(self, request, pk):
        """Resolve the parent work order scope-safely (404 on any mismatch)."""
        return get_object_or_404(_work_order_queryset(request.user), pk=pk)

    def run(self, request, work_order, service, **arguments):
        """Invoke one service and translate domain errors to the envelope."""
        correlation_id = uuid.uuid4()
        try:
            result = service(
                work_order_id=work_order.pk, actor=request.user, **arguments
            )
        except ReadinessBlocked as exc:
            readiness = WorkOrderReadinessSerializer(exc.readiness).data
            blockers = readiness['blockers']
            code = blockers[0]['code'] if blockers else exc.code
            return Response(
                _error_body(
                    code=code,
                    detail=str(exc),
                    correlation_id=correlation_id,
                    current_version=work_order.lifecycle_version,
                    blockers=blockers,
                ),
                status=status.HTTP_409_CONFLICT,
            )
        except (ScopeError, WorkOrderScopeError):
            raise Http404
        except PermissionDenied as exc:
            return Response(
                _error_body(
                    code='PERMISSION_DENIED',
                    detail=str(exc),
                    correlation_id=correlation_id,
                    current_version=_current_version(work_order.pk),
                ),
                status=status.HTTP_403_FORBIDDEN,
            )
        except WorkOrderCommandError as exc:
            code = getattr(exc, 'code', 'COMMAND_INVALID')
            conflict = code in {
                'STALE_VERSION',
                'IDEMPOTENCY_CONFLICT',
                'CAPTURE_STALE_REVISION',
                'READINESS_BLOCKED',
            }
            return Response(
                _error_body(
                    code=code,
                    detail=str(exc),
                    correlation_id=correlation_id,
                    current_version=_current_version(work_order.pk),
                ),
                status=status.HTTP_409_CONFLICT
                if conflict
                else status.HTTP_400_BAD_REQUEST,
            )
        return result


def _is_response(value) -> bool:
    return isinstance(value, Response)


class CloseoutCaptureList(CloseoutCommandView):
    """List captures for a scoped work order, or create a typed capture."""

    def get(self, request, pk):
        """Return every capture for the work order, newest first."""
        work_order = self.get_work_order(request, pk)
        captures = work_order.closeout_captures.select_related(
            'current_revision'
        ).order_by('-pk')
        return Response(CloseoutCaptureSerializer(captures, many=True).data)

    def post(self, request, pk):
        """Create a typed narrative capture."""
        work_order = self.get_work_order(request, pk)
        serializer = CaptureCreateCommandSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = self.run(
            request,
            work_order,
            create_capture,
            narrative=data['narrative'],
            expected_version=data['expected_version'],
            idempotency_key=data['idempotency_key'],
        )
        if _is_response(result):
            return result
        return Response(asdict(result), status=status.HTTP_200_OK)


class CloseoutCaptureDetail(CloseoutCommandView):
    """Revise the narrative (new revision) or abandon the capture."""

    def patch(self, request, pk, cap):
        """Apply one revise-or-abandon intent."""
        work_order = self.get_work_order(request, pk)
        serializer = CaptureReviseCommandSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data['abandon']:
            result = self.run(
                request,
                work_order,
                abandon_capture,
                capture_id=cap,
                expected_version=data['expected_version'],
                idempotency_key=data['idempotency_key'],
                reason=data.get('reason', ''),
            )
        else:
            if 'expected_revision' not in data:
                return Response(
                    {'detail': 'expected_revision is required to revise'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            result = self.run(
                request,
                work_order,
                revise_capture,
                capture_id=cap,
                narrative=data.get('narrative', ''),
                expected_revision=data['expected_revision'],
                expected_version=data['expected_version'],
                idempotency_key=data['idempotency_key'],
            )
        if _is_response(result):
            return result
        return Response(asdict(result), status=status.HTTP_200_OK)


class CloseoutCaptureExtract(CloseoutCommandView):
    """Request schema-only extraction; idempotent per narrative revision."""

    def post(self, request, pk, cap):
        """Run extraction and return the stored proposal."""
        work_order = self.get_work_order(request, pk)
        result = self.run(request, work_order, request_extraction, capture_id=cap)
        if _is_response(result):
            return result
        return Response(CloseoutProposalSerializer(result).data)


class CloseoutCaptureProposal(CloseoutEnabledMixin, APIView):
    """Return the live proposal (with decisions) for a capture."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk, cap):
        """Scope-first read of the current proposal."""
        work_order = get_object_or_404(_work_order_queryset(request.user), pk=pk)
        capture = get_object_or_404(
            work_order.closeout_captures.select_related('current_revision'), pk=cap
        )
        proposal = (
            _live_proposal(capture.current_revision)
            if capture.current_revision
            else None
        )
        if proposal is None:
            raise Http404
        return Response(CloseoutProposalSerializer(proposal).data)


class CloseoutDecisionBatch(CloseoutCommandView):
    """Record a batch of explicit per-field promotion decisions."""

    def post(self, request, pk, cap):
        """Record decisions and return the command receipt."""
        work_order = self.get_work_order(request, pk)
        serializer = DecisionBatchCommandSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = self.run(
            request,
            work_order,
            record_decisions,
            capture_id=cap,
            decisions=[dict(entry) for entry in data['decisions']],
            expected_version=data['expected_version'],
            idempotency_key=data['idempotency_key'],
        )
        if _is_response(result):
            return result
        return Response(asdict(result), status=status.HTTP_200_OK)


class CloseoutPartUsageList(CloseoutCommandView):
    """List reconciliation rows, or add a walk-up/candidate row."""

    def get(self, request, pk):
        """Return every usage row for the work order."""
        work_order = self.get_work_order(request, pk)
        rows = CloseoutPartUsage.objects.filter(work_order=work_order).order_by('pk')
        return Response(CloseoutPartUsageSerializer(rows, many=True).data)

    def post(self, request, pk):
        """Add one walk-up usage row or narrative candidate."""
        work_order = self.get_work_order(request, pk)
        serializer = PartUsageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data['kind'] == 'walkup':
            required = {'stock_item', 'used_quantity', 'stock_tracking_id'}
            if not required <= set(data):
                return Response(
                    {
                        'detail': 'walkup rows require stock_item, used_quantity, stock_tracking_id'
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            result = self.run(
                request,
                work_order,
                add_walkup_usage,
                stock_item_id=data['stock_item'],
                used_quantity=data['used_quantity'],
                stock_tracking_id=data['stock_tracking_id'],
                reason=data.get('reason', ''),
            )
        else:
            result = self.run(
                request,
                work_order,
                add_narrative_candidate,
                candidate_text=data.get('candidate_text', ''),
            )
        if _is_response(result):
            return result
        return Response(
            CloseoutPartUsageSerializer(result).data, status=status.HTTP_201_CREATED
        )


class CloseoutPartUsageResolve(CloseoutCommandView):
    """Resolve one usage row with an explicit disposition."""

    def post(self, request, pk, row):
        """Apply the disposition and return the updated row."""
        work_order = self.get_work_order(request, pk)
        serializer = PartUsageResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = self.run(
            request,
            work_order,
            resolve_part_usage,
            row_id=row,
            disposition=data['disposition'],
            reason=data.get('reason', ''),
            used_quantity=data.get('used_quantity'),
            expected_row_version=data.get('expected_row_version'),
        )
        if _is_response(result):
            return result
        return Response(CloseoutPartUsageSerializer(result).data)


class CloseoutPartUsageRefresh(CloseoutCommandView):
    """Idempotent reconciliation refresh from custody truth."""

    def post(self, request, pk):
        """Re-derive rows and return counts only."""
        work_order = self.get_work_order(request, pk)
        result = self.run(request, work_order, refresh_closeout_reconciliation)
        if _is_response(result):
            return result
        return Response(result)


class CloseoutReadingList(CloseoutCommandView):
    """List readings, or record one."""

    def get(self, request, pk):
        """Return every reading for the work order."""
        work_order = self.get_work_order(request, pk)
        rows = CloseoutReading.objects.filter(work_order=work_order).order_by('pk')
        return Response(CloseoutReadingSerializer(rows, many=True).data)

    def post(self, request, pk):
        """Record one reading with deterministic normalization."""
        work_order = self.get_work_order(request, pk)
        serializer = ReadingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = self.run(
            request,
            work_order,
            record_reading,
            label=data['label'],
            raw_text=data.get('raw_text', ''),
            unit=data.get('unit', ''),
            phase=data.get('phase', 'after'),
            required=data.get('required', False),
            expected_min=data.get('expected_min'),
            expected_max=data.get('expected_max'),
            step_execution_id=data.get('step_execution'),
            evidence_attachment_ids=data.get('evidence_attachment_ids') or [],
        )
        if _is_response(result):
            return result
        return Response(
            CloseoutReadingSerializer(result).data, status=status.HTTP_201_CREATED
        )


class CloseoutReadingDisposition(CloseoutCommandView):
    """Resolve one failed or ambiguous reading."""

    def post(self, request, pk, reading):
        """Apply the disposition; retest returns the replacement row too."""
        work_order = self.get_work_order(request, pk)
        serializer = ReadingDispositionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = self.run(
            request,
            work_order,
            disposition_reading,
            reading_id=reading,
            disposition=data['disposition'],
            reason=data['reason'],
        )
        if _is_response(result):
            return result
        resolved, replacement = result
        return Response({
            'reading': CloseoutReadingSerializer(resolved).data,
            'replacement': (
                CloseoutReadingSerializer(replacement).data if replacement else None
            ),
        })


class CloseoutEffectList(CloseoutEnabledMixin, APIView):
    """Effect ledger with retry state for the work order's closeout."""

    permission_classes = [IsAuthenticated]
    pagination_class = WorkOrderPagination

    def get(self, request, pk):
        """Scope-first read of the fan-out ledger."""
        work_order = get_object_or_404(_work_order_queryset(request.user), pk=pk)
        effects = CloseoutEffect.objects.filter(
            closeout__work_order=work_order
        ).order_by('pk')
        return Response(CloseoutEffectSerializer(effects, many=True).data)


class CloseoutEffectRetry(CloseoutCommandView):
    """Authorized manual retry of one effect."""

    def post(self, request, pk, effect):
        """Return the effect to the pending pool."""
        work_order = self.get_work_order(request, pk)
        serializer = EffectRetrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        effect_row = get_object_or_404(
            CloseoutEffect.objects.filter(closeout__work_order=work_order), pk=effect
        )
        correlation_id = uuid.uuid4()
        try:
            result = retry_effect(effect_id=effect_row.pk, actor=request.user)
        except PermissionDenied as exc:
            return Response(
                _error_body(
                    code='PERMISSION_DENIED',
                    detail=str(exc),
                    correlation_id=correlation_id,
                    current_version=work_order.lifecycle_version,
                ),
                status=status.HTTP_403_FORBIDDEN,
            )
        except WorkOrderCommandError as exc:
            return Response(
                _error_body(
                    code=getattr(exc, 'code', 'COMMAND_INVALID'),
                    detail=str(exc),
                    correlation_id=correlation_id,
                    current_version=work_order.lifecycle_version,
                ),
                status=status.HTTP_409_CONFLICT,
            )
        return Response(CloseoutEffectSerializer(result).data)


class CloseoutVerify(CloseoutCommandView):
    """One-shot supervisor verification of the completed closeout."""

    def post(self, request, pk):
        """Set previously-null verified_by/verified_at exactly once."""
        from .workorder_serializers import BaseCommandSerializer

        work_order = self.get_work_order(request, pk)
        serializer = BaseCommandSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = self.run(
            request,
            work_order,
            verify_closeout,
            expected_version=data['expected_version'],
            idempotency_key=data['idempotency_key'],
        )
        if _is_response(result):
            return result
        return Response(asdict(result), status=status.HTTP_200_OK)


class CloseoutAmendmentList(CloseoutCommandView):
    """List amendments, or propose one."""

    def get(self, request, pk):
        """Return every amendment for the work order's closeout."""
        work_order = self.get_work_order(request, pk)
        amendments = CloseoutAmendment.objects.filter(
            closeout__work_order=work_order
        ).order_by('pk')
        return Response(CloseoutAmendmentSerializer(amendments, many=True).data)

    def post(self, request, pk):
        """Propose a governed correction."""
        work_order = self.get_work_order(request, pk)
        serializer = AmendmentProposeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = self.run(
            request,
            work_order,
            propose_amendment,
            changes=data['changes'],
            reason=data['reason'],
            expected_version=data['expected_version'],
            idempotency_key=data['idempotency_key'],
        )
        if _is_response(result):
            return result
        return Response(asdict(result), status=status.HTTP_200_OK)


class CloseoutAmendmentDecide(CloseoutCommandView):
    """Approve (and apply) or reject one amendment under policy."""

    def post(self, request, pk, amendment):
        """Decide the amendment and return the command receipt."""
        work_order = self.get_work_order(request, pk)
        serializer = AmendmentDecideSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = self.run(
            request,
            work_order,
            decide_amendment,
            amendment_id=amendment,
            approve=data['approve'],
            expected_version=data['expected_version'],
            idempotency_key=data['idempotency_key'],
            reason=data.get('reason', ''),
        )
        if _is_response(result):
            return result
        return Response(asdict(result), status=status.HTTP_200_OK)
