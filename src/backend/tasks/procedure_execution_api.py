"""REST API for applying and executing governed procedures."""

import uuid

from django.core.exceptions import PermissionDenied
from django.http import Http404

from rest_framework import status
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from InvenTree.mixins import ListAPI

from .models import (
    ProcedureRevision,
    WorkOrderDeviation,
    WorkOrderProcedureApplication,
    WorkOrderStepExecution,
)
from .permissions import EXECUTE_WORKORDER, require_permission
from .procedure_api import ProcedureEnabledMixin, _revision_queryset
from .procedure_execution_serializers import (
    ApplyProcedureCommandSerializer,
    CompleteStepCommandSerializer,
    NotApplicableStepCommandSerializer,
    ProcedureApplicationSerializer,
    ReopenStepCommandSerializer,
    StepExecutionSerializer,
    WorkOrderDeviationSerializer,
)
from .scope import ScopeError
from .services.procedure_execution import (
    ProcedureExecutionError,
    apply_procedure_revision,
    complete_step,
    mark_step_not_applicable,
    reopen_step,
)
from .services.work_orders import (
    CommandConflict,
    IdempotencyConflict,
    IllegalTransition,
    ReadinessBlocked,
    StaleVersion,
    WorkOrderScopeError,
)
from .workorder_api import (
    WorkOrderPagination,
    _current_version,
    _error_body,
    _work_order_queryset,
)


class ProcedureExecutionEnabledMixin(ProcedureEnabledMixin):
    """Hide governed execution resources unless Procedures are enabled."""


def _scoped_work_order(request, pk):
    """Resolve the parent before any child lookup to prevent scope leakage."""
    return get_object_or_404(_work_order_queryset(request.user), pk=pk)


def _application_queryset(work_order):
    """Return applications with bounded relations used by serializers."""
    return WorkOrderProcedureApplication.objects.filter(
        work_order=work_order
    ).select_related('revision__procedure', 'applied_by')


def _step_queryset(work_order):
    """Return ordered executions for the active primary application."""
    return WorkOrderStepExecution.objects.filter(
        application__work_order=work_order, application__primary=True
    ).select_related('application', 'completed_by')


class ProcedureApplicationList(ProcedureExecutionEnabledMixin, ListAPI):
    """List scoped immutable applications for a work order."""

    serializer_class = ProcedureApplicationSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = WorkOrderPagination

    def get_queryset(self):
        """Resolve scope before accessing the child collection."""
        work_order = _scoped_work_order(self.request, self.kwargs['pk'])
        return _application_queryset(work_order).order_by('sequence', 'pk')


class ProcedureApply(ProcedureExecutionEnabledMixin, APIView):
    """Apply one exact immutable revision through the transactional service."""

    permission_classes = [IsAuthenticated]
    serializer_class = ApplyProcedureCommandSerializer

    def post(self, request, pk):
        """Validate command intent and return the durable application resource."""
        work_order = _scoped_work_order(request, pk)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        revision_id = values.pop('revision_id')
        get_object_or_404(_revision_queryset(request.user), pk=revision_id)
        correlation_id = uuid.uuid4()
        try:
            application = apply_procedure_revision(
                work_order_id=work_order.pk,
                revision_id=revision_id,
                actor=request.user,
                correlation_id=correlation_id,
                **values,
            )
        except (ScopeError, WorkOrderScopeError, ProcedureRevision.DoesNotExist):
            raise Http404
        except PermissionDenied as exc:
            return _command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_current_version(work_order.pk),
                response_status=status.HTTP_403_FORBIDDEN,
            )
        except (
            StaleVersion,
            IllegalTransition,
            CommandConflict,
            IdempotencyConflict,
            ReadinessBlocked,
        ) as exc:
            return _command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_current_version(work_order.pk),
                response_status=status.HTTP_409_CONFLICT,
            )
        except ProcedureExecutionError as exc:
            return _command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_current_version(work_order.pk),
                response_status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            ProcedureApplicationSerializer(application).data,
            status=status.HTTP_201_CREATED,
        )


class StepExecutionList(ProcedureExecutionEnabledMixin, ListAPI):
    """List ordered execution state for the primary applied procedure."""

    serializer_class = StepExecutionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = WorkOrderPagination

    def get_queryset(self):
        """Resolve scope before accessing execution rows."""
        work_order = _scoped_work_order(self.request, self.kwargs['pk'])
        return _step_queryset(work_order).order_by('sequence', 'pk')


class StepExecutionDetail(ProcedureExecutionEnabledMixin, APIView):
    """Retrieve exact snapshot and state for one primary applied step."""

    permission_classes = [IsAuthenticated]
    serializer_class = StepExecutionSerializer

    def get(self, request, pk, step):
        """Resolve parent scope before the stable step key."""
        work_order = _scoped_work_order(request, pk)
        execution = get_object_or_404(_step_queryset(work_order), step_key=step)
        return Response(self.serializer_class(execution).data)


def _command_error(exc, *, correlation_id, current_version, response_status):
    """Return the shared stable work-order command error envelope."""
    return Response(
        _error_body(
            code=getattr(exc, 'code', 'PERMISSION_DENIED'),
            detail=str(exc),
            correlation_id=correlation_id,
            current_version=current_version,
        ),
        status=response_status,
    )


class StepExecutionCommandView(ProcedureExecutionEnabledMixin, APIView):
    """Shared adapter from one scoped execution to a transactional service."""

    permission_classes = [IsAuthenticated]
    serializer_class = None
    service = None

    def post(self, request, pk, step):
        """Validate intent, invoke one service, and translate domain errors."""
        work_order = _scoped_work_order(request, pk)
        execution = get_object_or_404(_step_queryset(work_order), step_key=step)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        correlation_id = uuid.uuid4()
        try:
            result = self.service(
                work_order_id=work_order.pk,
                application_id=execution.application_id,
                step_key=execution.step_key,
                actor=request.user,
                correlation_id=correlation_id,
                **serializer.validated_data,
            )
        except (ScopeError, WorkOrderScopeError):
            raise Http404
        except PermissionDenied as exc:
            return _command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_execution_version(execution.pk),
                response_status=status.HTTP_403_FORBIDDEN,
            )
        except (
            StaleVersion,
            IllegalTransition,
            CommandConflict,
            IdempotencyConflict,
            ReadinessBlocked,
        ) as exc:
            return _command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_execution_version(execution.pk),
                response_status=status.HTTP_409_CONFLICT,
            )
        except ProcedureExecutionError as exc:
            return _command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_execution_version(execution.pk),
                response_status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(StepExecutionSerializer(result).data)


def _execution_version(execution_id):
    """Read the step concurrency token after a transactional rollback."""
    return WorkOrderStepExecution.objects.values_list('version', flat=True).get(
        pk=execution_id
    )


class StepExecutionComplete(StepExecutionCommandView):
    """Validate and record a completed or failed step."""

    serializer_class = CompleteStepCommandSerializer
    service = staticmethod(complete_step)


class StepExecutionNotApplicable(StepExecutionCommandView):
    """Record an explicit not-applicable disposition and deviation."""

    serializer_class = NotApplicableStepCommandSerializer
    service = staticmethod(mark_step_not_applicable)


class StepExecutionReopen(StepExecutionCommandView):
    """Reopen a terminal step for authorized correction or rework."""

    serializer_class = ReopenStepCommandSerializer
    service = staticmethod(reopen_step)


class WorkOrderDeviationList(ProcedureExecutionEnabledMixin, APIView):
    """List or create scoped controlled deviations."""

    permission_classes = [IsAuthenticated]
    serializer_class = WorkOrderDeviationSerializer
    pagination_class = WorkOrderPagination

    def get(self, request, pk):
        """Return a paginated audit collection after resolving parent scope."""
        work_order = _scoped_work_order(request, pk)
        rows = (
            WorkOrderDeviation.objects
            .filter(work_order=work_order)
            .select_related('actor', 'approval')
            .order_by('-created_at', '-pk')
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(
            self.serializer_class(page, many=True).data
        )

    def post(self, request, pk):
        """Create a scoped deviation with server-owned actor and parent."""
        work_order = _scoped_work_order(request, pk)
        try:
            require_permission(request.user, EXECUTE_WORKORDER)
        except PermissionDenied as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        serializer = self.serializer_class(
            data=request.data, context={'work_order': work_order}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(work_order=work_order, actor=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
