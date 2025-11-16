"""REST API endpoints for the tasks application."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.urls import include, path

from django_filters.rest_framework import FilterSet, filters
from rest_framework.response import Response
from rest_framework.views import APIView

import InvenTree.helpers
import InvenTree.permissions
from InvenTree.filters import SEARCH_ORDER_FILTER
from InvenTree.mixins import ListCreateAPI, RetrieveUpdateDestroyAPI

from .models import KanbanCard
from .serializers import KanbanCardSerializer


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


kanban_api_urls = [
    path(
        'cards/',
        include([
            path('', KanbanCardList.as_view(), name='kanban-card-list'),
            path('<int:pk>/', KanbanCardDetail.as_view(), name='kanban-card-detail'),
            path('<int:pk>/restore/', KanbanCardRestore.as_view(), name='kanban-card-restore'),
        ]),
    ),
]
