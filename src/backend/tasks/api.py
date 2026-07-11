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

from .models import KanbanCard, KanbanCardPart
from .serializers import KanbanCardPartSerializer, KanbanCardSerializer


class KanbanCardFilter(FilterSet):
    """Filter set for Kanban cards."""

    tags = filters.CharFilter(method='filter_tags')

    class Meta:
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
    search_fields = ['title', 'description', 'assignee', 'job_number', 'service_quote', 'company']
    ordering_fields = ['created_at', 'updated_at', 'priority', 'due_date']
    ordering = '-created_at'
    pagination_class = None

    def get_queryset(self):
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
        if instance.is_active:
            instance.is_active = False
            instance.save(update_fields=['is_active', 'updated_at'])


class KanbanCardRestore(APIView):
    """Restore a previously archived Kanban card."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]
    serializer_class = KanbanCardSerializer

    def post(self, request, pk):
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
            KanbanCardPartSerializer(card_part).data,
            status=status.HTTP_201_CREATED,
        )


class KanbanCardPartDetail(APIView):
    """Retrieve, update, or remove a part from a Kanban card."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def get(self, request, card_pk, pk):
        card_part = get_object_or_404(KanbanCardPart, pk=pk, card_id=card_pk)
        return Response(KanbanCardPartSerializer(card_part).data)

    def patch(self, request, card_pk, pk):
        card_part = get_object_or_404(KanbanCardPart, pk=pk, card_id=card_pk)
        quantity = request.data.get('quantity')
        if quantity is not None:
            card_part.quantity = quantity
            card_part.save(update_fields=['quantity', 'updated_at'])
            card_part.check_and_allocate()
        return Response(KanbanCardPartSerializer(card_part).data)

    def delete(self, request, card_pk, pk):
        card_part = get_object_or_404(KanbanCardPart, pk=pk, card_id=card_pk)
        card_part.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class KanbanCardAllocateParts(APIView):
    """Check stock and allocate all parts for a Kanban card.

    Returns allocation results with warnings for any insufficient stock.
    """

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, card_pk):
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
                    f"need {cp.quantity}, only {cp.allocated_quantity} available"
                )
            elif cp.allocation_status == 'partial':
                warnings.append(
                    f"Part '{cp.part.name}' (ID {cp.part.pk}): "
                    f"partial allocation - {cp.allocated_quantity} of {cp.quantity}"
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
            path('<int:pk>/restore/', KanbanCardRestore.as_view(), name='kanban-card-restore'),
            path('<int:card_pk>/parts/', KanbanCardPartList.as_view(), name='kanban-card-part-list'),
            path('<int:card_pk>/parts/<int:pk>/', KanbanCardPartDetail.as_view(), name='kanban-card-part-detail'),
            path('<int:card_pk>/allocate-parts/', KanbanCardAllocateParts.as_view(), name='kanban-card-allocate'),
        ]),
    ),
]
