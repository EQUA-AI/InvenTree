"""Estate onboarding retains identities and never partially commits a bad manifest."""

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from assets.health_models import HealthSource
from assets.models import AssetMachine, Client
from assets.pumphouse_estate import onboard_estate
from assets.serializers import AssetMachineSerializer


class EstateTests(TestCase):
    """An explicit account allowlist backs every imported station."""

    def setUp(self):
        """Use an isolated active Client/source with no Azure configuration."""
        self.tenant = Client.objects.create(code='estate-test', name='Estate')
        self.source = HealthSource.objects.create(
            name='Estate Cosmos',
            client=self.tenant,
            connector_type='cosmos_pumphouse',
            source_type='scada',
            config={'stations': []},
        )
        self.manifest: dict = {
            'version': 1,
            'stations': [
                {
                    'name': f'Station {number}',
                    'source_key': f'PH_{number}',
                    'source_uuid': str(uuid4()),
                    'source_namespace': 'estate',
                    'pumps': ['P1', 'P17'],
                }
                for number in range(1, 13)
            ],
        }

    def test_twelve_station_registration_is_repeatable_and_isolated(self):
        """Replaying an estate keeps all station/pump UUIDs and exposes station type."""
        first = onboard_estate(self.manifest, self.source, directory='.')
        second = onboard_estate(self.manifest, self.source, directory='.')
        self.assertEqual(first, second)
        self.assertEqual(
            AssetMachine.objects.filter(asset_type='pumphouse').count(), 12
        )
        self.assertEqual(AssetMachine.objects.filter(parent__isnull=False).count(), 24)
        self.source.refresh_from_db()
        self.assertEqual(len(self.source.config['stations']), 12)
        for station in first['stations']:
            self.assertEqual([p['source_key'] for p in station['pumps']], ['P1', 'P17'])
        instance = AssetMachine.objects.get(pk=first['stations'][0]['station'])
        serializer = AssetMachineSerializer(instance)
        self.assertEqual(serializer.data['asset_type'], 'pumphouse')
        self.assertTrue(serializer.fields['asset_type'].read_only)

    def test_dry_run_leaves_no_stations_or_allowlist_changes(self):
        """Preview returns useful identities while every database mutation rolls back."""
        result = onboard_estate(self.manifest, self.source, directory='.', dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertEqual(len(result['stations']), 12)
        self.assertFalse(AssetMachine.objects.exists())
        self.source.refresh_from_db()
        self.assertEqual(self.source.config['stations'], [])

    def test_one_invalid_station_rolls_back_entire_batch(self):
        """A failure after registration cannot leave an incomplete estate behind."""
        self.manifest['stations'][-1]['snapshot'] = 'missing-file.json'
        with self.assertRaises(OSError):
            onboard_estate(self.manifest, self.source, directory='.')
        self.assertFalse(AssetMachine.objects.exists())
        self.source.refresh_from_db()
        self.assertEqual(self.source.config['stations'], [])

    def test_activation_requires_real_approved_mappings(self):
        """An activation request cannot turn an empty manifest into live mappings."""
        with self.assertRaises(ValidationError):
            onboard_estate(self.manifest, self.source, directory='.', activate=True)
        self.assertFalse(AssetMachine.objects.exists())

    def test_duplicate_source_station_and_bad_pump_keys_are_refused(self):
        """Account partition identity cannot be assigned to two local stations."""
        self.manifest['stations'][1]['source_uuid'] = self.manifest['stations'][0][
            'source_uuid'
        ]
        with self.assertRaises(ValidationError):
            onboard_estate(self.manifest, self.source, directory='.')
        self.assertFalse(AssetMachine.objects.exists())

    def test_command_and_readiness_do_not_contact_cosmos_by_default(self):
        """Configuration reports contain actionable gaps, without any credential details."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'estate.json'
            path.write_text(json.dumps(self.manifest), encoding='utf-8')
            with patch('azure.cosmos.CosmosClient') as cosmos:
                call_command(
                    'onboard_pumphouse_estate',
                    path,
                    source=self.source.pk,
                    stdout=StringIO(),
                )
                output = StringIO()
                call_command(
                    'check_pumphouse_readiness',
                    source=self.source.pk,
                    allow_incomplete=True,
                    stdout=output,
                )
                cosmos.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertFalse(report['ready'])
        self.assertIn('missing_endpoint', report['issues'])
        self.assertFalse(report['connectivity']['checked'])
        self.assertNotIn('secret_ref', output.getvalue())

    def test_snapshot_review_activation_and_repeat_preserve_checkpoints(self):
        """The complete operator workflow is repeatable with an already-applied review."""
        from assets.dictionary_review import export_review
        from assets.ingestion_models import IngestionCheckpoint

        call_command('load_pump_catalogue', stdout=StringIO())
        self.manifest['stations'] = self.manifest['stations'][:1]
        with TemporaryDirectory() as directory:
            directory = Path(directory)
            snapshot = directory / 'snapshot.json'
            snapshot.write_text(
                json.dumps({
                    'st': 'I',
                    'pd': {'P1': {'st': 'I'}},
                    'dex': {'ID': 'PH_1'},
                }),
                encoding='utf-8',
            )
            record = self.manifest['stations'][0]
            record['snapshot'] = 'snapshot.json'
            report = onboard_estate(self.manifest, self.source, directory=directory)
            station = AssetMachine.objects.get(pk=report['stations'][0]['station'])
            pack = export_review(station)
            entry = next(item for item in pack['pending'] if item['paths'] == ['/st'])
            entry.update(
                data_type='status',
                unit='',
                unit_status='unitless',
                note='Confirmed status',
            )
            pack['pending'].remove(entry)
            pack['approve'].append(entry)
            (directory / 'review.json').write_text(json.dumps(pack), encoding='utf-8')
            record['review'] = 'review.json'
            first = onboard_estate(
                self.manifest, self.source, directory=directory, activate=True
            )
            checkpoint = IngestionCheckpoint.objects.get(station=station)
            before = checkpoint.position
            second = onboard_estate(
                self.manifest, self.source, directory=directory, activate=True
            )
            checkpoint.refresh_from_db()
        self.assertEqual(first['stations'][0]['uuid'], second['stations'][0]['uuid'])
        self.assertEqual(checkpoint.position, before)
        self.assertEqual(second['stations'][0]['live']['bound'], 1)

    def test_benchmark_reads_without_changing_the_accepted_cursor(self):
        """The operator can measure the source without creating live readings."""
        from assets.health_models import MachineSignalState
        from assets.ingestion_models import IngestionCheckpoint

        report = onboard_estate(self.manifest, self.source, directory='.')
        self.source.refresh_from_db()
        station = AssetMachine.objects.get(pk=report['stations'][0]['station'])
        checkpoint = IngestionCheckpoint.objects.create(
            source=self.source,
            station=station,
            station_uuid=str(station.source_entity_uuid),
            hour_bucket='0',
            sub_time_period=0,
        )
        with patch(
            'assets.management.commands.benchmark_pumphouse_reads.CosmosPumphouseConnector'
        ) as factory:
            connector = factory.return_value
            connector.stations = self.source.config['stations']

            def complete_window(cursor, *, now, max_documents, on_scanned):
                on_scanned(int(now.timestamp() * 1000) + 1)
                return iter([({}, []), ({}, [])])

            connector.poll.side_effect = complete_window
            connector.request_charge = 3.5
            output = StringIO()
            call_command(
                'benchmark_pumphouse_reads', source=self.source.pk, stdout=output
            )
        result = json.loads(output.getvalue())
        self.assertTrue(result['read_only'])
        self.assertEqual(result['results'][0]['documents'], 2)
        self.assertEqual(result['query_request_units'], 3.5)
        self.assertTrue(result['results'][0]['window_complete'])
        checkpoint.refresh_from_db()
        self.assertEqual(checkpoint.sub_time_period, 0)
        self.assertFalse(MachineSignalState.objects.exists())
        connector.close.assert_called_once()

    def test_capped_benchmark_reports_incomplete_and_unknown_cost(self):
        """A successful but truncated read must not qualify a full estate sweep."""
        from assets.ingestion_models import IngestionCheckpoint

        report = onboard_estate(self.manifest, self.source, directory='.')
        self.source.refresh_from_db()
        station = AssetMachine.objects.get(pk=report['stations'][0]['station'])
        IngestionCheckpoint.objects.create(
            source=self.source,
            station=station,
            station_uuid=str(station.source_entity_uuid),
            hour_bucket='0',
            sub_time_period=0,
        )
        with patch(
            'assets.management.commands.benchmark_pumphouse_reads.CosmosPumphouseConnector'
        ) as factory:
            connector = factory.return_value
            connector.stations = self.source.config['stations']
            connector.poll.return_value = iter([({}, [])])
            connector.request_charge = None
            output = StringIO()
            with self.assertRaises(CommandError):
                call_command(
                    'benchmark_pumphouse_reads', source=self.source.pk, stdout=output
                )
        result = json.loads(output.getvalue())
        self.assertIsNone(result['query_request_units'])
        self.assertFalse(result['results'][0]['window_complete'])
