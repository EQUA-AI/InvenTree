"""REST API endpoints for the tasks application."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.urls import include, path

from django_filters.rest_framework import FilterSet, filters
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

import InvenTree.helpers
import InvenTree.permissions
from InvenTree.filters import SEARCH_ORDER_FILTER
from InvenTree.mixins import ListCreateAPI, RetrieveUpdateDestroyAPI

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
from .models import KanbanCard, KanbanCardPart
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
from .serializers import KanbanCardPartSerializer, KanbanCardSerializer
from .workorder_api import (
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


class KanbanCardFilter(FilterSet):
    """Filter set for Kanban cards."""

    tags = filters.CharFilter(method='filter_tags')

    class Meta:
        """Filter metadata."""

        model = KanbanCard
        fields = (
            'status',
            'priority',
            'assignee',
            'job_number',
            'service_quote',
            'company',
        )

    def filter_tags(self, queryset, name, value):
        """Filter cards by a comma separated list of tags."""
        if not value:
            return queryset

        tags = [tag.strip() for tag in value.split(',') if tag.strip()]

        for tag in tags:
            queryset = queryset.filter(tags__contains=[tag])

        return queryset


class KanbanCardList(ListCreateAPI):
    """List and create Kanban cards."""

    queryset = KanbanCard.objects.all()
    serializer_class = KanbanCardSerializer
    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]
    filter_backends = SEARCH_ORDER_FILTER
    filterset_class = KanbanCardFilter
    search_fields = [
        'title',
        'description',
        'assignee',
        'job_number',
        'service_quote',
        'company',
    ]
    ordering_fields = ['created_at', 'updated_at', 'priority', 'due_date']
    ordering = '-created_at'
    pagination_class = None

    def get_queryset(self):
        """Filter the card collection by activity flag."""
        queryset = super().get_queryset()

        include_inactive = InvenTree.helpers.str2bool(
            self.request.query_params.get('include_inactive', False)
        )

        if not include_inactive:
            queryset = queryset.filter(is_active=True)

        return queryset.order_by('-created_at')


class KanbanCardDetail(RetrieveUpdateDestroyAPI):
    """Retrieve, update, or archive a Kanban card."""

    queryset = KanbanCard.objects.all()
    serializer_class = KanbanCardSerializer
    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def perform_destroy(self, instance):
        """Archive (soft-delete) rather than remove the card."""
        if instance.is_active:
            instance.is_active = False
            instance.save(update_fields=['is_active', 'updated_at'])


class KanbanCardRestore(APIView):
    """Restore a previously archived Kanban card."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]
    serializer_class = KanbanCardSerializer

    def post(self, request, pk):
        """Restore an archived card."""
        card = get_object_or_404(KanbanCard, pk=pk)

        if not card.is_active:
            card.is_active = True
            card.save(update_fields=['is_active', 'updated_at'])

        serializer = self.serializer_class(card, context={'request': request})
        return Response(serializer.data)


class KanbanCardPartList(APIView):
    """List and add parts for a Kanban card."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def get(self, request, card_pk):
        """List all parts for a card."""
        card = get_object_or_404(KanbanCard, pk=card_pk)
        parts = card.card_parts.all().select_related('part')
        serializer = KanbanCardPartSerializer(parts, many=True)
        return Response(serializer.data)

    def post(self, request, card_pk):
        """Add a part to a card and check stock availability."""
        card = get_object_or_404(KanbanCard, pk=card_pk)
        serializer = KanbanCardPartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        card_part = KanbanCardPart.objects.create(
            card=card,
            part=serializer.validated_data['part'],
            quantity=serializer.validated_data.get('quantity', 1),
        )
        card_part.check_and_allocate()

        return Response(
            KanbanCardPartSerializer(card_part).data, status=status.HTTP_201_CREATED
        )


class KanbanCardPartDetail(APIView):
    """Retrieve, update, or remove a part from a Kanban card."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def get(self, request, card_pk, pk):
        """Return one card part."""
        card_part = get_object_or_404(KanbanCardPart, pk=pk, card_id=card_pk)
        return Response(KanbanCardPartSerializer(card_part).data)

    def patch(self, request, card_pk, pk):
        """Update the required quantity and re-check allocation."""
        card_part = get_object_or_404(KanbanCardPart, pk=pk, card_id=card_pk)
        quantity = request.data.get('quantity')
        if quantity is not None:
            card_part.quantity = quantity
            card_part.save(update_fields=['quantity', 'updated_at'])
            card_part.check_and_allocate()
        return Response(KanbanCardPartSerializer(card_part).data)

    def delete(self, request, card_pk, pk):
        """Remove a part from the card."""
        card_part = get_object_or_404(KanbanCardPart, pk=pk, card_id=card_pk)
        card_part.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class KanbanCardAllocateParts(APIView):
    """Check stock and allocate all parts for a Kanban card.

    Returns allocation results with warnings for any insufficient stock.
    """

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, card_pk):
        """Check and allocate stock for every card part."""
        card = get_object_or_404(KanbanCard, pk=card_pk)
        card_parts = card.card_parts.all().select_related('part')

        results = []
        warnings = []

        for cp in card_parts:
            cp.check_and_allocate()
            result = KanbanCardPartSerializer(cp).data
            results.append(result)

            if cp.allocation_status == 'insufficient':
                warnings.append(
                    f"Part '{cp.part.name}' (ID {cp.part.pk}): "
                    f'need {cp.quantity}, only {cp.allocated_quantity} available'
                )
            elif cp.allocation_status == 'partial':
                warnings.append(
                    f"Part '{cp.part.name}' (ID {cp.part.pk}): "
                    f'partial allocation - {cp.allocated_quantity} of {cp.quantity}'
                )

        return Response({
            'parts': results,
            'warnings': warnings,
            'all_allocated': len(warnings) == 0,
        })


kanban_api_urls = [
    path(
        'cards/',
        include([
            path('', KanbanCardList.as_view(), name='kanban-card-list'),
            path('<int:pk>/', KanbanCardDetail.as_view(), name='kanban-card-detail'),
            path(
                '<int:pk>/restore/',
                KanbanCardRestore.as_view(),
                name='kanban-card-restore',
            ),
            path(
                '<int:card_pk>/parts/',
                KanbanCardPartList.as_view(),
                name='kanban-card-part-list',
            ),
            path(
                '<int:card_pk>/parts/<int:pk>/',
                KanbanCardPartDetail.as_view(),
                name='kanban-card-part-detail',
            ),
            path(
                '<int:card_pk>/allocate-parts/',
                KanbanCardAllocateParts.as_view(),
                name='kanban-card-allocate',
            ),
        ]),
    )
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
            path('<int:pk>/', WorkOrderDetail.as_view(), name='work-order-detail'),
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
