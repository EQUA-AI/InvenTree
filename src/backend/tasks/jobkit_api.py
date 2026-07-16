"""REST API for Job Kit planning (build, manual lines, shortages, events)."""

import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404

from rest_framework import status
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .jobkit_models import JobKit, JobKitAllocation, JobKitShortage
from .jobkit_serializers import (
    AddManualLineSerializer,
    BuildJobKitCommandSerializer,
    DecideSubstitutionSerializer,
    JobKitAllocationSerializer,
    JobKitLineSerializer,
    JobKitSerializer,
    JobKitShortageSerializer,
    JobKitSubstitutionSerializer,
    LinkPurchaseOrderSerializer,
    ProposeSubstitutionSerializer,
    RemoveManualLineSerializer,
    ReserveJobKitCommandSerializer,
    UpdateManualLineSerializer,
)
from .scope import ScopeError
from .services.job_kit_custody import (
    JobKitCustodyError,
    consume_allocation,
    issue_allocation,
    return_allocation,
    stage_allocation,
)
from .services.job_kits import (
    JobKitBuildError,
    JobKitError,
    JobKitLineError,
    JobKitStaleVersion,
    JobKitStateError,
    add_manual_line,
    build_job_kit,
    decide_substitution,
    link_po_to_shortage,
    propose_substitution,
    reconcile_job_kit,
    release_allocation,
    remove_manual_line,
    reserve_job_kit,
    update_manual_line,
)
from .services.stock_allocation import StockOverAllocation
from .services.work_orders import (
    CommandConflict,
    IdempotencyConflict,
    StaleVersion,
    WorkOrderScopeError,
)
from .workorder_api import WorkOrderPagination, _error_body, _work_order_queryset
from .workorder_serializers import WorkOrderEventSerializer

# Ledger event types produced by the Job Kit domain.
JOB_KIT_EVENT_TYPES = ('JOB_KIT_BUILT',)


class JobKitEnabledMixin:
    """Hide Job Kit planning resources unless the deployment flag is set."""

    def dispatch(self, request, *args, **kwargs):
        """Return a normal 404 while the additive API is disabled."""
        if not getattr(settings, 'AIMMS_JOB_KITS_ENABLED', False):
            raise Http404
        return super().dispatch(request, *args, **kwargs)


def _scoped_work_order(request, pk):
    """Resolve the parent work order under actor scope before any child access."""
    return get_object_or_404(_work_order_queryset(request.user), pk=pk)


def _kit_or_404(work_order):
    """Return the work order's Job Kit or a scope-safe 404."""
    kit = (
        JobKit.objects
        .filter(work_order=work_order)
        .select_related('staging_location', 'created_by')
        .first()
    )
    if kit is None:
        raise Http404
    return kit


def _kit_version(work_order_id):
    """Read the kit optimistic token after a transactional rollback."""
    return (
        JobKit.objects
        .filter(work_order_id=work_order_id)
        .values_list('version', flat=True)
        .first()
    )


class JobKitDetail(JobKitEnabledMixin, APIView):
    """Return the planned Job Kit and its ordered lines for a work order."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        """Resolve parent scope, then serialize the kit."""
        work_order = _scoped_work_order(request, pk)
        kit = _kit_or_404(work_order)
        return Response(JobKitSerializer(kit).data)


class JobKitBuild(JobKitEnabledMixin, APIView):
    """Deterministically build or reconcile the Job Kit from the procedure."""

    permission_classes = [IsAuthenticated]
    serializer_class = BuildJobKitCommandSerializer

    def post(self, request, pk):
        """Validate intent and invoke the transactional build service."""
        work_order = _scoped_work_order(request, pk)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        correlation_id = uuid.uuid4()
        try:
            kit = build_job_kit(
                work_order_id=work_order.pk,
                actor=request.user,
                correlation_id=correlation_id,
                **serializer.validated_data,
            )
        except (ScopeError, WorkOrderScopeError):
            raise Http404
        except PermissionDenied as exc:
            return self._error(
                exc,
                correlation_id,
                work_order.lifecycle_version,
                status.HTTP_403_FORBIDDEN,
            )
        except (
            StaleVersion,
            CommandConflict,
            IdempotencyConflict,
            JobKitBuildError,
        ) as exc:
            return self._error(
                exc,
                correlation_id,
                work_order.lifecycle_version,
                status.HTTP_409_CONFLICT,
            )
        return Response(JobKitSerializer(kit).data, status=status.HTTP_201_CREATED)

    @staticmethod
    def _error(exc, correlation_id, current_version, response_status):
        return Response(
            _error_body(
                code=getattr(exc, 'code', 'PERMISSION_DENIED'),
                detail=str(exc),
                correlation_id=correlation_id,
                current_version=current_version,
            ),
            status=response_status,
        )


def _line_command_error(exc, *, correlation_id, current_version, response_status):
    """Return the shared stable command error envelope for line operations."""
    return Response(
        _error_body(
            code=getattr(exc, 'code', 'PERMISSION_DENIED'),
            detail=str(exc),
            correlation_id=correlation_id,
            current_version=current_version,
        ),
        status=response_status,
    )


class JobKitLineList(JobKitEnabledMixin, APIView):
    """Append an authorized manual line to an editable Job Kit."""

    permission_classes = [IsAuthenticated]
    serializer_class = AddManualLineSerializer

    def post(self, request, pk):
        """Validate the manual line intent and invoke the service."""
        work_order = _scoped_work_order(request, pk)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        correlation_id = uuid.uuid4()
        try:
            line = add_manual_line(
                work_order_id=work_order.pk,
                actor=request.user,
                **serializer.validated_data,
            )
        except (ScopeError, WorkOrderScopeError):
            raise Http404
        except PermissionDenied as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_403_FORBIDDEN,
            )
        except (JobKitStaleVersion, JobKitStateError) as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_409_CONFLICT,
            )
        except JobKitLineError as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(JobKitLineSerializer(line).data, status=status.HTTP_201_CREATED)


class JobKitLineDetail(JobKitEnabledMixin, APIView):
    """Amend or remove one editable manual line."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, pk, line):
        """Amend a manual line's mutable planning fields."""
        work_order = _scoped_work_order(request, pk)
        serializer = UpdateManualLineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._invoke(
            request,
            work_order,
            line,
            update_manual_line,
            dict(serializer.validated_data),
            lambda result: Response(JobKitLineSerializer(result).data),
        )

    def delete(self, request, pk, line):
        """Remove a manual line from an editable kit."""
        work_order = _scoped_work_order(request, pk)
        serializer = RemoveManualLineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._invoke(
            request,
            work_order,
            line,
            remove_manual_line,
            dict(serializer.validated_data),
            lambda result: Response(status=status.HTTP_204_NO_CONTENT),
        )

    def _invoke(self, request, work_order, line_id, service, kwargs, on_success):
        correlation_id = uuid.uuid4()
        try:
            result = service(
                work_order_id=work_order.pk,
                line_id=line_id,
                actor=request.user,
                **kwargs,
            )
        except (ScopeError, WorkOrderScopeError):
            raise Http404
        except PermissionDenied as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_403_FORBIDDEN,
            )
        except (JobKitStaleVersion, JobKitStateError) as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_409_CONFLICT,
            )
        except JobKitLineError as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_400_BAD_REQUEST,
            )
        return on_success(result)


class JobKitShortageList(JobKitEnabledMixin, APIView):
    """List the shortages recorded against a Job Kit's lines."""

    permission_classes = [IsAuthenticated]
    pagination_class = WorkOrderPagination

    def get(self, request, pk):
        """Return a scoped, paginated shortage collection."""
        work_order = _scoped_work_order(request, pk)
        _kit_or_404(work_order)
        rows = (
            JobKitShortage.objects
            .filter(line__kit__work_order=work_order)
            .select_related('line', 'purchase_order_line', 'approval')
            .order_by('-created_at', '-pk')
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(
            JobKitShortageSerializer(page, many=True).data
        )


class JobKitEventList(JobKitEnabledMixin, APIView):
    """List the Job Kit audit events recorded on the work-order ledger."""

    permission_classes = [IsAuthenticated]
    pagination_class = WorkOrderPagination

    def get(self, request, pk):
        """Return a scoped, paginated Job Kit event collection."""
        work_order = _scoped_work_order(request, pk)
        rows = (
            work_order.events
            .filter(event_type__in=JOB_KIT_EVENT_TYPES)
            .select_related('actor')
            .order_by('-created_at', '-pk')
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(
            WorkOrderEventSerializer(page, many=True).data
        )


class JobKitReserve(JobKitEnabledMixin, APIView):
    """Atomically reserve stock for the Job Kit's required lines."""

    permission_classes = [IsAuthenticated]
    serializer_class = ReserveJobKitCommandSerializer

    def post(self, request, pk):
        """Validate intent and invoke the transactional reservation service."""
        work_order = _scoped_work_order(request, pk)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        correlation_id = uuid.uuid4()
        try:
            kit = reserve_job_kit(
                work_order_id=work_order.pk,
                actor=request.user,
                correlation_id=correlation_id,
                **serializer.validated_data,
            )
        except (ScopeError, WorkOrderScopeError):
            raise Http404
        except PermissionDenied as exc:
            return JobKitBuild._error(
                exc,
                correlation_id,
                work_order.lifecycle_version,
                status.HTTP_403_FORBIDDEN,
            )
        except (
            StaleVersion,
            CommandConflict,
            IdempotencyConflict,
            JobKitStateError,
            JobKitError,
        ) as exc:
            return JobKitBuild._error(
                exc,
                correlation_id,
                work_order.lifecycle_version,
                status.HTTP_409_CONFLICT,
            )
        return Response(JobKitSerializer(kit).data, status=status.HTTP_200_OK)


class JobKitAllocationList(JobKitEnabledMixin, APIView):
    """List the real stock reservations for a Job Kit."""

    permission_classes = [IsAuthenticated]
    pagination_class = WorkOrderPagination

    def get(self, request, pk):
        """Return a scoped, paginated allocation collection."""
        work_order = _scoped_work_order(request, pk)
        _kit_or_404(work_order)
        rows = (
            JobKitAllocation.objects
            .filter(line__kit__work_order=work_order)
            .select_related('line', 'stock_item', 'reserved_by')
            .order_by('line__sequence', 'pk')
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(
            JobKitAllocationSerializer(page, many=True).data
        )


class JobKitAllocationRelease(JobKitEnabledMixin, APIView):
    """Release one active Job Kit reservation, freeing its stock."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk, allocation):
        """Resolve scope, then release the reservation through the service."""
        work_order = _scoped_work_order(request, pk)
        correlation_id = uuid.uuid4()
        try:
            released = release_allocation(
                work_order_id=work_order.pk,
                allocation_id=allocation,
                actor=request.user,
                correlation_id=correlation_id,
            )
        except (ScopeError, WorkOrderScopeError):
            raise Http404
        except PermissionDenied as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_403_FORBIDDEN,
            )
        except (JobKitStateError, StockOverAllocation) as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_409_CONFLICT,
            )
        except JobKitLineError as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_404_NOT_FOUND,
            )
        return Response(JobKitAllocationSerializer(released).data)


class _JobKitCustodyCommand(JobKitEnabledMixin, APIView):
    """Shared adapter from a scoped allocation to a custody service."""

    permission_classes = [IsAuthenticated]
    service = None

    def service_kwargs(self, request):
        """Return extra service kwargs from the request body."""
        return {}

    def post(self, request, pk, allocation):
        """Resolve scope, then invoke the custody transition service."""
        work_order = _scoped_work_order(request, pk)
        correlation_id = uuid.uuid4()
        try:
            result = self.service(
                work_order_id=work_order.pk,
                allocation_id=allocation,
                actor=request.user,
                correlation_id=correlation_id,
                **self.service_kwargs(request),
            )
        except (ScopeError, WorkOrderScopeError):
            raise Http404
        except PermissionDenied as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_403_FORBIDDEN,
            )
        except (JobKitStateError, JobKitCustodyError) as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_409_CONFLICT,
            )
        except JobKitLineError as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_404_NOT_FOUND,
            )
        return Response(JobKitAllocationSerializer(result).data)


class JobKitAllocationStage(_JobKitCustodyCommand):
    """Stage a reserved allocation, optionally with scan proof."""

    service = staticmethod(stage_allocation)

    def service_kwargs(self, request):
        """Pass optional scan proof through to the staging service."""
        scan_proof = request.data.get('scan_proof') if request.data else None
        return {'scan_proof': scan_proof} if scan_proof else {}


class JobKitAllocationIssue(_JobKitCustodyCommand):
    """Record custody leaving the storeroom."""

    service = staticmethod(issue_allocation)


class JobKitAllocationConsume(_JobKitCustodyCommand):
    """Consume an allocation with a real stock removal effect."""

    service = staticmethod(consume_allocation)


class JobKitAllocationReturn(_JobKitCustodyCommand):
    """Return a reusable allocation without consuming stock."""

    service = staticmethod(return_allocation)


class JobKitRefresh(JobKitEnabledMixin, APIView):
    """Live reconciliation of the Job Kit from authoritative allocations."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        """Resolve scope, then reconcile the kit."""
        work_order = _scoped_work_order(request, pk)
        correlation_id = uuid.uuid4()
        try:
            kit = reconcile_job_kit(
                work_order_id=work_order.pk,
                actor=request.user,
                correlation_id=correlation_id,
            )
        except (ScopeError, WorkOrderScopeError):
            raise Http404
        except PermissionDenied as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_403_FORBIDDEN,
            )
        except JobKitStateError as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_409_CONFLICT,
            )
        return Response(JobKitSerializer(kit).data)


class JobKitShortageLinkPO(JobKitEnabledMixin, APIView):
    """Link a real purchase-order line to a shortage (procurement handoff)."""

    permission_classes = [IsAuthenticated]
    serializer_class = LinkPurchaseOrderSerializer

    def post(self, request, pk, shortage):
        """Validate intent and link the shortage to a real PO line."""
        work_order = _scoped_work_order(request, pk)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        correlation_id = uuid.uuid4()
        try:
            linked = link_po_to_shortage(
                work_order_id=work_order.pk,
                shortage_id=shortage,
                actor=request.user,
                **serializer.validated_data,
            )
        except (ScopeError, WorkOrderScopeError):
            raise Http404
        except PermissionDenied as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_403_FORBIDDEN,
            )
        except JobKitLineError as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(JobKitShortageSerializer(linked).data)


class JobKitSubstitutionPropose(JobKitEnabledMixin, APIView):
    """Propose a governed alternate part for a Job Kit line."""

    permission_classes = [IsAuthenticated]
    serializer_class = ProposeSubstitutionSerializer

    def post(self, request, pk, line):
        """Validate the proposal intent and invoke the service."""
        work_order = _scoped_work_order(request, pk)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        correlation_id = uuid.uuid4()
        try:
            substitution = propose_substitution(
                work_order_id=work_order.pk,
                line_id=line,
                actor=request.user,
                **serializer.validated_data,
            )
        except (ScopeError, WorkOrderScopeError):
            raise Http404
        except PermissionDenied as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_403_FORBIDDEN,
            )
        except JobKitStateError as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_409_CONFLICT,
            )
        except JobKitLineError as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            JobKitSubstitutionSerializer(substitution).data,
            status=status.HTTP_201_CREATED,
        )


class JobKitSubstitutionDecide(JobKitEnabledMixin, APIView):
    """Approve or reject a proposed substitution under separation of duties."""

    permission_classes = [IsAuthenticated]
    serializer_class = DecideSubstitutionSerializer

    def post(self, request, pk, substitution):
        """Validate the decision intent and invoke the service."""
        work_order = _scoped_work_order(request, pk)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        correlation_id = uuid.uuid4()
        try:
            decided = decide_substitution(
                work_order_id=work_order.pk,
                substitution_id=substitution,
                actor=request.user,
                **serializer.validated_data,
            )
        except (ScopeError, WorkOrderScopeError):
            raise Http404
        except PermissionDenied as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_403_FORBIDDEN,
            )
        except JobKitStateError as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_409_CONFLICT,
            )
        except JobKitLineError as exc:
            return _line_command_error(
                exc,
                correlation_id=correlation_id,
                current_version=_kit_version(work_order.pk),
                response_status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(JobKitSubstitutionSerializer(decided).data)
