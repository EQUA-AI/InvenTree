"""Synthetic controls for the unwired recorded-telemetry contract."""

from datetime import datetime, timedelta, timezone
from importlib import import_module
from unittest import TestCase


class RecordedTelemetryContractTests(TestCase):
    """The representation preserves source facts without granting access."""

    def test_v2_separates_optional_ingest_time_from_observation_time(self):
        """A receipt time is optional and never inferred from observation time."""
        contract = import_module('machine_health.integration_contracts')
        source = contract.TelemetrySourceIdentity(
            environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
        )
        observed = datetime(2020, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)
        receipt = observed + timedelta(microseconds=98)
        sample = contract.RecordedTelemetrySample(
            source=source,
            observed_at=observed,
            value=0,
            quality=None,
            quality_origin='unknown',
            unit=None,
            unit_origin='unknown',
            value_origin='unknown',
            source_observation_id=None,
            source_sequence=None,
        )
        unknown = contract.RecordedTelemetrySampleV2(sample=sample)
        self.assertEqual(unknown.schema_version, 'aimms.telemetry.recorded.v2')
        self.assertIs(unknown.sample, sample)
        self.assertIsNone(unknown.ingested_at)
        self.assertEqual(unknown.ingest_time_origin, 'unknown')
        for origin in ('unknown', 'source_reported', 'aimms_received'):
            with self.subTest(origin=origin):
                result = contract.RecordedTelemetrySampleV2(
                    sample=sample, ingested_at=receipt, ingest_time_origin=origin
                )
                self.assertIs(result.ingested_at, receipt)
                self.assertIs(result.sample.observed_at, observed)
                self.assertEqual(result.ingest_time_origin, origin)
        self.assertEqual(sample.schema_version, 'aimms.telemetry.recorded.v1')

    def test_v2_ingest_time_requires_aware_datetime_and_honest_origin(self):
        """Missing, naive and unsupported provenance cannot become a known receipt."""
        contract = import_module('machine_health.integration_contracts')
        sample = contract.RecordedTelemetrySample(
            source=contract.TelemetrySourceIdentity(
                environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
            ),
            observed_at=datetime(2020, 1, 2, tzinfo=timezone.utc),
            value=0,
            quality=None,
            quality_origin='unknown',
            unit=None,
            unit_origin='unknown',
            value_origin='unknown',
            source_observation_id=None,
            source_sequence=None,
        )
        for kwargs in (
            {'sample': None},
            {'sample': sample, 'ingested_at': datetime(2020, 1, 2)},
            {'sample': sample, 'ingested_at': '2020-01-02'},
            {'sample': sample, 'ingest_time_origin': 'aimms_received'},
            {'sample': sample, 'ingest_time_origin': 'verified'},
            {'sample': sample, 'ingest_time_origin': True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                contract.RecordedTelemetrySampleV2(**kwargs)
        # Do not reject clock skew or silently reorder the two source clocks.
        earlier = sample.observed_at - timedelta(seconds=1)
        result = contract.RecordedTelemetrySampleV2(
            sample=sample, ingested_at=earlier, ingest_time_origin='aimms_received'
        )
        self.assertIs(result.ingested_at, earlier)

    def test_unix_millisecond_conversion_is_exact_and_never_rounds(self):
        """Only an exactly representable aware instant may become epoch millis."""
        contract = import_module('machine_health.integration_contracts')
        convert = contract.datetime_to_unix_ms_exact
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(convert(epoch), 0)
        self.assertEqual(convert(epoch + timedelta(milliseconds=1001)), 1001)
        self.assertEqual(convert(epoch - timedelta(milliseconds=1)), -1)
        east = timezone(timedelta(hours=5, minutes=30))
        self.assertEqual(convert(epoch.astimezone(east)), 0)
        fractional_offset = timezone(timedelta(microseconds=500))
        self.assertEqual(convert(epoch.astimezone(fractional_offset)), 0)
        for invalid in (
            None,
            datetime(1970, 1, 1),
            epoch + timedelta(microseconds=1),
            epoch - timedelta(microseconds=1),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                convert(invalid)

    def test_exact_conversion_preserves_half_open_boundary_membership(self):
        """An aligned instant at the end is not inside the preceding window."""
        contract = import_module('machine_health.integration_contracts')
        history = import_module('machine_health.services.integration_history')
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        window = history.HistoryWindow(-1, 1)
        for instant, included in (
            (epoch - timedelta(milliseconds=2), False),
            (epoch - timedelta(milliseconds=1), True),
            (epoch, True),
            (epoch + timedelta(milliseconds=1), False),
        ):
            with self.subTest(instant=instant):
                millis = contract.datetime_to_unix_ms_exact(instant)
                self.assertEqual(window.start_ms <= millis < window.end_ms, included)

    def test_origins_and_gap_reasons_reject_equality_spoofing(self):
        """Arbitrary objects cannot pass a provenance or gap allowlist by equality."""
        contract = import_module('machine_health.integration_contracts')
        history = import_module('machine_health.services.integration_history')

        class PretendsEqual:
            __hash__ = None

            def __eq__(self, other):
                return True

        class PretendsEqualText(str):
            __hash__ = None

            def __eq__(self, other):
                return True

        source = contract.TelemetrySourceIdentity(
            environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
        )
        values = {
            'source': source,
            'observed_at': datetime(2020, 1, 2, tzinfo=timezone.utc),
            'value': 0,
            'quality': None,
            'quality_origin': 'unknown',
            'unit': None,
            'unit_origin': 'unknown',
            'value_origin': 'unknown',
            'source_observation_id': None,
            'source_sequence': None,
        }
        for field in (
            'time_origin',
            'quality_origin',
            'unit_origin',
            'value_origin',
            'observation_id_origin',
            'sequence_origin',
        ):
            for invalid in (PretendsEqual(), PretendsEqualText('not-a-code')):
                with self.subTest(field=field), self.assertRaises(ValueError):
                    contract.RecordedTelemetrySample(**(values | {field: invalid}))
        for invalid in (PretendsEqual(), PretendsEqualText('not-a-code')):
            with self.assertRaises(ValueError):
                contract.RecordedTelemetryGap(
                    source=source, window=history.HistoryWindow(1, 2), reason=invalid
                )
        self.assertEqual(
            contract.RecordedTelemetrySample(**values).quality_origin, 'unknown'
        )

    def test_missing_metadata_cannot_claim_a_known_origin(self):
        """A missing quality, unit, identity or sequence has unknown origin."""
        contract = import_module('machine_health.integration_contracts')
        values = {
            'source': contract.TelemetrySourceIdentity(
                environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
            ),
            'observed_at': datetime(2020, 1, 2, tzinfo=timezone.utc),
            'value': None,
            'quality': None,
            'quality_origin': 'unknown',
            'unit': None,
            'unit_origin': 'unknown',
            'value_origin': 'source_reported',
            'source_observation_id': None,
            'source_sequence': None,
        }
        for field, claimed in (
            ('quality_origin', 'source_reported'),
            ('unit_origin', 'binding'),
            ('observation_id_origin', 'adapter_assigned'),
            ('sequence_origin', 'source_reported'),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                contract.RecordedTelemetrySample(**(values | {field: claimed}))
        sample = contract.RecordedTelemetrySample(**values)
        self.assertIsNone(sample.value)
        self.assertEqual(sample.value_origin, 'source_reported')

    def test_source_generation_is_not_invented_or_printed(self):
        """An optional source generation is a claim, not completeness proof."""
        contract = import_module('machine_health.integration_contracts')
        source = contract.TelemetrySourceIdentity(
            environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
        )
        values = {
            'source': source,
            'observed_at': datetime(2020, 1, 2, tzinfo=timezone.utc),
            'value': 0,
            'quality': None,
            'quality_origin': 'unknown',
            'unit': None,
            'unit_origin': 'unknown',
            'value_origin': 'unknown',
            'source_observation_id': None,
            'source_sequence': None,
        }
        generation = 'synthetic-generation-82c'
        sample = contract.RecordedTelemetrySample(
            **values, source_generation=generation
        )
        self.assertEqual(sample.source_generation, generation)
        self.assertNotIn(generation, repr(sample))
        self.assertIsNone(contract.RecordedTelemetrySample(**values).source_generation)
        with self.assertRaises(ValueError):
            contract.RecordedTelemetrySample(**values, source_generation=' ')

    def test_sample_and_gap_generation_reject_spoofing_text_subclasses(self):
        """Generation claims cannot override equality when compared downstream."""
        contract = import_module('machine_health.integration_contracts')
        history = import_module('machine_health.services.integration_history')

        class SpoofedGeneration(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

        source = contract.TelemetrySourceIdentity(
            environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
        )
        sample = {
            'source': source,
            'observed_at': datetime(2020, 1, 2, tzinfo=timezone.utc),
            'value': 0,
            'quality': None,
            'quality_origin': 'unknown',
            'unit': None,
            'unit_origin': 'unknown',
            'value_origin': 'unknown',
            'source_observation_id': None,
            'source_sequence': None,
        }
        gap = {
            'source': source,
            'window': history.HistoryWindow(1, 2),
            'reason': 'UNKNOWN',
        }
        for constructor, values in (
            (contract.RecordedTelemetrySample, sample),
            (contract.RecordedTelemetryGap, gap),
        ):
            with (
                self.subTest(constructor=constructor.__name__),
                self.assertRaises(ValueError),
            ):
                constructor(**values, source_generation=SpoofedGeneration('other'))
            self.assertEqual(
                constructor(**values, source_generation='generation').source_generation,
                'generation',
            )
            self.assertIsNone(constructor(**values).source_generation)

    def test_sample_observation_identity_rejects_spoofing_text_subclass(self):
        """An observation ID cannot claim equality with another identifier."""
        contract = import_module('machine_health.integration_contracts')

        class SpoofedId(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

        values = {
            'source': contract.TelemetrySourceIdentity(
                environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
            ),
            'observed_at': datetime(2020, 1, 2, tzinfo=timezone.utc),
            'value': 0,
            'quality': None,
            'quality_origin': 'unknown',
            'unit': None,
            'unit_origin': 'unknown',
            'value_origin': 'unknown',
            'source_observation_id': SpoofedId('other'),
            'source_sequence': None,
        }
        with self.assertRaises(ValueError):
            contract.RecordedTelemetrySample(**values)
        values['source_observation_id'] = 'literal-id'
        self.assertEqual(
            contract.RecordedTelemetrySample(**values).source_observation_id,
            'literal-id',
        )
        values['source_observation_id'] = None
        self.assertIsNone(
            contract.RecordedTelemetrySample(**values).source_observation_id
        )

    def test_sample_external_key_rejects_spoofing_text_subclass(self):
        """A source key cannot override equality with a different binding key."""
        contract = import_module('machine_health.integration_contracts')

        class SpoofedKey(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

        values = {
            'source': contract.TelemetrySourceIdentity(
                environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
            ),
            'observed_at': datetime(2020, 1, 2, tzinfo=timezone.utc),
            'value': 0,
            'quality': None,
            'quality_origin': 'unknown',
            'unit': None,
            'unit_origin': 'unknown',
            'value_origin': 'unknown',
            'source_observation_id': None,
            'source_sequence': None,
        }
        with self.assertRaises(ValueError):
            contract.RecordedTelemetrySample(**values, external_key=SpoofedKey('other'))
        for key in ('', 'binding-key', None):
            self.assertEqual(
                contract.RecordedTelemetrySample(
                    **values, external_key=key
                ).external_key,
                key,
            )

    def test_gap_preserves_optional_generation_without_printing_it(self):
        """A gap may carry the source generation without proving it valid."""
        contract = import_module('machine_health.integration_contracts')
        history = import_module('machine_health.services.integration_history')
        source = contract.TelemetrySourceIdentity(
            environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
        )
        values = {
            'source': source,
            'window': history.HistoryWindow(1, 2),
            'reason': 'SOURCE_UNAVAILABLE',
        }
        generation = 'synthetic-generation-gap-82c'
        gap = contract.RecordedTelemetryGap(**values, source_generation=generation)
        self.assertEqual(gap.source_generation, generation)
        self.assertNotIn(generation, repr(gap))
        self.assertIsNone(contract.RecordedTelemetryGap(**values).source_generation)
        with self.assertRaises(ValueError):
            contract.RecordedTelemetryGap(**values, source_generation=' ')

    def test_source_coordinates_cannot_be_swapped_positionally(self):
        """History context and source identity use different ID orderings."""
        contract = import_module('machine_health.integration_contracts')
        with self.assertRaises(TypeError):
            contract.TelemetrySourceIdentity('synthetic', 1, 2, 3)
        source = contract.TelemetrySourceIdentity(
            environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
        )
        self.assertEqual((source.source_pk, source.binding_pk), (2, 3))

    def test_sequence_and_observation_id_provenance_are_not_inferred(self):
        """A connector sequence and adapter ID do not imply native identity."""
        contract = import_module('machine_health.integration_contracts')
        values = {
            'source': contract.TelemetrySourceIdentity(
                environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
            ),
            'observed_at': datetime(2020, 1, 2, tzinfo=timezone.utc),
            'value': 0,
            'quality': None,
            'quality_origin': 'unknown',
            'unit': None,
            'unit_origin': 'unknown',
            'value_origin': 'unknown',
            'source_observation_id': 'synthetic-event',
            'source_sequence': 7,
        }
        sample = contract.RecordedTelemetrySample(
            **values,
            observation_id_origin='adapter_assigned',
            sequence_origin='unknown',
        )
        self.assertEqual(sample.observation_id_origin, 'adapter_assigned')
        self.assertEqual(sample.sequence_origin, 'unknown')
        with self.assertRaises(ValueError):
            contract.RecordedTelemetrySample(**values, sequence_origin='verified')

    def test_native_mapping_key_can_be_preserved_without_repr_disclosure(self):
        """Capture a point key rather than inferring it from today's binding."""
        contract = import_module('machine_health.integration_contracts')
        values = {
            'source': contract.TelemetrySourceIdentity(
                environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
            ),
            'observed_at': datetime(2020, 1, 2, tzinfo=timezone.utc),
            'value': 0,
            'quality': None,
            'quality_origin': 'unknown',
            'unit': None,
            'unit_origin': 'unknown',
            'value_origin': 'unknown',
            'source_observation_id': None,
            'source_sequence': None,
        }
        key = 'synthetic-point-4f38'  # gitleaks:allow -- Non-secret external-key visibility sentinel.
        sample = contract.RecordedTelemetrySample(**values, external_key=key)
        self.assertEqual(sample.external_key, key)
        self.assertNotIn(key, repr(sample))
        self.assertIsNone(contract.RecordedTelemetrySample(**values).external_key)
        with self.assertRaises(ValueError):
            contract.RecordedTelemetrySample(**values, external_key=123)

    def test_source_time_origin_is_explicit_and_not_a_display_clock(self):
        """Aware time alone does not prove that the source clock supplied it."""
        contract = import_module('machine_health.integration_contracts')
        values = {
            'source': contract.TelemetrySourceIdentity(
                environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
            ),
            'observed_at': datetime(2020, 1, 2, tzinfo=timezone.utc),
            'time_origin': 'unknown',
            'value': 0,
            'quality': None,
            'quality_origin': 'unknown',
            'unit': None,
            'unit_origin': 'unknown',
            'value_origin': 'unknown',
            'source_observation_id': None,
            'source_sequence': None,
        }
        self.assertEqual(
            contract.RecordedTelemetrySample(**values).time_origin, 'unknown'
        )
        self.assertEqual(
            contract.RecordedTelemetrySample(
                **(values | {'time_origin': 'source_reported'})
            ).time_origin,
            'source_reported',
        )
        with self.assertRaises(ValueError):
            contract.RecordedTelemetrySample(
                **(values | {'time_origin': 'presentation'})
            )

    def test_recorded_sample_preserves_unknowns_and_original_time(self):
        """No unit, quality, sequence or source event identity is invented."""
        contract = import_module('machine_health.integration_contracts')
        identity = contract.TelemetrySourceIdentity(
            environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
        )
        observed_at = datetime(2020, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)
        sample = contract.RecordedTelemetrySample(
            source=identity,
            observed_at=observed_at,
            value=0,
            quality=None,
            quality_origin='unknown',
            unit=None,
            unit_origin='unknown',
            value_origin='unknown',
            source_observation_id=None,
            source_sequence=None,
        )

        self.assertEqual(sample.schema_version, 'aimms.telemetry.recorded.v1')
        self.assertIs(sample.source, identity)
        self.assertIs(sample.observed_at, observed_at)
        self.assertEqual(sample.value, 0)
        self.assertIsNone(sample.quality)
        self.assertEqual(sample.quality_origin, 'unknown')
        self.assertIsNone(sample.unit)
        self.assertEqual(sample.unit_origin, 'unknown')
        self.assertEqual(sample.value_origin, 'unknown')
        self.assertIsNone(sample.source_observation_id)
        self.assertIsNone(sample.source_sequence)

    def test_source_coordinates_reject_invalid_or_coerced_identifiers(self):
        """Booleans, missing environments and nonpositive IDs are not keys."""
        contract = import_module('machine_health.integration_contracts')
        valid = {
            'environment': 'synthetic',
            'machine_pk': 1,
            'source_pk': 2,
            'binding_pk': 3,
        }
        for field, invalid in (
            ('environment', ''),
            ('environment', '  '),
            ('environment', None),
            ('machine_pk', True),
            ('machine_pk', 0),
            ('source_pk', '2'),
            ('binding_pk', -1),
        ):
            with (
                self.subTest(field=field, invalid=invalid),
                self.assertRaises(ValueError),
            ):
                contract.TelemetrySourceIdentity(**(valid | {field: invalid}))

    def test_recorded_time_requires_an_aware_original_instant(self):
        """Reject missing or naive times rather than inventing a timezone."""
        contract = import_module('machine_health.integration_contracts')
        values = {
            'source': contract.TelemetrySourceIdentity(
                environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
            ),
            'observed_at': datetime(2020, 1, 2, tzinfo=timezone.utc),
            'value': None,
            'quality': None,
            'quality_origin': 'unknown',
            'unit': None,
            'unit_origin': 'unknown',
            'value_origin': 'unknown',
            'source_observation_id': None,
            'source_sequence': None,
        }
        for invalid in (None, '2020-01-02', datetime(2020, 1, 2)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                contract.RecordedTelemetrySample(**(values | {'observed_at': invalid}))

    def test_sample_rejects_unrecognized_or_mistyped_metadata(self):
        """Metadata must not silently assert an unrecognized provenance."""
        contract = import_module('machine_health.integration_contracts')
        values = {
            'source': contract.TelemetrySourceIdentity(
                environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
            ),
            'observed_at': datetime(2020, 1, 2, tzinfo=timezone.utc),
            'value': 0,
            'quality': 'good',
            'quality_origin': 'connector_default',
            'unit': '',
            'unit_origin': 'binding',
            'value_origin': 'unknown',
            'source_observation_id': None,
            'source_sequence': None,
        }
        for name, invalid in (
            ('source', None),
            ('quality', 0),
            ('quality_origin', 'verified'),
            ('unit', 1),
            ('unit_origin', 'guessed'),
            ('value_origin', 'physical_truth'),
            ('source_observation_id', ''),
            ('source_observation_id', 0),
            ('source_sequence', True),
        ):
            with (
                self.subTest(field=name, invalid=invalid),
                self.assertRaises(ValueError),
            ):
                contract.RecordedTelemetrySample(**(values | {name: invalid}))
        sample = contract.RecordedTelemetrySample(**values)
        self.assertEqual(sample.quality, 'good')
        self.assertEqual(sample.quality_origin, 'connector_default')
        self.assertEqual(sample.unit, '')

    def test_sample_repr_omits_value_without_changing_content(self):
        """Automatic representations do not print measurement values."""
        contract = import_module('machine_health.integration_contracts')
        sentinel = 'synthetic-sensitive-observation-9e42'
        sample = contract.RecordedTelemetrySample(
            source=contract.TelemetrySourceIdentity(
                environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
            ),
            observed_at=datetime(2020, 1, 2, tzinfo=timezone.utc),
            value=sentinel,
            quality='good',
            quality_origin='connector_default',
            unit='',
            unit_origin='binding',
            value_origin='unknown',
            source_observation_id=None,
            source_sequence=None,
        )
        self.assertNotIn(sentinel, repr(sample))
        self.assertNotIn(sentinel, str([sample]))
        self.assertEqual(sample.value, sentinel)

    def test_gap_preserves_uncovered_source_interval(self):
        """A gap is an interval and reason, not a successful empty read."""
        contract = import_module('machine_health.integration_contracts')
        history = import_module('machine_health.services.integration_history')
        source = contract.TelemetrySourceIdentity(
            environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
        )
        interval = history.HistoryWindow(1_000, 2_000)
        gap = contract.RecordedTelemetryGap(
            source=source, window=interval, reason='SOURCE_UNAVAILABLE'
        )
        self.assertEqual(gap.schema_version, 'aimms.telemetry.gap.v1')
        self.assertIs(gap.source, source)
        self.assertIs(gap.window, interval)
        self.assertEqual(gap.reason, 'SOURCE_UNAVAILABLE')

    def test_gap_rejects_invalid_or_unclassified_inputs(self):
        """Provider error strings and malformed intervals are not gap codes."""
        contract = import_module('machine_health.integration_contracts')
        history = import_module('machine_health.services.integration_history')
        values = {
            'source': contract.TelemetrySourceIdentity(
                environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
            ),
            'window': history.HistoryWindow(1, 2),
            'reason': 'SOURCE_UNAVAILABLE',
        }
        for field, invalid in (
            ('source', None),
            ('window', (1, 2)),
            ('reason', 'provider exception with sensitive context'),
            ('reason', None),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                contract.RecordedTelemetryGap(**(values | {field: invalid}))

    def test_gap_accepts_all_current_traversal_reason_codes(self):
        """The contract does not drop any currently emitted C5 gap category."""
        contract = import_module('machine_health.integration_contracts')
        history = import_module('machine_health.services.integration_history')
        source = contract.TelemetrySourceIdentity(
            environment='synthetic', machine_pk=1, source_pk=2, binding_pk=3
        )
        reasons = history._SOURCE_GAP_REASONS | {
            'READ_BUDGET_EXHAUSTED',
            'SAMPLE_BUDGET_EXHAUSTED',
            'UNRESOLVED_TIMESTAMP_TIE',
        }
        for reason in reasons:
            with self.subTest(reason=reason):
                gap = contract.RecordedTelemetryGap(
                    source=source, window=history.HistoryWindow(1, 2), reason=reason
                )
                self.assertEqual(gap.reason, reason)
