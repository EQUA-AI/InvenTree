"""REST API endpoints for the tasks application."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import include, path
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from django_filters.rest_framework import FilterSet, filters
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

import InvenTree.helpers
import InvenTree.permissions
from InvenTree.filters import SEARCH_ORDER_FILTER
from InvenTree.mixins import ListCreateAPI, RetrieveUpdateDestroyAPI
from tasks.json_lookups import filter_json_array_contains
from tasks.time_api import WorkTime

from .attention import attention_filter, order_by_attention
from .authority_api import WorkOperationalAuthority
from .calibration_api import CalibrationTraceabilityCommand
from .closeout_api import (
    CloseoutAmendmentDecide,
    CloseoutAmendmentList,
    CloseoutCaptureDetail,
    CloseoutCaptureExtract,
    CloseoutCaptureList,
    CloseoutCaptureProposal,
    CloseoutDecisionBatch,
    CloseoutEffectList,
    CloseoutEffectRetry,
    CloseoutPartUsageList,
    CloseoutPartUsageRefresh,
    CloseoutPartUsageResolve,
    CloseoutReadingDisposition,
    CloseoutReadingList,
    CloseoutVerify,
)
from .closeout_pdf_api import WorkAcceptedReportPDF, WorkCloseoutPDF
from .dashboard_metrics import OPEN
from .exercise_verification_api import ExerciseEvidence, ExerciseVerification
from .investigation_api import WorkInvestigationRequests
from .jobkit_api import (
    JobKitAllocationConsume,
    JobKitAllocationIssue,
    JobKitAllocationList,
    JobKitAllocationRelease,
    JobKitAllocationReturn,
    JobKitAllocationStage,
    JobKitBuild,
    JobKitDetail,
    JobKitEventList,
    JobKitLineDetail,
    JobKitLineList,
    JobKitRefresh,
    JobKitReserve,
    JobKitShortageLinkPO,
    JobKitShortageList,
    JobKitSubstitutionDecide,
    JobKitSubstitutionPropose,
)
from .materials_api import WorkOrderMaterials
from .models import (
    KanbanCard,
    KanbanColumn,
    WorkOrder,
    WorkOrderDependency,
    WorkOrderPart,
)
from .persona import work_persona
from .procedure_api import (
    ProcedureArchive,
    ProcedureBlockers,
    ProcedureDetail,
    ProcedureList,
    ProcedurePublish,
    ProcedureRequestReview,
    ProcedureResourceDetail,
    ProcedureResourceList,
    ProcedureRevisionDetail,
    ProcedureRevisionList,
    ProcedureStepDetail,
    ProcedureStepList,
    ProcedureStepReorder,
)
from .procedure_execution_api import (
    ProcedureApplicationList,
    ProcedureApply,
    StepExecutionComplete,
    StepExecutionDetail,
    StepExecutionList,
    StepExecutionNotApplicable,
    StepExecutionReopen,
    WorkOrderDeviationList,
)
from .procurement_api import ProcurementAcknowledgment, ProcurementSourceSelection
from .repair_scope_draft_api import RepairScopeDraft
from .report_api import WorkAcceptedReport, WorkCaptureMedia, WorkCaptureMediaUpload
from .scope import (
    ScopeError,
    require_work_order_scope,
    scoped_machines,
    scoped_work_orders,
)
from .serializers import (
    KanbanCardSerializer,
    KanbanColumnSerializer,
    WorkOrderBoardSerializer,
    WorkOrderDependencySerializer,
    WorkOrderOverviewSerializer,
    WorkOrderPartSerializer,
    WorkOrderTableSerializer,
)
from .service_report_api import WorkServiceReport
from .services import schedule_planner, scheduling
from .services.conflicts import detect_conflicts
from .services.work_orders import CommandConflict
from .workorder_api import (
    WorkOrderAcceptHandover,
    WorkOrderAssign,
    WorkOrderCancel,
    WorkOrderComplete,
    WorkOrderDetail,
    WorkOrderEventList,
    WorkOrderHold,
    WorkOrderList,
    WorkOrderReadiness,
    WorkOrderResume,
    WorkOrderTransition,
)
from .workorder_mutations import (
    WorkOrderMachineInputMixin,
    object_data,
    parent_id,
    require_work_order_candidate,
)


class WorkOrderBoardFilter(FilterSet):
    """Filter set for Kanban cards.

    ``min_date`` / ``max_date`` bound a calendar or timeline viewport. A card is in
    the window when its scheduled range *overlaps* that window -- not when it is
    contained by it -- so a job running across the whole of a viewed month is
    returned even though neither endpoint falls inside it.

    Cards with no schedule fall back to ``due_date``, which keeps unscheduled work
    visible on the date it is due. A card with no schedule and no due date has no
    position in time and is excluded from a windowed query; it remains visible on
    the board, which is unwindowed.

    The two filters are deliberately independent and complementary: ``min_date``
    discards work that finished before the window, ``max_date`` discards work
    starting after it, and applying both yields the overlap.
    """

    tags = filters.CharFilter(method='filter_tags')
    min_date = filters.DateFilter(
        method='filter_min_date', label='Window start (inclusive)'
    )
    max_date = filters.DateFilter(
        method='filter_max_date', label='Window end (inclusive)'
    )

    class Meta:
        """Filter metadata."""

        model = WorkOrder
        fields = (
            'status',
            'priority',
            'assignee',
            'job_number',
            'service_quote',
            'company',
            'machine',
            'assigned_to',
            'lifecycle_status',
            'work_order_type',
        )

    #: True for a card carrying neither schedule endpoint.
    _UNSCHEDULED = Q(scheduled_start__isnull=True, scheduled_end__isnull=True)

    def filter_min_date(self, queryset, name, value):
        """Keep cards whose schedule ends on or after ``value``.

        A card with only a start is treated as ending at its start, so a
        zero-length placement still registers on its own day.
        """
        if not value:
            return queryset

        ends_after = Q(scheduled_end__date__gte=value) | Q(
            scheduled_end__isnull=True, scheduled_start__date__gte=value
        )

        return queryset.filter(
            ends_after | (self._UNSCHEDULED & Q(due_date__gte=value))
        )

    def filter_max_date(self, queryset, name, value):
        """Keep cards whose schedule starts on or before ``value``.

        A card with only an end is treated as starting at its end, mirroring
        ``filter_min_date``.
        """
        if not value:
            return queryset

        starts_before = Q(scheduled_start__date__lte=value) | Q(
            scheduled_start__isnull=True, scheduled_end__date__lte=value
        )

        return queryset.filter(
            starts_before | (self._UNSCHEDULED & Q(due_date__lte=value))
        )

    def filter_tags(self, queryset, name, value):
        """Filter cards by a comma separated list of tags."""
        if not value:
            return queryset

        tags = [tag.strip() for tag in value.split(',') if tag.strip()]

        for tag in tags:
            queryset = filter_json_array_contains(queryset, 'tags', tag)

        return queryset


class ScopedWorkOrderMixin(WorkOrderMachineInputMixin):
    """Keep legacy work reads and direct edits inside the canonical boundary."""

    def get_queryset(self):
        """Filter before lookup, search, ordering or pagination."""
        return (
            super().get_queryset().filter(pk__in=scoped_work_orders(self.request.user))
        )

    def perform_create(self, serializer):
        """Check the new machine's owning scope before writing."""
        require_work_order_candidate(self.request.user, serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        """Check the resulting boundary as well as the scoped original object."""
        require_work_order_candidate(self.request.user, serializer)
        super().perform_update(serializer)


class ScopedKanbanCardMixin:
    """Authorize a board card through its owning work order, not its card ID."""

    def get_queryset(self):
        """Retain only cards belonging to accessible work."""
        return (
            super()
            .get_queryset()
            .filter(work_order__in=scoped_work_orders(self.request.user))
        )

    def perform_create(self, serializer):
        """An add role cannot select another scope's work-order parent."""
        get_object_or_404(
            scoped_work_orders(self.request.user),
            pk=serializer.validated_data['work_order'].pk,
        )
        super().perform_create(serializer)


class KanbanCardList(ScopedKanbanCardMixin, ListCreateAPI):
    """List and create the cards a work order is tracked by.

    Unpaginated for the same reason the work-order board is: the board renders
    every card it is given, with no results envelope.
    """

    queryset = KanbanCard.objects.all()
    serializer_class = KanbanCardSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'
    filter_backends = SEARCH_ORDER_FILTER
    filterset_fields = ['work_order', 'status', 'card_kind', 'is_active']
    search_fields = ['title', 'description', 'assignee']
    ordering_fields = ['board_order', 'created_at', 'updated_at', 'scheduled_start']
    ordering = 'board_order'
    pagination_class = None

    def get_queryset(self):
        """Return active cards with the job context the serializer reads."""
        queryset = (
            super()
            .get_queryset()
            .select_related(
                'work_order',
                'work_order__machine',
                'assigned_to',
                'work_order__assigned_to',
            )
        )

        include_inactive = InvenTree.helpers.str2bool(
            self.request.query_params.get('include_inactive', False)
        )
        if not include_inactive:
            queryset = queryset.filter(is_active=True)

        return queryset.order_by('board_order', 'created_at')


class KanbanCardDetail(ScopedKanbanCardMixin, RetrieveUpdateDestroyAPI):
    """Retrieve, move, or archive one card.

    Moving a card between columns is a PATCH of ``status`` here. It changes
    where a piece of work sits on the board and nothing else - the job's
    lifecycle is moved by its own commands, which evaluate readiness.
    """

    queryset = KanbanCard.objects.all()
    serializer_class = KanbanCardSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'

    def perform_destroy(self, instance):
        """Archive rather than remove, matching the work-order board.

        The card tracking the job itself is never archived away: a work order
        with no card would vanish from the board while still being open.
        """
        if instance.card_kind == KanbanCard.KIND_WORK_ORDER:
            raise ValidationError({
                'card_kind': [
                    'The card tracking the work order itself cannot be archived; '
                    'archive the work order instead.'
                ]
            })
        if instance.is_active:
            instance.is_active = False
            instance.save(update_fields=['is_active', 'updated_at'])


class WorkOrderBoardList(ScopedWorkOrderMixin, ListCreateAPI):
    """List and create work orders as the board sees them."""

    queryset = WorkOrder.objects.all()
    serializer_class = WorkOrderBoardSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'
    filter_backends = SEARCH_ORDER_FILTER
    filterset_class = WorkOrderBoardFilter
    search_fields = [
        'title',
        'description',
        'assignee',
        'job_number',
        'service_quote',
        'company',
    ]
    ordering_fields = [
        'created_at',
        'updated_at',
        'priority',
        'due_date',
        'scheduled_start',
        'scheduled_end',
    ]
    ordering = '-created_at'
    # Deliberately unpaginated: the board reads ``response.data`` directly, with no
    # results envelope. Do not add pagination here without updating that client.
    pagination_class = None

    def get_queryset(self):
        """Filter the card collection by activity flag."""
        # select_related keeps the serializer's machine/assignee labels from
        # issuing a query per card -- the list is unpaginated, so an N+1 here
        # scales with the whole board. prefetch_related covers the nested parts
        # serializer, which was already issuing one query per card before the
        # scheduling fields were added.
        queryset = (
            super()
            .get_queryset()
            .select_related('machine', 'assigned_to')
            .prefetch_related('work_order_parts__part')
        )

        include_inactive = InvenTree.helpers.str2bool(
            self.request.query_params.get('include_inactive', False)
        )

        if not include_inactive:
            queryset = queryset.filter(is_active=True)

        return queryset.order_by('-created_at')


class WorkOrderTablePagination(LimitOffsetPagination):
    """Bound native Work-list reads without changing the Board's array API."""

    default_limit = 25
    max_limit = 200


class WorkOrderTableList(WorkOrderBoardList):
    """Read the native work projection with authorized server-side pagination.

    ``attention=true`` narrows to the ``tasks.attention`` "Needs attention"
    rule (version in ``NEEDS_ATTENTION_RULE``), orders by it unless an explicit
    ``ordering`` is given, and adds each row's reasons. The site's local date
    is taken once per request so the filter and the reasons agree.
    """

    http_method_names = ['get', 'head', 'options']
    pagination_class = WorkOrderTablePagination
    serializer_class = WorkOrderTableSerializer
    ordering = ['-created_at', '-pk']
    search_fields = [
        'reference',
        'title',
        'description',
        'machine__name',
        'assigned_to__username',
        'assigned_to__first_name',
        'assigned_to__last_name',
        'assignee',
    ]

    def _attention_today(self):
        """The site date when the attention scope was requested, else None."""
        if not hasattr(self, '_attention_date'):
            value = self.request.query_params.get('attention')
            if value not in (None, '', 'true', 'false'):
                raise ValidationError({'attention': ['Use true or false.']})
            self._attention_date = timezone.localdate() if value == 'true' else None
        return self._attention_date

    def _lifecycle_group(self):
        """``open`` narrows to the open lifecycle set; nothing else is offered.

        R6 P12: the machine Maintenance tab's Open work view. The set is the
        one the Machines list and location counts use (``dashboard_metrics.
        OPEN``), so its count agrees with theirs in the same scope.
        """
        value = self.request.query_params.get('lifecycle_group')
        if value not in (None, '', 'open'):
            raise ValidationError({'lifecycle_group': ['Use open.']})
        return value or None

    def get_queryset(self):
        """Narrow to the attention rule and lifecycle group when requested."""
        queryset = super().get_queryset()
        if self._lifecycle_group() == 'open':
            queryset = queryset.filter(lifecycle_status__in=OPEN)
        today = self._attention_today()
        return queryset if today is None else queryset.filter(attention_filter(today))

    def filter_queryset(self, queryset):
        """Apply search and filters, then the attention order by default."""
        queryset = super().filter_queryset(queryset)
        if self._attention_today() is not None and not self.request.query_params.get(
            'ordering'
        ):
            queryset = order_by_attention(queryset)
        return queryset

    def get_serializer_context(self):
        """Give the serializer the same date the filter used."""
        context = super().get_serializer_context()
        today = self._attention_today()
        if today is not None:
            context['attention_today'] = today
        return context


class WorkOrderPersona(APIView):
    """The reader's Work persona and first-use Work-list scope (``tasks.persona``).

    Read on first use only; the list's URL and saved choice take precedence.
    """

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'

    @extend_schema(responses={200: dict})
    def get(self, request):
        """Return the persona from live group membership; never cached."""
        response = Response(work_persona(request.user))
        response['Cache-Control'] = 'private, no-store'
        return response


class WorkOrderBoardDetail(ScopedWorkOrderMixin, RetrieveUpdateDestroyAPI):
    """Retrieve, update, or archive a Kanban card."""

    queryset = WorkOrder.objects.all()
    serializer_class = WorkOrderBoardSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'

    def perform_destroy(self, instance):
        """Archive (soft-delete) rather than remove the card."""
        if instance.is_active:
            instance.is_active = False
            instance.save(update_fields=['is_active', 'updated_at'])


class WorkOrderOverviewDetail(APIView):
    """Return complete work-order context from the stable Kanban surface."""

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'
    serializer_class = WorkOrderOverviewSerializer

    def get(self, request, pk):
        """Return one card with its hierarchy, execution, and repair context."""
        queryset = (
            scoped_work_orders(request.user)
            .select_related(
                'machine',
                'assigned_to',
                'requested_by',
                'repair_packet',
                'maintenance_record',
                'structured_closeout__completed_by',
            )
            .prefetch_related(
                'work_order_parts__part',
                'cards__assigned_to',
                'dependencies_in__predecessor__machine',
                'dependencies_in__predecessor__assigned_to',
                'dependencies_out__successor__machine',
                'dependencies_out__successor__assigned_to',
                'events__actor',
                'repair_packet__gates',
                # The new detail sections join here rather than issuing a request
                # per section: the page renders from one read.
                'repair_packet__findings__snapshot',
                'repair_packet__approved_scopes',
                'anomalies__source',
            )
        )
        work_order = get_object_or_404(queryset, pk=pk)
        return Response(
            self.serializer_class(work_order, context={'request': request}).data
        )


class WorkOrderRestore(APIView):
    """Restore a previously archived Kanban card."""

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order.change'
    serializer_class = WorkOrderBoardSerializer

    def post(self, request, pk):
        """Restore an archived card."""
        work_order = get_object_or_404(scoped_work_orders(request.user), pk=pk)

        if not work_order.is_active:
            work_order.is_active = True
            work_order.save(update_fields=['is_active', 'updated_at'])

        serializer = self.serializer_class(work_order, context={'request': request})
        return Response(serializer.data)


def _allocation_summary(work_order):
    """Serialize a card's parts and collect any stock-shortage warnings.

    Shared by the allocate-parts action and the reconcile PUT so both report
    allocation identically. Reads ``card.work_order_parts`` fresh, so callers should
    have run ``check_and_allocate`` on the affected parts first.
    """
    results = []
    warnings = []

    for cp in work_order.work_order_parts.all().select_related('part'):
        results.append(WorkOrderPartSerializer(cp).data)

        if cp.allocation_status == WorkOrderPart.ALLOCATION_INSUFFICIENT:
            warnings.append(
                f"Part '{cp.part.name}' (ID {cp.part.pk}): "
                f'need {cp.quantity}, only {cp.allocated_quantity} available'
            )
        elif cp.allocation_status == WorkOrderPart.ALLOCATION_PARTIAL:
            warnings.append(
                f"Part '{cp.part.name}' (ID {cp.part.pk}): "
                f'partial allocation - {cp.allocated_quantity} of {cp.quantity}'
            )

    return {'parts': results, 'warnings': warnings, 'all_allocated': len(warnings) == 0}


class WorkOrderPartList(APIView):
    """List, add, or reconcile the parts for a Kanban card."""

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'

    @extend_schema(operation_id='kanban_cards_parts_list')
    def get(self, request, work_order_pk):
        """List all parts for a card."""
        work_order = get_object_or_404(
            scoped_work_orders(request.user), pk=work_order_pk
        )
        parts = work_order.work_order_parts.all().select_related('part')
        serializer = WorkOrderPartSerializer(parts, many=True)
        return Response(serializer.data)

    def post(self, request, work_order_pk):
        """Add a part to a card and check stock availability."""
        work_order = get_object_or_404(
            scoped_work_orders(request.user), pk=work_order_pk
        )
        serializer = WorkOrderPartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        work_order_part = WorkOrderPart.objects.create(
            work_order=work_order,
            part=serializer.validated_data['part'],
            quantity=serializer.validated_data.get('quantity', 1),
        )
        work_order_part.check_and_allocate()

        return Response(
            WorkOrderPartSerializer(work_order_part).data,
            status=status.HTTP_201_CREATED,
        )

    def put(self, request, work_order_pk):
        """Reconcile the card's whole parts list in one transaction.

        The body is the *desired* full set of parts (``[{"part": id,
        "quantity": n}, ...]``); anything not listed is removed. This replaces a
        per-part POST loop on the client that had two data-loss bugs: it never
        deleted a removed part (its delete loop was empty), and re-POSTing an
        existing part hit the ``unique_together(card, part)`` constraint and
        raised a 500 the client swallowed, so quantity edits to existing parts
        were silently dropped. Reconciling server-side removes both failure modes
        and makes the save atomic.
        """
        work_order = get_object_or_404(
            scoped_work_orders(request.user), pk=work_order_pk
        )
        payload = request.data

        if not isinstance(payload, list):
            raise ValidationError({'detail': 'Expected a list of parts.'})

        desired = {}
        for item in payload:
            serializer = WorkOrderPartSerializer(data=item)
            serializer.is_valid(raise_exception=True)
            part = serializer.validated_data['part']

            if part.pk in desired:
                raise ValidationError({
                    'detail': f'Part {part.pk} is listed more than once.'
                })

            desired[part.pk] = serializer.validated_data.get('quantity', 1)

        with transaction.atomic():
            existing = {
                cp.part_id: cp
                for cp in work_order.work_order_parts.select_related('part')
            }

            for part_id, cp in existing.items():
                if part_id not in desired:
                    cp.delete()

            for part_id, quantity in desired.items():
                cp = existing.get(part_id)

                if cp is None:
                    cp = WorkOrderPart.objects.create(
                        work_order=work_order, part_id=part_id, quantity=quantity
                    )
                elif cp.quantity != quantity:
                    cp.quantity = quantity
                    cp.save(update_fields=['quantity', 'updated_at'])

                cp.check_and_allocate()

        return Response(_allocation_summary(work_order))


class WorkOrderPartDetail(APIView):
    """Retrieve, update, or remove a part from a Kanban card."""

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'

    def get(self, request, work_order_pk, pk):
        """Return one card part."""
        work_order_part = get_object_or_404(
            WorkOrderPart.objects.filter(
                work_order__in=scoped_work_orders(request.user)
            ),
            pk=pk,
            work_order_id=work_order_pk,
        )
        return Response(WorkOrderPartSerializer(work_order_part).data)

    def patch(self, request, work_order_pk, pk):
        """Update the required quantity and re-check allocation."""
        work_order_part = get_object_or_404(
            WorkOrderPart.objects.filter(
                work_order__in=scoped_work_orders(request.user)
            ),
            pk=pk,
            work_order_id=work_order_pk,
        )
        quantity = request.data.get('quantity')
        if quantity is not None:
            work_order_part.quantity = quantity
            work_order_part.save(update_fields=['quantity', 'updated_at'])
            work_order_part.check_and_allocate()
        return Response(WorkOrderPartSerializer(work_order_part).data)

    def delete(self, request, work_order_pk, pk):
        """Remove a part from the card."""
        work_order_part = get_object_or_404(
            WorkOrderPart.objects.filter(
                work_order__in=scoped_work_orders(request.user)
            ),
            pk=pk,
            work_order_id=work_order_pk,
        )
        work_order_part.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkOrderAllocateParts(APIView):
    """Check stock and allocate all parts for a Kanban card.

    Returns allocation results with warnings for any insufficient stock.
    """

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order.change'

    def post(self, request, work_order_pk):
        """Check and allocate stock for every card part."""
        work_order = get_object_or_404(
            scoped_work_orders(request.user), pk=work_order_pk
        )

        for cp in work_order.work_order_parts.all().select_related('part'):
            cp.check_and_allocate()

        return Response(_allocation_summary(work_order))


class KanbanColumnList(ListCreateAPI):
    """List all board columns or create a new one."""

    queryset = KanbanColumn.objects.all()
    serializer_class = KanbanColumnSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'
    # A board has a handful of columns; returning them all in one list keeps the
    # frontend simple and matches the unpaginated card list.
    pagination_class = None

    def perform_create(self, serializer):
        """Append new columns to the right of the board by default."""
        if serializer.validated_data.get('order') is None:
            last = KanbanColumn.objects.order_by('-order').first()
            serializer.validated_data['order'] = (last.order + 1) if last else 0

        serializer.save()


class KanbanColumnDetail(RetrieveUpdateDestroyAPI):
    """Retrieve, relabel, recolor or delete a single board column."""

    queryset = KanbanColumn.objects.all()
    serializer_class = KanbanColumnSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'

    def perform_destroy(self, instance):
        """Refuse to delete a seeded column or one that still holds cards.

        A column key is stored on every card in the column, so deleting a
        non-empty column would strand those cards with a status matching no
        column. The client must reassign the cards first. Seeded columns are
        protected outright so the board always has its original lanes.
        """
        if instance.is_default:
            raise ValidationError({'detail': 'Default columns cannot be deleted.'})

        active_cards = instance.card_count(active_only=True)

        if active_cards:
            raise ValidationError({
                'detail': (
                    f'This column still holds {active_cards} card(s). Move them '
                    'to another column before deleting it.'
                )
            })

        instance.delete()


class KanbanColumnReorder(APIView):
    """Persist a new left-to-right ordering of the board columns."""

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order.change'
    serializer_class = KanbanColumnSerializer

    def post(self, request):
        """Assign ``order`` from the index of each key in the supplied list.

        Expects ``{"order": ["backlog", "in-progress", ...]}``. The list must be
        a permutation of exactly the existing column keys -- a partial or unknown
        list is rejected rather than silently reordering a subset, which would
        leave the board in a state the client did not intend.
        """
        keys = request.data.get('order')

        if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
            raise ValidationError({'order': 'Expected a list of column keys.'})

        existing = set(KanbanColumn.objects.values_list('key', flat=True))

        if set(keys) != existing or len(keys) != len(existing):
            raise ValidationError({
                'order': (
                    'The supplied keys must be exactly the current set of '
                    'column keys, each listed once.'
                )
            })

        columns = {c.key: c for c in KanbanColumn.objects.all()}

        with transaction.atomic():
            for index, key in enumerate(keys):
                column = columns[key]
                if column.order != index:
                    column.order = index
                    column.save(update_fields=['order', 'updated_at'])

        ordered = KanbanColumn.objects.all()
        return Response(KanbanColumnSerializer(ordered, many=True).data)


# ── Scheduling command endpoints ─────────────────────────────────────────────
# Unflagged adapters over ``tasks.services.scheduling``, used by the board /
# calendar / timeline. Gated by the work_order ruleset per action (add / change /
# delete). The service enforces expected_version, validation and audit; these
# views translate its typed errors to HTTP responses.


def _command_error_response(exc):
    """Map a WorkOrderCommandError to an HTTP response with its stable code."""
    conflict = (
        CommandConflict,
        scheduling.StaleVersion,
        scheduling.IdempotencyConflict,
        scheduling.NotMutable,
        scheduling.ProtectedWorkOrder,
        scheduling.DependencyCycle,
    )
    code = getattr(exc, 'code', 'COMMAND_ERROR')
    http_status = (
        status.HTTP_409_CONFLICT
        if isinstance(exc, conflict)
        else status.HTTP_400_BAD_REQUEST
    )
    detail = (
        'A repair packet owns this Work lifecycle; its machine cannot be changed.'
        if isinstance(exc, CommandConflict)
        else str(exc)
    )
    return Response({'code': code, 'detail': detail}, status=http_status)


def _idempotency_key(request):
    """Require a stable caller intent so an uncertain retry cannot duplicate it."""
    key = request.data.get('idempotency_key')
    if not isinstance(key, str) or not key.strip() or len(key) > 128:
        raise ValidationError({
            'idempotency_key': 'A stable key of at most 128 characters is required.'
        })
    return key


def _parse_dt(value, field):
    """Parse an ISO datetime string (or None); reject anything unparsable."""
    if value is None:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValidationError({field: 'Expected an ISO 8601 datetime or null.'})
    return parsed


def _require_version_arg(request):
    version = request.data.get('expected_version')
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValidationError({
            'expected_version': 'An integer expected_version is required.'
        })
    return version


class _CommandView(APIView):
    """Common permission wiring for scheduling command endpoints."""

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]

    def _work_order_payload(self, work_order_id):
        work_order = get_object_or_404(
            scoped_work_orders(self.request.user), pk=work_order_id
        )
        return WorkOrderBoardSerializer(
            work_order, context={'request': self.request}
        ).data

    def _card_payload(self, card_id):
        card = get_object_or_404(
            KanbanCard.objects.filter(
                work_order__in=scoped_work_orders(self.request.user)
            ).select_related(
                'work_order', 'work_order__machine', 'work_order__assigned_to'
            ),
            pk=card_id,
        )
        return KanbanCardSerializer(card).data


class WorkOrderCreateCommand(_CommandView):
    """Create a work order through the command service."""

    role_required = 'work_order.add'

    def post(self, request):
        """Create a card, requiring a machine and recording a CREATED event."""
        data = dict(request.data)
        machine_id = data.get('machine') or data.get('machine_id')

        if not machine_id:
            raise ValidationError({'machine': 'A machine is required.'})
        get_object_or_404(scoped_machines(request.user), pk=machine_id)

        allowed = {
            key: data[key]
            for key in (
                'description',
                'corrective_origin_kind',
                'corrective_finding',
                'corrective_reading',
                'corrective_step',
                'recurrence_of',
                'recurrence_occurrence',
                'prior_version',
                'priority',
                'work_order_type',
                'assignee',
                'due_date',
                'assigned_to',
                'required_trades',
                'trade_requirements_confirmed',
                'override_reason',
            )
            if key in data
        }

        try:
            result = scheduling.create_work_order(
                actor=request.user,
                idempotency_key=_idempotency_key(request),
                title=data.get('title', ''),
                machine_id=machine_id,
                allow_assignment_override=True,
                **allowed,
            )
        except scheduling.WorkOrderCommandError as exc:
            return _command_error_response(exc)

        payload = self._work_order_payload(result.work_order_id)
        if data.get('recurrence_of') is not None:
            from dataclasses import asdict

            payload['process_receipt'] = asdict(result)
        return Response(payload, status=status.HTTP_201_CREATED)


class WorkOrderUpdateCommand(_CommandView):
    """Update planning metadata for a work order."""

    role_required = 'work_order.change'

    def post(self, request, pk):
        """Apply a versioned planning update."""
        work_order = get_object_or_404(scoped_work_orders(request.user), pk=pk)
        data = object_data(request.data)
        fields = dict(object_data(data.get('fields', {})))

        # Accept ``machine`` as an alias for ``machine_id``.
        if 'machine' in fields and 'machine_id' not in fields:
            fields['machine_id'] = fields.pop('machine')
        if 'machine_id' in fields:
            fields['machine_id'] = parent_id(fields, 'machine_id')
        if 'machine_id' in fields and fields['machine_id'] != work_order.machine_id:
            candidate = WorkOrder(
                customer_id=work_order.customer_id,
                machine=get_object_or_404(
                    scoped_machines(request.user), pk=fields['machine_id']
                ),
            )
            try:
                require_work_order_scope(request.user, candidate)
            except ScopeError as exc:
                raise Http404 from exc

        try:
            scheduling.update_work_order_plan(
                work_order_id=pk,
                actor=request.user,
                expected_version=_require_version_arg(request),
                idempotency_key=_idempotency_key(request),
                fields=fields,
            )
        except scheduling.WorkOrderCommandError as exc:
            return _command_error_response(exc)

        return Response(self._work_order_payload(pk))


class WorkOrderScheduleCommand(_CommandView):
    """Move a work order to a new scheduled window."""

    role_required = 'work_order.change'

    def post(self, request, pk):
        """Set the scheduled window (a move; duration handled by resize)."""
        get_object_or_404(scoped_work_orders(request.user), pk=pk)
        try:
            scheduling.schedule_work_order(
                work_order_id=pk,
                actor=request.user,
                expected_version=_require_version_arg(request),
                idempotency_key=_idempotency_key(request),
                scheduled_start=_parse_dt(
                    request.data.get('scheduled_start'), 'scheduled_start'
                ),
                scheduled_end=_parse_dt(
                    request.data.get('scheduled_end'), 'scheduled_end'
                ),
            )
        except scheduling.WorkOrderCommandError as exc:
            return _command_error_response(exc)

        return Response(self._work_order_payload(pk))


class WorkOrderResizeCommand(_CommandView):
    """Change a work order's duration."""

    role_required = 'work_order.change'

    def post(self, request, pk):
        """Set estimated_minutes and/or move the end."""
        get_object_or_404(scoped_work_orders(request.user), pk=pk)
        try:
            scheduling.resize_work_order(
                work_order_id=pk,
                actor=request.user,
                expected_version=_require_version_arg(request),
                idempotency_key=_idempotency_key(request),
                estimated_minutes=request.data.get('estimated_minutes'),
                scheduled_end=_parse_dt(
                    request.data.get('scheduled_end'), 'scheduled_end'
                ),
            )
        except scheduling.WorkOrderCommandError as exc:
            return _command_error_response(exc)

        return Response(self._work_order_payload(pk))


class WorkOrderDeleteCommand(_CommandView):
    """Governed hard delete of a work order."""

    role_required = 'work_order.delete'

    def post(self, request, pk):
        """Delete the card, leaving a durable deletion record."""
        get_object_or_404(scoped_work_orders(request.user), pk=pk)
        try:
            result = scheduling.delete_work_order(
                work_order_id=pk,
                actor=request.user,
                expected_version=_require_version_arg(request),
                idempotency_key=_idempotency_key(request),
                reason=request.data.get('reason', ''),
            )
        except scheduling.WorkOrderCommandError as exc:
            return _command_error_response(exc)

        return Response({
            'deleted': True,
            'work_order_id': result.work_order_id,
            'deletion_record_id': result.deletion_record_id,
            'reference': result.reference,
        })


class WorkOrderCreateChildCommand(_CommandView):
    """Create a child card under a work order."""

    role_required = 'work_order.add'

    def post(self, request, pk):
        """Create a subtask/procurement child inheriting machine + customer."""
        get_object_or_404(scoped_work_orders(request.user), pk=pk)
        try:
            result = scheduling.create_child(
                parent_id=pk,
                actor=request.user,
                idempotency_key=_idempotency_key(request),
                title=request.data.get('title', ''),
                card_kind=request.data.get('card_kind', 'subtask'),
            )
        except scheduling.WorkOrderCommandError as exc:
            return _command_error_response(exc)

        return Response(
            self._card_payload(result.metadata['card_id']),
            status=status.HTTP_201_CREATED,
        )


class WorkOrderGenerateProcurementCommand(_CommandView):
    """Raise a procurement child from a work order's parts shortfall."""

    role_required = 'work_order.add'

    def post(self, request, pk):
        """Create/refresh the procurement child, or 200 with none if no shortfall."""
        get_object_or_404(scoped_work_orders(request.user), pk=pk)
        try:
            child = scheduling.generate_procurement_child(
                parent_id=pk, actor=request.user
            )
        except scheduling.WorkOrderCommandError as exc:
            return _command_error_response(exc)

        if child is None:
            return Response({'generated': False, 'detail': 'No parts shortfall.'})

        return Response(
            {'generated': True, 'child': self._card_payload(child.pk)},
            status=status.HTTP_201_CREATED,
        )


class WorkOrderScheduleBatchCommand(_CommandView):
    """Apply many schedule moves atomically."""

    role_required = 'work_order.change'

    def post(self, request):
        """All operations succeed or none do."""
        operations = request.data.get('operations')

        if not isinstance(operations, list):
            raise ValidationError({'operations': 'Expected a list of operations.'})
        _require_work_order_ids(
            request.user, [operation.get('card_id') for operation in operations]
        )

        parsed = []
        for op in operations:
            parsed.append({
                'card_id': op.get('card_id'),
                'expected_version': op.get('expected_version'),
                'scheduled_start': _parse_dt(
                    op.get('scheduled_start'), 'scheduled_start'
                ),
                'scheduled_end': _parse_dt(op.get('scheduled_end'), 'scheduled_end'),
            })

        try:
            results = scheduling.apply_schedule_batch(
                actor=request.user,
                idempotency_key=_idempotency_key(request),
                operations=parsed,
            )
        except scheduling.WorkOrderCommandError as exc:
            return _command_error_response(exc)

        return Response({
            'applied': [
                {
                    'work_order_id': r.work_order_id,
                    'lifecycle_version': r.lifecycle_version,
                }
                for r in results
            ]
        })


def _require_work_order_ids(actor, identities):
    """Reject a mixed-scope batch before preview or any partial mutation."""
    if not all(
        isinstance(identity, int) and not isinstance(identity, bool)
        for identity in identities
    ):
        raise ValidationError({'work_order_ids': 'Expected integer work-order IDs.'})
    requested = set(identities)
    accessible = set(
        scoped_work_orders(actor).filter(pk__in=requested).values_list('pk', flat=True)
    )
    if requested != accessible:
        raise Http404


def _parse_plan_request(request):
    """Build a PlanRequest from the request body, validating types."""
    candidate_ids = request.data.get('candidate_ids')
    if not isinstance(candidate_ids, list) or not all(
        isinstance(i, int) for i in candidate_ids
    ):
        raise ValidationError({'candidate_ids': 'Expected a list of card ids.'})

    horizon = request.data.get('horizon_start')
    horizon_start = _parse_dt(horizon, 'horizon_start') or timezone.now()

    locked = request.data.get('locked_ids', [])
    if not isinstance(locked, list):
        raise ValidationError({'locked_ids': 'Expected a list of card ids.'})
    _require_work_order_ids(request.user, [*candidate_ids, *locked])

    return schedule_planner.PlanRequest(
        candidate_ids=candidate_ids,
        horizon_start=horizon_start,
        locked_ids=frozenset(locked),
        allow_move_existing=bool(request.data.get('allow_move_existing', True)),
        check_assignee=bool(request.data.get('check_assignee', False)),
    )


def _serialize_plan(result):
    """Serialize a PlanResult for the API."""
    return {
        'operations': [
            {
                'card_id': op.work_order_id,
                'old_start': op.old_start.isoformat() if op.old_start else None,
                'old_end': op.old_end.isoformat() if op.old_end else None,
                'new_start': op.new_start.isoformat(),
                'new_end': op.new_end.isoformat(),
            }
            for op in result.operations
        ],
        'warnings': result.warnings,
        'unscheduled': result.unscheduled,
    }


class WorkOrderSchedulePlan(APIView):
    """Compute a proposed schedule without saving (preview / dry-run).

    The deterministic planner decides the times; the caller only supplies which
    cards to consider and the constraints. Read-shaped, so gated on view.
    """

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'

    def post(self, request):
        """Return {operations, warnings, unscheduled} for the candidate cards."""
        result = schedule_planner.plan_schedule(_parse_plan_request(request))
        return Response(_serialize_plan(result))


class WorkOrderScheduleOptimize(APIView):
    """Compute and atomically apply a schedule (auto-schedule).

    Runs the planner, then applies its operations through the command service in
    one all-or-nothing batch, using each card's current ``lifecycle_version``.
    A concurrent change to any affected card fails the whole apply.
    """

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order.change'

    def post(self, request):
        """Plan, then apply atomically; returns the plan and applied versions."""
        plan = schedule_planner.plan_schedule(_parse_plan_request(request))

        if not plan.operations:
            return Response({'plan': _serialize_plan(plan), 'applied': []})

        versions = dict(
            WorkOrder.objects.filter(
                id__in=[op.work_order_id for op in plan.operations]
            ).values_list('id', 'lifecycle_version')
        )
        operations = [
            {
                'card_id': op.work_order_id,
                'expected_version': versions[op.work_order_id],
                'scheduled_start': op.new_start,
                'scheduled_end': op.new_end,
            }
            for op in plan.operations
        ]

        try:
            results = scheduling.apply_schedule_batch(
                actor=request.user,
                idempotency_key=_idempotency_key(request),
                operations=operations,
            )
        except scheduling.WorkOrderCommandError as exc:
            return _command_error_response(exc)

        return Response({
            'plan': _serialize_plan(plan),
            'applied': [
                {
                    'work_order_id': r.work_order_id,
                    'lifecycle_version': r.lifecycle_version,
                }
                for r in results
            ],
        })


class WorkOrderScheduleWindow(APIView):
    """Windowed read for the calendar and timeline.

    Returns the cards overlapping ``[min_date, max_date]`` (same overlap
    semantics as the board list filter), the dependencies among them, and any
    conflict warnings. ``warnings`` is empty until S6 conflict detection lands;
    the shape is stable so the client can rely on it now.
    """

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'

    def get(self, request):
        """Return {cards, dependencies, warnings} for the requested window."""
        queryset = (
            scoped_work_orders(request.user)
            .filter(is_active=True)
            .select_related('machine', 'assigned_to')
            .prefetch_related('work_order_parts__part')
        )

        work_order_filter = WorkOrderBoardFilter(
            request.query_params, queryset=queryset
        )
        work_orders = list(
            work_order_filter.qs.order_by('scheduled_start', 'due_date', '-created_at')
        )
        work_order_ids = [work_order.pk for work_order in work_orders]

        # Dependencies where both endpoints are inside the window, so the client
        # never has to resolve an edge to a card it did not receive.
        dependencies = WorkOrderDependency.objects.filter(
            predecessor_id__in=work_order_ids, successor_id__in=work_order_ids
        )

        return Response({
            'cards': WorkOrderBoardSerializer(
                work_orders, many=True, context={'request': request}
            ).data,
            'dependencies': WorkOrderDependencySerializer(dependencies, many=True).data,
            'warnings': detect_conflicts(work_orders),
        })


class WorkOrderDependencyCommand(_CommandView):
    """Create a scheduling dependency between two work orders."""

    role_required = 'work_order.change'

    def post(self, request):
        """Create ``predecessor -> successor``, rejecting self-loops and cycles."""
        _require_work_order_ids(
            request.user,
            [request.data.get('predecessor'), request.data.get('successor')],
        )
        try:
            dependency = scheduling.create_dependency(
                predecessor_id=request.data.get('predecessor'),
                successor_id=request.data.get('successor'),
                actor=request.user,
                dependency_type=request.data.get('dependency_type', 'FS'),
                lag_minutes=request.data.get('lag_minutes', 0),
            )
        except scheduling.WorkOrderCommandError as exc:
            return _command_error_response(exc)

        return Response(
            WorkOrderDependencySerializer(dependency).data,
            status=status.HTTP_201_CREATED,
        )


class WorkOrderDependencyDetailCommand(_CommandView):
    """Delete a scheduling dependency by id."""

    role_required = 'work_order.change'

    def delete(self, request, pk):
        """Remove the dependency; 404 if it does not exist."""
        accessible = scoped_work_orders(request.user)
        get_object_or_404(
            WorkOrderDependency.objects.filter(
                predecessor__in=accessible, successor__in=accessible
            ),
            pk=pk,
        )
        removed = scheduling.delete_dependency(dependency_id=pk, actor=request.user)

        if not removed:
            return Response(status=status.HTTP_404_NOT_FOUND)

        return Response(status=status.HTTP_204_NO_CONTENT)


kanban_api_urls = [
    path(
        'columns/',
        include([
            path('', KanbanColumnList.as_view(), name='kanban-column-list'),
            path(
                'reorder/', KanbanColumnReorder.as_view(), name='kanban-column-reorder'
            ),
            path(
                '<int:pk>/', KanbanColumnDetail.as_view(), name='kanban-column-detail'
            ),
        ]),
    ),
    path(
        'cards/',
        include([
            path('', KanbanCardList.as_view(), name='kanban-board-card-list'),
            path(
                '<int:pk>/', KanbanCardDetail.as_view(), name='kanban-board-card-detail'
            ),
        ]),
    ),
    # Everything below concerns the *job*, not a card on the board. The route
    # said 'cards/' from when a work order and its card were one row; the URL
    # names are unchanged so existing reverse() callers keep resolving.
    path(
        'work-orders/',
        include([
            path('', WorkOrderBoardList.as_view(), name='kanban-card-list'),
            path('list/', WorkOrderTableList.as_view(), name='kanban-work-order-table'),
            path(
                'persona/', WorkOrderPersona.as_view(), name='kanban-work-order-persona'
            ),
            path(
                '<int:pk>/calibration-traceability/<int:reading>/',
                CalibrationTraceabilityCommand.as_view(),
                name='work-calibration-traceability',
            ),
            path(
                '<int:pk>/investigation-requests/',
                WorkInvestigationRequests.as_view(),
                name='work-investigation-requests',
            ),
            path(
                '<int:pk>/investigation-requests/<int:request_id>/',
                WorkInvestigationRequests.as_view(),
                name='work-investigation-request',
            ),
            path(
                '<int:pk>/operational-authority/',
                WorkOperationalAuthority.as_view(),
                name='work-operational-authority',
            ),
            path(
                '<int:pk>/overview/',
                WorkOrderOverviewDetail.as_view(),
                name='kanban-card-overview',
            ),
            path(
                '<int:pk>/', WorkOrderBoardDetail.as_view(), name='kanban-card-detail'
            ),
            path(
                '<int:pk>/restore/',
                WorkOrderRestore.as_view(),
                name='kanban-card-restore',
            ),
            path(
                '<int:work_order_pk>/parts/',
                WorkOrderPartList.as_view(),
                name='kanban-card-part-list',
            ),
            path(
                '<int:work_order_pk>/parts/<int:pk>/',
                WorkOrderPartDetail.as_view(),
                name='kanban-card-part-detail',
            ),
            path(
                '<int:work_order_pk>/allocate-parts/',
                WorkOrderAllocateParts.as_view(),
                name='kanban-card-allocate',
            ),
            path(
                'commands/create/',
                WorkOrderCreateCommand.as_view(),
                name='kanban-command-create',
            ),
            path(
                '<int:pk>/commands/update/',
                WorkOrderUpdateCommand.as_view(),
                name='kanban-command-update',
            ),
            path(
                '<int:pk>/commands/schedule/',
                WorkOrderScheduleCommand.as_view(),
                name='kanban-command-schedule',
            ),
            path(
                '<int:pk>/commands/resize/',
                WorkOrderResizeCommand.as_view(),
                name='kanban-command-resize',
            ),
            path(
                '<int:pk>/commands/delete/',
                WorkOrderDeleteCommand.as_view(),
                name='kanban-command-delete',
            ),
            path(
                '<int:pk>/commands/create-child/',
                WorkOrderCreateChildCommand.as_view(),
                name='kanban-command-create-child',
            ),
            path(
                '<int:pk>/commands/generate-procurement/',
                WorkOrderGenerateProcurementCommand.as_view(),
                name='kanban-command-generate-procurement',
            ),
        ]),
    ),
    path(
        'schedule/',
        include([
            path('', WorkOrderScheduleWindow.as_view(), name='kanban-schedule-window'),
            path(
                'apply/',
                WorkOrderScheduleBatchCommand.as_view(),
                name='kanban-schedule-apply',
            ),
            path('plan/', WorkOrderSchedulePlan.as_view(), name='kanban-schedule-plan'),
            path(
                'optimize/',
                WorkOrderScheduleOptimize.as_view(),
                name='kanban-schedule-optimize',
            ),
        ]),
    ),
    path(
        'dependencies/',
        include([
            path(
                '',
                WorkOrderDependencyCommand.as_view(),
                name='kanban-dependency-create',
            ),
            path(
                '<int:pk>/',
                WorkOrderDependencyDetailCommand.as_view(),
                name='kanban-dependency-detail',
            ),
        ]),
    ),
]


tasks_api_urls = [
    path(
        'procedures/',
        include([
            path('', ProcedureList.as_view(), name='procedure-list'),
            path('<int:pk>/', ProcedureDetail.as_view(), name='procedure-detail'),
            path(
                '<int:pk>/revisions/',
                ProcedureRevisionList.as_view(),
                name='procedure-revision-list',
            ),
        ]),
    ),
    path(
        'procedure-revisions/',
        include([
            path(
                '<int:pk>/',
                ProcedureRevisionDetail.as_view(),
                name='procedure-revision-detail',
            ),
            path(
                '<int:pk>/steps/',
                ProcedureStepList.as_view(),
                name='procedure-step-list',
            ),
            path(
                '<int:pk>/steps/<uuid:step>/',
                ProcedureStepDetail.as_view(),
                name='procedure-step-detail',
            ),
            path(
                '<int:pk>/reorder-steps/',
                ProcedureStepReorder.as_view(),
                name='procedure-step-reorder',
            ),
            path(
                '<int:pk>/resources/',
                ProcedureResourceList.as_view(),
                name='procedure-resource-list',
            ),
            path(
                '<int:pk>/resources/<uuid:line>/',
                ProcedureResourceDetail.as_view(),
                name='procedure-resource-detail',
            ),
            path(
                '<int:pk>/blockers/',
                ProcedureBlockers.as_view(),
                name='procedure-blockers',
            ),
            path(
                '<int:pk>/request-review/',
                ProcedureRequestReview.as_view(),
                name='procedure-request-review',
            ),
            path(
                '<int:pk>/publish/',
                ProcedurePublish.as_view(),
                name='procedure-publish',
            ),
            path(
                '<int:pk>/archive/',
                ProcedureArchive.as_view(),
                name='procedure-archive',
            ),
        ]),
    ),
    path(
        'work-orders/',
        include([
            path('', WorkOrderList.as_view(), name='work-order-list'),
            path('<int:pk>/time/', WorkTime.as_view(), name='work-order-time'),
            path('<int:pk>/', WorkOrderDetail.as_view(), name='work-order-detail'),
            path(
                '<int:pk>/repair-scope-draft/',
                RepairScopeDraft.as_view(),
                name='work-order-repair-scope-draft',
            ),
            path(
                '<int:pk>/readiness/',
                WorkOrderReadiness.as_view(),
                name='work-order-readiness',
            ),
            path(
                '<int:pk>/transition/',
                WorkOrderTransition.as_view(),
                name='work-order-transition',
            ),
            path(
                '<int:pk>/accept_handover/',
                WorkOrderAcceptHandover.as_view(),
                name='work-order-accept-handover',
            ),
            path(
                '<int:pk>/assign/', WorkOrderAssign.as_view(), name='work-order-assign'
            ),
            path('<int:pk>/hold/', WorkOrderHold.as_view(), name='work-order-hold'),
            path(
                '<int:pk>/resume/', WorkOrderResume.as_view(), name='work-order-resume'
            ),
            path(
                '<int:pk>/cancel/', WorkOrderCancel.as_view(), name='work-order-cancel'
            ),
            path(
                '<int:pk>/complete/',
                WorkOrderComplete.as_view(),
                name='work-order-complete',
            ),
            path(
                '<int:pk>/events/',
                WorkOrderEventList.as_view(),
                name='work-order-events',
            ),
            path(
                '<int:pk>/procedures/',
                ProcedureApplicationList.as_view(),
                name='work-order-procedure-list',
            ),
            path(
                '<int:pk>/exercise-verification/',
                ExerciseVerification.as_view(),
                name='work-order-exercise-verification',
            ),
            path(
                '<int:pk>/exercise-verification/evidence/',
                ExerciseEvidence.as_view(),
                name='work-order-exercise-evidence',
            ),
            path(
                '<int:pk>/procedures/apply/',
                ProcedureApply.as_view(),
                name='work-order-procedure-apply',
            ),
            path(
                '<int:pk>/steps/',
                StepExecutionList.as_view(),
                name='work-order-step-list',
            ),
            path(
                '<int:pk>/steps/<uuid:step>/',
                StepExecutionDetail.as_view(),
                name='work-order-step-detail',
            ),
            path(
                '<int:pk>/steps/<uuid:step>/complete/',
                StepExecutionComplete.as_view(),
                name='work-order-step-complete',
            ),
            path(
                '<int:pk>/steps/<uuid:step>/not-applicable/',
                StepExecutionNotApplicable.as_view(),
                name='work-order-step-not-applicable',
            ),
            path(
                '<int:pk>/steps/<uuid:step>/reopen/',
                StepExecutionReopen.as_view(),
                name='work-order-step-reopen',
            ),
            path(
                '<int:pk>/deviations/',
                WorkOrderDeviationList.as_view(),
                name='work-order-deviation-list',
            ),
            path(
                '<int:pk>/materials/',
                WorkOrderMaterials.as_view(),
                name='work-order-materials',
            ),
            path(
                '<int:pk>/procurement/source-selection/',
                ProcurementSourceSelection.as_view(),
                name='work-order-procurement-source-selection',
            ),
            path(
                '<int:pk>/procurement/acknowledgment/',
                ProcurementAcknowledgment.as_view(),
                name='work-order-procurement-acknowledgment',
            ),
            path(
                '<int:pk>/job-kit/', JobKitDetail.as_view(), name='work-order-job-kit'
            ),
            path(
                '<int:pk>/job-kit/build/',
                JobKitBuild.as_view(),
                name='work-order-job-kit-build',
            ),
            path(
                '<int:pk>/job-kit/lines/',
                JobKitLineList.as_view(),
                name='work-order-job-kit-lines',
            ),
            path(
                '<int:pk>/job-kit/lines/<int:line>/',
                JobKitLineDetail.as_view(),
                name='work-order-job-kit-line-detail',
            ),
            path(
                '<int:pk>/job-kit/refresh/',
                JobKitRefresh.as_view(),
                name='work-order-job-kit-refresh',
            ),
            path(
                '<int:pk>/job-kit/shortages/',
                JobKitShortageList.as_view(),
                name='work-order-job-kit-shortages',
            ),
            path(
                '<int:pk>/job-kit/shortages/<int:shortage>/link-po/',
                JobKitShortageLinkPO.as_view(),
                name='work-order-job-kit-shortage-link-po',
            ),
            path(
                '<int:pk>/job-kit/events/',
                JobKitEventList.as_view(),
                name='work-order-job-kit-events',
            ),
            path(
                '<int:pk>/job-kit/reserve/',
                JobKitReserve.as_view(),
                name='work-order-job-kit-reserve',
            ),
            path(
                '<int:pk>/job-kit/allocations/',
                JobKitAllocationList.as_view(),
                name='work-order-job-kit-allocations',
            ),
            path(
                '<int:pk>/job-kit/allocations/<int:allocation>/release/',
                JobKitAllocationRelease.as_view(),
                name='work-order-job-kit-allocation-release',
            ),
            path(
                '<int:pk>/job-kit/allocations/<int:allocation>/stage/',
                JobKitAllocationStage.as_view(),
                name='work-order-job-kit-allocation-stage',
            ),
            path(
                '<int:pk>/job-kit/allocations/<int:allocation>/issue/',
                JobKitAllocationIssue.as_view(),
                name='work-order-job-kit-allocation-issue',
            ),
            path(
                '<int:pk>/job-kit/allocations/<int:allocation>/consume/',
                JobKitAllocationConsume.as_view(),
                name='work-order-job-kit-allocation-consume',
            ),
            path(
                '<int:pk>/job-kit/allocations/<int:allocation>/return/',
                JobKitAllocationReturn.as_view(),
                name='work-order-job-kit-allocation-return',
            ),
            path(
                '<int:pk>/job-kit/lines/<int:line>/substitutions/',
                JobKitSubstitutionPropose.as_view(),
                name='work-order-job-kit-line-substitution',
            ),
            path(
                '<int:pk>/job-kit/substitutions/<int:substitution>/decide/',
                JobKitSubstitutionDecide.as_view(),
                name='work-order-job-kit-substitution-decide',
            ),
            path('<int:pk>/capture-media/', WorkCaptureMediaUpload.as_view()),
            path('<int:pk>/closeout/service-report/', WorkServiceReport.as_view()),
            path('<int:pk>/closeout/report.pdf', WorkCloseoutPDF.as_view()),
            path(
                '<int:pk>/closeout/reports/<int:report_id>/pdf/',
                WorkAcceptedReportPDF.as_view(),
            ),
            path('<int:pk>/capture-media/<int:media_id>/', WorkCaptureMedia.as_view()),
            path(
                '<int:pk>/closeout/reports/<int:report_id>/',
                WorkAcceptedReport.as_view(),
            ),
            path(
                '<int:pk>/closeout/captures/',
                CloseoutCaptureList.as_view(),
                name='work-order-closeout-captures',
            ),
            path(
                '<int:pk>/closeout/captures/<int:cap>/',
                CloseoutCaptureDetail.as_view(),
                name='work-order-closeout-capture-detail',
            ),
            path(
                '<int:pk>/closeout/captures/<int:cap>/extract/',
                CloseoutCaptureExtract.as_view(),
                name='work-order-closeout-capture-extract',
            ),
            path(
                '<int:pk>/closeout/captures/<int:cap>/proposal/',
                CloseoutCaptureProposal.as_view(),
                name='work-order-closeout-capture-proposal',
            ),
            path(
                '<int:pk>/closeout/captures/<int:cap>/decisions/',
                CloseoutDecisionBatch.as_view(),
                name='work-order-closeout-capture-decisions',
            ),
            path(
                '<int:pk>/closeout/part-usage/',
                CloseoutPartUsageList.as_view(),
                name='work-order-closeout-part-usage',
            ),
            path(
                '<int:pk>/closeout/part-usage/<int:row>/resolve/',
                CloseoutPartUsageResolve.as_view(),
                name='work-order-closeout-part-usage-resolve',
            ),
            path(
                '<int:pk>/closeout/part-usage/refresh/',
                CloseoutPartUsageRefresh.as_view(),
                name='work-order-closeout-part-usage-refresh',
            ),
            path(
                '<int:pk>/closeout/readings/',
                CloseoutReadingList.as_view(),
                name='work-order-closeout-readings',
            ),
            path(
                '<int:pk>/closeout/readings/<int:reading>/disposition/',
                CloseoutReadingDisposition.as_view(),
                name='work-order-closeout-reading-disposition',
            ),
            path(
                '<int:pk>/closeout/effects/',
                CloseoutEffectList.as_view(),
                name='work-order-closeout-effects',
            ),
            path(
                '<int:pk>/closeout/effects/<int:effect>/retry/',
                CloseoutEffectRetry.as_view(),
                name='work-order-closeout-effect-retry',
            ),
            path(
                '<int:pk>/closeout/verify/',
                CloseoutVerify.as_view(),
                name='work-order-closeout-verify',
            ),
            path(
                '<int:pk>/closeout/amendments/',
                CloseoutAmendmentList.as_view(),
                name='work-order-closeout-amendments',
            ),
            path(
                '<int:pk>/closeout/amendments/<int:amendment>/decide/',
                CloseoutAmendmentDecide.as_view(),
                name='work-order-closeout-amendment-decide',
            ),
        ]),
    ),
]
