"""Scoped APIs for physical locations and explicit machine transfers."""

from django.conf import settings
from django.db.models import Count, Q
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
from tasks.scope import ScopeError, machine_scope_filter, work_order_scope_filter

from InvenTree.permissions import IsAuthenticatedOrReadScope, RolePermission
from machine_health.services.summary import health_conditions
from users.permissions import check_user_role

from . import locations
from .machine_profile import CRITICALITY_VALUES, declared_profile
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


def _active_location_ids(graph):
    """Exclude archived nodes and every node beneath an archived ancestor."""
    return {
        pk
        for pk in graph
        if not any(node.archived for node in locations.ancestors(graph, pk))
    }


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
            'plant_timezone': settings.AIMMS_PLANT_TIMEZONE or None,
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
        active_only = _boolean(request.query_params.get('active_only'), 'active_only')
        active_locations = _active_location_ids(graph) if active_only else None
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
        if active_only:
            rows = [row for row in rows if row.pk in active_locations]
        rows.sort(key=lambda row: (row.name.casefold(), row.pk))
        pagination = LocationPagination()
        page = pagination.paginate_queryset(rows, request, view=self)
        data = [locations.location_data(row, graph) for row in page]
        if _boolean(request.query_params.get('include_counts'), 'include_counts'):
            machines = AssetMachine.objects.filter(
                client_id__in=locations.client_ids(request.user)
            )
            if 'active' in request.query_params:
                machines = machines.filter(
                    active=_boolean(request.query_params.get('active'), 'active')
                )
            if active_only:
                machines = machines.filter(physical_location_id__in=active_locations)
            direct = {
                row['physical_location_id']: row['count']
                for row in machines.values('physical_location_id').annotate(
                    count=Count('pk')
                )
            }
            for row in data:
                row['counts'] = {
                    'direct_machines': direct.get(row['pk'], 0),
                    'total_machines': sum(
                        direct.get(pk, 0)
                        for pk in locations.descendants(graph, row['pk'])
                    ),
                }
        return pagination.get_paginated_response(data)

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
        if 'active' in request.query_params:
            machines = machines.filter(
                active=_boolean(request.query_params.get('active'), 'active')
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


#: Native Work priorities from lowest to highest (``WorkOrder.PRIORITY_*``).
PRIORITY_RANK = {'low': 1, 'medium': 2, 'high': 3}


def _open_work_by_machine(actor, machine_ids):
    """Open Work per machine in the actor's canonical Work scope, or None.

    One grouped query for the page, using the same open-status set, active
    flag and scope predicate as the location counts above. ``None`` when the
    actor's maintenance scope cannot be resolved: unknown is not zero.
    """
    if not machine_ids:
        return {}
    try:
        scope = work_order_scope_filter(actor)
    except ScopeError:
        return None
    rows = (
        WorkOrder.objects
        .filter(
            scope, machine_id__in=machine_ids, is_active=True, lifecycle_status__in=OPEN
        )
        .values('machine_id', 'priority')
        .annotate(count=Count('pk'))
    )
    by_machine = {}
    for row in rows:
        entry = by_machine.setdefault(
            row['machine_id'], {'count': 0, 'highest_priority': None}
        )
        entry['count'] += row['count']
        if PRIORITY_RANK.get(row['priority'], 0) > PRIORITY_RANK.get(
            entry['highest_priority'], 0
        ):
            entry['highest_priority'] = row['priority']
    return by_machine


#: The parts of a machine's Health condition the Machines list shows.
CONDITION_KEYS = (
    'state',
    'configured',
    'signal_count',
    'stale_signal_count',
    'degraded_data',
)


def _condition_by_machine(actor, machines):
    """Health condition per machine, judged as its Health tab judges it.

    Only machines inside the Health tab's own machine scope are judged; any
    other row gets no condition. ``None`` when that scope cannot be resolved:
    an unknown condition is not a normal one.
    """
    if not machines:
        return {}
    try:
        scope = machine_scope_filter(actor)
    except ScopeError:
        return None
    allowed = set(
        AssetMachine.objects.filter(
            scope, pk__in=[machine.pk for machine in machines]
        ).values_list('pk', flat=True)
    )
    conditions = health_conditions([
        machine for machine in machines if machine.pk in allowed
    ])
    return {
        pk: {key: condition[key] for key in CONDITION_KEYS}
        for pk, condition in conditions.items()
    }


class LocationMachines(LocationView):
    """The same scoped machine population for all, unassigned and locations."""

    @extend_schema(responses={200: dict})
    def get(self, request):
        """Filter before pagination; never compute totals from displayed rows."""
        graph = locations.graph_for(request.user)
        rows = AssetMachine.objects.filter(
            client_id__in=locations.client_ids(request.user)
        ).order_by('name', 'pk')
        if 'active' in request.query_params:
            rows = rows.filter(
                active=_boolean(request.query_params.get('active'), 'active')
            )
        if _boolean(request.query_params.get('active_only'), 'active_only'):
            rows = rows.filter(
                Q(physical_location__isnull=True)
                | Q(physical_location_id__in=_active_location_ids(graph))
            )
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
        criticality = request.query_params.get('criticality') or None
        if criticality is not None:
            if criticality not in CRITICALITY_VALUES:
                raise ValidationError({
                    'criticality': 'Use critical, high, medium or low.'
                })
            # R6 UI01: the stored key narrows the scan and the canonical
            # profile reader decides, so a profile that no longer validates
            # counts as not declared here exactly as on the dashboard.
            candidates = rows.filter(profile__criticality=criticality).only(
                'pk', 'profile'
            )
            rows = rows.filter(
                pk__in=[
                    machine.pk
                    for machine in candidates
                    if declared_profile(machine).get('criticality') == criticality
                ]
            )
        # The counts the machine list and the dashboard read, so that this
        # list cannot disagree with them. Imported here because assets.api
        # mounts this module's routes.
        from .api import with_open_alarms

        rows = with_open_alarms(rows)
        if _boolean(request.query_params.get('has_alarms'), 'has_alarms'):
            rows = rows.filter(open_alarms__gt=0)
        pagination = LocationPagination()
        page = pagination.paginate_queryset(rows, request, view=self)
        # R6 UI01: each row's open Work — count and highest priority — from the
        # canonical Work scope. Null when that scope is unavailable.
        open_work = _open_work_by_machine(
            request.user, [machine.pk for machine in page]
        )
        # R6 UI01: each row's Health condition and coverage, or null when the
        # reader's Health scope is unavailable or does not cover the machine.
        conditions = _condition_by_machine(request.user, list(page))
        data = []
        for machine in page:
            node = graph.get(machine.physical_location_id)
            data.append({
                **AssetMachineSerializer(machine, context={'request': request}).data,
                'physical_location': locations.location_data(node, graph)
                if node
                else None,
                'placement_version': machine.placement_version,
                'open_work': None
                if open_work is None
                else open_work.get(machine.pk, {'count': 0, 'highest_priority': None}),
                # R6 UI01: the declared classification, never an alarm
                # severity; null when nothing valid is declared.
                'criticality': declared_profile(machine).get('criticality'),
                'condition': None if conditions is None else conditions.get(machine.pk),
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
