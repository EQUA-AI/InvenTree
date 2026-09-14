"""Import a bounded, station-owned telemetry dump through the live normalizer."""

import json
from pathlib import Path
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from assets.health_models import HealthSource
from assets.models import AssetMachine
from assets.registry import MAX_BYTES, decode_upload
from machine_health.connectors.pumphouse_payload import (
    SnapshotError,
    flatten_snapshot,
    in_batches,
)
from machine_health.services.ingestion import IngestionError, ingest_readings


def decode(raw):
    """Reuse registry bounds and validation with the live reader's float representation."""
    decode_upload(raw)
    return json.loads(raw)


def document_for(row, station_uuid, envelope_uuid=None):
    """Unwrap a Cassandra row without reimplementing measurement normalization."""
    if not isinstance(row, dict):
        raise CommandError('Every dump row must be an object.')
    identities = [row[key] for key in ('station_uuid', 'entity_uuid') if key in row]
    if envelope_uuid is not None:
        identities.append(envelope_uuid)
    if not identities or any(UUID(str(value)) != station_uuid for value in identities):
        raise CommandError('Every row must identify the selected station.')

    sample = row.get('sub_time_period')
    bucket = row.get('hour_bucket', row.get('time_period'))
    if (
        isinstance(sample, bool)
        or not isinstance(sample, int)
        or isinstance(bucket, bool)
    ):
        raise CommandError(
            'Rows require an integer sample timestamp and an hourly bucket.'
        )
    if not isinstance(bucket, (int, str)) or not str(bucket).isdigit():
        raise CommandError('Hour buckets must be epoch milliseconds.')
    if not int(bucket) <= sample < int(bucket) + 3_600_000:
        raise CommandError('A sample is outside its hourly bucket.')

    document = dict(row)
    if 'data1' in row:
        payload = row['data1']
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        if not isinstance(decode(raw.encode('utf-8')), dict):
            raise CommandError('data1 must contain a JSON object.')
        if 'data1_raw' in row and row['data1_raw'] != raw:
            raise CommandError('Dump carries conflicting raw payloads.')
        document['data1_raw'] = raw
    if 'data1_raw' in document:
        if not isinstance(document['data1_raw'], str) or not isinstance(
            decode(document['data1_raw'].encode('utf-8')), dict
        ):
            raise CommandError('data1_raw must contain a JSON object.')
    return document


class Command(BaseCommand):
    """Import locally without connecting to Cosmos or moving the live checkpoint."""

    help = 'Import pumphouse JSON snapshots into existing station signal bindings'

    def add_arguments(self, parser):
        """Require explicit local station and source identities."""
        parser.add_argument('dump', type=Path)
        parser.add_argument('--station', type=int, required=True)
        parser.add_argument('--source', type=int, required=True)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        """Apply the entire bounded file atomically; previews and pure replays write nothing."""
        try:
            with options['dump'].open('rb') as stream:
                value = decode(stream.read(MAX_BYTES + 1))
            station = AssetMachine.objects.get(
                pk=options['station'],
                asset_type='pumphouse',
                active=True,
                client__active=True,
            )
            source = HealthSource.objects.get(
                pk=options['source'], connector_type='cosmos_pumphouse', active=True
            )
            self.import_rows(value, station, source, options['dry_run'])
        except (
            OSError,
            ValueError,
            TypeError,
            ValidationError,
            SnapshotError,
            IngestionError,
            AssetMachine.DoesNotExist,
            HealthSource.DoesNotExist,
        ) as exc:
            raise CommandError(
                'Dump import refused: invalid data, station, source or bindings.'
            ) from exc

    def import_rows(self, value, station, source, dry_run):
        """Share flattening, batching and replay protection with live ingestion."""
        envelope_uuid = (
            value.get('station_uuid')
            if isinstance(value, dict) and 'snapshots' in value
            else None
        )
        rows = value.get('snapshots', [value]) if isinstance(value, dict) else value
        if not isinstance(rows, list) or not rows or len(rows) > 2000:
            raise CommandError('Dump must contain between 1 and 2000 snapshots.')
        if str(station.source_entity_uuid) not in source.config.get('stations', []):
            raise CommandError(
                'The selected source is not configured for this station.'
            )
        documents = [
            document_for(row, station.source_entity_uuid, envelope_uuid) for row in rows
        ]
        documents.sort(key=lambda document: document['sub_time_period'])
        totals = {'accepted': 0, 'replayed': 0, 'unmapped': 0}
        with transaction.atomic():
            for document in documents:
                for batch in in_batches(flatten_snapshot(document)):
                    result = ingest_readings(
                        source,
                        [reading.as_dict() for reading in batch],
                        station=station,
                    )
                    if result.rejected:
                        raise CommandError(
                            'Dump contains rejected readings; nothing was imported.'
                        )
                    for key in totals:
                        totals[key] += getattr(result, key)
            if dry_run or totals['accepted'] == 0:
                transaction.set_rollback(True)
        self.stdout.write(
            json.dumps({'dry_run': dry_run, 'snapshots': len(documents), **totals})
        )
