"""Bridge reviewed station dictionaries to explicitly scoped live signal bindings."""

import hashlib
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from assets.health_models import HealthSource, MachineSignalBinding, MachineSignalState
from assets.ingestion_models import IngestionCheckpoint
from assets.models import AssetMachine, DictionaryPoint
from InvenTree.conversion import convert_physical_value
from InvenTree.validators import validate_physical_units
from machine_health.connectors.cosmos_pumphouse import bucket_of, to_epoch_ms


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def point_hash(point):
    """Identify measurement meaning independently of its human review note."""
    return _digest([
        point.station_id,
        point.machine_id,
        point.path,
        point.component_id,
        point.template_id,
        point.data_type,
        point.unit,
        point.unit_status,
    ])


def configured_stations(source):
    """Read account configuration without exposing it through the station API."""
    if not isinstance(source.config, dict):
        return []
    stations = source.config.get('stations', [])
    if isinstance(stations, str):
        return [stations]
    return stations if isinstance(stations, list) else []


def source_choices(station):
    """Only a deployment-admin assigned Client can select this account."""
    return [
        source
        for source in HealthSource.objects.filter(
            client_id=station.client_id, active=True, connector_type='cosmos_pumphouse'
        ).order_by('name')
        if str(station.source_entity_uuid) in configured_stations(source)
    ]


def _validate_source(station, source):
    if (
        station.asset_type != 'pumphouse'
        or not station.client.active
        or source.client_id != station.client_id
        or source.connector_type != 'cosmos_pumphouse'
    ):
        raise ValidationError('Source is not configured for this station and Client.')


def _approved_points(station):
    points = list(
        DictionaryPoint.objects
        .filter(station=station, status='approved')
        .select_related('machine', 'component__part', 'template')
        .order_by('pk')
    )
    targets = set()
    for point in points:
        point.clean()
        if (
            not point.machine.active
            or not point.component_id
            or not point.template_id
            or not point.template.enabled
            or not point.review_note.strip()
            or point.data_type == 'unknown'
            or point.unit_status not in {'verified', 'unitless'}
        ):
            raise ValidationError(
                'Approved points must retain reviewed ownership, type and units.'
            )
        if (
            point.component.machine_id != point.machine_id
            or not point.component.part.parameters_list.filter(
                template=point.template
            ).exists()
        ):
            raise ValidationError(
                'Approved parameters must belong to their owner component.'
            )
        if (point.unit_status == 'verified') != bool(point.unit):
            raise ValidationError('Reviewed units do not match their declared status.')
        if point.unit:
            validate_physical_units(point.unit)
        if point.template.units:
            if not point.unit:
                raise ValidationError('This parameter requires physical units.')
            convert_physical_value(f'1 {point.unit}', point.template.units)
        target = (point.component_id, point.template_id)
        if target in targets:
            raise ValidationError(
                'Several approved paths map the same component parameter.'
            )
        targets.add(target)
    return points


def activation_plan(station, source):
    """Produce an opaque preview hash that changes when reviewed mappings or config change."""
    _validate_source(station, source)
    points = list(
        DictionaryPoint.objects.filter(station=station, status='approved').order_by(
            'pk'
        )
    )
    source_hash = _digest({
        'station': station.pk,
        'source': source.pk,
        'client': source.client_id,
        'config': source.config,
        'active': source.active,
        'points': [
            (point.pk, point_hash(point), point.display_name) for point in points
        ],
    })
    return {'source': source.pk, 'source_hash': source_hash, 'approved': len(points)}


def live_status(station):
    """Return station state without endpoint, configuration or credential references."""
    checkpoints = IngestionCheckpoint.objects.filter(
        station=station, active=True, source__client_id=station.client_id
    ).select_related('source')
    checkpoint = checkpoints.first()
    bindings = MachineSignalBinding.objects.filter(
        dictionary_point__station=station,
        active=True,
        source__client_id=station.client_id,
    )
    approved = DictionaryPoint.objects.filter(
        station=station, status='approved'
    ).count()
    total = DictionaryPoint.objects.filter(station=station).count()
    bound = bindings.count()
    return {
        'activated': checkpoint is not None and bound > 0,
        'enabled': bool(
            settings.AIMMS_COSMOS_PUMPHOUSE_ENABLED
            and checkpoint
            and checkpoint.source.active
            and station.active
            and station.client.active
            and str(station.source_entity_uuid)
            in configured_stations(checkpoint.source)
        ),
        'source': {'pk': checkpoint.source_id, 'name': checkpoint.source.name}
        if checkpoint
        else None,
        'sources': [
            {'pk': source.pk, 'name': source.name} for source in source_choices(station)
        ],
        'bound': bound,
        'unbound': max(0, total - bound),
        'approved': approved,
        'last_poll_at': checkpoint.last_poll_at if checkpoint else None,
        'last_error_code': checkpoint.last_error_code if checkpoint else '',
    }


def _check_leases(station, now):
    if IngestionCheckpoint.objects.filter(
        station=station, lease_until__gt=now
    ).exists():
        raise ValidationError('A station poll is in progress; retry after it finishes.')


def _refresh_binding(point, source):
    if (
        MachineSignalBinding.objects
        .filter(source=source, external_key=point.path)
        .filter(Q(machine=point.station) | Q(machine__parent=point.station))
        .exclude(dictionary_point=point)
        .exists()
    ):
        raise ValidationError(
            'Another binding already owns an approved station pointer.'
        )
    existing = (
        MachineSignalBinding.objects
        .filter(source=source)
        .filter(
            Q(dictionary_point=point)
            | Q(machine=point.machine, external_key=point.path)
        )
        .first()
    )
    if existing is not None and existing.dictionary_point_id != point.pk:
        raise ValidationError(
            'A manually managed binding already owns an approved pointer.'
        )
    binding = existing or MachineSignalBinding(source=source, dictionary_point=point)
    was_active = binding.active
    meaning = point_hash(point)
    changed = binding.dictionary_hash != meaning
    if changed:
        if binding.pk:
            MachineSignalState.objects.filter(binding=binding).delete()
        for name in (
            'normal_min',
            'normal_max',
            'warn_min',
            'warn_max',
            'critical_min',
            'critical_max',
        ):
            setattr(binding, name, None)
        binding.transform = {}
    binding.machine, binding.external_key = point.machine, point.path
    binding.display_name, binding.unit = point.display_name, point.unit
    binding.dictionary_hash, binding.active = meaning, True
    if existing is None or changed or not was_active:
        binding.full_clean()
        binding.save()
    else:
        # Display names can change without changing measurement meaning.
        MachineSignalBinding.objects.filter(pk=binding.pk).exclude(
            display_name=point.display_name
        ).update(display_name=point.display_name)


@transaction.atomic
def activate_station(station, source, *, expected_hash, deactivate=False, now=None):
    """Commit only the previewed mapping and never move an existing accepted cursor."""
    now = now or timezone.now()
    station = AssetMachine.objects.select_for_update().get(pk=station.pk)
    source = HealthSource.objects.select_for_update().get(pk=source.pk)
    plan = activation_plan(station, source)
    if not expected_hash or expected_hash != plan['source_hash']:
        raise ValidationError(
            'Mappings or source configuration changed; refresh the activation preview.'
        )
    _check_leases(station, now)
    checkpoints = IngestionCheckpoint.objects.select_for_update().filter(
        station=station
    )
    if deactivate:
        checkpoints.filter(source=source).update(active=False)
        MachineSignalBinding.objects.filter(
            source=source, dictionary_point__station=station
        ).delete()
        return live_status(station)
    if (
        not source.active
        or not station.active
        or not plan['approved']
        or str(station.source_entity_uuid) not in configured_stations(source)
    ):
        raise ValidationError(
            'Activation requires an active source and at least one approved point.'
        )
    if checkpoints.filter(active=True).exclude(source=source).exists():
        raise ValidationError(
            'Deactivate the current station source before selecting another.'
        )
    checkpoint = (
        IngestionCheckpoint.objects
        .select_for_update()
        .filter(source=source, station_uuid=str(station.source_entity_uuid))
        .first()
    )
    if checkpoint is not None and checkpoint.station_id not in (None, station.pk):
        raise ValidationError(
            'This source station already belongs to another registered station.'
        )
    points = _approved_points(station)
    MachineSignalBinding.objects.filter(
        source=source, dictionary_point__station=station
    ).exclude(dictionary_point__in=points).delete()
    for point in points:
        _refresh_binding(point, source)
    if checkpoint is None:
        # Start with the source's five-minute validity window; older history is
        # available through the offline importer without rewinding live state.
        initial = to_epoch_ms(now) - 300_000 - 1
        IngestionCheckpoint.objects.create(
            source=source,
            station=station,
            station_uuid=str(station.source_entity_uuid),
            hour_bucket=str(bucket_of(initial)),
            sub_time_period=initial,
        )
    elif checkpoint.station_id != station.pk or not checkpoint.active:
        checkpoint.station, checkpoint.active = station, True
        checkpoint.save(update_fields=['station', 'active', 'updated_at'])
    return live_status(station)
