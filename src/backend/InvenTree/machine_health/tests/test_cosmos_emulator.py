"""Opt-in SDK integration against a disposable local Cosmos emulator.

Set INVENTREE_TEST_COSMOS_EMULATOR=1 and COSMOS_EMULATOR_KEY to run. The test
creates and removes its own uniquely named database; live endpoints are refused.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest import skipUnless
from urllib.parse import urlsplit
from uuid import uuid4

from django.test import TestCase, override_settings, tag

from assets.health_models import HealthSource, MachineSignalBinding, MachineSignalState
from assets.ingestion_models import IngestionCheckpoint
from assets.models import AssetMachine, Client
from assets.tasks import poll_cosmos_pumphouse_sources
from machine_health.connectors.cosmos_pumphouse import (
    HOUR_MS,
    CosmosPumphouseConnector,
    bucket_of,
    to_epoch_ms,
)

from .test_cosmos_pumphouse import snapshot


@tag('cosmos_emulator')
@skipUnless(
    os.environ.get('INVENTREE_TEST_COSMOS_EMULATOR') == '1',
    'Local Cosmos emulator integration is opt-in.',
)
@override_settings(AIMMS_COSMOS_PUMPHOUSE_ENABLED=True)
class CosmosEmulatorTests(TestCase):
    """Prove partitioned reads, capped resume, scheduler ingestion and replay."""

    @classmethod
    def setUpClass(cls):
        """Provision only a disposable loopback-emulator database."""
        super().setUpClass()
        from azure.cosmos import CosmosClient, PartitionKey

        cls.endpoint = os.environ.get(
            'INVENTREE_TEST_COSMOS_ENDPOINT', 'http://localhost:8081'
        )
        parsed = urlsplit(cls.endpoint)
        if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1'}:
            raise ValueError('Integration provisioning requires a local HTTP emulator.')
        key = os.environ['COSMOS_EMULATOR_KEY']
        cls.sdk = CosmosClient(
            cls.endpoint,
            credential=key,
            connection_timeout=5,
            read_timeout=5,
            retry_total=0,
        )
        cls.addClassCleanup(cls.sdk.close)
        cls.database_name = f'iot-integration-{uuid4().hex}'
        database = cls.sdk.create_database(cls.database_name)
        cls.addClassCleanup(cls.sdk.delete_database, cls.database_name)
        root = Path(__file__).resolve().parents[5]
        definition = json.loads(
            (
                root / 'contrib/cosmos/schema/pumphouse_readings.container.json'
            ).read_text()
        )
        cls.container = database.create_container(
            id=definition['id'],
            partition_key=PartitionKey(
                path=definition['partitionKey']['paths'], kind='MultiHash', version=2
            ),
            default_ttl=definition['defaultTtl'],
            indexing_policy=definition['indexingPolicy'],
        )

    def test_seed_poll_history_and_replay_across_hours(self):
        """Use the real SDK transport through both connector and scheduled task."""
        client = Client.objects.create(name='Emulator integration', code='cosmos-e2e')
        station_uuid = str(uuid4())
        station = AssetMachine.objects.create(
            name='Integration station',
            client=client,
            asset_type='pumphouse',
            source_entity_uuid=station_uuid,
            source_namespace='cosmos-integration',
            source_key='PH_TEST',
        )
        source = HealthSource.objects.create(
            name='Local emulator',
            client=client,
            connector_type='cosmos_pumphouse',
            secret_ref='COSMOS_EMULATOR_KEY',
            config={
                'endpoint': self.endpoint,
                'database': self.database_name,
                'readings_container': self.container.id,
                'stations': [station_uuid],
            },
        )
        binding = MachineSignalBinding.objects.create(
            machine=station, source=source, external_key='/sl', display_name='Level'
        )
        # Both documents are in the past even if CI starts at an hour boundary.
        boundary = bucket_of(to_epoch_ms(datetime.now(timezone.utc))) - HOUR_MS
        first, second = boundary - 5000, boundary + 5000
        for timestamp, level in [(first, 131.5), (second, 132.0)]:
            self.container.create_item(
                snapshot(timestamp, station=station_uuid, sl=level)
            )
        # Identical timestamps/IDs in a different station partition must not leak.
        self.container.create_item(snapshot(second, station=str(uuid4()), sl=999.0))
        checkpoint = IngestionCheckpoint.objects.create(
            source=source,
            station=station,
            station_uuid=station_uuid,
            hour_bucket=str(bucket_of(first - 1)),
            sub_time_period=first - 1,
        )
        connector = CosmosPumphouseConnector(source, station_uuid=station_uuid)
        try:
            self.assertEqual(connector.ingest(checkpoint, max_documents=1), (1, 1))
            checkpoint.refresh_from_db()
            self.assertEqual(checkpoint.sub_time_period, first)
            self.assertEqual(connector.last_error_code, '')
            start = datetime.fromtimestamp((first - 1) / 1000, tz=timezone.utc)
            end = datetime.fromtimestamp((second + 1) / 1000, tz=timezone.utc)
            history = connector.read_window('/sl', start, end, max_samples=10)
            self.assertEqual([reading.value for reading in history], [131.5, 132.0])
            self.assertEqual(
                len(connector.read_window('/sl', start, end, max_samples=1)), 1
            )
        finally:
            connector.close()

        self.assertEqual(poll_cosmos_pumphouse_sources(), 1)
        checkpoint.refresh_from_db()
        self.assertEqual(checkpoint.last_error_code, '')
        self.assertEqual(checkpoint.sub_time_period, second)
        state = MachineSignalState.objects.get(binding=binding)
        self.assertEqual(state.value['value'], 132.0)
        received_at = state.received_at
        self.assertEqual(poll_cosmos_pumphouse_sources(), 1)
        checkpoint.refresh_from_db()
        state.refresh_from_db()
        self.assertEqual(checkpoint.sub_time_period, second)
        self.assertEqual(state.received_at, received_at)
        self.assertEqual(MachineSignalState.objects.count(), 1)
