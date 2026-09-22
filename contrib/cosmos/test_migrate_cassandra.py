"""Offline tests for the Cassandra -> Cosmos migration.

Stdlib only, no cluster and no Azure account: the migration logic takes an
injected reader and writer precisely so its behaviour under interruption,
throttling and bad input can be asserted without either. The one real Cassandra
payload in the repository (`samples/ph3_snapshots.json`) is used as the fixture,
so the shape under test is the shape the source actually produces.

Run:  python3 -m unittest discover -s contrib/cosmos -p 'test_*.py'
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import migrate_cassandra as mc

SAMPLES = Path(__file__).resolve().parent / 'samples' / 'ph3_snapshots.json'
HOUR = 1_752_850_800_000


def sample_payload(index: int = 0) -> dict:
    """One real data1 payload, as Cassandra stores it."""
    return json.loads(SAMPLES.read_text())['snapshots'][index]['data1']


def cassandra_row(
    sample_ms: int, payload: dict | None = None, hour: int = HOUR
) -> dict:
    """A row shaped like the driver's, with data1 as text - the real column type."""
    return {
        'entity_uuid': 'bafc976f-1ccc-4a91-aaa6-c3eac2470d36',
        'sub_time_period': sample_ms,
        'time_period': str(hour),
        'data1': json.dumps(payload if payload is not None else sample_payload()),
    }


class FakeReader:
    """Returns prepared rows per hour and records what was asked of it."""

    def __init__(self, rows_by_hour: dict[int, list[dict]]):
        """Hold the rows each hour will serve."""
        self.rows_by_hour = rows_by_hour
        self.calls: list[tuple[int, int | None]] = []

    def rows_for_hour(self, _partition, hour, after=None):
        """Serve one hour's rows, recording what was asked of it.

        Yields:
            Each prepared row for that hour, oldest first.
        """
        self.calls.append((hour, after))
        for row in self.rows_by_hour.get(hour, []):
            if after is not None and row['sub_time_period'] <= after:
                continue
            yield row


class ServiceResponseTimeoutError(Exception):
    """Stands in for azure.core's exception of the same name.

    `_is_transient` classifies by type NAME, so this exercises the real code
    path without importing the SDK - this suite deliberately runs on the
    stdlib alone, with no Azure account and no driver.
    """


PARTITION = mc.Partition(
    parent_entity_uuid='dd4b923d-945d-47b3-aff3-de032d15f864',
    location_type='PUMP_HOUSE',
    component_type=65,
    event_value_type=41,
)
STATION = 'bafc976f-1ccc-4a91-aaa6-c3eac2470d36'
SELECTORS = {
    'location_type': 'PUMP_HOUSE',
    'component_type': 65,
    'event_value_type': 41,
}


def run(reader, hours, **kwargs):
    """Migrate with a recording writer unless one is supplied."""
    written = kwargs.pop('written', None)
    if written is not None:

        def writer(document):
            """Collect what would be written, without a Cosmos account."""
            written.append(document)
            return 10.0

        kwargs.setdefault('writer', writer)
    return mc.migrate(
        reader=reader,
        partition=PARTITION,
        station_uuid=STATION,
        selectors=SELECTORS,
        hours=hours,
        log=lambda *_: None,
        **kwargs,
    )


class IdentityTests(unittest.TestCase):
    """The mapping from the registered sample, which must not be retyped."""

    def test_reads_station_parent_and_selectors_from_the_sample(self):
        """Identity is read from the sample, so a typo cannot invent a station."""
        station, parent, selectors = mc.identities_from_sample(SAMPLES)
        self.assertEqual(station, STATION)
        self.assertEqual(parent, 'dd4b923d-945d-47b3-aff3-de032d15f864')
        self.assertEqual(selectors, SELECTORS)

    def test_station_and_parent_are_different_values(self):
        """If these were ever conflated, rows would land in the wrong partition."""
        station, parent, _ = mc.identities_from_sample(SAMPLES)
        self.assertNotEqual(station, parent)

    def test_a_sample_without_selectors_is_refused(self):
        """Without selectors the reader would address the wrong partition."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.json'
            path.write_text(json.dumps({'station_uuid': STATION}))
            with self.assertRaises(mc.MigrationError):
                mc.identities_from_sample(path)


class EstateTests(unittest.TestCase):
    """The estate inventory: source identity in, display identity out."""

    @classmethod
    def setUpClass(cls):
        """Load the estate inventory once for the whole class."""
        cls.estate = mc.load_estate()

    # STATION above is Parvathi PH (the already-registered one); Saraswati is
    # the station that actually runs, so both are worth naming explicitly.
    SARASWATI = '9c09411d-ab94-44b8-a398-8768f58c223e'

    def test_a_station_resolves_by_plant_name_display_name_and_uuid(self):
        """All three identifiers a caller may hold must reach the same station."""
        by_plant = mc.resolve_station('Saraswati PH', self.estate)
        by_display = mc.resolve_station('Millbrook Influent Pump Station', self.estate)
        by_uuid = mc.resolve_station(self.SARASWATI, self.estate)
        self.assertEqual(by_plant, by_display)
        self.assertEqual(by_plant, by_uuid)

    def test_the_registered_station_resolves_to_its_own_display_name(self):
        """The demo label resolves as readily as the plant name it replaces."""
        parvathi = mc.resolve_station(STATION, self.estate)
        self.assertEqual(parvathi['source_name'], 'Parvathi PH')
        self.assertEqual(parvathi['display_name'], 'Cedar Creek Effluent Pump Station')

    def test_lookup_ignores_case(self):
        """Operators type names, and case is not identity."""
        self.assertEqual(
            mc.resolve_station('saraswati ph', self.estate)['source_uuid'],
            mc.resolve_station(self.SARASWATI.upper(), self.estate)['source_uuid'],
        )

    def test_an_unknown_station_is_refused_and_lists_the_known_ones(self):
        """A refusal that names the alternatives saves a second round trip."""
        with self.assertRaises(mc.MigrationError) as caught:
            mc.resolve_station('Nowhere PH', self.estate)
        self.assertIn('Saraswati PH', str(caught.exception))

    def test_display_names_never_replace_source_uuids(self):
        """A label must not be able to move data.

        `source_uuid` is the Cassandra entity_uuid, the Cosmos partition key
        and the registry's source_entity_uuid all at once. Renaming a station
        is a presentation change; if it altered identity, migrated documents
        would land in a partition the connector never reads.
        """
        for station in {s['source_uuid']: s for s in self.estate.values()}.values():
            self.assertNotEqual(station['display_name'], station['source_uuid'])
            self.assertRegex(station['source_uuid'], r'^[0-9a-f-]{36}$')

    def test_every_station_has_both_names_and_a_pump_count(self):
        """An entry missing either name cannot be mapped in either direction."""
        seen = {s['source_uuid']: s for s in self.estate.values()}
        self.assertEqual(len(seen), 15, 'the estate is 15 pump stations')
        for station in seen.values():
            self.assertTrue(station['source_name'])
            self.assertTrue(station['display_name'])
            self.assertIsInstance(station['pumps_stated'], int)

    def test_observed_pump_counts_agree_with_the_stated_ones(self):
        """Measured against the dump; a disagreement is a real inventory bug."""
        for station in {s['source_uuid']: s for s in self.estate.values()}.values():
            if station.get('pumps_observed') is None:
                continue
            self.assertEqual(
                station['pumps_observed'],
                station['pumps_stated'],
                f'{station["source_name"]} bay count disagrees',
            )

    def _stations(self):
        """Every station entry in the inventory."""
        return {s['source_uuid']: s for s in self.estate.values()}.values()

    def test_total_discharge_is_derived_from_per_pump_and_count(self):
        """TDIS = DPP x PC, verified for every row that supplies both.

        Stored for traceability, but it is not an independent input: a value
        that can be recomputed will eventually disagree with its inputs, and
        this is what catches that.
        """
        checked = 0
        for st in self._stations():
            dpp, tdis = st.get('discharge_per_pump'), st.get('total_discharge')
            if dpp is None or tdis is None:
                continue
            self.assertAlmostEqual(
                dpp * st['pumps_stated'],
                tdis,
                places=2,
                msg=f'{st["source_name"]}: {dpp} x {st["pumps_stated"]} != {tdis}',
            )
            checked += 1
        self.assertEqual(checked, 14, 'expected 14 rows with both figures')

    def test_observed_power_never_exceeds_rated(self):
        """Observed above rated would mean the two figures describe different things."""
        for st in self._stations():
            obp, rtp = st.get('observed_power_mw'), st.get('rated_power_mw')
            if obp is None or rtp is None:
                continue
            self.assertLessEqual(obp, rtp, st['source_name'])

    def test_the_running_station_nameplate_matches_the_measured_power(self):
        """Saraswati's observed 24.5 MW is what confirms ACTIVE_POWER is MW.

        Measured from its running pump: ACTIVE_POWER = /pmw = 24.5, against a
        40 MW rating. At kW that would be 0.06% of rated.
        """
        saraswati = mc.resolve_station('Saraswati PH', self.estate)
        self.assertEqual(saraswati['observed_power_mw'], 24.5)
        self.assertEqual(saraswati['rated_power_mw'], 40.0)

    def test_source_coordinates_are_present_and_in_the_source_region(self):
        """Source coordinates are real; the display ones are still absent."""
        for st in self._stations():
            lat, lon = st['source_latitude'], st['source_longitude']
            self.assertIsNotNone(lat, st['source_name'])
            self.assertIsNotNone(lon, st['source_name'])
            self.assertTrue(17.0 < lat < 20.0, f'{st["source_name"]} lat {lat}')
            self.assertTrue(77.0 < lon < 81.0, f'{st["source_name"]} lon {lon}')

    def test_display_coordinates_are_pending_or_plausibly_us(self):
        """Either unset, or inside the continental US - never left as source.

        A display coordinate that silently kept the source value would put a
        US-labelled facility in Telangana on any map view, which is the one
        outcome the relabelling exists to avoid.
        """
        for st in self._stations():
            lat, lon = st.get('display_latitude'), st.get('display_longitude')
            if lat is None and lon is None:
                continue
            self.assertIsNotNone(lat, st['source_name'])
            self.assertIsNotNone(lon, st['source_name'])
            self.assertTrue(24.0 < lat < 50.0, f'{st["source_name"]} lat {lat}')
            self.assertTrue(-125.0 < lon < -66.0, f'{st["source_name"]} lon {lon}')

    def test_only_stations_on_feed_41_carry_dex_tags(self):
        """The dictionary and mimic are built on `dex`; feed 104 has none.

        Pinned because it decides what an onboarding can possibly yield: a
        station seen only on 104 can never produce instrument-level points.
        """
        for station in {s['source_uuid']: s for s in self.estate.values()}.values():
            if not station.get('present_in_dump'):
                continue
            if station.get('dex_tags'):
                self.assertIn(41, station['feeds'], station['source_name'])


class TableAndHourTests(unittest.TestCase):
    """Which month table a run reads, and over which hours."""

    def test_table_follows_the_hour_in_utc(self):
        """The table is chosen in UTC; local time would cross months an hour late."""
        self.assertEqual(mc.table_for(HOUR), 'iwm_data_202507')

    def test_hours_are_aligned_down_and_one_partition_past_the_end(self):
        """Start floors to the hour; the end reads one partition beyond.

        The trailing partition is not an off-by-one - it is where the range's
        own hh:59:59.999 sample is filed. `migrate()` discards that partition's
        other rows via `sample_range`.
        """
        hours = mc.source_buckets(HOUR + 90_000, HOUR + 2 * mc.HOUR_MS)
        self.assertEqual(hours, [HOUR, HOUR + mc.HOUR_MS, HOUR + 2 * mc.HOUR_MS])

    def test_an_empty_range_is_refused_rather_than_silently_doing_nothing(self):
        """A run that reads nothing and reports success is the worst outcome."""
        with self.assertRaises(mc.MigrationError):
            mc.source_buckets(HOUR, HOUR)

    def test_one_partition_past_the_end_is_read_for_its_boundary_row(self):
        """Regression: the last hour of every range was short by one document.

        The source files a sample at hh:59:59.999 under the NEXT hour, so the
        final sample of a range physically lives in the partition after it.
        Reading only [start, end) missed it - measured on the live account,
        hour 23:00 of a full day held 720 of 721 documents.
        """
        buckets = mc.source_buckets(HOUR, HOUR + mc.HOUR_MS)
        self.assertEqual(buckets, [HOUR, HOUR + mc.HOUR_MS])

        day = mc.source_buckets(HOUR, HOUR + 24 * mc.HOUR_MS)
        self.assertEqual(len(day), 25, '24 hours of data needs 25 partitions')

    def test_out_of_range_rows_from_the_extra_partition_are_discarded(self):
        """The extra partition is opened for one row, not for its contents."""
        boundary = HOUR + mc.HOUR_MS - 1  # 1 ms inside the range
        beyond = HOUR + mc.HOUR_MS + 5_000  # squarely in the next hour
        rows = [
            cassandra_row(boundary, hour=HOUR + mc.HOUR_MS),
            cassandra_row(beyond, hour=HOUR + mc.HOUR_MS),
        ]
        written: list[dict] = []
        outcome = mc.migrate(
            reader=FakeReader({HOUR + mc.HOUR_MS: rows}),
            partition=PARTITION,
            station_uuid=STATION,
            selectors=SELECTORS,
            hours=[HOUR + mc.HOUR_MS],
            writer=lambda d: (written.append(d), 1.0)[1],
            sample_range=(HOUR, HOUR + mc.HOUR_MS),
            log=lambda *_: None,
        )
        self.assertEqual(outcome.documents, 1, 'only the boundary row belongs')
        self.assertEqual(outcome.rows_out_of_range, 1)
        self.assertEqual(written[0]['id'], str(boundary))
        # And it is filed under the hour that contains it, not the one it came from.
        self.assertEqual(written[0]['hour_bucket'], str(HOUR))

    def test_an_unknown_bucket_mode_is_refused(self):
        """An unrecognised mode would file every sample under the wrong hour."""
        with self.assertRaises(mc.MigrationError):
            mc.source_buckets(HOUR, HOUR + mc.HOUR_MS, 'guess')


class LaggedBucketTests(unittest.TestCase):
    """The half-hour-lagged rule: a sample at 08:29 is filed under 07:30.

    Not the default. Measured against `iwm_data_202507`, every one of 729
    distinct time_period values is hour-aligned and both halves of each hour
    are populated, which only the `hour` rule permits. This mode exists because
    a different feed may use the lagged convention, and picking the wrong rule
    returns zero rows rather than wrong ones - a silent failure worth being
    able to switch deliberately.
    """

    def test_the_worked_example_holds(self):
        """08:29 -> 07:30, exactly as described."""
        eight_29 = mc._parse_moment('2025-07-02T08:29:00')
        seven_30 = mc._parse_moment('2025-07-02T07:30:00')
        self.assertEqual(mc.bucket_for_sample(eight_29, 'lagged'), seven_30)

    def test_hour_mode_puts_the_same_sample_in_its_own_hour(self):
        """Hour mode is the production convention: 08:29 belongs to 08:00."""
        eight_29 = mc._parse_moment('2025-07-02T08:29:00')
        eight_00 = mc._parse_moment('2025-07-02T08:00:00')
        self.assertEqual(mc.bucket_for_sample(eight_29, 'hour'), eight_00)

    def test_lagged_buckets_step_by_half_an_hour(self):
        """The lagged convention steps on the half hour, not the hour."""
        buckets = mc.source_buckets(HOUR, HOUR + mc.HOUR_MS, 'lagged')
        # Also carries one bucket past the range, for the same boundary reason
        # as `hour` mode: under lagged, bucket B covers [B+30m, B+1h).
        self.assertEqual(buckets, [HOUR - mc.HALF_MS, HOUR, HOUR + mc.HALF_MS])

    def test_every_lagged_bucket_precedes_the_samples_it_holds(self):
        """Bucket B holds samples in [B+30m, B+1h), so B is always earlier."""
        for offset in (0, 61_000, mc.HALF_MS - 1, mc.HALF_MS, mc.HOUR_MS - 1):
            sample = HOUR + offset
            bucket = mc.bucket_for_sample(sample, 'lagged')
            self.assertLessEqual(bucket + mc.HALF_MS, sample)
            self.assertLess(sample, bucket + mc.HOUR_MS)

    def test_cosmos_still_stores_the_real_hour_under_lagged(self):
        """Whatever the source's rule, the document's bucket holds its sample.

        This is the invariant the connector reads by, so it cannot inherit a
        lagged bucket: the document would sit in a partition no reader queries
        for that instant.
        """
        # A sample 20 min into the hour: its lagged bucket is the PREVIOUS
        # half-hour, so source bucket and real hour genuinely differ here.
        sample = HOUR + 20 * 60_000
        lagged = mc.bucket_for_sample(sample, 'lagged')
        self.assertEqual(lagged, HOUR - mc.HALF_MS)

        row = cassandra_row(sample, hour=lagged)
        written: list[dict] = []
        outcome = mc.migrate(
            reader=FakeReader({lagged: [row]}),
            partition=PARTITION,
            station_uuid=STATION,
            selectors=SELECTORS,
            hours=[lagged],
            writer=lambda d: (written.append(d), 1.0)[1],
            bucket_mode='lagged',
            log=lambda *_: None,
        )

        doc = written[0]
        bucket = int(doc['hour_bucket'])
        self.assertEqual(bucket, HOUR, 'Cosmos must hold the real hour')
        self.assertLessEqual(bucket, doc['sub_time_period'])
        self.assertLess(doc['sub_time_period'], bucket + mc.HOUR_MS)
        # The bucket really was rewritten, and under lagged that is by design.
        self.assertEqual(outcome.rebucketed, 1)
        self.assertEqual(outcome.unexpected_rebucket, 0)


class DocumentTests(unittest.TestCase):
    """The document must be exactly what the connector expects to read back."""

    def test_partition_key_fields_are_set_from_the_row(self):
        """Both partition key components come from the row, never from a default."""
        written: list[dict] = []
        run(FakeReader({HOUR: [cassandra_row(HOUR + 5)]}), [HOUR], written=written)

        doc = written[0]
        self.assertEqual(doc['station_uuid'], STATION)
        self.assertEqual(doc['hour_bucket'], str(HOUR))
        self.assertEqual(doc['id'], str(HOUR + 5))
        self.assertEqual(doc['month'], '202507')

    def test_hour_bucket_is_a_string_and_sub_time_period_an_int(self):
        """The container indexes both; mixing the types breaks ORDER BY on them."""
        written: list[dict] = []
        run(FakeReader({HOUR: [cassandra_row(HOUR + 5)]}), [HOUR], written=written)

        self.assertIsInstance(written[0]['hour_bucket'], str)
        self.assertIsInstance(written[0]['sub_time_period'], int)

    def test_parent_entity_uuid_is_preserved_not_replaced_by_the_station(self):
        """The shared parent identifies the tenant; overwriting it would lose that."""
        written: list[dict] = []
        run(FakeReader({HOUR: [cassandra_row(HOUR + 5)]}), [HOUR], written=written)

        self.assertEqual(
            written[0]['parent_entity_uuid'], 'dd4b923d-945d-47b3-aff3-de032d15f864'
        )
        self.assertEqual(written[0]['entity_uuid'], STATION)

    def test_raw_payload_round_trips_and_is_hashed(self):
        """The reader re-parses data1_raw, so it has to survive byte for byte."""
        written: list[dict] = []
        run(FakeReader({HOUR: [cassandra_row(HOUR + 5)]}), [HOUR], written=written)

        doc = written[0]
        self.assertEqual(json.loads(doc['data1_raw']), sample_payload())
        self.assertTrue(doc['payload_hash'].startswith('sha256:'))

    def test_out_of_range_values_are_carried_through_at_full_precision(self):
        """Out-of-range readings must survive verbatim, not rounded or dropped.

        The sample file's prose rounds these to -242.1 and 3276.7; the payload
        actually carries -242.09999084472656 and 3276.699951171875. The full
        values are asserted because truncating a reading is a decision about
        the plant that this pipeline has no standing to make.
        """
        written: list[dict] = []
        run(FakeReader({HOUR: [cassandra_row(HOUR + 5)]}), [HOUR], written=written)

        raw = written[0]['data1_raw']
        self.assertIn('-242.09999084472656', raw)
        self.assertIn('3276.699951171875', raw)
        # And the doubled-prefix / misspelled / spaced keys the source emits.
        self.assertIn('PUMP13_PUMP_POWERFATCOR', raw)
        self.assertIn('PUMP6_PUMP_MOTOR_HOT_AIR TEMP2', raw)

    def test_a_row_for_another_station_is_skipped_not_migrated(self):
        """A shared partition is the normal case, not corruption.

        Confirmed against the real data: one partition carries rows for
        776913b4, bafc976f and 4162f034 interleaved, because entity_uuid is a
        clustering column rather than part of the partition key. Failing here
        would make the tool unusable on the actual source.
        """
        mine = cassandra_row(HOUR + 5)
        theirs = cassandra_row(HOUR + 6)
        theirs['entity_uuid'] = '776913b4-d26f-4294-bf58-647ac401bae5'

        written: list[dict] = []
        outcome = run(FakeReader({HOUR: [mine, theirs]}), [HOUR], written=written)

        self.assertEqual(outcome.documents, 1)
        self.assertEqual(outcome.rows_read, 2)
        self.assertEqual(outcome.rows_other_stations, 1)
        self.assertEqual(
            outcome.other_stations, {'776913b4-d26f-4294-bf58-647ac401bae5'}
        )
        self.assertEqual([d['station_uuid'] for d in written], [STATION])

    def test_a_row_with_no_entity_uuid_is_refused(self):
        """Skipping is for *other* stations; an unidentifiable row is a defect."""
        row = cassandra_row(HOUR + 5)
        del row['entity_uuid']
        with self.assertRaises(mc.MigrationError):
            run(FakeReader({HOUR: [row]}), [HOUR], written=[])

    def test_skipped_foreign_rows_still_advance_the_resume_cursor(self):
        """Otherwise a partition ending in foreign rows is re-read forever."""
        theirs = cassandra_row(HOUR + 9)
        theirs['entity_uuid'] = '776913b4-d26f-4294-bf58-647ac401bae5'
        progress = mc.Progress()

        run(
            FakeReader({HOUR: [cassandra_row(HOUR + 5), theirs]}),
            [HOUR],
            progress=progress,
            max_docs=1,
            written=[],
        )
        # The document limit hit on row 1, so the hour is partial at that point.
        self.assertEqual(progress.partial[HOUR], HOUR + 5)

    def test_a_sample_outside_the_queried_hour_is_refiled_and_counted(self):
        """Out-of-hour samples are re-bucketed, not refused.

        The shared builder enforces `bucket <= sample < bucket+1h`, and the
        migrator satisfies it by deriving the bucket from the sample rather
        than copying the source's. So the invariant can no longer be violated
        by construction - which is what lets the real `hh:59:59.999` boundary
        rows through - and the rewrite is counted rather than silent.
        """
        stray = HOUR + mc.HOUR_MS + 1
        written: list[dict] = []
        outcome = run(
            FakeReader({HOUR: [cassandra_row(stray)]}), [HOUR], written=written
        )

        bucket = int(written[0]['hour_bucket'])
        self.assertEqual(bucket, HOUR + mc.HOUR_MS, 'filed under its own hour')
        self.assertLessEqual(bucket, written[0]['sub_time_period'])
        self.assertLess(written[0]['sub_time_period'], bucket + mc.HOUR_MS)
        self.assertEqual(outcome.rebucketed, 1)
        self.assertEqual(outcome.unexpected_rebucket, 1, 'surprising under hour mode')

    def test_unreadable_data1_is_refused(self):
        """A payload that will not parse must stop the run rather than be written."""
        row = cassandra_row(HOUR + 5)
        row['data1'] = '{not json'
        with self.assertRaises(mc.MigrationError):
            run(FakeReader({HOUR: [row]}), [HOUR], written=[])


class SeederEquivalenceTests(unittest.TestCase):
    """The migrated document must be indistinguishable from a seeded one.

    This is the most valuable guarantee here. The connector's read path is
    already proven against documents `seed.py` wrote to the live account, so if
    a migrated document is byte-identical to a seeded one, no separate proof
    that the dashboard can read migrated data is needed - it is the same data.

    It also pins decision D13: `parent_entity_uuid` is the identifier shared
    across pumphouses under one parent and is genuinely NOT the station's own
    uuid. The seeder falls back to the station value only for hand-written
    snapshots that omit a parent, so a migration that took that fallback would
    write a wrong-but-plausible identifier.
    """

    def test_migrated_documents_match_seed_py_field_for_field(self):
        """One normalizer: the reader must not be able to tell migrated from seeded."""
        import seed

        station, parent, selectors = mc.identities_from_sample(SAMPLES)
        seed_station, seed_selectors, snapshots = seed.load_snapshots(SAMPLES, None)
        expected = {
            d['id']: d
            for d in (
                seed.build_document(s, seed_station, seed_selectors) for s in snapshots
            )
        }

        rows_by_hour: dict[int, list[dict]] = {}
        for snapshot in json.loads(SAMPLES.read_text())['snapshots']:
            hour = int(snapshot['time_period'])
            rows_by_hour.setdefault(hour, []).append(
                cassandra_row(snapshot['sub_time_period'], snapshot['data1'], hour)
            )

        produced: list[dict] = []
        mc.migrate(
            reader=FakeReader(rows_by_hour),
            partition=mc.Partition(parent, 'PUMP_HOUSE', 65, 41),
            station_uuid=station,
            selectors=selectors,
            hours=sorted(rows_by_hour),
            writer=lambda document: (produced.append(document), 1.0)[1],
            log=lambda *_: None,
        )

        self.assertEqual({d['id'] for d in produced}, set(expected))
        for document in produced:
            want = expected[document['id']]
            for key, value in want.items():
                if key == 'ingested_at':
                    continue  # A wall-clock stamp; expected to differ.
                if key == 'dex':
                    # Deliberately omitted by the migrator: ~51 KB duplicating
                    # bytes the reader re-parses from data1_raw anyway. Asserted
                    # below rather than skipped silently.
                    continue
                self.assertEqual(document[key], value, f'field {key!r} diverged')

            # The authoritative payload and its attestation must be identical -
            # that is what makes the two forms interchangeable to a reader.
            self.assertEqual(document['data1_raw'], want['data1_raw'])
            self.assertEqual(document['payload_hash'], want['payload_hash'])
            self.assertNotIn('dex', document)
            self.assertIn('dex', want, 'the seeder still inlines it')

    def test_the_parent_written_is_the_shared_parent_not_the_station(self):
        """Writing the station as its own parent breaks tenant-scoped reads."""
        station, parent, selectors = mc.identities_from_sample(SAMPLES)
        written: list[dict] = []
        mc.migrate(
            reader=FakeReader({HOUR: [cassandra_row(HOUR + 5)]}),
            partition=mc.Partition(parent, 'PUMP_HOUSE', 65, 41),
            station_uuid=station,
            selectors=selectors,
            hours=[HOUR],
            writer=lambda d: (written.append(d), 1.0)[1],
            log=lambda *_: None,
        )
        self.assertEqual(written[0]['parent_entity_uuid'], parent)
        self.assertNotEqual(written[0]['parent_entity_uuid'], station)


class CompactDocumentTests(unittest.TestCase):
    """Omitting the parsed `dex` halves the document and loses nothing.

    `flatten_snapshot` re-parses `data1_raw` whenever it is present, so the
    parsed tag map is a duplicate of bytes the reader already has, and the
    indexing policy excludes `/*` so it is not queryable either. Verified
    against the real reader: 919 readings from both forms, identical.

    This matters because writes are the migration bottleneck - a full document
    is ~107 KB and the parsed `dex` is ~51 KB of it.
    """

    def _build(self, inline):
        """Build one document with the extension inlined or omitted."""
        import seed

        snap = {
            'time_period': str(HOUR),
            'sub_time_period': HOUR + 5,
            'data1': sample_payload(),
        }
        return seed.build_document(snap, STATION, SELECTORS, inline_extension=inline)

    def test_omitting_dex_roughly_halves_the_document(self):
        """Bytes on the wire, not throughput, was the real migration limit."""
        fat = len(json.dumps(self._build(True), separators=(',', ':')).encode())
        lean = len(json.dumps(self._build(False), separators=(',', ':')).encode())
        self.assertLess(lean, fat * 0.75, f'{lean} is not much smaller than {fat}')

    def test_dex_is_omitted_entirely_not_emptied(self):
        """An empty map would contradict data1_raw and fail verification."""
        self.assertNotIn('dex', self._build(False))
        self.assertIn('dex', self._build(True))

    def test_the_raw_payload_and_its_hash_are_unchanged(self):
        """Compacting may drop only what is redundant, never what is authoritative."""
        fat, lean = self._build(True), self._build(False)
        self.assertEqual(fat['data1_raw'], lean['data1_raw'])
        self.assertEqual(fat['payload_hash'], lean['payload_hash'])

    def test_the_compact_form_passes_the_writers_own_verification(self):
        """The writer verifies what it wrote; the compact form must satisfy that too."""
        import seed

        seed.verify(self._build(False))  # raises on failure

    def test_a_duplicated_field_that_disagrees_is_still_rejected(self):
        """Relaxing "every field" to "duplicated fields" must not relax that."""
        import seed

        doc = self._build(False)
        doc['st'] = 'WRONG'
        with self.assertRaises(seed.SeedError):
            seed.verify(doc)

    def test_the_migrator_defaults_to_the_compact_form(self):
        """The cheap form is the default, so nobody pays double by forgetting a flag."""
        written: list[dict] = []
        run(FakeReader({HOUR: [cassandra_row(HOUR + 5)]}), [HOUR], written=written)
        self.assertNotIn('dex', written[0], 'compact by default')
        self.assertIn('data1_raw', written[0], 'the authoritative copy stays')

    def test_inline_extension_can_be_asked_for(self):
        """The inlined form stays available for anyone who needs it."""
        written: list[dict] = []
        run(
            FakeReader({HOUR: [cassandra_row(HOUR + 5)]}),
            [HOUR],
            written=written,
            inline_extension=True,
        )
        self.assertIn('dex', written[0])


class DryRunTests(unittest.TestCase):
    """A dry run exercises everything except the write itself."""

    def test_no_writer_means_nothing_is_written_but_rows_are_still_checked(self):
        """A preview that skipped validation would approve a run that cannot work."""
        outcome = run(FakeReader({HOUR: [cassandra_row(HOUR + 5)]}), [HOUR])
        self.assertEqual(outcome.documents, 1)
        self.assertEqual(outcome.ru, 0.0)


class ResumeTests(unittest.TestCase):
    """Resuming must neither re-write a sample nor skip one."""

    def test_a_completed_hour_is_skipped_entirely(self):
        """A finished hour is not re-read, so a resume costs nothing for it."""
        reader = FakeReader({HOUR: [cassandra_row(HOUR + 5)]})
        progress = mc.Progress(completed_hours={HOUR})

        outcome = run(reader, [HOUR], progress=progress, written=[])

        self.assertEqual(outcome.hours_skipped, 1)
        self.assertEqual(outcome.documents, 0)
        self.assertEqual(reader.calls, [], 'a skipped hour must not be queried')

    def test_document_limit_records_partial_progress_not_completion(self):
        """Marking a truncated hour complete would silently lose its remainder."""
        rows = [cassandra_row(HOUR + n) for n in (1, 2, 3)]
        progress = mc.Progress()

        outcome = run(
            FakeReader({HOUR: rows}), [HOUR], progress=progress, max_docs=2, written=[]
        )

        self.assertEqual(outcome.documents, 2)
        self.assertTrue(outcome.stopped_early)
        self.assertNotIn(HOUR, progress.completed_hours)
        self.assertEqual(progress.partial[HOUR], HOUR + 2)

    def test_resuming_reads_only_what_came_after(self):
        """Resuming re-reads from the cursor, not from the top of the hour."""
        rows = [cassandra_row(HOUR + n) for n in (1, 2, 3)]
        progress = mc.Progress(partial={HOUR: HOUR + 2})
        written: list[dict] = []

        run(FakeReader({HOUR: rows}), [HOUR], progress=progress, written=written)

        self.assertEqual([d['id'] for d in written], [str(HOUR + 3)])
        self.assertIn(HOUR, progress.completed_hours)
        self.assertNotIn(HOUR, progress.partial)

    def test_an_exhausted_hour_with_no_rows_still_counts_as_done(self):
        """An empty hour is a migrated hour; re-reading it forever is the bug."""
        progress = mc.Progress()
        run(FakeReader({}), [HOUR], progress=progress, written=[])
        self.assertIn(HOUR, progress.completed_hours)

    def test_progress_survives_a_round_trip_through_disk(self):
        """Progress is only useful if it outlives the process that wrote it."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'
            progress = mc.Progress(completed_hours={HOUR}, partial={HOUR + 1: 42})
            progress.save(path)

            reloaded = mc.Progress.load(path)
            self.assertEqual(reloaded.completed_hours, {HOUR})
            self.assertEqual(reloaded.partial, {HOUR + 1: 42})

    def test_a_missing_progress_file_is_a_fresh_start_not_an_error(self):
        """A first run has no progress file, and that is not a failure."""
        with tempfile.TemporaryDirectory() as tmp:
            reloaded = mc.Progress.load(Path(tmp) / 'absent.json')
            self.assertEqual(reloaded.completed_hours, set())


class RuBudgetTests(unittest.TestCase):
    """The cap exists to protect live dashboard reads on a shared 400 RU/s."""

    def setUp(self):
        """A fake clock and sleeper, so the cap is tested without waiting."""
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self):
        """The current fake time."""
        return self.now

    def sleeper(self, seconds):
        """Record a wait and advance the fake clock by it."""
        self.slept.append(seconds)
        self.now += seconds

    def budget(self, cap):
        """A budget wired to the fake clock and sleeper."""
        return mc.RuBudget(cap, clock=self.clock, sleeper=self.sleeper)

    def test_spending_within_the_allowance_does_not_wait(self):
        """Throttling a run that is already under the cap would waste the window."""
        budget = self.budget(100.0)
        budget.spend(50.0)
        self.assertEqual(self.slept, [])

    def test_sustained_spend_is_held_at_the_cap(self):
        """Sustained spend settles at the cap rather than overshooting it."""
        budget = self.budget(100.0)
        for _ in range(5):
            budget.spend(100.0)

        self.assertEqual(budget.total_ru, 500.0)
        # One burst is free; each later 100 RU costs a second at 100 RU/s.
        self.assertAlmostEqual(budget.total_waited, 4.0, places=5)

    def test_a_single_charge_above_the_cap_still_completes(self):
        """A 96.76 RU document under a 10 RU/s cap must not deadlock."""
        budget = self.budget(10.0)
        budget.spend(96.76)
        self.assertAlmostEqual(budget.total_ru, 96.76, places=5)

    def test_a_nonpositive_cap_is_refused(self):
        """A zero or negative cap would either stall forever or not cap at all."""
        with self.assertRaises(mc.MigrationError):
            mc.RuBudget(0)

    def test_the_migration_charges_the_budget_what_the_writer_reports(self):
        """The budget is charged the measured RU, not an estimate of it."""
        rows = [cassandra_row(HOUR + n) for n in (1, 2)]
        budget = self.budget(1000.0)

        outcome = mc.migrate(
            reader=FakeReader({HOUR: rows}),
            partition=PARTITION,
            station_uuid=STATION,
            selectors=SELECTORS,
            hours=[HOUR],
            writer=lambda _doc: 96.76,
            budget=budget,
            log=lambda *_: None,
        )

        self.assertAlmostEqual(outcome.ru, 2 * 96.76, places=5)


class ConcurrentWriteTests(unittest.TestCase):
    """Concurrency must not let the resume cursor outrun durable writes."""

    def test_all_documents_are_written_across_batch_boundaries(self):
        """Nothing may fall between two batches."""
        rows = [cassandra_row(HOUR + n) for n in range(1, 21)]
        written: list[dict] = []
        outcome = run(
            FakeReader({HOUR: rows}), [HOUR], written=written, write_concurrency=8
        )
        self.assertEqual(outcome.documents, 20)
        self.assertEqual(len(written), 20)
        self.assertEqual(
            sorted(int(d['id']) for d in written), [HOUR + n for n in range(1, 21)]
        )

    def test_concurrency_of_one_matches_concurrent_results(self):
        """Concurrency changes the speed and nothing else about the result."""
        rows = [cassandra_row(HOUR + n) for n in range(1, 13)]
        serial, concurrent = [], []
        a = run(FakeReader({HOUR: rows}), [HOUR], written=serial, write_concurrency=1)
        b = run(
            FakeReader({HOUR: rows}), [HOUR], written=concurrent, write_concurrency=6
        )
        self.assertEqual(a.documents, b.documents)
        self.assertEqual(
            sorted(d['id'] for d in serial), sorted(d['id'] for d in concurrent)
        )

    def test_a_failed_write_stops_the_run_and_does_not_finish_the_hour(self):
        """An hour with a failed write must not be recorded as complete."""
        rows = [cassandra_row(HOUR + n) for n in range(1, 13)]
        progress = mc.Progress()

        def writer(document):
            """Fail on one nominated document, succeed on the rest."""
            if document['id'] == str(HOUR + 5):
                raise RuntimeError('cosmos said no')
            return 1.0

        with self.assertRaises(mc.MigrationError):
            mc.migrate(
                reader=FakeReader({HOUR: rows}),
                partition=PARTITION,
                station_uuid=STATION,
                selectors=SELECTORS,
                hours=[HOUR],
                writer=writer,
                progress=progress,
                write_concurrency=8,
                log=lambda *_: None,
            )
        self.assertNotIn(HOUR, progress.completed_hours)

    def test_the_cursor_stops_at_the_last_contiguous_success(self):
        """The property that makes a partial batch safe to resume.

        Writes finish out of order, so the cursor must advance only through an
        unbroken run of successes. Taking the newest success instead would mark
        a never-written document as done, and nothing would revisit it.
        """
        pending = [(HOUR + n, {'id': str(HOUR + n)}) for n in range(1, 9)]

        def writer(document):
            """Fail on one nominated document, succeed on the rest."""
            if document['id'] == str(HOUR + 4):
                raise RuntimeError('boom')
            return 1.0

        charges, cursor, failure = mc._write_batch(pending, writer, workers=8)

        self.assertIsNotNone(failure)
        self.assertEqual(cursor, HOUR + 3, 'cursor must not pass the failure')
        self.assertEqual(len(charges), 3)

    def test_a_failure_on_the_very_first_write_leaves_the_cursor_unset(self):
        """With nothing durably written there is no position to resume from."""
        pending = [(HOUR + n, {'id': str(HOUR + n)}) for n in range(1, 5)]

        def writer(_document):
            """Fail on the first document offered."""
            raise RuntimeError('boom')

        charges, cursor, failure = mc._write_batch(pending, writer, workers=4)
        self.assertIsNone(cursor)
        self.assertEqual(charges, [])
        self.assertIsNotNone(failure)

    def test_writes_really_do_overlap(self):
        """Otherwise this is just a buffer with extra steps."""
        import threading

        concurrent_peak = 0
        live = 0
        lock = threading.Lock()
        gate = threading.Barrier(4, timeout=5)

        def writer(_document):
            """Record how many writes are in flight at once."""
            nonlocal concurrent_peak, live
            with lock:
                live += 1
                concurrent_peak = max(concurrent_peak, live)
            try:
                gate.wait()  # deadlocks unless 4 run at once
            except threading.BrokenBarrierError:
                pass
            with lock:
                live -= 1
            return 1.0

        pending = [(HOUR + n, {'id': str(HOUR + n)}) for n in range(1, 5)]
        mc._write_batch(pending, writer, workers=4)
        self.assertEqual(concurrent_peak, 4)

    def test_a_dry_run_advances_the_cursor_without_writing(self):
        """A dry run still reports where a real run would have got to."""
        pending = [(HOUR + n, {'id': str(HOUR + n)}) for n in range(1, 5)]
        charges, cursor, failure = mc._write_batch(pending, None, workers=8)
        self.assertEqual(charges, [])
        self.assertEqual(cursor, HOUR + 4)
        self.assertIsNone(failure)

    def test_max_docs_is_exact_at_every_concurrency(self):
        """Regression: buffering made --max-docs overshoot by up to a batch.

        The limit has to count written plus buffered documents, because
        everything buffered will be written. Checking only the written count
        let a batch of 8 land when 2 were asked for - which on a live account
        is real, billed writes nobody authorised.
        """
        rows = [cassandra_row(HOUR + n) for n in range(1, 31)]
        for workers in (1, 2, 4, 8, 16):
            for limit in (1, 2, 5, 7, 13):
                written: list[dict] = []
                outcome = run(
                    FakeReader({HOUR: rows}),
                    [HOUR],
                    written=written,
                    write_concurrency=workers,
                    max_docs=limit,
                )
                self.assertEqual(
                    outcome.documents,
                    limit,
                    f'concurrency={workers} limit={limit} wrote {outcome.documents}',
                )
                self.assertEqual(len(written), limit)

    def test_a_document_limit_still_records_a_resumable_cursor(self):
        """Stopping on the limit must leave a position the next run can resume from."""
        rows = [cassandra_row(HOUR + n) for n in range(1, 21)]
        progress = mc.Progress()
        run(
            FakeReader({HOUR: rows}),
            [HOUR],
            written=[],
            progress=progress,
            write_concurrency=8,
            max_docs=5,
        )
        self.assertNotIn(HOUR, progress.completed_hours)
        self.assertEqual(progress.partial[HOUR], HOUR + 5)

    def test_progress_is_checkpointed_mid_hour_not_only_at_the_boundary(self):
        """A hard kill must not discard the whole hour's cursor.

        Verified against the live account: killing a run mid-hour left 409
        documents written but uncheckpointed, so resuming re-read the entire
        hour. Saving after each batch caps the redo at one batch.
        """
        rows = [cassandra_row(HOUR + n) for n in range(1, 25)]
        saves: list[int | None] = []
        progress = mc.Progress()
        real_save = progress.save

        def spy(path):
            """Record every progress save."""
            saves.append(progress.partial.get(HOUR))
            real_save(path)

        progress.save = spy
        run(
            FakeReader({HOUR: rows}),
            [HOUR],
            written=[],
            progress=progress,
            write_concurrency=8,
            progress_path=None,
        )
        mid = [s for s in saves if s is not None]
        self.assertTrue(mid, 'expected at least one mid-hour checkpoint')
        self.assertIn(HOUR + 8, mid, 'first batch boundary should be recorded')
        # And once the hour finishes, the partial marker is cleared.
        self.assertIn(HOUR, progress.completed_hours)
        self.assertNotIn(HOUR, progress.partial)

    def test_the_budget_is_charged_once_per_document(self):
        """Charging twice would throttle the run to half its allowance."""
        rows = [cassandra_row(HOUR + n) for n in range(1, 11)]
        budget = mc.RuBudget(10_000.0)
        mc.migrate(
            reader=FakeReader({HOUR: rows}),
            partition=PARTITION,
            station_uuid=STATION,
            selectors=SELECTORS,
            hours=[HOUR],
            writer=lambda _d: 96.76,
            budget=budget,
            write_concurrency=4,
            log=lambda *_: None,
        )
        self.assertAlmostEqual(budget.total_ru, 10 * 96.76, places=4)


class RunningBayTests(unittest.TestCase):
    """Reported because it is what unblocks the 264 withheld dictionary points."""

    def test_an_idle_payload_reports_no_running_bay(self):
        """An idle station must be reported as idle, not as unknown."""
        outcome = run(FakeReader({HOUR: [cassandra_row(HOUR + 5)]}), [HOUR], written=[])
        self.assertEqual(outcome.running_bays, 0)

    def test_a_running_bay_is_counted(self):
        """A running bay is what makes magnitudes usable as unit evidence."""
        payload = sample_payload()
        payload['pd'] = dict(payload.get('pd') or {})
        payload['pd']['P3'] = {'st': 'R'}
        row = cassandra_row(HOUR + 5, payload)

        outcome = run(FakeReader({HOUR: [row]}), [HOUR], written=[])
        self.assertEqual(outcome.running_bays, 1)

    def test_a_running_station_status_also_counts(self):
        """The station status reports running even when no bay does."""
        payload = sample_payload()
        payload['st'] = 'R'
        outcome = run(
            FakeReader({HOUR: [cassandra_row(HOUR + 5, payload)]}), [HOUR], written=[]
        )
        self.assertEqual(outcome.running_bays, 1)


class WriterTests(unittest.TestCase):
    """What the writer charges, retries and refuses to retry."""

    def test_the_real_charge_is_preferred_over_the_estimate(self):
        """The account reports the true RU; an estimate is only a fallback."""

        class Container:
            """A container that reports a request charge."""

            def upsert_item(self, _document, response_hook=None):
                """Answer as the SDK would, with a charge header."""
                response_hook({'x-ms-request-charge': '12.34'})

        self.assertAlmostEqual(mc.cosmos_writer(Container())({}), 12.34, places=5)

    def test_a_transient_timeout_is_retried_and_then_succeeds(self):
        """Safe only because the id is the sample time, so writes are upserts.

        Two consecutive live runs died on a 65 s read timeout, at concurrency
        8 and again at 4. Without retry a 38-hour backfill needs babysitting.
        """
        calls = []

        class Container:
            """A container that times out once, then succeeds."""

            def upsert_item(self, _document, response_hook=None):
                """Fail the first attempt, then answer normally."""
                calls.append(1)
                if len(calls) < 3:
                    raise ServiceResponseTimeoutError('read timed out')
                response_hook({'x-ms-request-charge': '50.67'})

        slept = []
        write = mc.cosmos_writer(Container(), attempts=4, sleeper=slept.append)
        self.assertAlmostEqual(write({'id': '1'}), 50.67, places=2)
        self.assertEqual(len(calls), 3, 'two failures then a success')
        self.assertEqual(slept, [1, 2], 'exponential backoff between attempts')

    def test_retries_are_bounded_and_the_error_surfaces(self):
        """Retrying forever would hide a failing account behind a hung run."""
        calls = []

        class Container:
            """A container that always times out."""

            def upsert_item(self, _document, response_hook=None):
                """Always fail, recording the attempt."""
                calls.append(1)
                raise ServiceResponseTimeoutError('read timed out')

        write = mc.cosmos_writer(Container(), attempts=3, sleeper=lambda _s: None)
        with self.assertRaises(ServiceResponseTimeoutError):
            write({'id': '1'})
        self.assertEqual(len(calls), 3, 'exactly `attempts` tries, then give up')

    def test_a_non_transient_failure_is_not_retried(self):
        """A rejected document fails identically on retry; surface it at once."""
        calls = []

        class Container:
            """A container that fails in a way retrying cannot fix."""

            def upsert_item(self, _document, response_hook=None):
                """Fail permanently, recording the attempt."""
                calls.append(1)
                raise ValueError('malformed document')

        write = mc.cosmos_writer(Container(), attempts=4, sleeper=lambda _s: None)
        with self.assertRaises(ValueError):
            write({'id': '1'})
        self.assertEqual(len(calls), 1, 'no retry for a permanent error')

    def test_transient_classification(self):
        """Retry the failures that a retry can fix, and only those."""
        self.assertTrue(mc._is_transient(ServiceResponseTimeoutError('x')))
        self.assertFalse(mc._is_transient(ValueError('nope')))

        class ThrottledError(Exception):
            """Throttling, which a retry does fix."""

            status_code = 429

        class ForbiddenError(Exception):
            """A permissions failure, which a retry does not fix."""

            status_code = 403

        self.assertTrue(mc._is_transient(ThrottledError()))
        self.assertFalse(mc._is_transient(ForbiddenError()))

    def test_a_missing_charge_header_falls_back_to_the_measured_figure(self):
        """The emulator omits the header; the cap must stay conservative."""

        class Container:
            """A container that reports no request charge."""

            def upsert_item(self, _document, response_hook=None):
                """Answer without a charge header."""
                response_hook({})

        self.assertEqual(mc.cosmos_writer(Container())({}), mc.ESTIMATED_RU_PER_DOC)


class MomentTests(unittest.TestCase):
    """Reading the instants a run is bounded by."""

    def test_epoch_milliseconds_pass_through(self):
        """Epoch milliseconds are the source representation and need no conversion."""
        self.assertEqual(mc._parse_moment(str(HOUR)), HOUR)

    def test_iso_without_a_zone_is_read_as_utc(self):
        """A bare ISO instant is UTC; guessing local time would shift every bound."""
        self.assertEqual(mc._parse_moment('2025-07-18T15:00:00'), HOUR)

    def test_nonsense_is_refused(self):
        """An unparsable bound must stop the run before it reads anything."""
        with self.assertRaises(mc.MigrationError):
            mc._parse_moment('last tuesday')


if __name__ == '__main__':
    unittest.main()
