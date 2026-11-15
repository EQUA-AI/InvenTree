"""Pure metadata projection controls, not authentication or physical source proof."""

from integration_state.tests.assertions import assert_literal_bool

import hashlib
import json
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from integration_state.shared_contracts import (
    SharedChange,
    SharedSourceKey,
    SourceObservation,
)
from machine_health.services import integration_s6_delivery as delivery
from machine_health.services import integration_s6_wire as wire
from machine_health.tests.test_integration_s6_adapter import context, fixture
from machine_health.tests.test_integration_s6_wire import observation


def payload(*, records=None, cap=100):
    """Use the real fixture/wire producer; only pure source authorization is scripted."""
    adapter = fixture(records=records)
    requested = context(adapter)
    captured = adapter.read_history(
        requested,
        adapter.coverage,
        authorize=lambda c, w: True,
        max_samples_per_read=cap,
        max_samples_total=100,
        max_reads=20,
    )
    return wire.encode_history_read(
        adapter, captured, expected_context=requested, expected_window=adapter.coverage
    )


class HistoryDeliveryMetadataTests(TestCase):
    """A digest commits to private source bytes without relabeling metadata as authority."""

    def test_actual_original_wire_bytes_are_bound_without_source_or_client_revision(
        self,
    ):
        """Actual producer clocks/provenance/IDs remain private and unchanged."""
        original = payload()
        snapshot = bytes(original)
        result = delivery.metadata_from_recorded_payload(original)
        self.assertEqual(result.state, 'available')
        self.assertEqual(result.content_digest, hashlib.sha256(original).hexdigest())
        self.assertIsNone(result.source_revision)
        self.assertIsNone(result.client_ids)
        assert_literal_bool(self, result.coverage_complete, False)
        assert_literal_bool(self, result.source_deleted, False)
        self.assertEqual(original, snapshot)
        body = json.loads(original)
        self.assertEqual(body['requested_window'], [-1, 4])
        self.assertEqual(body['mapping']['client_pk'], 7)
        self.assertEqual(body['source_evidence']['kind'], 'synthetic_fixture')

    def test_complete_empty_and_partial_gaps_never_become_population_or_deletion(self):
        """Empty valid read is available; an unresolved time tie remains incomplete."""
        empty = delivery.metadata_from_recorded_payload(payload(records=()))
        ties = tuple(
            observation(value=i, source_observation_id=f'tie:{i}') for i in range(3)
        )
        encoded = payload(records=ties, cap=2)
        self.assertTrue(json.loads(encoded)['gaps'])
        incomplete = delivery.metadata_from_recorded_payload(encoded)
        self.assertEqual((empty.state, incomplete.state), ('available', 'incomplete'))
        for value in (empty, incomplete):
            self.assertIsNone(value.source_revision)
            assert_literal_bool(self, value.coverage_complete, False)
            assert_literal_bool(self, value.source_deleted, False)

    def test_digest_retains_literal_scalar_clock_and_source_identity_distinctions(self):
        """Hash the original bytes, never a value-only or normalized projection."""
        results = []
        for value in (
            0,
            False,
            None,
            '',
            ' ',
            -0.0,
            0.0,
            {'private-s6-sentinel': [0, None]},
        ):
            original = payload(records=(observation(value=value),))
            result = delivery.metadata_from_recorded_payload(original)
            self.assertEqual(
                result.content_digest, hashlib.sha256(original).hexdigest()
            )
            self.assertNotIn('private-s6-sentinel', repr(result))
            results.append(result.content_digest)
        self.assertEqual(len(results), len(set(results)))

    def test_unsupported_or_coerced_source_markers_are_refused(self):
        """Exact builtins and source schema are mandatory; false is not integer zero."""
        original = json.loads(payload())
        for field, value in (
            ('schema_version', 'aimms.s6.history-source.v2'),
            ('schema_version', True),
            ('complete', 1),
            ('population_complete', 0),
            ('revision_complete', 0),
        ):
            with self.subTest(field=field, value=value):
                encoded = json.dumps(original | {field: value}).encode()
                with self.assertRaises(delivery.HistoryDeliveryError):
                    delivery.metadata_from_recorded_payload(encoded)
        for value in (
            None,
            b'',
            'not-bytes',
            bytearray(b'{}'),
            b'x' * (wire.MAX_HISTORY_BYTES + 1),
        ):
            with self.subTest(type=type(value).__name__):
                with self.assertRaises(delivery.HistoryDeliveryError):
                    delivery.metadata_from_recorded_payload(value)

    def test_returned_metadata_constructor_cannot_change_source_bound_fields(self):
        """Valid schema alone is not a digest, state, native revision or client binding."""
        original = payload()
        expected = delivery.metadata_from_recorded_payload(original)
        for changed in (
            replace(expected, content_digest='f' * 64),
            replace(expected, state='missing'),
            replace(expected, source_revision=0),
            replace(expected, client_ids=(7,)),
        ):
            with self.subTest(changed=repr(changed)):
                with patch.object(
                    delivery, 'SourceObservation', return_value=changed
                ) as hook:
                    with self.assertRaises(delivery.HistoryDeliveryError):
                        delivery.metadata_from_recorded_payload(original)
                    hook.assert_called_once()
                self.assertEqual(
                    delivery.metadata_from_recorded_payload(original), expected
                )

    def test_private_change_signature_rejects_boolean_fences_and_source_aliases(self):
        """Strict scalar admission precedes comparison or persistent metadata IO."""
        key = SharedSourceKey(
            namespace_id=1,
            environment='s6-test',
            epoch=uuid4(),
            entity_kind='telemetry_binding',
            aspect='recorded_history',
            source_id=29,
        )
        good = SharedChange(
            change_id=1,
            key=key,
            fence=1,
            observation=SourceObservation(state='available'),
        )
        expected = delivery._signature(good)
        for changed in (replace(good, change_id=True), replace(good, fence=True)):
            with self.subTest(changed=repr(changed)):
                with self.assertRaises(ValueError):
                    delivery._signature(changed)
        self.assertEqual(delivery._signature(good), expected)
