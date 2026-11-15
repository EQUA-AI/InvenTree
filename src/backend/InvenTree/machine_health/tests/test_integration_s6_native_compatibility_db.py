"""Native partition/checkpoint controls with actual ORM writes in isolated fixtures."""

from integration_state.tests.assertions import assert_literal_bool

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from assets.health_models import MachineSignalBinding, MachineSignalState
from assets.ingestion_models import IngestionCheckpoint
from machine_health.connectors.cosmos_pumphouse import (
    HOUR_MS,
    CosmosPumphouseConnector,
    bucket_of,
    to_epoch_ms,
)
from machine_health.tests.test_cosmos_pumphouse import FakeContainer, snapshot
from machine_health.tests.test_integration_s6_reader_db import S6RequestFixture


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s6-native-fixture',
)
class S6NativeCompatibilityTests(S6RequestFixture, TestCase):
    """Existing native code is unchanged; only the source container is fake."""

    def test_source_read_does_not_write_latest_state_checkpoint_or_source_status(self):
        """Source-local history is observational, not a new ingestion path."""
        before_states = list(MachineSignalState.objects.values())
        before_checkpoints = list(IngestionCheckpoint.objects.values())
        before_success = self.source.last_success_at
        self.assertEqual(self.read().state, 'available')
        self.assertEqual(list(MachineSignalState.objects.values()), before_states)
        self.assertEqual(list(IngestionCheckpoint.objects.values()), before_checkpoints)
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_success_at, before_success)

    def test_native_cross_hour_query_names_full_partition_and_original_instants(self):
        """Actual connector requests stay clipped, parameterized and station-local."""
        start = datetime(2025, 1, 1, 0, 59, 59, tzinfo=timezone.utc)
        end = start + timedelta(seconds=2)
        station = str(self.station.source_entity_uuid)
        moments = (start, start + timedelta(seconds=1))
        container = FakeContainer([
            *(snapshot(to_epoch_ms(moment), station=station) for moment in moments),
            snapshot(to_epoch_ms(start), station='unrelated-synthetic-station'),
        ])
        connector = CosmosPumphouseConnector(self.source)
        connector._container = container
        values = connector.read_window('/sl', start, end, max_samples=10)
        self.assertEqual([value.observed_at for value in values], list(moments))
        self.assertEqual(len(container.calls), 2)
        for call in container.calls:
            parameters = {item['name']: item['value'] for item in call['parameters']}
            bucket = int(parameters['@bucket'])
            self.assertEqual(call['partition_key'], [station, str(bucket)])
            assert_literal_bool(self, call['enable_cross_partition_query'], False)
            self.assertEqual(parameters['@station'], station)
            self.assertGreaterEqual(parameters['@from_ts'], bucket)
            self.assertLessEqual(parameters['@to_ts'], bucket + HOUR_MS)
            self.assertNotIn(station, call['query'])

    def test_later_real_batch_failure_rolls_back_snapshot_and_retry_is_whole(self):
        """Actual first-batch state cannot survive a failed later snapshot batch."""
        from machine_health.services import ingestion

        station = str(self.station.source_entity_uuid)
        tick = to_epoch_ms(datetime(2025, 1, 2, tzinfo=timezone.utc))
        for key in ('/sl', '/dex/KEY_699'):
            MachineSignalBinding.objects.create(
                machine=self.station,
                source=self.source,
                external_key=key,
                display_name=key,
                signal_kind='level',
                unit='m',
            )
        document = snapshot(
            tick, station=station, dex={f'KEY_{index}': index for index in range(700)}
        )
        checkpoint = IngestionCheckpoint.objects.create(
            source=self.source,
            station=self.station,
            station_uuid=station,
            hour_bucket=str(bucket_of(tick - 1)),
            sub_time_period=tick - 1,
        )
        connector = CosmosPumphouseConnector(self.source)
        connector._container = FakeContainer([document])
        real = ingestion.ingest_readings
        batches = []

        def fail_second(*args, **kwargs):
            batches.append(len(args[1]))
            if len(batches) == 2:
                raise ingestion.IngestionError('synthetic later-batch failure')
            return real(*args, **kwargs)

        with patch.object(ingestion, 'ingest_readings', new=fail_second):
            documents, accepted = connector.ingest(
                checkpoint, now=datetime(2025, 1, 2, 0, 1, tzinfo=timezone.utc)
            )
        self.assertEqual(documents, 0)
        self.assertEqual(accepted, 0)
        self.assertEqual(len(batches), 2)
        self.assertEqual(MachineSignalState.objects.count(), 0)
        checkpoint.refresh_from_db()
        self.assertEqual(checkpoint.sub_time_period, tick - 1)
        self.assertEqual(connector.applied_machine_ids, set())
        documents, accepted = connector.ingest(
            checkpoint, now=datetime(2025, 1, 2, 0, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(documents, 1)
        self.assertEqual(accepted, 2)
        self.assertEqual(MachineSignalState.objects.count(), 2)
        checkpoint.refresh_from_db()
        self.assertEqual(checkpoint.sub_time_period, tick)
        self.assertEqual(connector.applied_machine_ids, {self.station.pk})
        self.assertEqual(
            connector.ingest(
                checkpoint, now=datetime(2025, 1, 2, 0, 1, tzinfo=timezone.utc)
            ),
            (0, 0),
        )

    def test_late_beyond_lookback_can_exist_in_history_without_poll_progress(self):
        """A forward scan/checkpoint is not proof that old source history is empty."""
        from machine_health.connectors.cosmos_pumphouse import POLL_LOOKBACK_MS

        station = str(self.station.source_entity_uuid)
        tick = to_epoch_ms(datetime(2025, 1, 2, tzinfo=timezone.utc))
        scanned_until = tick + 600000
        old = tick + 60000
        recent = scanned_until - POLL_LOOKBACK_MS + 1
        connector = CosmosPumphouseConnector(self.source)
        connector._container = FakeContainer([
            snapshot(old, station=station),
            snapshot(recent, station=station),
        ])
        checkpoint = SimpleNamespace(
            station_uuid=station, sub_time_period=tick - 1, scan_until=scanned_until
        )
        now = datetime(2025, 1, 2, 0, 15, tzinfo=timezone.utc)
        polled = list(connector.poll(checkpoint, now=now))
        self.assertEqual(
            [document['sub_time_period'] for document, _ in polled], [recent]
        )
        start = datetime(2025, 1, 2, tzinfo=timezone.utc)
        history = connector.read_window('/sl', start, now, max_samples=10)
        self.assertEqual(
            [to_epoch_ms(reading.observed_at) for reading in history], [old, recent]
        )
