"""Lossless recorded-sample wire controls; no source or application I/O."""

from integration_state.tests.assertions import assert_literal_bool

import json
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from machine_health.integration_contracts import (
    RecordedTelemetrySample,
    RecordedTelemetrySampleV2,
    TelemetrySourceIdentity,
)


def observation(*, value=None, source=None, observed_at=None, **fields):
    """Build an independently specified synthetic producer record."""
    sample = RecordedTelemetrySample(
        source=source
        or TelemetrySourceIdentity(
            environment='s6-test', machine_pk=41, source_pk=17, binding_pk=29
        ),
        observed_at=observed_at
        or datetime(1969, 12, 31, 23, 59, 59, 999000, tzinfo=timezone.utc),
        value=value,
        quality=None,
        quality_origin='unknown',
        unit='',
        unit_origin='binding',
        value_origin='source_reported',
        source_observation_id='literal-event:0',
        source_sequence=0,
        time_origin='source_reported',
        external_key='/synthetic/signal',
        observation_id_origin='source_reported',
        sequence_origin='unknown',
        source_generation='fixture-generation',
    )
    for name, value in fields.items():
        object.__setattr__(sample, name, value)
    return RecordedTelemetrySampleV2(
        sample=sample,
        ingested_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        ingest_time_origin='aimms_received',
    )


class RecordedObservationWireTests(TestCase):
    """Exercise actual producer-shaped values, not an invented shallow DTO."""

    def test_original_metadata_and_detached_nested_value_round_trip(self):
        """Every producer field survives and detached bytes retain original values."""
        from machine_health.services import integration_s6_wire as wire

        value = {'nested': [0, False, '', None, {'literal': 'synthetic-private'}]}
        record = observation(value=value)
        payload = wire.encode_observation(record)
        self.assertIs(type(payload), bytes)
        value['nested'].append('later producer mutation')
        decoded = wire.decode_observation(payload)
        self.assertEqual(
            decoded.sample.value,
            {'nested': [0, False, '', None, {'literal': 'synthetic-private'}]},
        )
        self.assertEqual(decoded.sample.source, record.sample.source)
        self.assertEqual(decoded.sample.observed_at, record.sample.observed_at)
        self.assertEqual(decoded.ingested_at, record.ingested_at)
        self.assertEqual(decoded.ingest_time_origin, 'aimms_received')
        for name in (
            'quality',
            'quality_origin',
            'unit',
            'unit_origin',
            'value_origin',
            'source_observation_id',
            'source_sequence',
            'time_origin',
            'external_key',
            'observation_id_origin',
            'sequence_origin',
            'source_generation',
            'schema_version',
        ):
            self.assertEqual(
                getattr(decoded.sample, name), getattr(record.sample, name), name
            )
        decoded.sample.value['nested'].clear()
        self.assertEqual(
            len(wire.decode_observation(payload).sample.value['nested']), 5
        )

    def test_mutated_nested_identity_is_rejected_at_capture(self):
        """Frozen constructors alone do not protect a corrupted nested source."""
        from machine_health.services import integration_s6_wire as wire

        record = observation(value=0)
        object.__setattr__(record.sample.source, 'source_pk', True)
        with self.assertRaises(wire.SourceSerializationError):
            wire.encode_observation(record)

    def test_unknown_receipt_and_origins_are_not_filled(self):
        """A legacy sample gains only explicit unknown receipt metadata."""
        from machine_health.services import integration_s6_wire as wire

        record = observation(
            value=False,
            source_generation=None,
            source_sequence=None,
            source_observation_id=None,
            observation_id_origin='unknown',
            unit=None,
            unit_origin='unknown',
            time_origin='unknown',
        )
        decoded = wire.decode_observation(wire.encode_observation(record.sample))
        self.assertIsNone(decoded.ingested_at)
        self.assertEqual(decoded.ingest_time_origin, 'unknown')
        assert_literal_bool(self, decoded.sample.value, False)
        self.assertIsNone(decoded.sample.quality)
        self.assertIsNone(decoded.sample.unit)
        self.assertIsNone(decoded.sample.source_generation)
        self.assertIsNone(decoded.sample.source_sequence)
        self.assertIsNone(decoded.sample.source_observation_id)
        self.assertEqual(decoded.sample.time_origin, 'unknown')

    def test_offset_precision_and_skew_are_preserved_without_rounding(self):
        """Wire precision is retained even when millisecond traversal is unsupported."""
        from machine_health.services import integration_s6_wire as wire

        instant = datetime(
            2025, 1, 1, 8, 0, 0, 123456, tzinfo=timezone(timedelta(hours=8))
        )
        record = observation(value=0, observed_at=instant)
        decoded = wire.decode_observation(wire.encode_observation(record))
        self.assertEqual(decoded.sample.observed_at.isoformat(), instant.isoformat())
        self.assertLess(decoded.ingested_at, decoded.sample.observed_at)

    def test_unknown_schema_extra_fields_and_duplicate_json_are_not_repaired(self):
        """A syntactically valid wrong representation is still a source fault."""
        from machine_health.services import integration_s6_wire as wire

        payload = wire.encode_observation(observation(value='literal'))
        body = json.loads(payload)
        for mutation in (
            lambda data: data.update(schema_version='unknown'),
            lambda data: data['sample'].update(extra='unapproved'),
            lambda data: data['sample']['source'].update(source_pk=True),
        ):
            with self.subTest(mutation=mutation):
                modified = json.loads(payload)
                mutation(modified)
                with self.assertRaises(wire.SourceSerializationError):
                    wire.decode_observation(json.dumps(modified).encode())
        self.assertEqual(body['sample']['value'], 'literal')
        with self.assertRaises(wire.SourceSerializationError):
            wire.decode_observation(b'{"sample":null,"sample":null}')

    def test_lossy_values_and_unbounded_trees_are_refused(self):
        """Unsupported values cannot become text, zero, null or truncated success."""
        from machine_health.services import integration_s6_wire as wire

        nested = 0
        for _ in range(wire.MAX_VALUE_DEPTH + 2):
            nested = [nested]
        cyclic = []
        cyclic.append(cyclic)
        for value in (
            float('nan'),
            float('inf'),
            b'bytes',
            (1, 2),
            {1: 'key'},
            'x' * (wire.MAX_OBSERVATION_BYTES + 1),
            nested,
            cyclic,
        ):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(wire.SourceSerializationError):
                    wire.encode_observation(observation(value=value))

    def test_schema_origin_and_identity_comparators_cannot_spoof(self):
        """Each compared scalar is a plain native value, not an equality oracle."""
        from machine_health.services import integration_s6_wire as wire

        class Equal(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

        for field, value in (
            ('schema_version', Equal('wrong')),
            ('quality_origin', Equal('wrong')),
            ('unit', Equal('wrong')),
            ('source_generation', Equal('wrong')),
            ('external_key', Equal('wrong')),
            ('source_sequence', True),
        ):
            with self.subTest(field=field):
                record = observation(value=None)
                object.__setattr__(record.sample, field, value)
                with self.assertRaises(wire.SourceSerializationError):
                    wire.encode_observation(record)

    def test_mutation_during_final_encoding_cannot_change_observation(self):
        """The capture revalidates the original producer after encoding work."""
        from machine_health.services import integration_s6_wire as wire

        record = observation(value={'x': 1})
        original = wire.bounded_json
        calls = 0

        def mutate(body, **kwargs):
            nonlocal calls
            payload = original(body, **kwargs)
            calls += 1
            if calls == 1:
                record.sample.value['x'] = 2
            return payload

        with patch.object(wire, 'bounded_json', new=mutate):
            with self.assertRaises(wire.SourceSerializationError):
                wire.encode_observation(record)
