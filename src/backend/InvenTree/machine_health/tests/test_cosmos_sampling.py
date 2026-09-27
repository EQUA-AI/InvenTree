"""The Cosmos adapter's sampled read: one document per slot, and no more.

The property that matters is cost. A full read of a day is 17,280 documents;
this must be a few hundred single-document queries whatever the window, each
answered by the first snapshot in its slot, with every requested key served
from the same pass.
"""

from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from machine_health.connectors.cosmos_pumphouse import (
    HOUR_MS,
    QUERY_FIRST_IN_RANGE,
    to_epoch_ms,
)

from .test_cosmos_pumphouse import connector_for, snapshot
from .test_cosmos_replay import ANCHOR, WINDOW_START, replay_for


class SampledReadTests(SimpleTestCase):
    """Slot queries against a fake container holding a steady 5 s cadence."""

    def setUp(self):
        """Three hours of snapshots at the plant's cadence, two tags each."""
        self.base = datetime(2025, 7, 10, 8, 0, tzinfo=timezone.utc)
        base_ms = to_epoch_ms(self.base)
        self.documents = [
            snapshot(
                base_ms + index * 5_000,
                sl=100.0 + index,
                dex={'PUMP1_SPEED': 400.0 + index, 'PUMP1_ACTIVE_POWER': 8.0},
            )
            for index in range(3 * 720)
        ]
        self.start = self.base
        self.end = self.base + timedelta(hours=3)

    def calls(self, connector):
        """The slot queries the container answered."""
        return [
            call
            for call in connector._container.calls
            if call['query'] == QUERY_FIRST_IN_RANGE
        ]

    def test_one_query_per_slot_and_one_document_each(self):
        """Thirty points across three hours is thirty small queries."""
        connector = connector_for(self.documents)
        sampled = connector.sample_windows(
            ['/dex/PUMP1_SPEED'], self.start, self.end, slots=30
        )

        self.assertEqual(len(self.calls(connector)), 30)
        self.assertEqual(sampled.slots, 30)
        self.assertEqual(sampled.documents_read, 30)
        self.assertEqual(len(sampled.readings['/dex/PUMP1_SPEED']), 30)

    def test_each_reading_is_the_first_in_its_slot(self):
        """Six minutes a slot at five seconds a sample: every 72nd reading."""
        connector = connector_for(self.documents)
        sampled = connector.sample_windows(
            ['/dex/PUMP1_SPEED'], self.start, self.end, slots=30
        )
        values = [r.value for r in sampled.readings['/dex/PUMP1_SPEED']]
        self.assertEqual(values, [400.0 + 72 * index for index in range(30)])
        moments = [r.observed_at for r in sampled.readings['/dex/PUMP1_SPEED']]
        self.assertEqual(moments, sorted(moments))

    def test_every_key_is_served_from_the_same_documents(self):
        """Two tags cost the same thirty queries as one."""
        connector = connector_for(self.documents)
        sampled = connector.sample_windows(
            ['/dex/PUMP1_SPEED', '/dex/PUMP1_ACTIVE_POWER', '/sl'],
            self.start,
            self.end,
            slots=30,
        )
        self.assertEqual(len(self.calls(connector)), 30)
        self.assertEqual(
            {key: len(found) for key, found in sampled.readings.items()},
            {'/dex/PUMP1_SPEED': 30, '/dex/PUMP1_ACTIVE_POWER': 30, '/sl': 30},
        )
        # And they line up: the same instant for every key in a slot.
        speed = [r.observed_at for r in sampled.readings['/dex/PUMP1_SPEED']]
        level = [r.observed_at for r in sampled.readings['/sl']]
        self.assertEqual(speed, level)

    def test_a_slot_with_no_snapshot_is_absent(self):
        """A source that paused leaves the slot empty rather than repeating."""
        base_ms = to_epoch_ms(self.base)
        with_gap = [
            document
            for document in self.documents
            if not (base_ms + HOUR_MS <= document['sub_time_period'] < base_ms + 2 * HOUR_MS)
        ]
        connector = connector_for(with_gap)
        sampled = connector.sample_windows(
            ['/dex/PUMP1_SPEED'], self.start, self.end, slots=30
        )
        self.assertEqual(len(self.calls(connector)), 30)
        self.assertEqual(sampled.documents_read, 20)
        self.assertEqual(len(sampled.readings['/dex/PUMP1_SPEED']), 20)

    def test_a_slot_straddling_an_hour_tries_the_next_bucket_only_if_needed(self):
        """Two partitions may serve one slot, but the earlier one is asked first."""
        base_ms = to_epoch_ms(self.base)
        # Nothing in the first hour's last twenty minutes, so the slot that
        # spans the boundary must go on to the second hour's partition.
        thinned = [
            document
            for document in self.documents
            if not (base_ms + 40 * 60_000 <= document['sub_time_period'] < base_ms + HOUR_MS)
        ]
        connector = connector_for(thinned)
        # 45-minute slots: the second slot runs 0:45-1:30 and straddles the hour.
        sampled = connector.sample_windows(
            ['/dex/PUMP1_SPEED'], self.start, self.end, slots=4
        )
        calls = self.calls(connector)
        self.assertEqual(len(calls), 5)  # four slots, one of which needed two
        readings = sampled.readings['/dex/PUMP1_SPEED']
        self.assertEqual(len(readings), 4)
        # The straddling slot's reading is the first snapshot of the second hour.
        self.assertEqual(readings[1].observed_at, self.base + timedelta(hours=1))

    def test_a_reversed_window_is_refused_before_any_query(self):
        """Bounds are checked before a request is built."""
        connector = connector_for(self.documents)
        with self.assertRaises(ValueError):
            connector.sample_windows(['/sl'], self.end, self.start, slots=10)
        self.assertEqual(connector._container.calls, [])

    def test_no_keys_means_no_queries(self):
        """An empty request costs nothing."""
        connector = connector_for(self.documents)
        sampled = connector.sample_windows([], self.start, self.end, slots=10)
        self.assertEqual(sampled.readings, {})
        self.assertEqual(connector._container.calls, [])


class ReplaySampledReadTests(SimpleTestCase):
    """A replay samples the mapped source window and answers in wall time."""

    def setUp(self):
        """One source hour of snapshots inside the replayed window."""
        self.source_start = WINDOW_START + timedelta(hours=1)
        base_ms = to_epoch_ms(self.source_start)
        self.documents = [
            snapshot(base_ms + index * 5_000, dex={'PUMP1_SPEED': 428.0 + index})
            for index in range(720)
        ]

    def test_the_window_is_mapped_and_the_readings_restamped(self):
        """Asked about today, the query addresses July 2025 and answers today."""
        connector = replay_for(self.documents)
        wall_from = ANCHOR + timedelta(hours=1)
        sampled = connector.sample_windows(
            ['/dex/PUMP1_SPEED'], wall_from, wall_from + timedelta(hours=1), slots=6
        )
        readings = sampled.readings['/dex/PUMP1_SPEED']
        self.assertEqual(len(readings), 6)
        self.assertEqual(readings[0].observed_at, wall_from)
        self.assertEqual(readings[1].observed_at, wall_from + timedelta(minutes=10))
        buckets = {
            p['value']
            for call in connector._container.calls
            for p in call['parameters']
            if p['name'] == '@bucket'
        }
        self.assertEqual(buckets, {str(to_epoch_ms(self.source_start))})

    def test_a_speed_that_overruns_a_day_is_refused(self):
        """At 60x an hour of wall time is 2.5 source days."""
        connector = replay_for(self.documents, config={'replay_speed': 60})
        wall_from = ANCHOR + timedelta(hours=1)
        with self.assertRaises(ValueError) as caught:
            connector.sample_windows(
                ['/dex/PUMP1_SPEED'], wall_from, wall_from + timedelta(hours=1), slots=6
            )
        self.assertIn('source hours', str(caught.exception))
