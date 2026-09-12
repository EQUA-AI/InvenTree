"""Tests for the Cosmos pumphouse connector.

The Azure SDK is never touched: a fake container stands in for it, and it records
every call so the tests can assert on the *shape of the request* - full partition
key, parameterised values, cross-partition disabled - and not merely on the rows
that come back. Those properties are the ones that cost money and leak data when
they regress, and they are invisible in a test that only checks return values.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest import mock

from django.test import SimpleTestCase, TestCase

from assets.health_models import MachineSignalBinding
from assets.ingestion_models import IngestionCheckpoint
from machine_health.connectors.base import get_connector
from machine_health.connectors.cosmos_pumphouse import (
    HOUR_MS,
    MAX_BUCKETS_PER_POLL,
    PAGE_SIZE,
    CosmosConfigError,
    CosmosPumphouseConnector,
    _classify,
    bucket_of,
    to_epoch_ms,
)

from .fixtures import HealthEnvMixin

STATION = 'bafc976f-1ccc-4a91-aaa6-c3eac2470d36'
ACCOUNT = 'https://epconchatcosmos9d6b.documents.azure.com'
EMULATOR = 'https://localhost:8081'


def snapshot(sub_time_period: int, *, station: str = STATION, sl=132.0, st='I', dex=None):
    """Build a document shaped exactly as the seeder writes one."""
    payload = {
        'sr': 'SCADA',
        'st': st,
        'pc': 0.0,
        'sl': sl,
        'dv': 0.0,
        'pmw': 0.0,
        'pmvar': 0.0,
        'pd': {'P1': {'st': st, 'dv': 1.5, 'pmw': 0.0, 'pmvar': 0.0}},
        'egt': sub_time_period,
        'ext': sub_time_period + 300_000,
        'dsc': 24,
        'dex': dex or {},
    }
    raw = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
    bucket = bucket_of(sub_time_period)
    return {
        'id': str(sub_time_period),
        'station_uuid': station,
        'hour_bucket': str(bucket),
        'sub_time_period': sub_time_period,
        'month': datetime.fromtimestamp(bucket / 1000, tz=timezone.utc).strftime(
            '%Y%m'
        ),
        **payload,
        'data1_raw': raw,
    }


class FakeContainer:
    """A stand-in that answers the two queries the connector issues."""

    def __init__(self, documents=None, *, error=None):
        """Hold the rows to serve, or the failure to raise instead."""
        self.documents = list(documents or [])
        self.error = error
        self.calls: list[dict] = []
        self.reads = 0

    def read(self):
        """Container properties, as ``check()`` asks for."""
        self.reads += 1
        if self.error:
            raise self.error
        return {'id': 'pumphouse_readings'}

    def query_items(self, **kwargs):
        """Record the request, then serve rows matching its parameters."""
        self.calls.append(kwargs)
        if self.error:
            raise self.error

        params = {p['name']: p['value'] for p in kwargs.get('parameters', [])}
        rows = [
            document
            for document in self.documents
            if document['station_uuid'] == params['@station']
            and document['hour_bucket'] == params['@bucket']
        ]

        if 'TOP 1' in kwargs['query']:
            rows.sort(key=lambda d: d['sub_time_period'], reverse=True)
            return iter(rows[:1])

        rows = [
            document
            for document in rows
            if params['@from_ts'] <= document['sub_time_period'] < params['@to_ts']
        ]
        rows.sort(key=lambda d: d['sub_time_period'])
        return iter(rows)


class StubSource:
    """The few source attributes a connector reads, without a database row."""

    pk = 1

    def __init__(self, config=None, secret_ref=''):
        """Carry non-secret config and a secret reference only."""
        self.config = config or {}
        self.secret_ref = secret_ref


def connector_for(documents=None, *, error=None, config=None, secret_ref=''):
    """Return a connector wired to a fake container."""
    settings = {
        'endpoint': ACCOUNT,
        'database': 'aimms',
        'readings_container': 'pumphouse_readings',
        'stations': [STATION],
    }
    settings.update(config or {})
    connector = CosmosPumphouseConnector(StubSource(settings, secret_ref))
    connector._container = FakeContainer(documents, error=error)
    return connector


class HttpError(Exception):
    """Minimal stand-in for ``CosmosHttpResponseError``."""

    def __init__(self, status_code, message='endpoint=https://secret/ token=abc'):
        """Carry a status code and a message that must never be surfaced."""
        super().__init__(message)
        self.status_code = status_code


class RegistrationTests(TestCase):
    """The adapter must be reachable through the registry."""

    def test_source_resolves_to_this_connector(self):
        """A source naming the key gets this adapter, not a default."""
        env = HealthEnvMixin()
        env.build_health_env()
        env.source.connector_type = 'cosmos_pumphouse'
        env.source.save(update_fields=['connector_type'])

        self.assertIsInstance(get_connector(env.source), CosmosPumphouseConnector)

    def test_unknown_connector_type_resolves_to_nothing(self):
        """An unregistered key stays unconfigured rather than falling back."""
        env = HealthEnvMixin()
        env.build_health_env()
        env.source.connector_type = 'not_a_connector'
        env.source.save(update_fields=['connector_type'])

        self.assertIsNone(get_connector(env.source))


class TimeTests(SimpleTestCase):
    """Bucket arithmetic, where an off-by-one hides a whole hour of data."""

    def test_bucket_is_the_hour_a_sample_falls_in(self):
        """A sample maps to the hour that contains it."""
        self.assertEqual(bucket_of(1752853013703), 1752850800000)

    def test_a_sample_on_the_boundary_starts_the_next_bucket(self):
        """The bucket is half-open, so the boundary belongs to the next hour."""
        self.assertEqual(bucket_of(1752850800000), 1752850800000)
        self.assertEqual(bucket_of(1752850800000 + HOUR_MS), 1752850800000 + HOUR_MS)

    def test_epoch_conversion_truncates_downward(self):
        """Rounding up could push a sample past a boundary and hide it."""
        moment = datetime(2025, 7, 18, 15, 36, 53, 703999, tzinfo=timezone.utc)
        self.assertEqual(to_epoch_ms(moment), 1752853013703)

    def test_a_naive_datetime_is_read_as_utc(self):
        """UTC throughout (D4); a naive value is not silently local."""
        naive = datetime(2025, 7, 18, 15, 0, 0)
        aware = datetime(2025, 7, 18, 15, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(to_epoch_ms(naive), to_epoch_ms(aware))


class PartitionKeyTests(SimpleTestCase):
    """Every request must name a full partition key."""

    def test_hierarchical_key_is_station_then_hour(self):
        """The default container is partitioned on both, in that order."""
        connector = connector_for()
        self.assertEqual(
            connector._partition_key(STATION, '1752850800000'),
            [STATION, '1752850800000'],
        )

    def test_composite_mode_joins_the_components(self):
        """A single-path container is supported through config, not a code change."""
        connector = connector_for(config={'partition_key_mode': 'composite'})
        self.assertEqual(
            connector._partition_key(STATION, '1752850800000'),
            f'{STATION}|1752850800000',
        )

    def test_a_missing_hour_bucket_issues_no_query(self):
        """A partial key would fan the query out over every hour of a station."""
        connector = connector_for([snapshot(1752853013703)])

        with self.assertRaises(CosmosConfigError):
            connector.latest_document(STATION, '')

        self.assertEqual(connector._container.calls, [])

    def test_a_missing_station_issues_no_query(self):
        """The same, across every station in the account."""
        connector = connector_for([snapshot(1752853013703)])

        with self.assertRaises(CosmosConfigError):
            connector.latest_document('', '1752850800000')

        self.assertEqual(connector._container.calls, [])


class QueryShapeTests(SimpleTestCase):
    """Properties of the request that no return value would reveal."""

    def setUp(self):
        """Run one slice query and keep the recorded call."""
        self.connector = connector_for([snapshot(1752853013703)])
        list(
            self.connector.documents_in_bucket(
                STATION, 1752850800000, 1752850800000, 1752850800000 + HOUR_MS
            )
        )
        self.call = self.connector._container.calls[0]

    def test_cross_partition_query_is_explicitly_disabled(self):
        """Off by default is not enough; it is stated on every call."""
        self.assertFalse(self.call['enable_cross_partition_query'])

    def test_the_full_partition_key_is_passed(self):
        """Scope is set by key, not inferred from the WHERE clause."""
        self.assertEqual(self.call['partition_key'], [STATION, '1752850800000'])

    def test_values_travel_as_parameters_not_in_the_query_text(self):
        """Interpolating a station uuid into SQL is how injection arrives."""
        self.assertNotIn(STATION, self.call['query'])
        self.assertIn('@station', self.call['query'])
        names = {p['name'] for p in self.call['parameters']}
        self.assertEqual(names, {'@station', '@bucket', '@from_ts', '@to_ts'})

    def test_results_are_paged(self):
        """A slow account cannot return an unbounded response in one trip."""
        self.assertEqual(self.call['max_item_count'], PAGE_SIZE)


class CheckTests(SimpleTestCase):
    """``check()`` returns a code, and only a code."""

    def test_a_reachable_container_is_ok(self):
        """Success reads properties, not documents."""
        connector = connector_for()
        self.assertEqual(connector.check(), (True, 'OK'))
        self.assertEqual(connector._container.reads, 1)
        self.assertEqual(connector._container.calls, [])

    def test_status_codes_map_to_the_fixed_vocabulary(self):
        """An operator can tell a permissions problem from a throttle."""
        for status, expected in (
            (401, 'AUTH'),
            (403, 'AUTH'),
            (404, 'NOT_FOUND'),
            (429, 'THROTTLED'),
            (500, 'NETWORK'),
        ):
            with self.subTest(status=status):
                connector = connector_for(error=HttpError(status))
                self.assertEqual(connector.check(), (False, expected))

    def test_a_credential_failure_is_auth_not_network(self):
        """Nothing was sent, so calling it a network fault misdirects the fix."""

        class ClientAuthenticationError(Exception):
            pass

        connector = connector_for(error=ClientAuthenticationError('no token'))
        self.assertEqual(connector.check(), (False, 'AUTH'))

    def test_missing_configuration_is_reported_as_config(self):
        """No request is possible; retrying will not help."""
        connector = CosmosPumphouseConnector(StubSource({'endpoint': ACCOUNT}))
        self.assertEqual(connector.check(), (False, 'CONFIG'))

    def test_the_provider_message_is_never_returned(self):
        """Provider text carries endpoints and sometimes tokens."""
        connector = connector_for(error=HttpError(403, 'endpoint=https://x token=SEC'))
        _ok, code = connector.check()

        self.assertNotIn('SEC', code)
        self.assertNotIn('endpoint', code)
        self.assertIn(code, {'AUTH'})

    def test_classification_covers_an_exception_with_no_status(self):
        """An unknown failure is still reduced to one of the codes."""
        self.assertEqual(_classify(ValueError('boom')), 'NETWORK')


class StationTests(SimpleTestCase):
    """One source, one station."""

    def test_no_station_configured_is_refused(self):
        """Reading "whatever is in the container" is not a defined request."""
        connector = connector_for(config={'stations': []})
        with self.assertRaises(CosmosConfigError):
            _ = connector.station

    def test_several_stations_are_refused(self):
        """'/sl' does not say which station, so two would overwrite each other."""
        connector = connector_for(config={'stations': [STATION, 'other-uuid']})
        with self.assertRaises(CosmosConfigError):
            _ = connector.station

    def test_a_single_station_string_is_accepted(self):
        """Config written as a bare string still means one station."""
        connector = connector_for(config={'stations': STATION})
        self.assertEqual(connector.station, STATION)


class ReadLatestTests(SimpleTestCase):
    """Current values, including across the hour boundary."""

    def setUp(self):
        """Anchor on the real current hour, as ``read_latest`` does."""
        self.now_ms = to_epoch_ms(datetime.now(tz=timezone.utc))
        self.current = bucket_of(self.now_ms)
        self.previous = self.current - HOUR_MS

    def test_the_newest_snapshot_of_the_current_hour_wins(self):
        """Two samples in an hour: the later one is current."""
        connector = connector_for([
            snapshot(self.current + 1000, sl=100.0),
            snapshot(self.current + 2000, sl=200.0),
        ])
        values = {r.external_key: r.value for r in connector.read_latest()}
        self.assertEqual(values['/sl'], 200.0)

    def test_an_empty_current_hour_falls_back_to_the_previous_one(self):
        """At 10:00:01 the current bucket is empty but the plant is not."""
        connector = connector_for([snapshot(self.previous + 60_000, sl=131.5)])
        values = {r.external_key: r.value for r in connector.read_latest()}
        self.assertEqual(values['/sl'], 131.5)

    def test_the_fallback_keeps_the_sources_own_timestamp(self):
        """A recovered reading must look as old as it is."""
        sample = self.previous + 60_000
        connector = connector_for([snapshot(sample)])
        reading = next(
            r for r in connector.read_latest() if r.external_key == '/sl'
        )
        self.assertEqual(to_epoch_ms(reading.observed_at), sample)

    def test_nothing_in_either_hour_returns_nothing(self):
        """Silence is reported as silence, not as a stale value."""
        connector = connector_for([snapshot(self.previous - 5 * HOUR_MS)])
        self.assertEqual(connector.read_latest(), [])

    def test_requested_keys_are_filtered(self):
        """A caller asking for one tag does not receive the whole snapshot."""
        connector = connector_for([snapshot(self.current + 1000)])
        readings = connector.read_latest(['/sl', '/pd/P1/dv'])
        self.assertEqual(
            {r.external_key for r in readings}, {'/sl', '/pd/P1/dv'}
        )

    def test_pump_and_station_keys_come_from_the_same_snapshot(self):
        """Position decides ownership: '/sl' is the station, '/pd/P1/dv' a pump."""
        connector = connector_for([snapshot(self.current + 1000)])
        values = {r.external_key: r.value for r in connector.read_latest()}
        self.assertEqual(values['/sl'], 132.0)
        self.assertEqual(values['/pd/P1/dv'], 1.5)


class ReadWindowTests(SimpleTestCase):
    """Bounded history for one tag."""

    def setUp(self):
        """Nine samples spanning three hour buckets."""
        self.base = 1752850800000
        self.documents = [
            snapshot(self.base + hour * HOUR_MS + index * 60_000, sl=float(index))
            for hour in range(3)
            for index in range(3)
        ]
        self.start = datetime.fromtimestamp(self.base / 1000, tz=timezone.utc)
        self.end = self.start + timedelta(hours=3)

    def test_every_bucket_in_the_window_is_walked(self):
        """A window spanning three hours reads three partitions."""
        connector = connector_for(self.documents)
        readings = connector.read_window('/sl', self.start, self.end)

        self.assertEqual(len(readings), 9)
        self.assertEqual(len(connector._container.calls), 3)

    def test_only_the_requested_key_is_returned(self):
        """A sparkline for one signal does not carry the other 700."""
        connector = connector_for(self.documents)
        readings = connector.read_window('/pd/P1/dv', self.start, self.end)

        self.assertEqual({r.external_key for r in readings}, {'/pd/P1/dv'})

    def test_the_sample_cap_stops_the_read_early(self):
        """The cap bounds work done, not just rows returned."""
        connector = connector_for(self.documents)
        readings = connector.read_window('/sl', self.start, self.end, max_samples=4)

        self.assertEqual(len(readings), 4)
        self.assertLess(len(connector._container.calls), 3)

    def test_samples_come_back_in_source_order(self):
        """Oldest first, with the source's own timestamps."""
        connector = connector_for(self.documents)
        readings = connector.read_window('/sl', self.start, self.end)
        moments = [r.observed_at for r in readings]

        self.assertEqual(moments, sorted(moments))

    def test_a_reversed_window_is_refused(self):
        """``bounded_window`` guards this before any request is built."""
        connector = connector_for(self.documents)
        with self.assertRaises(ValueError):
            connector.read_window('/sl', self.end, self.start)
        self.assertEqual(connector._container.calls, [])


class CredentialTests(SimpleTestCase):
    """Entra ID against a real account; a key only against the emulator."""

    def test_a_key_is_refused_against_a_real_account(self):
        """Read-only must be enforced by the Data Reader role, not by us."""
        connector = connector_for(secret_ref='COSMOS_KEY')
        with self.assertRaises(CosmosConfigError):
            connector._credential()

    def test_the_emulator_key_comes_from_the_environment(self):
        """Never from the database, an API response or the config blob."""
        connector = connector_for(
            config={'endpoint': EMULATOR}, secret_ref='COSMOS_EMULATOR_KEY'
        )
        with mock.patch.dict('os.environ', {'COSMOS_EMULATOR_KEY': 'local-key'}):
            self.assertEqual(connector._credential(), 'local-key')

    def test_an_unset_emulator_variable_fails_closed(self):
        """No credential means no request, not an anonymous one."""
        connector = connector_for(
            config={'endpoint': EMULATOR}, secret_ref='COSMOS_EMULATOR_KEY'
        )
        with mock.patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(CosmosConfigError):
                connector._credential()


class PollTests(SimpleTestCase):
    """Reading forward from a stored position."""

    class Checkpoint:
        """The two fields ``poll`` reads from a checkpoint."""

        def __init__(self, station_uuid, hour_bucket, sub_time_period):
            """Hold a position only."""
            self.station_uuid = station_uuid
            self.hour_bucket = str(hour_bucket)
            self.sub_time_period = sub_time_period

    def setUp(self):
        """A bucket with three samples and a checkpoint on the first."""
        self.base = 1752850800000
        self.samples = [self.base + index * 60_000 for index in range(3)]
        self.documents = [snapshot(sample) for sample in self.samples]
        self.now = datetime.fromtimestamp(
            (self.base + HOUR_MS - 1) / 1000, tz=timezone.utc
        )

    def test_polling_resumes_strictly_after_the_checkpoint(self):
        """Re-reading the recorded sample would re-present accepted data."""
        checkpoint = self.Checkpoint(STATION, self.base, self.samples[0])
        connector = connector_for(self.documents)

        produced = [doc for doc, _ in connector.poll(checkpoint, now=self.now)]

        self.assertEqual(
            [d['sub_time_period'] for d in produced], self.samples[1:]
        )

    def test_the_query_starts_one_millisecond_past_the_checkpoint(self):
        """The bound is explicit, not left to a >= that would re-read."""
        checkpoint = self.Checkpoint(STATION, self.base, self.samples[0])
        connector = connector_for(self.documents)
        list(connector.poll(checkpoint, now=self.now))

        params = {
            p['name']: p['value'] for p in connector._container.calls[0]['parameters']
        }
        self.assertEqual(params['@from_ts'], self.samples[0] + 1)

    def test_the_document_cap_bounds_one_poll(self):
        """One slow station cannot monopolise the worker."""
        checkpoint = self.Checkpoint(STATION, self.base, self.base - 1)
        connector = connector_for(self.documents, config={'max_docs_per_poll': 2})

        produced = list(connector.poll(checkpoint, now=self.now))
        self.assertEqual(len(produced), 2)

    def test_a_poll_walks_a_bounded_number_of_hours(self):
        """Falling days behind catches up over runs, not in one burst."""
        checkpoint = self.Checkpoint(STATION, self.base, self.base)
        connector = connector_for([])
        later = datetime.fromtimestamp(
            (self.base + 48 * HOUR_MS) / 1000, tz=timezone.utc
        )

        list(connector.poll(checkpoint, now=later))
        self.assertEqual(len(connector._container.calls), MAX_BUCKETS_PER_POLL)


class IngestTests(HealthEnvMixin, TestCase):
    """The checkpoint advances per snapshot, never per batch."""

    def setUp(self):
        """A source with two mapped keys and a snapshot that spans batches."""
        self.build_health_env()
        self.source.connector_type = 'cosmos_pumphouse'
        self.source.config = {
            'endpoint': ACCOUNT,
            'database': 'aimms',
            'readings_container': 'pumphouse_readings',
            'stations': [STATION],
        }
        self.source.save(update_fields=['connector_type', 'config'])

        for key in ('/sl', '/pd/P1/dv'):
            MachineSignalBinding.objects.create(
                machine=self.machine,
                source=self.source,
                external_key=key,
                display_name=key,
                signal_kind='level',
                unit='m',
            )

        self.base = 1752850800000
        self.samples = [self.base + index * 60_000 for index in range(2)]
        # ~700 extension tags is what a real snapshot carries; the point is that
        # one snapshot cannot fit in a single 500-reading batch.
        self.dex = {f'TAG_{index}': str(index) for index in range(700)}
        self.documents = [
            snapshot(sample, dex=self.dex) for sample in self.samples
        ]
        self.now = datetime.fromtimestamp(
            (self.base + HOUR_MS - 1) / 1000, tz=timezone.utc
        )
        # Sit the checkpoint at the end of the previous hour so both samples are
        # still unread: a checkpoint *on* a sample means that sample was already
        # taken, and polling resumes strictly after it.
        self.start_position = self.base - 1
        self.checkpoint = IngestionCheckpoint.objects.create(
            source=self.source,
            station_uuid=STATION,
            hour_bucket=str(self.base - HOUR_MS),
            sub_time_period=self.start_position,
        )

    def connector(self, documents=None):
        """A connector on this source, wired to a fake container."""
        connector = CosmosPumphouseConnector(self.source)
        connector._container = FakeContainer(
            self.documents if documents is None else documents
        )
        return connector

    def test_a_snapshot_larger_than_one_batch_is_applied(self):
        """A single ``ingest_readings`` call would raise above 500 readings."""
        documents, readings = self.connector().ingest(
            self.checkpoint, now=self.now
        )

        self.assertEqual(documents, 2)
        self.assertEqual(readings, 4)

    def test_the_checkpoint_lands_on_the_last_snapshot_read(self):
        """Progress is recorded so a restart does not re-read the hour."""
        self.connector().ingest(self.checkpoint, now=self.now)
        self.checkpoint.refresh_from_db()

        self.assertEqual(self.checkpoint.sub_time_period, self.samples[-1])
        self.assertEqual(self.checkpoint.hour_bucket, str(self.base))

    def test_a_failed_batch_leaves_the_checkpoint_where_it_was(self):
        """Half a snapshot must never be recorded as a sample fully read."""
        real = self.connector()
        calls = {'count': 0}
        from machine_health.services import ingestion

        def failing(source, readings, *, now=None):
            calls['count'] += 1
            if calls['count'] == 2:
                raise ingestion.IngestionError('batch rejected')
            return ingestion.IngestResult()

        with mock.patch.object(ingestion, 'ingest_readings', side_effect=failing):
            documents, _readings = real.ingest(self.checkpoint, now=self.now)

        self.checkpoint.refresh_from_db()
        self.assertEqual(documents, 0)
        self.assertEqual(self.checkpoint.sub_time_period, self.start_position)

    def test_a_read_failure_stops_without_advancing(self):
        """A partition that will not answer is not progress."""
        connector = self.connector()
        connector._container = FakeContainer(self.documents, error=HttpError(429))

        documents, _readings = connector.ingest(self.checkpoint, now=self.now)

        self.checkpoint.refresh_from_db()
        self.assertEqual(documents, 0)
        self.assertEqual(self.checkpoint.sub_time_period, self.start_position)

    def test_the_second_run_reads_nothing_new(self):
        """Ingestion is idempotent across runs through the checkpoint."""
        self.connector().ingest(self.checkpoint, now=self.now)
        documents, _readings = self.connector().ingest(self.checkpoint, now=self.now)

        self.assertEqual(documents, 0)

    def test_an_aware_read_horizon_does_not_break_ingestion(self):
        """The two clocks in play must not be confused for one another.

        ``ingest``'s ``now`` bounds how far forward the *source* is read;
        ingestion's ``now`` is the server clock it measures skew against. The
        project runs with ``USE_TZ`` off, so forwarding one as the other produced
        a naive/aware subtraction that failed every batch - and would have
        surfaced in production as a NETWORK error, sending someone to look at a
        firewall.
        """
        documents, readings = self.connector().ingest(self.checkpoint, now=self.now)

        self.assertEqual(documents, 2)
        self.assertGreater(readings, 0)

    def test_historical_samples_are_not_rejected_as_clock_skew(self):
        """July 2025 data is old, not wrong; skew only guards the future."""
        self.connector().ingest(self.checkpoint, now=self.now)
        self.source.refresh_from_db()

        self.assertEqual(self.source.last_error_code, '')

    def test_a_halted_run_logs_a_code_and_no_provider_detail(self):
        """Log lines are read by people who should not be shown a token."""
        connector = self.connector()
        connector._container = FakeContainer(
            self.documents, error=HttpError(403, 'token=SECRET endpoint=https://x')
        )

        with self.assertLogs('inventree', level='WARNING') as captured:
            connector.ingest(self.checkpoint, now=self.now)

        joined = '\n'.join(captured.output)
        self.assertIn('code=AUTH', joined)
        self.assertNotIn('SECRET', joined)
        self.assertNotIn('https://', joined)
