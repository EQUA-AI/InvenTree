"""The pumphouse payload normalizer, proved against real snapshot shapes.

No Azure and no database: the rules that turn a payload into readings are pure,
and the point of keeping them pure is that they can be held to account here.
"""

import json
from pathlib import Path

from django.test import SimpleTestCase

from assets.health_models import SignalQuality
from machine_health.connectors.pumphouse_payload import (
    SnapshotError,
    flatten_snapshot,
    in_batches,
    observed_at,
    status_label,
)
from machine_health.services.ingestion import MAX_READINGS_PER_BATCH

# tests/ -> machine_health/ -> InvenTree/ -> backend/ -> src/ -> the repository
# root, where contrib/ lives alongside src/.
SAMPLES = (
    Path(__file__).resolve().parents[5]
    / 'contrib'
    / 'cosmos'
    / 'samples'
    / 'ph3_snapshots.json'
)


def payload(**overrides):
    """A snapshot payload shaped like the real PH_3 feed."""
    base = {
        'sr': 'SCADA',
        'st': 'I',
        'pc': 0.0,
        'sl': 132.0436248779297,
        'dv': 0.0,
        'pmw': 0.0,
        'pmvar': 0.0,
        'pd': {
            'P3': {'st': 'R', 'dv': 12.5, 'pmw': 8.75, 'pmvar': 2.1},
            'P4': {'st': 'I', 'dv': 0.0, 'pmw': 0.0, 'pmvar': 0.0},
        },
        'egt': 1752853013703,
        'ext': 1752853313703,
        'dsc': 24,
        'dex': {
            'ID': 'PH_3',
            'TIMESTAMP': '1.752853013703E9',
            'COMMAN_FORBAY_LEVEL': '132.0436248779297',
            'PUMP4_MOTOR_CORE_RTD2_PROCESS_VALUE': '-242.09999084472656',
            'PUMP6_PUMP_MOTOR_HOT_AIR TEMP2': '37.29999923706055',
        },
    }
    base.update(overrides)
    return base


def document(**overrides):
    """A Cosmos document wrapping that payload."""
    base = {
        'id': '1752853013703',
        'station_uuid': 'bafc976f-1ccc-4a91-aaa6-c3eac2470d36',
        'hour_bucket': '1752850800000',
        'sub_time_period': 1752853013703,
        **payload(),
    }
    base.update(overrides)
    return base


class KeyDerivationTests(SimpleTestCase):
    """Where a value sits is what decides which machine it belongs to."""

    def keys(self, **overrides):
        """External keys produced for a document."""
        return {r.external_key for r in flatten_snapshot(document(**overrides))}

    def test_station_and_pump_levels_fall_out_of_one_walk(self):
        """The same measurement name appears at both levels, distinctly."""
        keys = self.keys()
        for station_key in ('/sl', '/dv', '/pmw', '/pmvar', '/pc', '/st'):
            self.assertIn(station_key, keys)
        for pump_key in ('/pd/P3/st', '/pd/P3/dv', '/pd/P3/pmw', '/pd/P3/pmvar'):
            self.assertIn(pump_key, keys)

    def test_keys_match_registry_dictionary_paths(self):
        """The external key *is* DictionaryPoint.path, or bindings drift apart."""
        self.assertIn('/dex/COMMAN_FORBAY_LEVEL', self.keys())
        self.assertIn('/dex/PUMP4_MOTOR_CORE_RTD2_PROCESS_VALUE', self.keys())

    def test_tag_containing_a_space_is_keyed_intact(self):
        """Real tags contain spaces; the key must not be tidied or truncated."""
        self.assertIn('/dex/PUMP6_PUMP_MOTOR_HOT_AIR TEMP2', self.keys())

    def test_pointer_escaping(self):
        """A tag with pointer syntax in it stays unambiguous."""
        keys = self.keys(dex={'ODD/TAG~NAME': '1.0'})
        self.assertIn('/dex/ODD~1TAG~0NAME', keys)

    def test_envelope_and_metadata_are_not_measurements(self):
        """Message bookkeeping must not masquerade as plant data."""
        keys = self.keys()
        for absent in ('/sr', '/dsc', '/egt', '/ext', '/dex/ID', '/dex/TIMESTAMP'):
            self.assertNotIn(absent, keys)

    def test_extension_tags_can_be_left_out(self):
        """A caller may take the summary without ~700 detail tags."""
        readings = flatten_snapshot(document(), include_extension=False)
        self.assertFalse([r for r in readings if r.external_key.startswith('/dex/')])
        self.assertTrue([r for r in readings if r.external_key == '/sl'])


class ValueTests(SimpleTestCase):
    """What a reading carries, and how honestly it says so."""

    def reading(self, key, **overrides):
        """One reading by key."""
        readings = flatten_snapshot(document(**overrides))
        return next(r for r in readings if r.external_key == key)

    def test_numeric_strings_become_numbers(self):
        """dex transports numbers as strings; comparisons need real numbers."""
        found = self.reading('/dex/COMMAN_FORBAY_LEVEL')
        self.assertEqual(found.value, 132.0436248779297)
        self.assertEqual(found.quality, SignalQuality.GOOD)

    def test_out_of_range_values_are_preserved_not_clamped(self):
        """-242.1 is what the source said; deciding it is wrong is not our job."""
        found = self.reading('/dex/PUMP4_MOTOR_CORE_RTD2_PROCESS_VALUE')
        self.assertEqual(found.value, -242.09999084472656)
        self.assertEqual(found.quality, SignalQuality.GOOD)

    def test_pumps_running_stays_a_float(self):
        """The source sends pc as a float; coercing to int would be a guess."""
        self.assertIsInstance(self.reading('/pc').value, float)

    def test_unparsable_string_is_kept_and_flagged(self):
        """Neither dropped nor zeroed: unparsable and zero are different facts."""
        found = self.reading(
            '/dex/COMMAN_FORBAY_LEVEL', dex={'COMMAN_FORBAY_LEVEL': 'FAULT'}
        )
        self.assertEqual(found.value, 'FAULT')
        self.assertEqual(found.quality, SignalQuality.UNCERTAIN)

    def test_empty_and_null_are_bad_quality_with_no_value(self):
        """An absent reading must not arrive as a plausible number."""
        for raw in ['', '   ', None]:
            with self.subTest(raw=raw):
                found = self.reading(
                    '/dex/COMMAN_FORBAY_LEVEL', dex={'COMMAN_FORBAY_LEVEL': raw}
                )
                self.assertIsNone(found.value)
                self.assertEqual(found.quality, SignalQuality.BAD)

    def test_non_finite_values_are_bad_quality(self):
        """NaN and infinity are not measurements."""
        for raw in ['NaN', 'Infinity', '-Infinity']:
            with self.subTest(raw=raw):
                found = self.reading(
                    '/dex/COMMAN_FORBAY_LEVEL', dex={'COMMAN_FORBAY_LEVEL': raw}
                )
                self.assertIsNone(found.value)
                self.assertEqual(found.quality, SignalQuality.BAD)

    def test_oversized_string_is_omitted(self):
        """Ingestion bounds a value at 2 KiB; an oversized one is not stored."""
        keys = {
            r.external_key
            for r in flatten_snapshot(document(dex={'BIG_TAG': 'x' * 3000}))
        }
        self.assertNotIn('/dex/BIG_TAG', keys)


class StatusTests(SimpleTestCase):
    """Status codes are recorded, not interpreted."""

    def reading(self, key, **overrides):
        """One reading by key."""
        readings = flatten_snapshot(document(**overrides))
        return next(r for r in readings if r.external_key == key)

    def test_known_codes_are_stored_raw_and_good(self):
        """I and R are confirmed; the stored value stays the source's code."""
        self.assertEqual(self.reading('/st').value, 'I')
        self.assertEqual(self.reading('/st').quality, SignalQuality.GOOD)
        self.assertEqual(self.reading('/pd/P3/st').value, 'R')

    def test_unknown_code_is_passed_through_as_uncertain(self):
        """Guessing an unfamiliar code is how a stopped pump reads as running."""
        found = self.reading('/st', st='X')
        self.assertEqual(found.value, 'X')
        self.assertEqual(found.quality, SignalQuality.UNCERTAIN)

    def test_labels_never_invent_a_meaning(self):
        """An unrecognised code is displayed as itself."""
        self.assertEqual(status_label('I'), 'Idle')
        self.assertEqual(status_label('R'), 'Running')
        self.assertEqual(status_label('X'), 'X')


class TimestampTests(SimpleTestCase):
    """Observation time comes from the source, never from the clock."""

    def test_observed_at_is_the_sample_timestamp_in_utc(self):
        """sub_time_period is when the source says it happened."""
        moment = observed_at(document())
        self.assertEqual(moment.isoformat(), '2025-07-18T15:36:53.703000+00:00')

    def test_egt_is_the_only_fallback(self):
        """Import time is not observation time, so there is no 'now' fallback."""
        payload_only = {key: value for key, value in document().items()}
        del payload_only['sub_time_period']
        self.assertEqual(observed_at(payload_only).year, 2025)

        del payload_only['egt']
        with self.assertRaises(SnapshotError):
            observed_at(payload_only)

    def test_sequence_is_the_sample_timestamp(self):
        """Ordering uses the source's own monotonic sample number."""
        found = next(
            r for r in flatten_snapshot(document()) if r.external_key == '/sl'
        )
        self.assertEqual(found.sequence, 1752853013703)


class RawPayloadTests(SimpleTestCase):
    """data1_raw is the record of truth."""

    def test_raw_payload_is_used_when_present(self):
        """Readings come from the verbatim text, not the indexed copy."""
        raw = json.dumps(payload(), separators=(',', ':'))
        readings = flatten_snapshot(document(data1_raw=raw))
        self.assertTrue([r for r in readings if r.external_key == '/sl'])

    def test_disagreement_between_parsed_and_raw_fails_the_snapshot(self):
        """A snapshot we cannot vouch for must not become a reading."""
        raw = json.dumps(payload(), separators=(',', ':'))
        with self.assertRaises(SnapshotError) as caught:
            flatten_snapshot(document(data1_raw=raw, sl=999.0))
        self.assertIn('data1_raw', str(caught.exception))

    def test_unreadable_raw_payload_fails_the_snapshot(self):
        """Corrupt text is reported, not silently skipped."""
        with self.assertRaises(SnapshotError):
            flatten_snapshot(document(data1_raw='{not json'))


class SampleFileTests(SimpleTestCase):
    """The checked-in pilot snapshots flatten as expected."""

    def snapshots(self):
        """Payloads from the sample file."""
        data = json.loads(SAMPLES.read_text(encoding='utf-8'))
        return [
            dict(entry['data1'], sub_time_period=entry['sub_time_period'])
            for entry in data['snapshots']
        ]

    def test_every_sample_flattens_without_error(self):
        """The samples the seeder writes are the samples the reader accepts."""
        for snapshot in self.snapshots():
            readings = flatten_snapshot(snapshot)
            self.assertTrue(readings)

    def test_running_pump_is_visible_as_a_reading(self):
        """The I to R transition must be observable through the readings."""
        statuses = [
            r.value
            for snapshot in self.snapshots()
            for r in flatten_snapshot(snapshot)
            if r.external_key == '/pd/P3/st'
        ]
        self.assertIn('R', statuses)
        self.assertIn('I', statuses)

    def test_batch_size_stays_within_the_ingestion_limit(self):
        """The abridged samples fit one batch; a real snapshot does not."""
        for snapshot in self.snapshots():
            self.assertLess(len(flatten_snapshot(snapshot)), MAX_READINGS_PER_BATCH)


class BatchingTests(SimpleTestCase):
    """A whole snapshot does not fit one ingestion batch, so it must be paged."""

    def full_snapshot(self):
        """A snapshot the size PH_3 really sends: ~700 dex tags, 14 pumps."""
        return payload(
            pd={
                f'P{n}': {'st': 'I', 'dv': 0.0, 'pmw': 0.0, 'pmvar': 0.0}
                for n in range(1, 15)
            },
            dex={f'PUMP{p}_TAG_{i}': '40.5' for p in range(1, 15) for i in range(50)},
            sub_time_period=1752853013703,
        )

    def test_a_real_snapshot_exceeds_one_batch(self):
        """This is why in_batches exists; if it ever stops being true, say so."""
        readings = flatten_snapshot(self.full_snapshot())
        self.assertGreater(len(readings), MAX_READINGS_PER_BATCH)

    def test_batches_are_acceptable_to_ingestion(self):
        """Every batch is within the limit ingest_readings enforces."""
        readings = flatten_snapshot(self.full_snapshot())
        for batch in in_batches(readings):
            self.assertLessEqual(len(batch), MAX_READINGS_PER_BATCH)

    def test_batching_loses_nothing(self):
        """Paging must reorder nothing and drop nothing."""
        readings = flatten_snapshot(self.full_snapshot())
        regrouped = [r for batch in in_batches(readings) for r in batch]
        self.assertEqual([r.external_key for r in regrouped],
                         [r.external_key for r in readings])

    def test_empty_input_yields_no_batches(self):
        """No readings means no ingestion call, not one empty call."""
        self.assertEqual(list(in_batches([])), [])

    def test_a_meaningless_batch_size_is_refused(self):
        """A size of zero would loop forever rather than fail."""
        with self.assertRaises(ValueError):
            list(in_batches([1, 2, 3], size=0))
