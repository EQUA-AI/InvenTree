"""Scoped APIs for physical locations and explicit machine transfers."""

from django.db.models import Q
from django.http import Http404
from django.urls import path

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView
from tasks.dashboard_metrics import OPEN
from tasks.models import WorkOrder
from tasks.scope import ScopeError, work_order_scope_filter

from InvenTree.permissions import IsAuthenticatedOrReadScope, RolePermission
from users.permissions import check_user_role

from . import locations
from .models import AssetLocation, AssetMachine, Client
from .serializers import AssetMachineSerializer


class StrictInput(serializers.Serializer):
    """Reject unsupported temporal writes instead of silently dropping fields."""

    def to_internal_value(self, data):
        """Reject unknown fields before normal serializer validation."""
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise ValidationError({
                'non_field_errors': ['The request contains unsupported fields.']
            })
        return super().to_internal_value(data)


class LocationInput(StrictInput):
    """Editable location metadata; client is immutable after creation."""

    client = serializers.IntegerField(min_value=1, required=False)
    parent = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    name = serializers.CharField(max_length=255, required=False)
    code = serializers.SlugField(max_length=64, required=False)
    kind = serializers.ChoiceField(choices=AssetLocation.Kind.choices, required=False)
    description = serializers.CharField(
        max_length=10000, allow_blank=True, required=False
    )
    timezone = serializers.CharField(max_length=64, allow_blank=True, required=False)
    archived = serializers.BooleanField(required=False)
    expected_version = serializers.IntegerField(min_value=1, required=False)
    reason = serializers.CharField(max_length=1000)


class TransferMachineInput(StrictInput):
    """The current optimistic version of one selected machine."""

    machine_id = serializers.IntegerField(min_value=1)
    expected_placement_version = serializers.IntegerField(min_value=0)


class TransferInput(StrictInput):
    """All-or-nothing transfer; effective time is always chosen by the server."""

    machines = TransferMachineInput(
        many=True, allow_empty=False, max_length=locations.MAX_TRANSFER
    )
    destination_location_id = serializers.IntegerField(min_value=1, allow_null=True)
    reason = serializers.CharField(max_length=1000)
    idempotency_key = serializers.UUIDField()


class LocationPagination(LimitOffsetPagination):
    """Bound option and machine lists before serialization."""

    default_limit = 25
    max_limit = 100


class LocationView(APIView):
    """Keep the existing asset role and independently enforce client scope."""

    permission_classes = [IsAuthenticatedOrReadScope, RolePermission]
    role_required = 'work_order'

    def finalize_response(self, request, response, *args, **kwargs):
        """Do not share a user's authorized tree or machine counts in caches."""
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response


def _integer(value, field):
    try:
        result = int(value)
        if result <= 0:
            raise ValueError
        return result
    except (ValueError, TypeError) as exc:
        raise ValidationError({field: 'Enter a valid positive identifier.'}) from exc


def _boolean(value, field, default=False):
    if value is None:
        return default
    if value not in ('true', 'false'):
        raise ValidationError({field: 'Use true or false.'})
    return value == 'true'


class LocationContext(LocationView):
    """Only the workspaces this actor can use for a new physical location."""

    @extend_schema(responses={200: dict})
    def get(self, request):
        """Return authorized choices and role capabilities."""
        return Response({
            'workspaces': list(
                Client.objects.filter(pk__in=locations.client_ids(request.user)).values(
                    'pk', 'name'
                )
            ),
            'can_add': check_user_role(request.user, 'work_order', 'add'),
            'can_change': check_user_role(request.user, 'work_order', 'change'),
        })


class LocationList(LocationView):
    """Search paths or load a bounded page of a parent's children."""

    @extend_schema(responses={200: dict})
    def get(self, request):
        """Return locations with authorized breadcrumbs and child hints."""
        graph = locations.graph_for(request.user)
        rows = list(graph.values())
        parent = request.query_params.get('parent')
        search = request.query_params.get('search', '').strip().casefold()
        if parent is not None:
            pk = None if parent == 'root' else _integer(parent, 'parent')
            if pk is not None and pk not in graph:
                raise Http404
            rows = [row for row in rows if row.parent_id == pk]
        if request.query_params.get('client'):
            client_id = _integer(request.query_params['client'], 'client')
            rows = [row for row in rows if row.client_id == client_id]
        if search:
            rows = [
                row
                for row in rows
                if search in row.name.casefold() or search in row.code.casefold()
            ]
        if _boolean(request.query_params.get('active_only'), 'active_only'):
            rows = [
                row
                for row in rows
                if not any(p.archived for p in locations.ancestors(graph, row.pk))
            ]
        rows.sort(key=lambda row: (row.name.casefold(), row.pk))
        pagination = LocationPagination()
        page = pagination.paginate_queryset(rows, request, view=self)
        return pagination.get_paginated_response([
            locations.location_data(row, graph) for row in page
        ])

    @extend_schema(request=LocationInput, responses={201: dict})
    def post(self, request):
        """Create a node with a declared client and reason."""
        serializer = LocationInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        for field in ('client', 'name', 'code'):
            if field not in serializer.validated_data:
                raise ValidationError({field: 'This field is required.'})
        node = locations.save_location(request.user, serializer.validated_data)
        return Response(
            locations.location_data(node, locations.graph_for(request.user)), status=201
        )


class LocationDetail(LocationView):
    """View or version-check a location without deleting its history."""

    @extend_schema(responses={200: dict})
    def get(self, request, pk):
        """Return current direct/descendant counts from authorized source rows."""
        graph = locations.graph_for(request.user)
        if pk not in graph:
            raise Http404
        ids = locations.descendants(graph, pk)
        machines = AssetMachine.objects.filter(
            client_id=graph[pk].client_id, physical_location_id__in=ids
        )
        direct = machines.filter(physical_location_id=pk).count()
        try:
            orders = WorkOrder.objects.filter(
                work_order_scope_filter(request.user),
                machine_id__in=machines.values('pk'),
                is_active=True,
                lifecycle_status__in=OPEN,
            )
            direct_orders = orders.filter(machine__physical_location_id=pk).count()
            total_orders = orders.count()
        except ScopeError:
            direct_orders = total_orders = None
        return Response({
            **locations.location_data(graph[pk], graph),
            'counts': {
                'direct_machines': direct,
                'total_machines': machines.count(),
                'direct_open_work_orders': direct_orders,
                'total_open_work_orders': total_orders,
            },
        })

    @extend_schema(request=LocationInput, responses={200: dict, 409: dict})
    def patch(self, request, pk):
        """Edit metadata, archive or reparent under the client structural guard."""
        serializer = LocationInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        if 'expected_version' not in serializer.validated_data:
            raise ValidationError({
                'expected_version': 'Refresh the location before editing.'
            })
        node = locations.save_location(request.user, serializer.validated_data, pk=pk)
        return Response(
            locations.location_data(node, locations.graph_for(request.user))
        )


class LocationMachines(LocationView):
    """The same scoped machine population for all, unassigned and locations."""

    @extend_schema(responses={200: dict})
    def get(self, request):
        """Filter before pagination; never compute totals from displayed rows."""
        graph = locations.graph_for(request.user)
        rows = AssetMachine.objects.filter(
            client_id__in=locations.client_ids(request.user)
        ).order_by('name', 'pk')
        location = request.query_params.get('location')
        unassigned = _boolean(request.query_params.get('unassigned'), 'unassigned')
        include = _boolean(
            request.query_params.get('include_descendants'),
            'include_descendants',
            default=True,
        )
        if location and unassigned:
            raise ValidationError(
                'Location and unassigned filters are mutually exclusive.'
            )
        if location:
            pk = _integer(location, 'location')
            if pk not in graph:
                raise Http404
            rows = rows.filter(
                physical_location_id__in=locations.descendants(graph, pk)
                if include
                else [pk]
            )
        if unassigned:
            rows = rows.filter(physical_location__isnull=True)
        if request.query_params.get('machine'):
            rows = rows.filter(pk=_integer(request.query_params['machine'], 'machine'))
        search = request.query_params.get('search', '').strip()
        if search:
            rows = rows.filter(
                Q(name__icontains=search)
                | Q(serial__icontains=search)
                | Q(manufacturer__icontains=search)
            )
        pagination = LocationPagination()
        page = pagination.paginate_queryset(rows, request, view=self)
        data = []
        for machine in page:
            node = graph.get(machine.physical_location_id)
            data.append({
                **AssetMachineSerializer(machine, context={'request': request}).data,
                'physical_location': locations.location_data(node, graph)
                if node
                else None,
                'placement_version': machine.placement_version,
            })
        return pagination.get_paginated_response(data)


class LocationTransfer(LocationView):
    """Moving an asset exercises change authority, not add-work-order authority."""

    rolemap = {'POST': 'change'}

    @extend_schema(request=TransferInput, responses={200: dict, 409: dict})
    def post(self, request):
        """Commit a transfer or return its already committed result."""
        serializer = TransferInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            locations.transfer_machines(request.user, serializer.validated_data)
        )


location_api_urls = [
    path('', LocationList.as_view(), name='asset-location-list'),
    path('context/', LocationContext.as_view(), name='asset-location-context'),
    path('machines/', LocationMachines.as_view(), name='asset-location-machines'),
    path('transfers/', LocationTransfer.as_view(), name='asset-location-transfer'),
    path('<int:pk>/', LocationDetail.as_view(), name='asset-location-detail'),
]
