"""Dump imports use live semantics without changing polling or contacting Cosmos."""

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from assets.health_models import HealthSource, MachineSignalBinding, MachineSignalState
from assets.models import AssetMachine, Client
from machine_health.services.ingestion import IngestionError, ingest_readings
from machine_health.tests.test_cosmos_pumphouse import snapshot


class PumphouseDumpTests(TestCase):
    """Exercise the actual management command against a temporary local file."""

    def setUp(self):
        """Create two stations with the same source and pointer."""
        tenant = Client.objects.create(name='Dump test', code='dump-test')
        self.station = AssetMachine.objects.create(
            name='Dump station',
            asset_type='pumphouse',
            client=tenant,
            source_namespace='test',
            source_entity_uuid=uuid4(),
        )
        self.other = AssetMachine.objects.create(
            name='Other dump station',
            asset_type='pumphouse',
            client=tenant,
            source_namespace='test',
            source_entity_uuid=uuid4(),
        )
        self.source = HealthSource.objects.create(
            name='Dump source',
            connector_type='cosmos_pumphouse',
            source_type='scada',
            config={
                'stations': [
                    str(self.station.source_entity_uuid),
                    str(self.other.source_entity_uuid),
                ]
            },
        )
        for station in (self.station, self.other):
            MachineSignalBinding.objects.create(
                machine=station,
                source=self.source,
                external_key='/sl',
                display_name='Level',
            )
        self.document = snapshot(
            1752850800000, station=str(self.station.source_entity_uuid), sl=12
        )
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'dump.json'

    def run_import(self, value, **options):
        """Serialize a file and invoke the command with real bindings."""
        self.path.write_text(json.dumps(value))
        output = StringIO()
        with patch('azure.cosmos.CosmosClient') as client:
            call_command(
                'import_pumphouse_dump',
                str(self.path),
                station=self.station.pk,
                source=self.source.pk,
                stdout=output,
                **options,
            )
        client.assert_not_called()
        return json.loads(output.getvalue())

    def test_repeat_import_preserves_state_and_source_timestamps(self):
        """A pure replay changes neither the cache nor source bookkeeping."""
        self.assertEqual(self.run_import([self.document])['accepted'], 1)
        before = list(MachineSignalState.objects.values())
        self.source.refresh_from_db()
        success = self.source.last_success_at
        result = self.run_import([self.document])
        self.assertEqual(result['accepted'], 0)
        self.assertEqual(result['replayed'], 1)
        self.assertEqual(list(MachineSignalState.objects.values()), before)
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_success_at, success)
        self.assertFalse(
            MachineSignalState.objects.filter(binding__machine=self.other).exists()
        )

    def test_dry_run_rolls_back_all_writes(self):
        """Preview uses the real ingestion checks and leaves no state behind."""
        result = self.run_import([self.document], dry_run=True)
        self.assertEqual(result['accepted'], 1)
        self.assertFalse(MachineSignalState.objects.exists())
        self.source.refresh_from_db()
        self.assertIsNone(self.source.last_success_at)

    def test_cassandra_text_payload_and_sample_envelope(self):
        """Cassandra data1 and the checked-in seed envelope share live flattening."""
        row = {
            'entity_uuid': str(self.station.source_entity_uuid),
            'time_period': self.document['hour_bucket'],
            'sub_time_period': self.document['sub_time_period'],
            'data1': self.document['data1_raw'],
        }
        self.assertEqual(self.run_import(row)['accepted'], 1)
        row.pop('entity_uuid')
        self.assertEqual(
            self.run_import({
                'station_uuid': str(self.station.source_entity_uuid),
                'snapshots': [row],
            })['replayed'],
            1,
        )

    def test_foreign_station_refuses_entire_file(self):
        """A mixed-station dump cannot be assigned to whichever station was selected."""
        foreign = snapshot(1752850800000, station=str(self.other.source_entity_uuid))
        with self.assertRaises(CommandError):
            self.run_import([self.document, foreign])
        self.assertFalse(MachineSignalState.objects.exists())

    def test_failed_later_snapshot_rolls_back_file(self):
        """A malformed later row cannot leave an earlier row committed."""
        broken = dict(self.document, data1_raw='[]')
        with self.assertRaises(CommandError):
            self.run_import([self.document, broken])
        self.assertFalse(MachineSignalState.objects.exists())

    def test_batches_share_live_normalizer_and_rollback_on_failure(self):
        """A 700-tag snapshot is batched, and batch two cannot leave partial state."""
        document = snapshot(
            1752850800000,
            station=str(self.station.source_entity_uuid),
            dex={f'TAG{i}': i for i in range(700)},
        )
        calls = []

        def ingest(source, readings, **kwargs):
            calls.append(len(readings))
            if len(calls) == 2:
                raise IngestionError('second batch failed')
            return ingest_readings(source, readings, **kwargs)

        with (
            patch(
                'assets.management.commands.import_pumphouse_dump.ingest_readings',
                side_effect=ingest,
            ),
            self.assertRaises(CommandError),
        ):
            self.run_import([document])
        self.assertEqual(calls[0], 500)
        self.assertEqual(len(calls), 2)
        self.assertFalse(MachineSignalState.objects.exists())

    def test_duplicate_keys_and_missing_identity_are_refused(self):
        """Input validation precedes any writes."""
        for value in [{}, [dict(self.document, station_uuid=None)], []]:
            with self.subTest(value=value), self.assertRaises(CommandError):
                self.run_import(value)
        self.path.write_text('{"sl": 1, "sl": 2}')
        with self.assertRaises(CommandError):
            call_command(
                'import_pumphouse_dump',
                str(self.path),
                station=self.station.pk,
                source=self.source.pk,
            )
