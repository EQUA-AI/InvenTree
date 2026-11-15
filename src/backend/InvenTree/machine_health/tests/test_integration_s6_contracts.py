"""Strict request/outcome and invocation-bound source result contracts."""

from integration_state.tests.assertions import assert_literal_bool

from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from machine_health.services.integration_history import (
    HistoryProtocolError,
    HistoryWindow,
)
from machine_health.tests.test_integration_s6_adapter import context, fixture


class HistoryResultContractTests(TestCase):
    """No pure DTO or byte payload is current authentication or authority."""

    def test_emission_cannot_rebind_fixture_to_different_source_coordinates(self):
        """Even matching forged expected/actual contexts cannot move source truth."""
        from machine_health.services import integration_s6_wire as wire

        adapter = fixture()
        original = context(adapter)
        captured = adapter.read_history(
            original, adapter.coverage, authorize=lambda c, w: True
        )
        for change in (
            {'environment': 'other'},
            {'machine_pk': 42},
            {'source_pk': 18},
            {'binding_pk': 30},
            {'generation': 'other-generation'},
        ):
            with self.subTest(change=change):
                forged = replace(original, **change)
                wrong = replace(captured, context=forged)
                with self.assertRaises(HistoryProtocolError):
                    wire.encode_history_read(
                        adapter,
                        wrong,
                        expected_context=forged,
                        expected_window=adapter.coverage,
                    )

    def test_outcomes_reject_coercion_payload_and_completeness_spoofing(self):
        """The outer outcome is an opaque observation, not a whole source census."""
        from machine_health.services.integration_s6_reader import S6ReadOutcome

        for state in ('unavailable', 'unsupported'):
            result = S6ReadOutcome(state=state)
            self.assertIsNone(result.payload)
            assert_literal_bool(self, result.population_complete, False)
            assert_literal_bool(self, result.revision_complete, False)
        for fields in (
            {'state': None},
            {'state': 'complete'},
            {'state': 'available'},
            {'state': 'available', 'payload': 'text'},
            {'state': 'unavailable', 'payload': b'private'},
        ):
            with self.subTest(fields=tuple(fields)):
                with self.assertRaises(ValueError):
                    S6ReadOutcome(**fields)
        result = S6ReadOutcome(state='available', payload=b'opaque source observation')
        object.__setattr__(result, 'population_complete', 0)
        with self.assertRaises(ValueError):
            S6ReadOutcome.__post_init__(result)

    def test_invalid_request_shape_is_rejected_before_intake_or_source(self):
        """Exact scalar/range/work limits are checked rather than normalized."""
        from machine_health.services import integration_s6_reader as reader

        valid = {
            'environment': 's6-test',
            'machine_pk': 41,
            'source_pk': 17,
            'binding_pk': 29,
            'window': HistoryWindow(-1, 4),
        }
        for key, value in (
            ('environment', ''),
            ('machine_pk', True),
            ('source_pk', '17'),
            ('binding_pk', 0),
            ('window', None),
            ('max_reads', True),
            ('max_reads', 257),
            ('max_samples_per_read', 0),
            ('max_samples_total', 10001),
            ('split_recoverable_errors', 1),
            ('max_elapsed_seconds', 31),
        ):
            with self.subTest(key=key, value=value):
                with patch.object(reader, '_actor') as intake:
                    with self.assertRaises(ValueError):
                        reader.read_recorded_history(
                            None, **dict(valid, **{key: value})
                        )
                    intake.assert_not_called()

    def test_observation_and_outcome_representations_do_not_log_payload(self):
        """Representation protection is separate from explicit authorized access."""
        from machine_health.services.integration_s6_reader import S6ReadOutcome

        adapter = fixture()
        captured = adapter.read_history(
            context(adapter), adapter.coverage, authorize=lambda c, w: True
        )
        result = S6ReadOutcome(state='available', payload=b's6-private-sentinel')
        self.assertNotIn('s6-private-sentinel', repr(result))
        self.assertNotIn('literal-event', repr(captured))
        self.assertEqual(result.payload, b's6-private-sentinel')
