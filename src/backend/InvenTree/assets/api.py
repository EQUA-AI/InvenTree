"""REST API endpoints for the assets (equipment machines) application."""

from __future__ import annotations

from django.urls import include, path

from django_filters.rest_framework import FilterSet, filters

import InvenTree.permissions
from InvenTree.filters import SEARCH_ORDER_FILTER
from InvenTree.mixins import ListCreateAPI, RetrieveUpdateDestroyAPI

from .models import AssetMachine, AssetMaintenanceRecord, MachinePart
from .serializers import (
    AssetMachineSerializer,
    AssetMaintenanceRecordSerializer,
    MachinePartSerializer,
)


# ---- Filters ----------------------------------------------------------------


class AssetMachineFilter(FilterSet):
    """Filter set for AssetMachine."""

    active = filters.BooleanFilter()

    class Meta:
        model = AssetMachine
        fields = ('active', 'location', 'customer', 'manufacturer')


class MachinePartFilter(FilterSet):
    """Filter set for MachinePart."""

    class Meta:
        model = MachinePart
        fields = ('machine', 'part')


class AssetMaintenanceRecordFilter(FilterSet):
    """Filter set for AssetMaintenanceRecord."""

    class Meta:
        model = AssetMaintenanceRecord
        fields = ('machine', 'work_order')


# ---- Views -------------------------------------------------------------------


class AssetMachineList(ListCreateAPI):
    """List and create asset machines."""

    queryset = AssetMachine.objects.all()
    serializer_class = AssetMachineSerializer
    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]
    filter_backends = SEARCH_ORDER_FILTER
    filterset_class = AssetMachineFilter
    search_fields = ['name', 'description', 'location', 'manufacturer', 'model', 'serial']
    ordering_fields = ['name', 'location', 'manufacturer', 'created_at', 'updated_at']
    ordering = 'name'


class AssetMachineDetail(RetrieveUpdateDestroyAPI):
    """Retrieve, update, or delete an asset machine."""

    queryset = AssetMachine.objects.all()
    serializer_class = AssetMachineSerializer
    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]


class MachinePartList(ListCreateAPI):
    """List and create machine-part relationships."""

    queryset = MachinePart.objects.select_related('part').all()
    serializer_class = MachinePartSerializer
    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]
    filter_backends = SEARCH_ORDER_FILTER
    filterset_class = MachinePartFilter
    search_fields = ['part__name', 'notes']
    ordering_fields = ['part__name', 'quantity']
    ordering = 'part__name'


class MachinePartDetail(RetrieveUpdateDestroyAPI):
    """Retrieve, update, or delete a machine-part relationship."""

    queryset = MachinePart.objects.all()
    serializer_class = MachinePartSerializer
    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]


class AssetMaintenanceRecordList(ListCreateAPI):
    """List and create maintenance records."""

    queryset = AssetMaintenanceRecord.objects.select_related('work_order').all()
    serializer_class = AssetMaintenanceRecordSerializer
    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]
    filter_backends = SEARCH_ORDER_FILTER
    filterset_class = AssetMaintenanceRecordFilter
    search_fields = ['summary', 'details', 'performed_by']
    ordering_fields = ['date', 'created_at']
    ordering = '-date'


class AssetMaintenanceRecordDetail(RetrieveUpdateDestroyAPI):
    """Retrieve, update, or delete a maintenance record."""

    queryset = AssetMaintenanceRecord.objects.all()
    serializer_class = AssetMaintenanceRecordSerializer
    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]


# ---- URL patterns -----------------------------------------------------------


assets_api_urls = [
    path(
        'machines/',
        include([
            path('', AssetMachineList.as_view(), name='asset-machine-list'),
            path('<int:pk>/', AssetMachineDetail.as_view(), name='asset-machine-detail'),
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
            path('', AssetMaintenanceRecordList.as_view(), name='maintenance-record-list'),
            path('<int:pk>/', AssetMaintenanceRecordDetail.as_view(), name='maintenance-record-detail'),
        ]),
    ),
]
