"""Offline checks for the seeder's document rules.

Nothing here touches Azure. The rules that decide what a document looks like are
the ones a reader will depend on, so they are proved without an account.
"""

import json
import unittest
from pathlib import Path

from seed import (
    HOUR_MS,
    SeedError,
    build_document,
    ladder,
    load_snapshots,
    month_of,
    verify,
)

SAMPLES = Path(__file__).parent / 'samples' / 'ph3_snapshots.json'
STATION = 'bafc976f-1ccc-4a91-aaa6-c3eac2470d36'
SELECTORS = {
    'location_type': 'PUMP_HOUSE',
    'component_type': 65,
    'event_value_type': 41,
}


def snapshot(**overrides):
    """A minimal but valid snapshot, with room to break one thing at a time."""
    base = {
        'time_period': '1752850800000',
        'sub_time_period': 1752853013703,
        'data1': {
            'sr': 'SCADA',
            'st': 'I',
            'pc': 0.0,
            'sl': 132.0436248779297,
            'dv': 0.0,
            'pmw': 0.0,
            'pmvar': 0.0,
            'pd': {'P3': {'st': 'I', 'dv': 0.0, 'pmw': 0.0, 'pmvar': 0.0}},
            'egt': 1752853013703,
            'ext': 1752853313703,
            'dsc': 24,
            'dex': {'ID': 'PH_3', 'COMMAN_FORBAY_LEVEL': '132.0436248779297'},
        },
    }
    base.update(overrides)
    return base


class DocumentTests(unittest.TestCase):
    """What a built document must and must not contain."""

    def build(self, **overrides):
        """Build a document from the adjustable snapshot."""
        return build_document(snapshot(**overrides), STATION, SELECTORS)

    def test_identity_and_partition_key(self):
        """Id is the sample; the partition key is station plus hour bucket."""
        document = self.build()
        self.assertEqual(document['id'], '1752853013703')
        self.assertEqual(document['station_uuid'], STATION)
        self.assertEqual(document['hour_bucket'], '1752850800000')
        self.assertEqual(document['sub_time_period'], 1752853013703)

    def test_hour_bucket_is_stored_as_text(self):
        """The source column is text; storing a number invites a type mismatch."""
        self.assertIsInstance(self.build()['hour_bucket'], str)

    def test_month_is_derived_in_utc(self):
        """The legacy table suffix is UTC, so the derived month must be too."""
        self.assertEqual(self.build()['month'], '202507')
        # 2025-08-01T00:00:00Z exactly: the first bucket of the next month, and
        # the case a local-time derivation would file under July.
        self.assertEqual(month_of('1754006400000'), '202508')

    def test_sample_outside_its_bucket_is_refused(self):
        """Half-open at the top: the next boundary is the next bucket."""
        bucket = int(snapshot()['time_period'])
        for sample in [bucket - 1, bucket + HOUR_MS]:
            with self.subTest(sample=sample), self.assertRaises(SeedError):
                self.build(sub_time_period=sample)

        self.assertEqual(self.build(sub_time_period=bucket)['id'], str(bucket))

    def test_raw_payload_is_preserved_and_hashed(self):
        """data1_raw round-trips and payload_hash covers that exact text."""
        import hashlib

        document = self.build()
        raw = document['data1_raw']
        self.assertEqual(json.loads(raw), snapshot()['data1'])
        self.assertEqual(
            document['payload_hash'],
            'sha256:' + hashlib.sha256(raw.encode('utf-8')).hexdigest(),
        )

    def test_parsed_fields_must_agree_with_the_raw_payload(self):
        """A document whose parsed fields contradict data1_raw is refused.

        data1_raw is the record of truth, so a parsed field that disagrees with
        it would misrepresent what the source sent - the one failure this format
        exists to make impossible.
        """
        document = self.build()
        verify(document)  # unmodified, this passes

        document['sl'] = 999.0
        with self.assertRaises(SeedError) as caught:
            verify(document)
        self.assertIn('data1_raw', str(caught.exception))

    def test_confirmed_basic_params_survive_at_both_levels(self):
        """dv, pmw and pmvar are recorded at station and pump level alike."""
        document = self.build()
        for key in ('dv', 'pmw', 'pmvar', 'pc', 'sl', 'st'):
            self.assertIn(key, document)
        for key in ('st', 'dv', 'pmw', 'pmvar'):
            self.assertIn(key, document['pd']['P3'])
        # pc is station-only in the observed payload; inventing it per pump
        # would fabricate a measurement the source never sent.
        self.assertNotIn('pc', document['pd']['P3'])

    def test_pumps_running_count_is_not_coerced_to_int(self):
        """The source transports pc as a float; coercing it would be a guess."""
        self.assertIsInstance(self.build()['pc'], float)

    def test_selectors_are_carried_through(self):
        """Constant selectors identify the feed a document came from."""
        document = self.build()
        for key, value in SELECTORS.items():
            self.assertEqual(document[key], value)

    def test_station_stands_in_when_no_parent_is_supplied(self):
        """Without a parent in the source row, the station is used for both.

        Real PH_3 rows *do* carry a distinct parent (`dd4b923d…`, shared across
        pumphouses), so this is a fallback for hand-written snapshots, not a
        claim that the two are the same thing.
        """
        document = self.build()
        self.assertEqual(document['entity_uuid'], STATION)
        self.assertEqual(document['parent_entity_uuid'], STATION)

    def test_real_parent_is_preserved_when_present(self):
        """The shared parent identifier must not be overwritten by the station."""
        station, selectors, snapshots = load_snapshots(SAMPLES, None)
        document = build_document(snapshots[0], station, selectors)
        self.assertEqual(
            document['parent_entity_uuid'], 'dd4b923d-945d-47b3-aff3-de032d15f864'
        )
        self.assertNotEqual(document['parent_entity_uuid'], document['entity_uuid'])


class LadderTests(unittest.TestCase):
    """Synthesising a timestamp ladder."""

    def test_ladder_rebuckets_when_the_hour_rolls_over(self):
        """Samples past the hour end must move to the next bucket, not stretch."""
        produced = ladder(snapshot(), count=800, every_ms=5000)
        documents = [build_document(s, STATION, SELECTORS) for s in produced]
        self.assertGreater(len({d['hour_bucket'] for d in documents}), 1)
        for document in documents:
            bucket = int(document['hour_bucket'])
            self.assertTrue(bucket <= document['sub_time_period'] < bucket + HOUR_MS)

    def test_ladder_ids_are_unique(self):
        """One document per sample timestamp; upserts must not collide."""
        produced = ladder(snapshot(), count=50, every_ms=5000)
        documents = [build_document(s, STATION, SELECTORS) for s in produced]
        self.assertEqual(len({d['id'] for d in documents}), 50)


class SampleFileTests(unittest.TestCase):
    """The checked-in pilot snapshots."""

    def test_samples_build_and_span_two_hour_buckets(self):
        """Bucket enumeration and the read_latest fallback need two buckets."""
        station, selectors, snapshots = load_snapshots(SAMPLES, None)
        documents = [build_document(s, station, selectors) for s in snapshots]
        self.assertGreaterEqual(len({d['hour_bucket'] for d in documents}), 2)

    def test_samples_include_a_status_transition(self):
        """A pump that changes state is what makes the sample worth reading."""
        station, selectors, snapshots = load_snapshots(SAMPLES, None)
        documents = [build_document(s, station, selectors) for s in snapshots]
        statuses = {d['pd']['P3']['st'] for d in documents}
        self.assertEqual(statuses, {'I', 'R'})

    def test_samples_keep_out_of_range_values_verbatim(self):
        """Odd readings are carried through, not cleaned into plausibility."""
        station, selectors, snapshots = load_snapshots(SAMPLES, None)
        dex = build_document(snapshots[0], station, selectors)['dex']
        self.assertEqual(
            dex['PUMP4_MOTOR_CORE_RTD2_PROCESS_VALUE'], '-242.09999084472656'
        )
        self.assertEqual(
            dex['PUMP5_PUMP_COOLING_WATER_INLET_TEMP5'], '3276.699951171875'
        )

    def test_samples_cover_the_awkward_tag_shapes(self):
        """A space in a key and a doubled prefix both appear in real payloads."""
        station, selectors, snapshots = load_snapshots(SAMPLES, None)
        keys = build_document(snapshots[0], station, selectors)['dex'].keys()
        self.assertTrue(any(' ' in key for key in keys))
        self.assertTrue(any('PUMP_PUMP_' in key for key in keys))
        self.assertTrue(any('POWERFATCOR' in key for key in keys))


if __name__ == '__main__':
    unittest.main()
