"""Atomic, repeatable station-estate onboarding from an explicit local manifest."""

from io import StringIO
from pathlib import Path
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import transaction

from assets.activation import activate_station, activation_plan, configured_stations
from assets.models import AssetMachine, DictionaryPoint
from assets.registry import (
    MAX_BYTES,
    decode_upload,
    ensure_pump,
    import_dictionary,
    plan_dictionary,
    register_station,
)
from machine_health.mimic_layout import layout_coverage


def read_manifest(path):
    """Bound local inputs and reject duplicate JSON keys before writes."""
    with Path(path).open('rb') as stream:
        return decode_upload(stream.read(MAX_BYTES + 1))


@transaction.atomic
def onboard_estate(manifest, source, *, directory, activate=False, dry_run=False):
    """Return a durable identity crosswalk; any station failure rolls back the batch."""
    if (
        not isinstance(manifest, dict)
        or manifest.get('version') != 1
        or set(manifest) - {'version', 'stations'}
    ):
        raise ValidationError('Manifest requires version 1 and stations only.')
    records = manifest.get('stations')
    if not isinstance(records, list) or not 1 <= len(records) <= 128:
        raise ValidationError('Manifest must contain between 1 and 128 stations.')
    if (
        not source.active
        or source.connector_type != 'cosmos_pumphouse'
        or not source.client_id
        or not source.client.active
        or not isinstance(source.config, dict)
    ):
        raise ValidationError(
            'Select an active Cosmos source with an explicitly assigned active Client.'
        )
    seen = set()
    allowed = {
        'name',
        'source_namespace',
        'source_uuid',
        'source_key',
        'uuid',
        'pumps',
        'snapshot',
        'review',
        'source_context',
    }
    for record in records:
        if not isinstance(record, dict) or set(record) - allowed:
            raise ValidationError('Unknown station manifest fields.')
        identity = str(UUID(record['source_uuid']))
        if identity in seen:
            raise ValidationError(
                'A source station occurs more than once in this account manifest.'
            )
        seen.add(identity)
        keys = record.get('pumps', [])
        if (
            not isinstance(keys, list)
            or len(keys) > 128
            or any(not isinstance(key, str) for key in keys)
            or len(keys) != len(set(keys))
        ):
            raise ValidationError(
                'Pump keys must be a unique list of at most 128 source identifiers.'
            )
    # Match activation's station-before-source lock order and refresh the account
    # allowlist under its lock so simultaneous manifests cannot lose station IDs.
    list(
        AssetMachine.objects
        .select_for_update()
        .filter(asset_type='pumphouse', source_entity_uuid__in=seen)
        .order_by('pk')
    )
    original_client = source.client_id
    source = type(source).objects.select_for_update().get(pk=source.pk)
    if (
        source.client_id != original_client
        or not source.active
        or not source.client.active
        or source.connector_type != 'cosmos_pumphouse'
        or not isinstance(source.config, dict)
    ):
        raise ValidationError(
            'Source ownership or configuration changed; retry the manifest.'
        )
    # Only the explicit station allowlist is changed, never endpoint or credentials.
    source.config = {
        **source.config,
        'stations': sorted(set(configured_stations(source)) | seen),
    }
    source.save(update_fields=['config'])
    report = []
    for record in records:
        station = register_station(
            client=source.client,
            name=record['name'],
            source_namespace=record['source_namespace'],
            source_key=record['source_key'],
            source_entity_uuid=UUID(record['source_uuid']),
            public_uuid=UUID(record['uuid']) if record.get('uuid') else None,
            source_context=record.get('source_context', {}),
        )
        for key in record.get('pumps', []):
            ensure_pump(station, key)
        if record.get('snapshot'):
            with (Path(directory) / record['snapshot']).open('rb') as stream:
                raw = stream.read(MAX_BYTES + 1)
            plan = plan_dictionary(station, raw)
            import_dictionary(station, raw, expected_hash=plan['source_hash'])
        if record.get('review'):
            review_path = Path(directory) / record['review']
            review = read_manifest(review_path)
            if UUID(review['station_source_uuid']) != station.source_entity_uuid or (
                review.get('station_uuid')
                and UUID(review['station_uuid']) != station.uuid
            ):
                raise ValidationError(
                    'Review identity differs from its manifest station.'
                )
            call_command(
                'apply_dictionary_review', review=review_path, stdout=StringIO()
            )
        live = None
        if activate:
            plan = activation_plan(station, source)
            live = activate_station(station, source, expected_hash=plan['source_hash'])
        report.append({
            'station': station.pk,
            'uuid': str(station.uuid),
            'name': station.name,
            'source_uuid': str(station.source_entity_uuid),
            'source_key': station.source_key,
            'source_namespace': station.source_namespace,
            'pumps': [
                {'source_key': pump.source_key, 'uuid': str(pump.uuid), 'pk': pump.pk}
                for pump in station.children.order_by('source_key')
            ],
            'approved': DictionaryPoint.objects.filter(
                station=station, status='approved'
            ).count(),
            'pending': DictionaryPoint.objects
            .filter(station=station)
            .exclude(status='approved')
            .count(),
            'live': live,
            'layout': layout_coverage(station),
        })
    if dry_run:
        transaction.set_rollback(True)
    return {'dry_run': dry_run, 'source': source.pk, 'stations': report}
