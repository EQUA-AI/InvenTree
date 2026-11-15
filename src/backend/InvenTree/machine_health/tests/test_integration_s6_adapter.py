"""Independent frozen-fixture history and provenance composition controls."""

from integration_state.tests.assertions import assert_literal_bool

from dataclasses import replace
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import patch

from machine_health.integration_contracts import TelemetrySourceIdentity
from machine_health.services.integration_history import (
    HistoryReadContext,
    HistoryWindow,
)
from machine_health.tests.test_integration_s6_wire import observation


def fixture(*, records=None, coverage=None, **changes):
    """Prepare fixture truth before asking the adapter to construct any page."""
    from machine_health.services.integration_s6_adapter import (
        FixtureHistoryAdapter,
        FixtureMapping,
    )

    source = TelemetrySourceIdentity(
        environment='s6-test', machine_pk=41, source_pk=17, binding_pk=29
    )
    window = coverage or HistoryWindow(-1, 4)
    mapping = FixtureMapping(
        client_pk=7,
        station_pk=11,
        dictionary_point_pk=19,
        station_uuid='synthetic-station',
        external_key='/synthetic/signal',
        unit='',
        dictionary_hash='synthetic-reviewed-hash',
        effective_window=window,
    )
    values = {
        'source': source,
        'mapping': mapping,
        'coverage': window,
        'generation': 'fixture-generation',
        'records': records
        if records is not None
        else (
            observation(value={'one': [0, False, None]}),
            observation(
                value=2,
                observed_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
                source_observation_id='literal-event:1',
            ),
        ),
    }
    values.update(changes)
    return FixtureHistoryAdapter.from_records(**values)


def context(adapter, *, actor_pk=23):
    """Map unequal source/binding coordinates by their real keyword names."""
    return HistoryReadContext(
        environment=adapter.source.environment,
        actor_pk=actor_pk,
        machine_pk=adapter.source.machine_pk,
        source_pk=adapter.source.source_pk,
        binding_pk=adapter.source.binding_pk,
        generation=adapter.generation,
    )


class FixtureHistoryAdapterTests(TestCase):
    """A fixture is a local source model, not real historian qualification."""

    def test_bounded_history_preserves_actual_producer_payloads(self):
        """The traversal carrier must retain the complete observation, not values only."""
        from machine_health.services import integration_s6_wire as wire

        adapter = fixture()
        request_context = context(adapter)
        result = adapter.read_history(
            request_context,
            adapter.coverage,
            authorize=lambda c, w: True,
            max_samples_per_read=1,
            max_samples_total=10,
            max_reads=20,
        )
        self.assertTrue(result.complete)
        self.assertEqual(result.context, request_context)
        self.assertEqual(result.requested_window, adapter.coverage)
        self.assertEqual(len(result.observations), 2)
        first = wire.decode_observation(result.observations[0])
        self.assertEqual(first.sample.source.source_pk, 17)
        self.assertEqual(first.sample.source.binding_pk, 29)
        self.assertEqual(first.sample.value, {'one': [0, False, None]})
        self.assertEqual(first.ingest_time_origin, 'aimms_received')
        self.assertIsNotNone(first.ingested_at)
        self.assertEqual(first.sample.observation_id_origin, 'source_reported')
        self.assertEqual(result.fixture_fingerprint, adapter.fingerprint())

    def test_unresolved_equal_time_group_is_not_a_partial_success(self):
        """Splitting cannot skip or complete an overfull minimum-resolution tie."""
        records = tuple(
            observation(value=index, source_observation_id=f'tie:{index}')
            for index in range(3)
        )
        adapter = fixture(records=records, coverage=HistoryWindow(-1, 0))
        result = adapter.read_history(
            context(adapter),
            adapter.coverage,
            authorize=lambda c, w: True,
            max_samples_per_read=2,
            max_samples_total=10,
            max_reads=10,
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.observations, ())
        self.assertEqual(
            [(gap.window, gap.reason) for gap in result.gaps],
            [(adapter.coverage, 'UNRESOLVED_TIMESTAMP_TIE')],
        )

    def test_generation_comparator_cannot_spoof_snapshot_after_capture(self):
        """A changed field type is invalid even when its JSON text is unchanged."""

        class Equal(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

        adapter = fixture()
        request_context = context(adapter)
        object.__setattr__(adapter, 'generation', Equal(adapter.generation))
        with self.assertRaises((ValueError, TypeError)):
            adapter.fingerprint()
        self.assertEqual(request_context.generation, 'fixture-generation')

    def test_final_constructor_cannot_replace_complete_nonempty_history(self):
        """After-construction content must still match independent snapshot truth."""
        from machine_health.services import integration_s6_adapter as module
        from machine_health.services.integration_history import HistoryProtocolError

        adapter = fixture()
        original = module.FixtureHistoryRead

        def corrupt(*args, **kwargs):
            result = original(*args, **kwargs)
            object.__setattr__(result, 'observations', ())
            return result

        with patch.object(module, 'FixtureHistoryRead', new=corrupt):
            with self.assertRaises(HistoryProtocolError):
                adapter.read_history(
                    context(adapter), adapter.coverage, authorize=lambda c, w: True
                )

    def test_under_exact_and_over_cap_results_use_independent_fixture_truth(self):
        """An exact cap can be complete; a capped prefix cannot pretend it is."""
        adapter = fixture()
        for cap in (1, 2, 3):
            with self.subTest(cap=cap):
                result = adapter.read_history(
                    context(adapter),
                    adapter.coverage,
                    authorize=lambda c, w: True,
                    max_samples_per_read=cap,
                    max_samples_total=10,
                    max_reads=20,
                )
                self.assertTrue(result.complete)
                self.assertEqual(len(result.observations), 2)

    def test_empty_qualified_source_and_unavailable_interval_are_distinct(self):
        """Missing source access does not certify an empty interval."""
        from machine_health.services.integration_history import HistoryGap

        empty = fixture(records=())
        result = empty.read_history(
            context(empty), empty.coverage, authorize=lambda c, w: True
        )
        self.assertTrue(result.complete)
        self.assertEqual(result.observations, ())
        unavailable = fixture(
            records=(), unavailable=(HistoryGap(empty.coverage, 'SOURCE_UNAVAILABLE'),)
        )
        result = unavailable.read_history(
            context(unavailable), unavailable.coverage, authorize=lambda c, w: True
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.gaps[0].reason, 'SOURCE_UNAVAILABLE')

    def test_budgets_remain_global_through_splits(self):
        """Bounded partial results account for every skipped requested interval."""
        adapter = fixture()
        result = adapter.read_history(
            context(adapter),
            adapter.coverage,
            authorize=lambda c, w: True,
            max_samples_per_read=1,
            max_samples_total=10,
            max_reads=1,
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.observations, ())
        self.assertEqual({gap.reason for gap in result.gaps}, {'READ_BUDGET_EXHAUSTED'})
        result = adapter.read_history(
            context(adapter),
            adapter.coverage,
            authorize=lambda c, w: True,
            max_samples_per_read=2,
            max_samples_total=1,
            max_reads=30,
        )
        self.assertFalse(result.complete)
        self.assertEqual(len(result.observations), 1)
        self.assertIn('SAMPLE_BUDGET_EXHAUSTED', {gap.reason for gap in result.gaps})

    def test_recoverable_retention_gap_recovers_only_available_halves(self):
        """Retries subdivide within the same budget and retain the one-ms hole."""
        from machine_health.services.integration_history import HistoryGap

        adapter = fixture(
            unavailable=(HistoryGap(HistoryWindow(0, 1), 'RETENTION_UNAVAILABLE'),)
        )
        result = adapter.read_history(
            context(adapter),
            adapter.coverage,
            authorize=lambda c, w: True,
            split_recoverable_errors=True,
            max_reads=30,
        )
        self.assertFalse(result.complete)
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(
            [
                (gap.window.start_ms, gap.window.end_ms, gap.reason)
                for gap in result.gaps
            ],
            [(0, 1, 'RETENTION_UNAVAILABLE')],
        )

    def test_corrupted_pages_fail_without_accumulated_partial_success(self):
        """Generation, order, duplicate, precision and bounds are independently checked."""
        from machine_health.services.integration_history import (
            HistoryPage,
            HistoryProtocolError,
        )
        from machine_health.services.integration_s6_adapter import FixtureHistoryAdapter

        adapter = fixture()
        rows = adapter._rows
        pages = (
            HistoryPage('wrong-generation', rows, False),
            HistoryPage(adapter.generation, tuple(reversed(rows)), False),
            HistoryPage(adapter.generation, (rows[0], rows[0]), False),
            HistoryPage(adapter.generation, (replace(rows[0], observed_ms=10),), False),
        )
        for page in pages:
            with self.subTest(page=page.generation):
                with patch.object(
                    FixtureHistoryAdapter, 'read_window', return_value=page
                ):
                    with self.assertRaises(HistoryProtocolError):
                        adapter.read_history(
                            context(adapter),
                            adapter.coverage,
                            authorize=lambda c, w: True,
                        )

    def test_unknown_provenance_or_finer_precision_cannot_enable_history(self):
        """The wire can preserve uncertainty that the history capability must refuse."""
        from machine_health.services.integration_s6_adapter import (
            HistoryCapabilityError,
        )

        for change in (
            {'source_observation_id': None, 'observation_id_origin': 'unknown'},
            {'time_origin': 'unknown'},
            {'source_generation': None},
            {'observed_at': datetime(1970, 1, 1, microsecond=1, tzinfo=timezone.utc)},
        ):
            with self.subTest(change=tuple(change)):
                kwargs = dict(change)
                stamp = kwargs.pop('observed_at', None)
                record = observation(value=0, observed_at=stamp, **kwargs)
                with self.assertRaises(HistoryCapabilityError):
                    fixture(records=(record,))

    def test_snapshot_bytes_detach_mutable_producer_and_query_partitions_are_bounded(
        self,
    ):
        """Caller mutation cannot relabel captured samples or widen station/hour work."""
        from machine_health.services.integration_s6_adapter import HOUR_MS
        from machine_health.services.integration_s6_wire import decode_observation

        record = observation(value={'list': [1]})
        adapter = fixture(records=(record,))
        record.sample.value['list'].append(2)
        result = adapter.read_history(
            context(adapter), adapter.coverage, authorize=lambda c, w: True
        )
        self.assertEqual(
            decode_observation(result.observations[0]).sample.value, {'list': [1]}
        )
        empty = fixture(records=(), coverage=HistoryWindow(0, HOUR_MS * 9))
        result = empty.read_history(
            context(empty), empty.coverage, authorize=lambda c, w: True, max_reads=20
        )
        self.assertTrue(result.complete)
        self.assertLessEqual(len(result.queries), 20 * 7)
        for station, bucket, start, end, cross_partition in result.queries:
            self.assertEqual(station, 'synthetic-station')
            self.assertEqual(bucket, str(start // HOUR_MS * HOUR_MS))
            self.assertLessEqual(end - start, HOUR_MS)
            assert_literal_bool(self, cross_partition, False)
