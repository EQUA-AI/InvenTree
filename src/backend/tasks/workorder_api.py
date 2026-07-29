"""Canonical REST API endpoints for maintenance work orders."""

from __future__ import annotations

import uuid
from dataclasses import asdict

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404

from rest_framework import status
from rest_framework.generics import get_object_or_404
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from InvenTree.mixins import ListAPI, ListCreateAPI, RetrieveUpdateAPI

from .models import WorkOrder, WorkOrderEvent
from .permissions import PLAN_WORKORDER, VIEW_WORKORDER_AUDIT, require_permission
from .scope import ScopeError, require_work_order_scope, work_order_scope_filter
from .services.closeout import complete_work_order
from .services.readiness import evaluate_work_order_readiness
from .services.work_orders import (
    CommandConflict,
    IdempotencyConflict,
    IllegalTransition,
    ReadinessBlocked,
    StaleVersion,
    WorkOrderCommandError,
    WorkOrderScopeError,
    assign_work_order,
    cancel_work_order,
    hold_work_order,
    resume_work_order,
    transition_work_order,
)
from .workorder_serializers import (
    AssignCommandSerializer,
    CompleteCommandSerializer,
    HoldCommandSerializer,
    ResumeCommandSerializer,
    TransitionCommandSerializer,
    WorkOrderCancelCommandSerializer,
    WorkOrderEventSerializer,
    WorkOrderReadinessSerializer,
    WorkOrderSerializer,
)

_CLOSEOUT_FIELDS = (
    'action',
    'result',
    'verification_summary',
    'cause',
    'downtime_minutes',
    'follow_up_required',
    'follow_up',
)


class WorkOrderPagination(LimitOffsetPagination):
    """Ensure canonical collections remain paginated without a global page size."""

    default_limit = 50
    max_limit = 200


class WorkOrderEnabledMixin:
    """Hide the canonical API unless its deployment flag is enabled."""

    def dispatch(self, request, *args, **kwargs):
        """Return a normal 404 while the additive API is disabled."""
        if not getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False):
            raise Http404
        return super().dispatch(request, *args, **kwargs)


def _scoped_work_orders(actor):
    """Apply actor scope before any other queryset operation."""
    try:
        return WorkOrder.objects.filter(work_order_scope_filter(actor))
    except ScopeError:
        return WorkOrder.objects.none()


def _work_order_queryset(actor):
    """Return the bounded resource queryset after scope has been applied."""
    return _scoped_work_orders(actor).select_related(
        'machine', 'customer', 'assigned_to', 'requested_by'
    )


class WorkOrderList(WorkOrderEnabledMixin, ListCreateAPI):
    """List scoped work orders or create planning metadata."""

    serializer_class = WorkOrderSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = WorkOrderPagination

    def get_queryset(self):
        """Scope the collection before ordering and pagination."""
        return _work_order_queryset(self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        """Require planning authority and prove scope before persistence."""
        require_permission(self.request.user, PLAN_WORKORDER)
        candidate = WorkOrder(
            requested_by=self.request.user, **serializer.validated_data
        )
        try:
            require_work_order_scope(self.request.user, candidate)
        except ScopeError as exc:
            raise Http404 from exc
        # The reference is assigned by WorkOrder.save(), so every creation path
        # produces the same identifier.
        serializer.save(requested_by=self.request.user)


class WorkOrderDetail(WorkOrderEnabledMixin, RetrieveUpdateAPI):
    """Retrieve or update non-command planning metadata."""

    serializer_class = WorkOrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Make object lookup scope-safe."""
        return _work_order_queryset(self.request.user)

    def perform_update(self, serializer):
        """Require planning authority and retain the resulting scope."""
        require_permission(self.request.user, PLAN_WORKORDER)
        candidate = serializer.instance
        for field, value in serializer.validated_data.items():
            setattr(candidate, field, value)
        try:
            require_work_order_scope(self.request.user, candidate)
        except ScopeError as exc:
            raise Http404 from exc
        serializer.save()


class WorkOrderReadiness(WorkOrderEnabledMixin, APIView):
    """Explain the unified readiness decision for a scoped work order."""

    permission_classes = [IsAuthenticated]
    serializer_class = WorkOrderReadinessSerializer

    def get(self, request, pk):
        """Evaluate the requested action without changing state."""
        work_order = get_object_or_404(_work_order_queryset(request.user), pk=pk)
        action = request.query_params.get('action', 'start')
        readiness = evaluate_work_order_readiness(
            work_order, action=action, actor=request.user
        )
        return Response(self.serializer_class(readiness).data)


def _error_body(*, code, detail, correlation_id, current_version, blockers=None):
    """Build the stable command error envelope."""
    body = {
        'code': code,
        'detail': detail,
        'correlation_id': correlation_id,
        'current_version': current_version,
        'blockers': blockers or [],
    }
    return body


#: Service arguments the HTTP adapter supplies itself, or that name a caller's
#: authority rather than its intent. ``post`` splats validated data into the
#: service, so anything a serializer happened to declare under one of these
#: names would arrive as if the request had claimed it. Stripped rather than
#: rejected: a request cannot legitimately mean any of them, and dropping the
#: key leaves the service on its own default, which is the safe one.
RESERVED_SERVICE_ARGUMENTS = frozenset({
    'actor',
    'correlation_id',
    'packet_finalization',
    'work_order_id',
})


class WorkOrderCommandView(WorkOrderEnabledMixin, APIView):
    """Shared adapter from validated command intent to domain services."""

    permission_classes = [IsAuthenticated]
    serializer_class = None
    service = None

    def service_arguments(self, validated_data):
        """Return service-specific arguments from validated command data."""
        return validated_data

    def post(self, request, pk):
        """Validate intent, invoke one service, and translate domain errors."""
        work_order = get_object_or_404(_work_order_queryset(request.user), pk=pk)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        correlation_id = uuid.uuid4()
        arguments = {
            name: value
            for name, value in self.service_arguments(
                dict(serializer.validated_data)
            ).items()
            if name not in RESERVED_SERVICE_ARGUMENTS
        }
        try:
            result = self.service(
                work_order_id=work_order.pk,
                actor=request.user,
                correlation_id=correlation_id,
                **arguments,
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
        except (
            StaleVersion,
            IllegalTransition,
            CommandConflict,
            IdempotencyConflict,
        ) as exc:
            return Response(
                _error_body(
                    code=exc.code,
                    detail=str(exc),
                    correlation_id=correlation_id,
                    current_version=_current_version(work_order.pk),
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
            return Response(
                _error_body(
                    code=getattr(exc, 'code', 'COMMAND_INVALID'),
                    detail=str(exc),
                    correlation_id=correlation_id,
                    current_version=_current_version(work_order.pk),
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(asdict(result), status=status.HTTP_200_OK)


def _current_version(work_order_id):
    """Read the current optimistic concurrency token after a rollback."""
    return WorkOrder.objects.values_list('lifecycle_version', flat=True).get(
        pk=work_order_id
    )


class WorkOrderTransition(WorkOrderCommandView):
    """Apply a legal standalone lifecycle transition."""

    serializer_class = TransitionCommandSerializer
    service = staticmethod(transition_work_order)


class WorkOrderAssign(WorkOrderCommandView):
    """Assign the work order to a typed user."""

    serializer_class = AssignCommandSerializer
    service = staticmethod(assign_work_order)


class WorkOrderHold(WorkOrderCommandView):
    """Place in-progress work on hold."""

    serializer_class = HoldCommandSerializer
    service = staticmethod(hold_work_order)


class WorkOrderResume(WorkOrderCommandView):
    """Resume held work after readiness revalidation."""

    serializer_class = ResumeCommandSerializer
    service = staticmethod(resume_work_order)


class WorkOrderCancel(WorkOrderCommandView):
    """Cancel standalone work."""

    serializer_class = WorkOrderCancelCommandSerializer
    service = staticmethod(cancel_work_order)


class WorkOrderComplete(WorkOrderCommandView):
    """Complete standalone work with structured closeout and writeback."""

    serializer_class = CompleteCommandSerializer
    service = staticmethod(complete_work_order)

    def service_arguments(self, validated_data):
        """Pack the closeout fields into the service's closeout payload."""
        arguments = {
            'expected_version': validated_data['expected_version'],
            'idempotency_key': validated_data['idempotency_key'],
            'closeout': {name: validated_data[name] for name in _CLOSEOUT_FIELDS},
        }
        if validated_data.get('capture_id') is not None:
            arguments['capture_id'] = validated_data['capture_id']
        return arguments


class WorkOrderEventList(WorkOrderEnabledMixin, ListAPI):
    """Return a paginated, scoped audit timeline."""

    serializer_class = WorkOrderEventSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = WorkOrderPagination

    def get_queryset(self):
        """Scope the parent before selecting its event collection."""
        work_order = get_object_or_404(
            _work_order_queryset(self.request.user), pk=self.kwargs['pk']
        )
        require_permission(self.request.user, VIEW_WORKORDER_AUDIT)
        return (
            WorkOrderEvent.objects
            .filter(work_order=work_order)
            .select_related('actor')
            .order_by('-created_at')
        )
