"""O15 registry commands. Local facts do not grant operating authority."""

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import serializers
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.response import Response
from tasks.services.work_orders import _canonical_hash

from users.permissions import check_user_role

from .health_models import MachineSignalBinding
from .record_models import (
    ComponentFit,
    RegistryCommand,
    SharedPerformanceView,
    SignalDesignation,
)
from .recorded_facts import binding_signature, designation, fit_projection, fit_queryset
from .registry_api import RegistryAPI
from .registry_models import AssetComponent


def engineer(actor):
    """Use the owner-selected native admin role for engineering decisions."""
    if not check_user_role(actor, 'admin', 'change'):
        raise PermissionDenied('Engineer (admin change) permission required.')


class CommandInput(serializers.Serializer):
    """One durable versioned intent."""

    operation = serializers.CharField(max_length=32)
    expected_version = serializers.IntegerField(min_value=0)
    idempotency_key = serializers.CharField(max_length=128)
    fields = serializers.DictField(default=dict)


class VersionConflict(APIException):
    """A stale registry record cannot be silently overwritten."""

    status_code = 409
    default_detail = 'Version changed. Refresh before recording a new intent.'


class RecordAPI(RegistryAPI):
    """Serialize machine commands and reauthorize exact replays."""

    @transaction.atomic
    def post(self, request, pk, **kwargs):
        """Keep a machine-scoped receipt after the command succeeds."""
        self.require_role('change')
        machine = self.machine()
        from .models import AssetMachine

        AssetMachine.objects.select_for_update().get(pk=machine.pk)
        if not isinstance(request.data, dict) or set(request.data) != {
            'operation',
            'fields',
            'expected_version',
            'idempotency_key',
        }:
            raise ValidationError('Unsupported command fields.')
        if type(request.data.get('expected_version')) is not int:
            raise ValidationError('Integer expected version required.')
        form = CommandInput(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        self.authorize(request.user, data)
        intent = {'endpoint': request.path, **data}
        digest = _canonical_hash('registry_records', request.user, intent)
        previous = RegistryCommand.objects.filter(
            machine=machine, actor=request.user, key=data['idempotency_key']
        ).first()
        if previous:
            if previous.request_hash != digest:
                return Response(
                    {'detail': 'Idempotency key belongs to a different intent.'},
                    status=409,
                )
            return Response(previous.result)
        if request.headers.get('X-AIMMS-Reconcile-Only') == '1':
            result = {
                'record_command_outcome': 'not_committed',
                'idempotency_key': data['idempotency_key'],
            }
        else:
            result = self.apply(machine, request.user, data, **kwargs)
        RegistryCommand.objects.create(
            machine=machine,
            actor=request.user,
            key=data['idempotency_key'],
            request_hash=digest,
            result=result,
        )
        return Response(result)

    def authorize(self, actor, data):
        """Subclasses add engineering/stock permissions before replay."""

    def version(self, actual, expected):
        """No silent overwrite after a concurrent decision."""
        if actual != expected:
            raise VersionConflict()


class CriticalDesignations(RecordAPI):
    """Registry review of each live signal binding."""

    def get(self, request, pk):
        """Read binding identity and the complete decision history."""
        machine = self.machine()
        rows = MachineSignalBinding.objects.filter(machine=machine).prefetch_related(
            'designations__recorded_by'
        )
        return Response({
            'results': [
                {
                    'binding_id': row.pk,
                    'display_name': row.display_name,
                    'external_key': row.external_key,
                    'unit': row.unit,
                    'source_name': row.source.name,
                    'binding_signature': binding_signature(row),
                    'active': row.active,
                    'history': [
                        {
                            **designation(d),
                            'applies_current': d.binding_signature
                            == binding_signature(row),
                        }
                        for d in row.designations.all()
                    ],
                }
                for row in rows
            ]
        })

    def authorize(self, actor, data):
        """Only engineers designate critical signals."""
        engineer(actor)

    def apply(self, machine, actor, data):
        """Append a decision and its next binding version."""
        fields = data['fields']
        if (
            data['operation'] != 'designate'
            or set(fields) != {'binding_id', 'binding_signature', 'critical', 'reason'}
            or type(fields.get('critical')) is not bool
            or type(fields.get('binding_id')) is not int
        ):
            raise ValidationError(
                'Designation requires binding_id, critical boolean and reason.'
            )
        binding = get_object_or_404(
            MachineSignalBinding.objects.select_for_update(of=('self',)),
            machine=machine,
            pk=fields['binding_id'],
        )
        if fields['critical'] and not binding.active:
            raise ValidationError(
                'An inactive binding cannot acquire a critical designation.'
            )
        if fields['binding_signature'] != binding_signature(binding):
            raise VersionConflict(
                'The binding meaning changed. Refresh and review the exact source binding.'
            )
        latest = binding.designations.first()
        self.version(latest.version if latest else 0, data['expected_version'])
        reason = serializers.CharField(max_length=4000).run_validation(fields['reason'])
        row = SignalDesignation.objects.create(
            binding=binding,
            version=data['expected_version'] + 1,
            critical=fields['critical'],
            binding_signature=binding_signature(binding),
            reason=reason,
            recorded_by=actor,
        )
        return designation(row)


class Fits(RecordAPI):
    """Manual proposals require explicit verification before becoming current."""

    def authorize(self, actor, data):
        """Fit identity follows the native stock read boundary."""
        if not check_user_role(actor, 'stock', 'view'):
            raise PermissionDenied('Stock view permission required.')

    def get(self, request, pk, component_pk):
        """Read exact slot history without borrowing another slot's stock."""
        self.authorize(request.user, {'operation': 'read'})
        slot = get_object_or_404(
            AssetComponent, machine=self.machine(), pk=component_pk
        )
        return Response(fit_projection(fit_queryset().filter(component=slot)))

    def apply(self, machine, actor, data, component_pk):
        """Record or verify one physical-identity assertion."""
        from stock.models import StockItem

        slot = get_object_or_404(
            AssetComponent.objects.select_for_update(of=('self',)),
            machine=machine,
            pk=component_pk,
            part__virtual=False,
        )
        current = fit_projection(fit_queryset().filter(component=slot))
        self.version(current['version'], data['expected_version'])
        fields = data['fields']
        if data['operation'] == 'record':
            if (
                set(fields) != {'stock_item', 'fitted_at', 'reason'}
                or type(fields.get('stock_item')) is not int
            ):
                raise ValidationError('Provide stock_item, fitted_at and reason.')
            item = get_object_or_404(StockItem, pk=fields['stock_item'], part=slot.part)
            if not item.serial and not item.batch:
                raise ValidationError(
                    'Record the real serial or lot before recording a fit.'
                )
            from tasks.work_execution.common import record_instant

            fitted = record_instant(fields['fitted_at'])
            if fitted > timezone.now():
                raise ValidationError('Fit date cannot be in the future.')
            ComponentFit.objects.create(
                component=slot,
                stock_item=item,
                fitted_at=fitted,
                recorded_by=actor,
                reason=serializers.CharField(max_length=4000).run_validation(
                    fields['reason']
                ),
                source='manual',
                serial=item.serial or '',
                lot=item.batch or '',
            )
        elif data['operation'] == 'verify':
            if set(fields) != {'fit_id'} or type(fields.get('fit_id')) is not int:
                raise ValidationError('Provide fit_id.')
            row = get_object_or_404(
                ComponentFit,
                pk=fields['fit_id'],
                component=slot,
                source='manual',
                verified_at__isnull=True,
            )
            if row.recorded_by_id == actor.pk:
                raise ValidationError(
                    'A different authorized person must verify this fit.'
                )
            if row.stock_item.part_id != slot.part_id:
                raise ValidationError(
                    'The slot catalogue part changed. Review the fitted identity before verification.'
                )
            row.verified_by, row.verified_at = actor, timezone.now()
            row.save(update_fields=['verified_by', 'verified_at'])
        else:
            raise ValidationError('Unsupported fit operation.')
        return fit_projection(fit_queryset().filter(component=slot))


def view_row(row):
    """Shared views carry ownership and latest curator attribution."""
    from tasks.work_execution.common import person

    return {
        'id': row.pk,
        'name': row.name,
        'config': row.config,
        'owner_id': row.owner_id,
        'owner': person(row.owner),
        'is_default': row.is_default,
        'version': row.version,
        'recorded_at': row.recorded_at.isoformat(),
        'updated_by': person(row.updated_by),
        'updated_at': row.updated_at.isoformat(),
    }


class SharedViews(RecordAPI):
    """Everyone authorized to read this machine can read its shared views."""

    def get(self, request, pk):
        """Read defaults first; never apply choices automatically."""
        machine = self.machine()
        return Response({
            'results': [
                view_row(row)
                for row in SharedPerformanceView.objects
                .filter(machine=machine, active=True)
                .select_related('owner', 'updated_by')
                .order_by('-is_default', 'name')
            ]
        })

    def authorize(self, actor, data):
        """Recheck default curation authority before replay too."""
        if data['operation'] == 'curate':
            engineer(actor)

    def apply(self, machine, actor, data):
        """Creator owns changes; engineers curate the default set."""
        fields = data['fields']
        if data['operation'] == 'create':
            self.version(0, data['expected_version'])
            if set(fields) != {'name', 'config'}:
                raise ValidationError('Provide name and config.')
            config = fields['config']
            if not isinstance(config, dict) or set(config) != {
                'search',
                'section',
                'bindings',
            }:
                raise ValidationError('Unsupported display configuration.')
            if (
                not isinstance(config['search'], str)
                or len(config['search']) > 200
                or config['section']
                not in {
                    'all',
                    'electrical',
                    'operation',
                    'winding',
                    'vibration',
                    'cooling',
                    'bearing',
                    'other',
                }
            ):
                raise ValidationError('Unsupported display choices.')
            bindings = config['bindings']
            if (
                not isinstance(bindings, list)
                or len(bindings) > 12
                or any(type(b) is not int for b in bindings)
                or len(set(bindings)) != len(bindings)
                or MachineSignalBinding.objects.filter(
                    machine=machine, active=True, pk__in=bindings
                ).count()
                != len(bindings)
            ):
                raise ValidationError(
                    'Channels must be current bindings on this machine.'
                )
            name = serializers.CharField(max_length=80).run_validation(fields['name'])
            if SharedPerformanceView.objects.filter(
                machine=machine, owner=actor, name=name, active=True
            ).exists():
                raise ValidationError(
                    'An owned shared view with this name already exists.'
                )
            row = SharedPerformanceView.objects.create(
                machine=machine, owner=actor, updated_by=actor, name=name, config=config
            )
        else:
            if type(fields.get('id')) is not int:
                raise ValidationError('View id required.')
            row = get_object_or_404(
                SharedPerformanceView, machine=machine, pk=fields['id'], active=True
            )
            self.version(row.version, data['expected_version'])
            if (
                data['operation'] == 'curate'
                and set(fields) == {'id', 'is_default'}
                and type(fields['is_default']) is bool
            ):
                row.is_default = fields['is_default']
            elif (
                data['operation'] == 'delete'
                and set(fields) == {'id'}
                and row.owner_id == actor.pk
            ):
                if row.is_default:
                    engineer(actor)
                row.active = False
            else:
                raise PermissionDenied(
                    'Only the creator deletes a shared view; engineers curate defaults.'
                )
            row.version += 1
            row.updated_by = actor
            row.save()
        return view_row(row)
