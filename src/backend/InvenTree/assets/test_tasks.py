"""Regression coverage for station ownership, scheduling and scan progress."""

import uuid
from datetime import datetime, timedelta
from datetime import timezone as utc_timezone
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from assets.health_models import HealthSource, MachineSignalBinding, MachineSignalState
from assets.ingestion_models import IngestionCheckpoint
from assets.models import AssetMachine, Client
from assets.tasks import poll_cosmos_pumphouse_sources
from machine_health.connectors.base import get_connector
from machine_health.connectors.cosmos_pumphouse import (
    HOUR_MS,
    CosmosConfigError,
    CosmosPumphouseConnector,
)
from machine_health.services.ingestion import IngestionError, ingest_readings
from machine_health.services.trends import read_trend
from machine_health.tests.test_cosmos_pumphouse import (
    FakeContainer,
    HttpError,
    snapshot,
)


@override_settings(AIMMS_COSMOS_PUMPHOUSE_ENABLED=True)
class CosmosPollerTests(TestCase):
    """One source must safely serve stations that reuse every external tag."""

    def setUp(self):
        """Create an account source with explicitly registered local ownership."""
        self.tenant = Client.objects.create(name='Poller test', code='poller-test')
        self.source = HealthSource.objects.create(
            name='Shared Cosmos',
            connector_type='cosmos_pumphouse',
            source_type='scada',
            config={'stations': []},
        )
        self.base = 1752850800000
        self.horizon = datetime.fromtimestamp(
            (self.base + HOUR_MS - 1) / 1000, tz=utc_timezone.utc
        )
        self.documents = []

    def station(self, number):
        """Register a station with a repeated /sl binding and an unread sample."""
        station_uuid = str(uuid.uuid4())
        station = AssetMachine.objects.create(
            name=f'Station {number}',
            asset_type='pumphouse',
            client=self.tenant,
            source_namespace='klsw',
            source_entity_uuid=station_uuid,
        )
        binding = MachineSignalBinding.objects.create(
            machine=station,
            source=self.source,
            external_key='/sl',
            display_name='Level',
            unit='m',
        )
        checkpoint = IngestionCheckpoint.objects.create(
            source=self.source,
            station=station,
            station_uuid=station_uuid,
            hour_bucket=str(self.base - HOUR_MS),
            sub_time_period=self.base - 1,
        )
        self.source.config['stations'].append(station_uuid)
        self.source.save(update_fields=['config'])
        self.documents.append(snapshot(self.base, station=station_uuid, sl=number))
        return checkpoint, binding

    def connector(self, checkpoint, documents=None):
        """Use the real ingestion path with only Cosmos transport replaced."""
        connector = CosmosPumphouseConnector(
            self.source, station_uuid=checkpoint.station_uuid
        )
        connector._container = FakeContainer(
            self.documents if documents is None else documents
        )
        return connector

    @override_settings(AIMMS_COSMOS_PUMPHOUSE_ENABLED=False)
    def test_disabled_performs_no_database_or_network_work(self):
        """A disabled scheduled task never even constructs a credential or client."""
        with (
            patch('assets.tasks.CosmosPumphouseConnector') as connector,
            self.assertNumQueries(0),
        ):
            self.assertEqual(poll_cosmos_pumphouse_sources(), 0)
        connector.assert_not_called()

    def test_one_failed_station_does_not_stop_eleven_others(self):
        """Identical tags remain station scoped and AUTH stays on the failing station."""
        entries = [self.station(number) for number in range(12)]
        failed = entries[0][0].station_uuid

        def factory(source, **kwargs):
            connector = CosmosPumphouseConnector(source, **kwargs)
            error = HttpError(403) if kwargs['station_uuid'] == failed else None
            connector._container = FakeContainer(self.documents, error=error)
            return connector

        with patch('assets.tasks.CosmosPumphouseConnector', side_effect=factory):
            self.assertEqual(poll_cosmos_pumphouse_sources(), 12)

        for number, (checkpoint, binding) in enumerate(entries):
            checkpoint.refresh_from_db()
            self.assertIsNone(checkpoint.lease_until)
            self.assertIsNotNone(checkpoint.last_poll_at)
            if number == 0:
                self.assertEqual(checkpoint.last_error_code, 'AUTH')
                self.assertIsNone(checkpoint.last_success_at)
                self.assertFalse(
                    MachineSignalState.objects.filter(binding=binding).exists()
                )
            else:
                self.assertEqual(checkpoint.last_error_code, '')
                self.assertIsNotNone(checkpoint.last_success_at)
                state = MachineSignalState.objects.get(binding=binding)
                self.assertEqual(state.value['value'], number)
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_error_code, '')

    def test_budget_rotates_start_even_after_failure(self):
        """A station consuming the run does not get first place on the next sweep."""
        entries = [self.station(number) for number in range(3)]
        clock = [0.0]
        visited = []

        def ingest(checkpoint, **kwargs):
            visited.append(checkpoint.pk)
            clock[0] += 51
            raise HttpError(429)

        connector = Mock(ingest=Mock(side_effect=ingest), last_error_code='')
        with (
            patch('assets.tasks.CosmosPumphouseConnector', return_value=connector),
            patch('assets.tasks.time.monotonic', side_effect=lambda: clock[0]),
        ):
            for _ in range(3):
                clock[0] = 0
                self.assertEqual(poll_cosmos_pumphouse_sources(), 1)
        self.assertEqual(visited, [checkpoint.pk for checkpoint, _ in entries])

    def test_unexpired_lease_is_skipped_and_expired_lease_is_recovered(self):
        """Overlapping workers skip a station; a crashed worker cannot lock it forever."""
        checkpoint, _ = self.station(1)
        checkpoint.lease_until = timezone.now() + timedelta(seconds=120)
        checkpoint.save()
        with patch('assets.tasks.CosmosPumphouseConnector') as factory:
            self.assertEqual(poll_cosmos_pumphouse_sources(), 0)
            factory.assert_not_called()
            checkpoint.lease_until = timezone.now() - timedelta(seconds=1)
            checkpoint.save()
            factory.return_value.last_error_code = ''
            self.assertEqual(poll_cosmos_pumphouse_sources(), 1)

    def test_unlinked_checkpoint_fails_before_network_access(self):
        """A source UUID is insufficient to choose a local tenant and station."""
        checkpoint, _ = self.station(1)
        checkpoint.station = None
        checkpoint.save()
        connector = self.connector(checkpoint)
        with self.assertRaises(CosmosConfigError):
            connector.ingest(checkpoint)
        self.assertEqual(connector._container.calls, [])

    def test_scanning_empty_hours_eventually_reaches_new_data(self):
        """The accepted-sample position alone would repeat six empty hours forever."""
        checkpoint, binding = self.station(1)
        later = self.base + 20 * HOUR_MS
        documents = [snapshot(later, station=checkpoint.station_uuid, sl=42)]
        connector = self.connector(checkpoint, documents)
        horizon = datetime.fromtimestamp((later + 1000) / 1000, tz=utc_timezone.utc)
        for _ in range(5):
            connector.ingest(checkpoint, now=horizon)
        checkpoint.refresh_from_db()
        self.assertEqual(checkpoint.sub_time_period, later)
        self.assertEqual(
            MachineSignalState.objects.get(binding=binding).value['value'], 42
        )

    def test_scan_overlap_recovers_recent_delayed_document(self):
        """A document arriving in the five-minute overlap is still considered."""
        checkpoint, binding = self.station(1)
        horizon_ms = self.base + 240_000
        horizon = datetime.fromtimestamp(horizon_ms / 1000, tz=utc_timezone.utc)
        self.connector(checkpoint, []).ingest(checkpoint, now=horizon)
        delayed = snapshot(horizon_ms - 60_000, station=checkpoint.station_uuid, sl=19)
        self.connector(checkpoint, [delayed]).ingest(checkpoint, now=horizon)
        self.assertEqual(
            MachineSignalState.objects.get(binding=binding).value['value'], 19
        )

    def test_document_cap_does_not_mark_unread_range_scanned(self):
        """Stopping in a page preserves its remaining documents for the next run."""
        checkpoint, binding = self.station(1)
        self.documents.append(
            snapshot(self.base + 1000, station=checkpoint.station_uuid, sl=2)
        )
        connector = self.connector(checkpoint)
        self.assertEqual(
            connector.ingest(checkpoint, now=self.horizon, max_documents=1)[0], 1
        )
        self.assertIsNone(checkpoint.scan_until)
        self.assertEqual(
            connector.ingest(checkpoint, now=self.horizon, max_documents=1)[0], 1
        )
        self.assertEqual(
            MachineSignalState.objects.get(binding=binding).value['value'], 2
        )

    def test_poll_never_reads_past_its_horizon(self):
        """The current bucket's end is not permission to ingest future samples."""
        checkpoint, binding = self.station(1)
        self.documents.append(
            snapshot(self.base + 1000, station=checkpoint.station_uuid, sl=2)
        )
        horizon = datetime.fromtimestamp(self.base / 1000, tz=utc_timezone.utc)
        self.connector(checkpoint).ingest(checkpoint, now=horizon)
        self.assertEqual(
            MachineSignalState.objects.get(binding=binding).value['value'], 1
        )

    def test_failed_second_batch_rolls_back_first_batch(self):
        """The cache never exposes a partially applied snapshot."""
        checkpoint, binding = self.station(1)
        document = snapshot(
            self.base,
            station=checkpoint.station_uuid,
            dex={f'TAG{i}': i for i in range(700)},
        )
        calls = []

        def fail_second(source, readings, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise IngestionError('second batch failed')
            return ingest_readings(source, readings, **kwargs)

        with patch(
            'machine_health.services.ingestion.ingest_readings', side_effect=fail_second
        ):
            self.connector(checkpoint, [document]).ingest(checkpoint, now=self.horizon)
        self.assertEqual(len(calls), 2)
        self.assertFalse(MachineSignalState.objects.filter(binding=binding).exists())
        checkpoint.refresh_from_db()
        self.assertEqual(checkpoint.sub_time_period, self.base - 1)

    def test_cosmos_ingestion_requires_station_scope(self):
        """Other entry points cannot bypass station ownership."""
        self.station(1)
        with self.assertRaises(IngestionError):
            ingest_readings(
                self.source,
                [{'external_key': '/sl', 'value': 1, 'observed_at': timezone.now()}],
            )

    def test_scoped_latest_and_window_support_shared_source(self):
        """Reads select an explicit station when one account serves several."""
        first, _ = self.station(1)
        self.station(2)
        connector = self.connector(first)
        values = connector.read_window(
            '/sl', self.horizon - timedelta(hours=1), self.horizon
        )
        self.assertEqual([reading.value for reading in values], [1])
        with patch('machine_health.connectors.cosmos_pumphouse.datetime') as clock:
            clock.now.return_value = self.horizon
            latest = connector.read_latest(['/sl'])
        self.assertEqual([reading.value for reading in latest], [1])

    def test_cosmos_default_freshness_preserves_explicit_configuration(self):
        """Only new Cosmos sources default to 300 seconds."""
        self.assertEqual(self.source.freshness_threshold_seconds, 300)
        self.assertEqual(
            HealthSource(connector_type='webhook').freshness_threshold_seconds, 900
        )
        self.assertEqual(
            HealthSource(
                connector_type='cosmos_pumphouse', freshness_threshold_seconds=900
            ).freshness_threshold_seconds,
            900,
        )

    def test_trend_resolves_station_from_authorized_machine(self):
        """The trend service supplies station identity rather than trusting a tag."""
        checkpoint, binding = self.station(1)
        self.station(2)
        with patch.object(
            CosmosPumphouseConnector,
            'container',
            return_value=FakeContainer(self.documents),
        ):
            result = read_trend(
                checkpoint.station,
                binding_id=binding.pk,
                start=self.horizon - timedelta(hours=1),
                end=self.horizon,
            )
        self.assertTrue(result['available'])
        self.assertEqual([item['value'] for item in result['samples']], [1])
        checkpoint.station = None
        checkpoint.save()
        self.assertIsNone(get_connector(self.source, machine=binding.machine))

    def test_time_budget_stops_before_next_document_and_resumes(self):
        """An expired time slice preserves progress without reporting a network fault."""
        checkpoint, binding = self.station(1)
        self.documents.append(
            snapshot(self.base + 1000, station=checkpoint.station_uuid, sl=2)
        )
        connector = self.connector(checkpoint)
        connector.deadline = 1.0
        clock = [0.0]
        query = connector._container.query_items

        def slow_query(**kwargs):
            for index, row in enumerate(query(**kwargs)):
                if index:
                    clock[0] = 2.0
                yield row

        connector._container.query_items = slow_query
        with patch(
            'machine_health.connectors.cosmos_pumphouse.time.monotonic',
            side_effect=lambda: clock[0],
        ):
            self.assertEqual(connector.ingest(checkpoint, now=self.horizon)[0], 1)
        self.assertEqual(connector.last_error_code, '')
        self.assertEqual(checkpoint.sub_time_period, self.base)
        self.assertEqual(
            self.connector(checkpoint).ingest(checkpoint, now=self.horizon)[0], 1
        )
        self.assertEqual(
            MachineSignalState.objects.get(binding=binding).value['value'], 2
        )

    def test_overlapping_sweep_cannot_claim_in_flight_station(self):
        """A second scheduler invocation must not ingest a station already leased."""
        self.station(1)

        def ingest(*args, **kwargs):
            self.assertEqual(poll_cosmos_pumphouse_sources(), 0)

        connector = Mock(last_error_code='', ingest=Mock(side_effect=ingest))
        with patch('assets.tasks.CosmosPumphouseConnector', return_value=connector):
            self.assertEqual(poll_cosmos_pumphouse_sources(), 1)
        connector.ingest.assert_called_once()

    def test_inactive_source_performs_no_network_work(self):
        """Deactivating an account stops all its stations."""
        self.station(1)
        self.source.active = False
        self.source.save()
        with patch('assets.tasks.CosmosPumphouseConnector') as connector:
            self.assertEqual(poll_cosmos_pumphouse_sources(), 0)
        connector.assert_not_called()

    def test_source_configuration_cannot_exceed_document_budget(self):
        """A configured cap is honoured below, and clamped above, the worker cap."""
        checkpoint, _ = self.station(1)
        connector = Mock(last_error_code='')
        with patch('assets.tasks.CosmosPumphouseConnector', return_value=connector):
            for configured, expected in [(2, 2), (10000, 200)]:
                self.source.config['max_docs_per_poll'] = configured
                self.source.save()
                poll_cosmos_pumphouse_sources()
                self.assertEqual(
                    connector.ingest.call_args.kwargs['max_documents'], expected
                )
        checkpoint.refresh_from_db()
        self.assertEqual(checkpoint.last_error_code, '')
