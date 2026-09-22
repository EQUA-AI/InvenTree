"""Tests for the replaying pumphouse connector.

The point of this adapter is a single affine map, so the tests are mostly about
the map's edges rather than about Cosmos: what happens before the anchor, after
the window, and at a speed that turns a legal request into an illegal one. The
container is the same fake the live connector's tests use, because the replay
must issue exactly the same queries - it changes *when* it asks, never *how*.

Two properties are worth more than the rest and are asserted directly:

* readings leave in wall time, so the dashboard does not mark them stale;
* documents are handed on untouched, so the checkpoint keeps advancing in real
  source time and stays forward-only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase, TestCase

from machine_health.connectors.base import (
    PUMPHOUSE_CONNECTOR_TYPES,
    load_builtin_connectors,
    pumphouse_connector_class,
)
from machine_health.connectors.cosmos_pumphouse import CosmosConfigError, to_epoch_ms
from machine_health.connectors.cosmos_replay import CosmosPumphouseReplayConnector

from .test_cosmos_pumphouse import ACCOUNT, STATION, FakeContainer, StubSource, snapshot

# The migrated window, and a wall-clock anchor well clear of it.
WINDOW_START = datetime(2025, 7, 2, tzinfo=timezone.utc)
WINDOW_END = datetime(2025, 7, 12, tzinfo=timezone.utc)
ANCHOR = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def replay_for(documents=None, *, error=None, config=None):
    """Return a replay connector wired to a fake container."""
    settings = {
        'endpoint': ACCOUNT,
        'database': 'aimms',
        'readings_container': 'pumphouse_readings',
        'stations': [STATION],
        'replay_window_start': WINDOW_START.isoformat(),
        'replay_window_end': WINDOW_END.isoformat(),
        'replay_anchor': ANCHOR.isoformat(),
    }
    settings.update(config or {})
    connector = CosmosPumphouseReplayConnector(StubSource(settings))
    connector._container = FakeContainer(documents, error=error)
    return connector


class RegistrationTests(TestCase):
    """The adapter must be reachable everywhere the live one is."""

    def test_registered_under_its_own_key(self):
        load_builtin_connectors()
        self.assertIs(
            pumphouse_connector_class('cosmos_pumphouse_replay'),
            CosmosPumphouseReplayConnector,
        )

    def test_counts_as_a_pumphouse_connector(self):
        # The polling sweep filters on this set. A replay source left out of it
        # would be registered, activated, and never polled.
        self.assertIn('cosmos_pumphouse_replay', PUMPHOUSE_CONNECTOR_TYPES)
        self.assertIn('cosmos_pumphouse', PUMPHOUSE_CONNECTOR_TYPES)

    def test_unknown_type_resolves_to_none(self):
        self.assertIsNone(pumphouse_connector_class('not_a_connector'))


class TimeMapTests(SimpleTestCase):
    """The affine map, and what it does at its edges."""

    def test_anchor_maps_to_window_start(self):
        self.assertEqual(replay_for().to_source(ANCHOR), WINDOW_START)

    def test_elapsed_wall_time_advances_source_time(self):
        connector = replay_for()
        self.assertEqual(
            connector.to_source(ANCHOR + timedelta(hours=5)),
            WINDOW_START + timedelta(hours=5),
        )

    def test_round_trips(self):
        connector = replay_for()
        moment = WINDOW_START + timedelta(days=3, minutes=17, seconds=4)
        self.assertEqual(connector.to_source(connector.to_wall(moment)), moment)

    def test_speed_multiplies_source_time(self):
        connector = replay_for(config={'replay_speed': 60})
        self.assertEqual(
            connector.to_source(ANCHOR + timedelta(minutes=1)),
            WINDOW_START + timedelta(hours=1),
        )

    def test_before_the_anchor_clamps_to_window_start(self):
        # The replay has not begun. Without the clamp this maps to a time before
        # the window, and the poll would read a bucket that holds another
        # month's data.
        connector = replay_for()
        self.assertEqual(
            connector.to_source(ANCHOR - timedelta(days=400)), WINDOW_START
        )

    def test_after_the_window_clamps_to_window_end(self):
        # The replay is over. Clamping makes it look like a source that stopped
        # reporting, which is the truth, instead of wrapping to the beginning.
        connector = replay_for()
        self.assertEqual(
            connector.to_source(ANCHOR + timedelta(days=99)), WINDOW_END
        )


class ConfigTests(SimpleTestCase):
    """A misconfigured replay must refuse rather than read the wrong window."""

    def test_missing_window_start_is_rejected(self):
        connector = replay_for(config={'replay_window_start': None})
        with self.assertRaises(CosmosConfigError):
            connector.replay

    def test_unparseable_instant_is_rejected(self):
        connector = replay_for(config={'replay_anchor': 'last tuesday'})
        with self.assertRaises(CosmosConfigError):
            connector.replay

    def test_naive_instant_is_read_as_utc(self):
        connector = replay_for(config={'replay_anchor': '2026-09-22T12:00:00'})
        self.assertEqual(connector.replay[2], ANCHOR)

    def test_trailing_z_is_accepted(self):
        connector = replay_for(config={'replay_anchor': '2026-09-22T12:00:00Z'})
        self.assertEqual(connector.replay[2], ANCHOR)

    def test_inverted_window_is_rejected(self):
        connector = replay_for(
            config={'replay_window_end': (WINDOW_START - timedelta(days=1)).isoformat()}
        )
        with self.assertRaises(CosmosConfigError):
            connector.replay

    def test_zero_speed_is_rejected(self):
        # A zero speed would freeze the replay on one instant forever, and a
        # negative one would run it backwards past the window start.
        for speed in (0, -1):
            with self.subTest(speed=speed):
                with self.assertRaises(CosmosConfigError):
                    replay_for(config={'replay_speed': speed}).replay

    def test_non_numeric_speed_is_rejected(self):
        with self.assertRaises(CosmosConfigError):
            replay_for(config={'replay_speed': 'fast'}).replay


class ReadWindowTests(SimpleTestCase):
    """Reads are delegated against mapped bounds and returned in wall time."""

    def setUp(self):
        self.base = to_epoch_ms(WINDOW_START + timedelta(hours=1))
        self.documents = [
            snapshot(self.base + offset, dex={'PUMP1_SPEED': 428.0 + index})
            for index, offset in enumerate((0, 5_000, 10_000))
        ]

    def test_samples_come_back_stamped_in_wall_time(self):
        connector = replay_for(self.documents)
        wall_from = ANCHOR + timedelta(hours=1)
        readings = connector.read_window(
            '/dex/PUMP1_SPEED', wall_from, wall_from + timedelta(minutes=5)
        )

        self.assertEqual([r.value for r in readings], [428.0, 429.0, 430.0])
        self.assertEqual(
            [r.observed_at for r in readings],
            [wall_from, wall_from + timedelta(seconds=5),
             wall_from + timedelta(seconds=10)],
        )

    def test_query_names_the_source_bucket_not_the_wall_bucket(self):
        # The whole point: the request must address July 2025 even though the
        # caller asked about today.
        connector = replay_for(self.documents)
        wall_from = ANCHOR + timedelta(hours=1)
        connector.read_window(
            '/dex/PUMP1_SPEED', wall_from, wall_from + timedelta(minutes=5)
        )
        buckets = {
            p['value']
            for call in connector._container.calls
            for p in call['parameters']
            if p['name'] == '@bucket'
        }
        self.assertEqual(buckets, {str(to_epoch_ms(WINDOW_START + timedelta(hours=1)))})

    def test_speed_that_overruns_the_window_limit_is_refused(self):
        # One wall hour at 60x is 60 source hours, far past the six-hour cap. The
        # caller bounded its own request correctly, so the error has to name the
        # real reason rather than blame the window it asked for.
        connector = replay_for(self.documents, config={'replay_speed': 60})
        wall_from = ANCHOR + timedelta(hours=1)
        with self.assertRaises(ValueError) as caught:
            connector.read_window(
                '/dex/PUMP1_SPEED', wall_from, wall_from + timedelta(hours=1)
            )
        self.assertIn('source hours', str(caught.exception))

    def test_window_entirely_before_the_anchor_is_empty(self):
        connector = replay_for(self.documents)
        readings = connector.read_window(
            '/dex/PUMP1_SPEED', ANCHOR - timedelta(hours=2), ANCHOR - timedelta(hours=1)
        )
        self.assertEqual(readings, [])


class ReadLatestTests(SimpleTestCase):
    """The replay must not serve a sample from its own future."""

    def setUp(self):
        self.base = to_epoch_ms(WINDOW_START + timedelta(hours=1))
        self.documents = [
            snapshot(self.base + offset, dex={'PUMP1_SPEED': 428.0 + index})
            for index, offset in enumerate((0, 5_000, 10_000))
        ]

    def _at(self, connector, wall_now):
        import machine_health.connectors.cosmos_replay as module

        class FrozenDatetime(module.datetime):
            @classmethod
            def now(cls, tz=None):
                return wall_now

        original = module.datetime
        module.datetime = FrozenDatetime
        try:
            return connector.read_latest(['/dex/PUMP1_SPEED'])
        finally:
            module.datetime = original

    def test_returns_the_newest_sample_at_or_before_the_replay_instant(self):
        connector = replay_for(self.documents)
        # Five seconds in: the second snapshot exists, the third does not yet.
        readings = self._at(connector, ANCHOR + timedelta(hours=1, seconds=5))
        self.assertEqual([r.value for r in readings], [429.0])

    def test_does_not_run_ahead_of_the_feed(self):
        # The parent returns the newest document in the bucket. Here that is
        # 430.0, ten seconds into a replay that has run for one - serving it
        # would put the dashboard ahead of the replay it is following.
        connector = replay_for(self.documents)
        readings = self._at(connector, ANCHOR + timedelta(hours=1, seconds=1))
        self.assertEqual([r.value for r in readings], [428.0])

    def test_reading_is_stamped_in_wall_time(self):
        connector = replay_for(self.documents)
        wall_now = ANCHOR + timedelta(hours=1, seconds=5)
        readings = self._at(connector, wall_now)
        self.assertEqual(readings[0].observed_at, wall_now)

    def test_empty_when_the_replay_instant_precedes_every_sample(self):
        connector = replay_for(self.documents)
        self.assertEqual(self._at(connector, ANCHOR), [])


class PollTests(SimpleTestCase):
    """Readings shift; the documents that move the checkpoint do not."""

    class Checkpoint:
        """The few checkpoint attributes ``poll`` reads."""

        def __init__(self, station_uuid, sub_time_period):
            self.station_uuid = station_uuid
            self.sub_time_period = sub_time_period
            self.scan_until = None

    def setUp(self):
        self.base = to_epoch_ms(WINDOW_START + timedelta(hours=1))
        self.documents = [
            snapshot(self.base + offset, dex={'PUMP1_SPEED': 428.0 + index})
            for index, offset in enumerate((0, 5_000, 10_000))
        ]

    def test_documents_keep_their_true_source_timestamps(self):
        # The checkpoint advances from the document, so shifting it here would
        # push sub_time_period into 2026 and the next poll would never find
        # another 2025 sample. This is the property that keeps the replay
        # restartable.
        connector = replay_for(self.documents)
        checkpoint = self.Checkpoint(STATION, self.base - 1)
        polled = list(
            connector.poll(checkpoint, now=ANCHOR + timedelta(hours=1, seconds=30))
        )
        self.assertEqual(
            [d['sub_time_period'] for d, _ in polled],
            [self.base, self.base + 5_000, self.base + 10_000],
        )

    def test_readings_are_stamped_in_wall_time(self):
        connector = replay_for(self.documents)
        checkpoint = self.Checkpoint(STATION, self.base - 1)
        polled = list(
            connector.poll(checkpoint, now=ANCHOR + timedelta(hours=1, seconds=30))
        )
        speeds = [
            reading
            for _document, readings in polled
            for reading in readings
            if reading.external_key == '/dex/PUMP1_SPEED'
        ]
        self.assertEqual(
            [r.observed_at for r in speeds],
            [
                ANCHOR + timedelta(hours=1),
                ANCHOR + timedelta(hours=1, seconds=5),
                ANCHOR + timedelta(hours=1, seconds=10),
            ],
        )

    def test_poll_stops_at_the_replay_instant(self):
        # Seven seconds in, the third snapshot has not "happened" yet.
        connector = replay_for(self.documents)
        checkpoint = self.Checkpoint(STATION, self.base - 1)
        polled = list(
            connector.poll(checkpoint, now=ANCHOR + timedelta(hours=1, seconds=7))
        )
        self.assertEqual(
            [d['sub_time_period'] for d, _ in polled], [self.base, self.base + 5_000]
        )
