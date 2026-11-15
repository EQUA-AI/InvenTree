"""Pure S3 delivery projections; constructed bytes never grant source authority."""

from integration_state.tests.assertions import assert_literal_bool

import hashlib
import json
from dataclasses import replace
from importlib import import_module
from importlib.util import find_spec
from unittest import TestCase
from unittest.mock import patch

from tasks import integration_s3_wire as wire
from tasks.tests import test_integration_s3_wire as fixtures


class SourceDeliveryMetadataTests(TestCase):
    """Use actual pure producer encodings and independently computed digests."""

    def delivery(self):
        """Make missing-entry RED distinct from reached behavioral failures."""
        name = 'tasks.integration_s3_delivery'
        self.assertIsNotNone(find_spec(name), 'S3 needs a source-owned delivery bridge')
        return import_module(name)

    def payload(self):
        """Keep native lifecycle zero distinct from a universal source revision."""
        return wire.encode_source_record(fixtures.SourceWireTests().work())

    def test_whole_wire_digest_preserves_unknown_history_and_false_authority(self):
        """Metadata binds original bytes, not a lifecycle/metadata/content-only token."""
        payload = self.payload()
        observed = self.delivery().metadata_from_source_payload(payload)
        self.assertEqual(observed.state, 'available')
        self.assertEqual(observed.content_digest, hashlib.sha256(payload).hexdigest())
        self.assertIsNone(observed.source_revision)
        self.assertIsNone(observed.client_ids)
        assert_literal_bool(self, observed.coverage_complete, False)
        assert_literal_bool(self, observed.source_deleted, False)
        self.assertNotIn('Synthetic literal title', repr(observed))

    def test_constructor_cannot_substitute_schema_valid_metadata(self):
        """Fresh source and valid schema do not bind a forged returned observation."""
        module = self.delivery()
        payload = self.payload()
        legitimate = module.metadata_from_source_payload(payload)
        for changes in (
            {'content_digest': '0' * 64},
            {'source_revision': 0},
            {'client_ids': ()},
            {'state': 'missing'},
        ):
            with self.subTest(fields=tuple(changes)):
                substituted = replace(legitimate, **changes)
                with patch.object(
                    module, 'SourceObservation', return_value=substituted
                ) as hook:
                    with self.assertRaises(module.SourceDeliveryError):
                        module.metadata_from_source_payload(payload)
                    hook.assert_called_once()

    def test_whole_wire_format_is_preserved_instead_of_semantic_reencoding(self):
        """Equal decoded data does not make differently encoded observations identical."""
        module = self.delivery()
        first = self.payload()
        second = first + b' \n'
        self.assertEqual(json.loads(first), json.loads(second))
        self.assertNotEqual(
            module.metadata_from_source_payload(first).content_digest,
            module.metadata_from_source_payload(second).content_digest,
        )
        self.assertEqual(
            module.metadata_from_source_payload(second).content_digest,
            hashlib.sha256(second).hexdigest(),
        )

    def test_future_malformed_oversized_and_foreign_bytes_are_nondisclosing_faults(
        self,
    ):
        """Unsupported input never becomes a missing/available source observation."""
        module = self.delivery()
        future = json.loads(self.payload())
        future['wire_schema'] = 'aimms.s3.source-wire.future'
        future_record = json.loads(self.payload())
        future_record['record']['schema_version'] = 'aimms.work.core_details.future'
        for value in (
            None,
            '',
            b'',
            b'\xffsynthetic-private-sentinel',
            b'{synthetic-private-sentinel',
            json.dumps(future).encode(),
            json.dumps(future_record).encode(),
            b' ' * (module.MAX_SOURCE_WIRE_BYTES + 1),
        ):
            with self.subTest(
                kind=type(value).__name__,
                size=len(value) if value is not None else None,
            ):
                with self.assertRaises(module.SourceDeliveryError) as raised:
                    module.metadata_from_source_payload(value)
                self.assertNotIn('synthetic-private-sentinel', str(raised.exception))

    def test_foreign_metadata_constructor_return_is_rejected_without_property_access(
        self,
    ):
        """Exact carrier admission precedes every projection/validator property."""
        module = self.delivery()
        accessed = []

        class Foreign:
            @property
            def state(self):
                accessed.append(True)
                raise AssertionError('foreign property accessed')

        with patch.object(module, 'SourceObservation', return_value=Foreign()):
            with self.assertRaises(module.SourceDeliveryError):
                module.metadata_from_source_payload(self.payload())
        self.assertEqual(accessed, [])

    def test_metadata_faults_are_sanitized_but_memory_and_process_control_propagate(
        self,
    ):
        """A fixed content-free ordinary error is distinct from fatal/control flow."""
        module = self.delivery()
        payload = self.payload()
        for error in (
            RuntimeError('synthetic-private-sentinel'),
            MemoryError(),
            KeyboardInterrupt(),
        ):
            with self.subTest(kind=type(error).__name__):
                with patch.object(module.hashlib, 'sha256', side_effect=error):
                    expected = (
                        module.SourceDeliveryError
                        if type(error) is RuntimeError
                        else type(error)
                    )
                    with self.assertRaises(expected) as raised:
                        module.metadata_from_source_payload(payload)
                    if type(error) is RuntimeError:
                        self.assertNotIn(
                            'synthetic-private-sentinel', str(raised.exception)
                        )
                    else:
                        self.assertIs(raised.exception, error)
