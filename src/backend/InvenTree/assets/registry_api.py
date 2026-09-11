"""Scoped equipment registry and offline dictionary review APIs."""

from django.core.exceptions import ValidationError as ModelValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from tasks.scope import ScopeError, require_machine_scope, scope_for_actor

from common.models import ParameterTemplate
from InvenTree.permissions import InvenTreeTokenMatchesOASRequirements, map_scope
from part.models import Part, PartCategoryParameterTemplate
from users.permissions import check_user_role

from .models import AssetComponent, AssetMachine, Client, DictionaryPoint
from .registry import (
    MAX_BYTES,
    ensure_pump,
    import_dictionary,
    plan_dictionary,
    register_station,
)


def authorized_client_ids(actor):
    """Only exact Client-only grants authorize equipment, not mixed or site grants."""
    return {
        s.client_id
        for s in scope_for_actor(actor)
        if s.client_id is not None and s.customer_id is None and s.site_key is None
    }


class EquipmentSerializer(serializers.ModelSerializer):
    """Read-only equipment identities; mutations have dedicated validated inputs."""

    parent_name = serializers.CharField(source='parent.name', default=None)

    class Meta:
        """Read-only public equipment fields."""

        model = AssetMachine
        fields = [
            'pk',
            'uuid',
            'name',
            'asset_type',
            'parent',
            'parent_name',
            'client',
            'source_namespace',
            'source_entity_uuid',
            'source_key',
            'created_at',
        ]
        read_only_fields = fields


class ComponentSerializer(serializers.ModelSerializer):
    """Component occurrence projection with catalogue context."""

    part_name = serializers.CharField(source='part.name')
    virtual = serializers.BooleanField(source='part.virtual')
    machine_name = serializers.CharField(source='machine.name')

    class Meta:
        """Read-only component fields."""

        model = AssetComponent
        fields = [
            'pk',
            'uuid',
            'machine',
            'machine_name',
            'part',
            'part_name',
            'virtual',
            'code',
            'name',
            'status',
            'provenance',
            'review_note',
            'reviewed_at',
        ]
        read_only_fields = fields


class PointSerializer(serializers.ModelSerializer):
    """Dictionary projection without historical sample values."""

    machine_name = serializers.CharField(source='machine.name')
    component_name = serializers.CharField(source='component.name', default=None)
    template_name = serializers.CharField(source='template.name', default=None)

    class Meta:
        """Read-only dictionary fields."""

        model = DictionaryPoint
        fields = [
            'pk',
            'uuid',
            'station',
            'machine',
            'machine_name',
            'path',
            'raw_tag',
            'display_name',
            'component',
            'component_name',
            'template',
            'template_name',
            'match_method',
            'issue',
            'data_type',
            'unit',
            'unit_status',
            'status',
            'review_note',
            'reviewed_at',
        ]
        read_only_fields = fields


class StationInput(serializers.Serializer):
    """Explicit source identity and authorized Client registration inputs."""

    client = serializers.IntegerField(min_value=1)
    name = serializers.CharField(max_length=255)
    source_namespace = serializers.SlugField(max_length=64)
    source_entity_uuid = serializers.UUIDField()
    source_key = serializers.CharField(max_length=64)
    uuid = serializers.UUIDField(required=False)


class ComponentInput(serializers.Serializer):
    """Identify one catalogue-backed component occurrence."""

    part = serializers.IntegerField(min_value=1)
    code = serializers.CharField(max_length=100)
    name = serializers.CharField(max_length=255)


class ReviewInput(serializers.Serializer):
    """An explicit mapping decision, never a source-control command."""

    machine = serializers.IntegerField(min_value=1)
    component = serializers.IntegerField(min_value=1, allow_null=True)
    template = serializers.IntegerField(min_value=1, allow_null=True)
    display_name = serializers.CharField(max_length=255)
    data_type = serializers.ChoiceField(
        choices=['number', 'status', 'boolean', 'text', 'unknown']
    )
    unit = serializers.CharField(max_length=32, allow_blank=True)
    unit_status = serializers.ChoiceField(
        choices=['verified', 'unitless', 'proposed', 'unresolved']
    )
    status = serializers.ChoiceField(
        choices=['approved', 'draft', 'unresolved', 'rejected']
    )
    review_note = serializers.CharField(max_length=4000, allow_blank=True)


class RegistryAPI(APIView):
    """Shared authentication, exact Client scope and bounded API helpers."""

    permission_classes = [IsAuthenticated, InvenTreeTokenMatchesOASRequirements]
    required_alternate_scopes = map_scope(roles=['work_order'])

    def initial(self, request, *args, **kwargs):
        """Require view authority before dispatching any registry action."""
        super().initial(request, *args, **kwargs)
        self.require_role('view')

    def require_role(self, action):
        """Require an additional mutation authority where applicable."""
        if not check_user_role(self.request.user, 'work_order', action):
            raise PermissionDenied(f'Work-order {action} permission is required.')

    def machines(self):
        """Select only the actor's exact authorized Client equipment."""
        return AssetMachine.objects.filter(
            client_id__in=authorized_client_ids(self.request.user), client__active=True
        ).select_related('client', 'parent')

    def machine(self):
        """Resolve a scoped owner or return a non-disclosing 404."""
        return get_object_or_404(self.machines(), pk=self.kwargs['pk'])

    def handle_exception(self, exc):
        """Translate model and scope failures without leaking database details."""
        if isinstance(exc, ScopeError):
            exc = PermissionDenied(
                'Equipment scope is not configured for this account.'
            )
        elif isinstance(exc, ModelValidationError):
            exc = ValidationError(exc.messages)
        elif isinstance(exc, IntegrityError):
            exc = ValidationError(
                'Identity collision; refresh and review the existing registration.'
            )
        return super().handle_exception(exc)

    def page(self, queryset, serializer):
        """Return a bounded slice and total count."""
        try:
            offset = max(0, int(self.request.query_params.get('offset', 0)))
            limit = min(500, max(1, int(self.request.query_params.get('limit', 100))))
        except ValueError as exc:
            raise ValidationError('Invalid pagination.') from exc
        return Response({
            'count': queryset.count(),
            'results': serializer(queryset[offset : offset + limit], many=True).data,
        })

    def upload(self):
        """Bound uploaded bytes before decoding JSON."""
        file = self.request.FILES.get('data_file')
        if file is None or file.size > MAX_BYTES:
            raise ValidationError('Attach data_file (JSON, at most 8 MiB).')
        return file.read(MAX_BYTES + 1)


class RegistryOptions(RegistryAPI):
    """Authorized Client choices and shared catalogue review definitions."""

    def get(self, request):
        """Return review choices without creating missing catalogue data."""
        client_ids = authorized_client_ids(request.user)
        templates = ParameterTemplate.objects.filter(
            name__startswith='PUMP | ', enabled=True
        )
        assignments = PartCategoryParameterTemplate.objects.filter(
            template__in=templates
        )
        parts = Part.objects.filter(IPN__startswith='PS-', active=True)
        return Response({
            'clients': list(
                Client.objects.filter(pk__in=client_ids, active=True).values(
                    'pk', 'name', 'code'
                )
            ),
            'parts': list(parts.values('pk', 'name', 'IPN', 'category', 'virtual')),
            'templates': list(templates.values('pk', 'name', 'units')),
            'assignments': list(assignments.values('category_id', 'template_id')),
        })


class RegistryList(RegistryAPI):
    """Scoped station listing and idempotent registration."""

    def get(self, request):
        """List visible pump stations."""
        queryset = self.machines().filter(asset_type='pumphouse').order_by('name')
        return self.page(queryset, EquipmentSerializer)

    def post(self, request):
        """Register an authoritative source station in an allowed Client."""
        self.require_role('add')
        form = StationInput(data=request.data)
        form.is_valid(raise_exception=True)
        values = dict(form.validated_data)
        client = get_object_or_404(Client, pk=values.pop('client'), active=True)
        require_machine_scope(request.user, AssetMachine(client=client))
        station = register_station(
            client=client, public_uuid=values.pop('uuid', None), **values
        )
        return Response(EquipmentSerializer(station).data, status=201)


class RegistryDetail(RegistryAPI):
    """Equipment hierarchy and pump-slot registration."""

    def get(self, request, pk):
        """Return one equipment identity and its scoped children."""
        machine = self.machine()
        return Response({
            'equipment': EquipmentSerializer(machine).data,
            'children': EquipmentSerializer(
                self.machines().filter(parent=machine).order_by('source_key'), many=True
            ).data,
        })

    def post(self, request, pk):
        """Register a stable child pump slot."""
        self.require_role('add')
        station = self.machine()
        if station.asset_type != 'pumphouse':
            raise ValidationError('Pump slots must be created under a station.')
        key = serializers.CharField(max_length=64).run_validation(
            request.data.get('source_key')
        )
        with transaction.atomic():
            station = AssetMachine.objects.select_for_update().get(pk=station.pk)
            pump = ensure_pump(station, key)
        return Response(EquipmentSerializer(pump).data, status=201)


class Components(RegistryAPI):
    """List or propose individually addressable component occurrences."""

    def get(self, request, pk):
        """List components for a selected owner or station subtree."""
        machine = self.machine()
        owners = [machine.pk]
        if machine.asset_type == 'pumphouse':
            owners.extend(
                self.machines().filter(parent=machine).values_list('pk', flat=True)
            )
        queryset = AssetComponent.objects.filter(machine_id__in=owners).select_related(
            'part', 'machine'
        )
        if status := request.query_params.get('status'):
            queryset = queryset.filter(status=status)
        return self.page(queryset, ComponentSerializer)

    def post(self, request, pk):
        """Propose an occurrence without asserting verified installation."""
        self.require_role('add')
        machine = self.machine()
        form = ComponentInput(data=request.data)
        form.is_valid(raise_exception=True)
        values = dict(form.validated_data)
        part = get_object_or_404(Part, pk=values.pop('part'), active=True)
        with transaction.atomic():
            obj = AssetComponent(
                machine=machine,
                part=part,
                provenance='Manually proposed assignment',
                **values,
            )
            obj.full_clean()
            obj.save()
        return Response(ComponentSerializer(obj).data, status=201)


class ComponentReview(RegistryAPI):
    """Explicit physical/logical component assignment review."""

    def patch(self, request, pk, component_pk):
        """Record a scoped review decision and reviewer attribution."""
        self.require_role('change')
        machine = self.machine()
        obj = get_object_or_404(AssetComponent, pk=component_pk, machine=machine)
        status = serializers.ChoiceField(choices=['draft', 'verified']).run_validation(
            request.data.get('status')
        )
        note = serializers.CharField(max_length=4000).run_validation(
            request.data.get('review_note')
        )
        obj.status, obj.review_note = status, note
        obj.reviewed_by, obj.reviewed_at = request.user, timezone.now()
        obj.save(update_fields=['status', 'review_note', 'reviewed_by', 'reviewed_at'])
        return Response(ComponentSerializer(obj).data)


class Dictionary(RegistryAPI):
    """Filterable dictionary of station or pump-owned source paths."""

    def get(self, request, pk):
        """Filter source paths by equipment, component, status and text."""
        machine = self.machine()
        queryset = (
            DictionaryPoint.objects.filter(station=machine)
            if machine.asset_type == 'pumphouse'
            else DictionaryPoint.objects.filter(machine=machine)
        )
        queryset = queryset.select_related('machine', 'component', 'template')
        for key in ['status', 'component_id', 'machine_id', 'match_method']:
            if value := request.query_params.get(key):
                if key.endswith('_id') and not value.isdigit():
                    raise ValidationError('Invalid equipment/component filter.')
                queryset = queryset.filter(**{key: value})
        if search := request.query_params.get('search'):
            queryset = queryset.filter(
                Q(raw_tag__icontains=search[:255])
                | Q(display_name__icontains=search[:255])
            )
        return self.page(queryset, PointSerializer)


class Preview(RegistryAPI):
    """Read-only domain planning; authentication bookkeeping may still occur."""

    required_alternate_scopes = map_scope(only_read=True)

    def post(self, request, pk):
        """Report candidates and conflicts without persisting the preview."""
        return Response(plan_dictionary(self.machine(), self.upload()))


class Import(RegistryAPI):
    """Additive import requiring both creation and change authority."""

    def post(self, request, pk):
        """Revalidate the selected file and preserve existing mappings."""
        self.require_role('add')
        self.require_role('change')
        return Response(
            import_dictionary(
                self.machine(),
                self.upload(),
                expected_hash=request.data.get('source_hash'),
            )
        )


class PointReview(RegistryAPI):
    """Review exact source paths with owner and catalogue validation."""

    @transaction.atomic
    def patch(self, request, pk, point_pk):
        """Record an explicit mapping decision with a station-wide approval lock."""
        self.require_role('change')
        selected = self.machine()
        points = DictionaryPoint.objects.select_for_update()
        points = (
            points.filter(station=selected)
            if selected.asset_type == 'pumphouse'
            else points.filter(machine=selected)
        )
        point = get_object_or_404(points, pk=point_pk)
        AssetMachine.objects.select_for_update().get(pk=point.station_id)
        form = ReviewInput(data=request.data)
        form.is_valid(raise_exception=True)
        values = dict(form.validated_data)
        owner = get_object_or_404(self.machines(), pk=values.pop('machine'))
        if owner.pk != point.station_id and owner.parent_id != point.station_id:
            raise ValidationError('Owner must belong to this station.')
        component_id, template_id = values.pop('component'), values.pop('template')
        component = (
            get_object_or_404(AssetComponent, pk=component_id, machine=owner)
            if component_id
            else None
        )
        template = (
            get_object_or_404(ParameterTemplate, pk=template_id, enabled=True)
            if template_id
            else None
        )
        if template:
            if (
                not component
                or not component.part.parameters_list.filter(template=template).exists()
            ):
                raise ValidationError(
                    'Parameter must belong to the selected component catalogue Part.'
                )
            if template.units:
                if not values['unit']:
                    raise ValidationError('This parameter requires physical units.')
                from InvenTree.conversion import convert_physical_value

                convert_physical_value(f'1 {values["unit"]}', template.units)
        if values['unit']:
            from InvenTree.validators import validate_physical_units

            validate_physical_units(values['unit'])
        if values['unit_status'] == 'unitless' and values['unit']:
            raise ValidationError('Unitless points cannot specify units.')
        if values['unit_status'] == 'verified' and not values['unit']:
            raise ValidationError(
                'Verified units require a unit; use unitless otherwise.'
            )
        if values['status'] == 'approved':
            if (
                not component
                or not template
                or values['unit_status'] not in {'verified', 'unitless'}
                or values['data_type'] == 'unknown'
                or not values['review_note'].strip()
            ):
                raise ValidationError(
                    'Approval requires an owner component, parameter, reviewed unit/type and a review note.'
                )
            if (
                DictionaryPoint.objects
                .filter(
                    station=point.station,
                    component=component,
                    template=template,
                    status='approved',
                )
                .exclude(pk=point.pk)
                .exists()
            ):
                raise ValidationError(
                    'Another approved path already maps this component parameter; reject or remap it first.'
                )
        point.machine, point.component, point.template = owner, component, template
        for key, value in values.items():
            setattr(point, key, value)
        point.reviewed_by, point.reviewed_at = request.user, timezone.now()
        if values['status'] == 'approved':
            point.issue = ''
        point.save()
        return Response(PointSerializer(point).data)


registry_urls = [
    path('', RegistryList.as_view(), name='registry-list'),
    path('options/', RegistryOptions.as_view(), name='registry-options'),
    path('<int:pk>/', RegistryDetail.as_view(), name='registry-detail'),
    path('<int:pk>/components/', Components.as_view(), name='registry-components'),
    path(
        '<int:pk>/components/<int:component_pk>/review/',
        ComponentReview.as_view(),
        name='registry-component-review',
    ),
    path('<int:pk>/dictionary/', Dictionary.as_view(), name='registry-dictionary'),
    path('<int:pk>/preview/', Preview.as_view(), name='registry-preview'),
    path('<int:pk>/import/', Import.as_view(), name='registry-import'),
    path(
        '<int:pk>/dictionary/<int:point_pk>/review/',
        PointReview.as_view(),
        name='registry-point-review',
    ),
]
