"""REST API endpoints for the assets (equipment machines) application."""

from __future__ import annotations

from django.db.models import Count, F, Q
from django.urls import include, path

from django_filters.rest_framework import FilterSet, filters
from tasks.scope import ScopeError

import InvenTree.permissions
from InvenTree.filters import SEARCH_ORDER_FILTER, InvenTreeDateFilter
from InvenTree.mixins import ListCreateAPI, RetrieveUpdateDestroyAPI

from .models import AssetMachine, AssetMaintenanceRecord, Client, MachinePart
from .registry_api import authorized_client_ids, registry_urls
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
    has_alarms = filters.BooleanFilter(method='filter_has_alarms')

    def filter_has_alarms(self, queryset, name, value):
        """Machines with an active alarm on them or on a pump under them."""
        if value:
            return queryset.filter(open_alarms__gt=0)
        return queryset.filter(open_alarms=0)

    class Meta:
        """Filter configuration for AssetMachine."""

        model = AssetMachine
        fields = ('active', 'location', 'client', 'manufacturer')


class MachinePartFilter(FilterSet):
    """Filter set for MachinePart."""

    category = filters.NumberFilter(field_name='part__category')
    group = filters.CharFilter(
        field_name='part__category__name', lookup_expr='icontains'
    )
    created_before = InvenTreeDateFilter(
        field_name='part__creation_date', lookup_expr='lt'
    )
    created_after = InvenTreeDateFilter(
        field_name='part__creation_date', lookup_expr='gt'
    )

    class Meta:
        """Filter configuration for MachinePart."""

        model = MachinePart
        fields = (
            'machine',
            'part',
            'category',
            'group',
            'created_before',
            'created_after',
        )


class AssetMaintenanceRecordFilter(FilterSet):
    """Filter set for AssetMaintenanceRecord."""

    class Meta:
        """Filter configuration for AssetMaintenanceRecord."""

        model = AssetMaintenanceRecord
        fields = ('machine', 'work_order')


# ---- Views -------------------------------------------------------------------


def registry_visibility(queryset, actor, prefix=''):
    """Retain legacy generic equipment behavior but scope registered equipment."""
    try:
        clients = authorized_client_ids(actor)
    except ScopeError:
        clients = set()
    return queryset.filter(
        Q(**{prefix + 'asset_type': 'equipment'})
        | Q(**{prefix + 'client_id__in': clients, prefix + 'client__active': True})
    )


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


def with_open_alarms(queryset):
    """Count the active alarms on each machine, and on the pumps under it.

    What the morning check wants from the machine list is which stations have
    something to look at. A station's own alarms are few; its pumps' are where
    the trouble is, so the station's count carries both. Counted distinct,
    because the two joins multiply each other's rows.
    """
    from assets.health_models import ACTIVE_ANOMALY_STATUSES

    active = [status.value for status in ACTIVE_ANOMALY_STATUSES]
    own = Q(anomalies__status__in=active)
    below = Q(children__anomalies__status__in=active)
    return queryset.annotate(
        own_alarms=Count('anomalies', filter=own, distinct=True),
        child_alarms=Count('children__anomalies', filter=below, distinct=True),
        own_critical=Count(
            'anomalies', filter=own & Q(anomalies__severity='critical'), distinct=True
        ),
        child_critical=Count(
            'children__anomalies',
            filter=below & Q(children__anomalies__severity='critical'),
            distinct=True,
        ),
    ).annotate(
        open_alarms=F('own_alarms') + F('child_alarms'),
        open_critical_alarms=F('own_critical') + F('child_critical'),
    )


class AssetMachineList(ListCreateAPI):
    """List and create asset machines."""

    queryset = AssetMachine.objects.select_related('client', 'parent').all()
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
    ordering_fields = [
        'name',
        'location',
        'manufacturer',
        'created_at',
        'updated_at',
        'open_alarms',
        'open_critical_alarms',
    ]
    ordering = 'name'

    def get_queryset(self):
        """Apply registry scope to station/pump records."""
        return with_open_alarms(
            registry_visibility(super().get_queryset(), self.request.user)
        )


class AssetMachineDetail(RetrieveUpdateDestroyAPI):
    """Retrieve, update, or delete an asset machine."""

    queryset = AssetMachine.objects.select_related('parent').all()
    serializer_class = AssetMachineSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'

    def get_queryset(self):
        """Apply registry scope to station/pump records."""
        return with_open_alarms(
            registry_visibility(super().get_queryset(), self.request.user)
        )


class MachinePartList(ListCreateAPI):
    """List and create machine-part relationships."""

    queryset = MachinePart.objects.select_related('part', 'part__category').all()
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
    path('registry/', include(registry_urls)),
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
