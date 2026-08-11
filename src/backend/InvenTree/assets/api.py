"""REST API endpoints for the assets (equipment machines) application."""

from __future__ import annotations

from django.urls import include, path

from django_filters.rest_framework import FilterSet, filters
from rest_framework.response import Response
from rest_framework.views import APIView

import InvenTree.permissions
from InvenTree.filters import SEARCH_ORDER_FILTER
from InvenTree.mixins import ListCreateAPI, RetrieveUpdateDestroyAPI

from .models import AssetMachine, AssetMaintenanceRecord, Client, MachinePart
from .serializers import (
    AssetMachineSerializer,
    AssetMaintenanceRecordSerializer,
    ClientSerializer,
    MachinePartSerializer,
)

# ---- Filters ----------------------------------------------------------------


class AssetMachineFilter(FilterSet):
    """Filter set for AssetMachine."""

    active = filters.BooleanFilter()

    class Meta:
        """Filter configuration for AssetMachine."""

        model = AssetMachine
        fields = ('active', 'location', 'client', 'manufacturer')


class MachinePartFilter(FilterSet):
    """Filter set for MachinePart."""

    class Meta:
        """Filter configuration for MachinePart."""

        model = MachinePart
        fields = ('machine', 'part')


class AssetMaintenanceRecordFilter(FilterSet):
    """Filter set for AssetMaintenanceRecord."""

    class Meta:
        """Filter configuration for AssetMaintenanceRecord."""

        model = AssetMaintenanceRecord
        fields = ('machine', 'work_order')


# ---- Views -------------------------------------------------------------------


class ClientList(ListCreateAPI):
    """List and create software clients.

    A Client is the tenant an internal asset belongs to, and is what makes such
    an asset scope-resolvable. It is deliberately separate from a sales customer.
    """

    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'admin'
    filter_backends = SEARCH_ORDER_FILTER
    search_fields = ['name', 'code']
    ordering_fields = ['name', 'code', 'created_at']
    ordering = 'name'


class ClientDetail(RetrieveUpdateDestroyAPI):
    """Retrieve, update, or delete a client."""

    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'admin'


class AssetMachineList(ListCreateAPI):
    """List and create asset machines."""

    queryset = AssetMachine.objects.select_related('client').all()
    serializer_class = AssetMachineSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'
    filter_backends = SEARCH_ORDER_FILTER
    filterset_class = AssetMachineFilter
    search_fields = [
        'name',
        'description',
        'location',
        'manufacturer',
        'model',
        'serial',
    ]
    ordering_fields = ['name', 'location', 'manufacturer', 'created_at', 'updated_at']
    ordering = 'name'


class AssetMachineDetail(RetrieveUpdateDestroyAPI):
    """Retrieve, update, or delete an asset machine."""

    queryset = AssetMachine.objects.all()
    serializer_class = AssetMachineSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'


class AssetMachineFaultHistory(APIView):
    """Deterministic fault-history rollup for one machine (C4).

    Unlike the plain detail endpoints, this projection aggregates closeout
    text, so it uses the ai_read discipline: machine scope is re-derived per
    request and an out-of-scope machine is indistinguishable from a missing
    one.
    """

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'

    def get(self, request, pk):
        """Return the rollup, 404ing out-of-scope machines."""
        from django.http import Http404

        from tasks.scope import ScopeError, require_machine_scope

        from assets.ai_read import machine_fault_history

        machine = AssetMachine.objects.filter(pk=pk).first()
        if machine is None:
            raise Http404
        try:
            require_machine_scope(request.user, machine)
        except ScopeError as exc:
            raise Http404 from exc
        return Response(machine_fault_history(request.user, machine, fenced=False))


class MachinePartList(ListCreateAPI):
    """List and create machine-part relationships."""

    queryset = MachinePart.objects.select_related('part').all()
    serializer_class = MachinePartSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'
    filter_backends = SEARCH_ORDER_FILTER
    filterset_class = MachinePartFilter
    search_fields = ['part__name', 'notes']
    ordering_fields = ['part__name', 'quantity']
    ordering = 'part__name'


class MachinePartDetail(RetrieveUpdateDestroyAPI):
    """Retrieve, update, or delete a machine-part relationship."""

    queryset = MachinePart.objects.all()
    serializer_class = MachinePartSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'


class AssetMaintenanceRecordList(ListCreateAPI):
    """List and create maintenance records.

    The work order and its structured closeout are joined so the blade renders
    reference, type, lifecycle, completion, downtime and verification without a
    query per row.
    """

    queryset = AssetMaintenanceRecord.objects.select_related(
        'work_order', 'work_order__structured_closeout'
    ).all()
    serializer_class = AssetMaintenanceRecordSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'
    filter_backends = SEARCH_ORDER_FILTER
    filterset_class = AssetMaintenanceRecordFilter
    search_fields = ['summary', 'details', 'performed_by']
    ordering_fields = ['date', 'created_at']
    ordering = '-date'


class AssetMaintenanceRecordDetail(RetrieveUpdateDestroyAPI):
    """Retrieve, update, or delete a maintenance record."""

    queryset = AssetMaintenanceRecord.objects.all()
    serializer_class = AssetMaintenanceRecordSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'


# ---- URL patterns -----------------------------------------------------------


assets_api_urls = [
    path(
        'clients/',
        include([
            path('', ClientList.as_view(), name='asset-client-list'),
            path('<int:pk>/', ClientDetail.as_view(), name='asset-client-detail'),
        ]),
    ),
    path(
        'machines/',
        include([
            path('', AssetMachineList.as_view(), name='asset-machine-list'),
            path(
                '<int:pk>/', AssetMachineDetail.as_view(), name='asset-machine-detail'
            ),
            path(
                '<int:pk>/fault-history/',
                AssetMachineFaultHistory.as_view(),
                name='asset-machine-fault-history',
            ),
        ]),
    ),
    path(
        'parts/',
        include([
            path('', MachinePartList.as_view(), name='machine-part-list'),
            path('<int:pk>/', MachinePartDetail.as_view(), name='machine-part-detail'),
        ]),
    ),
    path(
        'maintenance/',
        include([
            path(
                '', AssetMaintenanceRecordList.as_view(), name='maintenance-record-list'
            ),
            path(
                '<int:pk>/',
                AssetMaintenanceRecordDetail.as_view(),
                name='maintenance-record-detail',
            ),
        ]),
    ),
]
