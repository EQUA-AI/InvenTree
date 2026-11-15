"""Pure controls for S3 request outcomes and caller-input boundaries."""

from integration_state.tests.assertions import assert_literal_bool

from dataclasses import FrozenInstanceError
from unittest.mock import patch

from django.test import SimpleTestCase

from assets.integration_sources import MAX_MACHINE_ATTACHMENT_CONTENT_BYTES
from tasks import integration_s3_reader as reader


class ReadOutcomeContractTests(SimpleTestCase):
    """Opaque result carriers neither grant access nor assert complete populations."""

    def test_nonavailable_states_are_distinct_and_never_complete(self):
        """Unsupported capability is not an absent record or an empty population."""
        for state in ('unsupported', 'unavailable'):
            with self.subTest(state=state):
                result = reader.S3ReadOutcome(state, None)
                self.assertEqual(result.state, state)
                self.assertIsNone(result.payload)
                assert_literal_bool(self, result.population_complete, False)
                assert_literal_bool(self, result.revision_complete, False)
                self.assertEqual(result.schema_version, 'aimms.s3.read-result.v1')

    def test_carrier_is_frozen_and_does_not_display_protected_payload(self):
        """This opaque carrier preserves bytes; only read_source validates/binds them."""
        payload = b'synthetic-private-source-payload\x00\xff'
        result = reader.S3ReadOutcome('available', payload)
        self.assertEqual(result.payload, payload)
        self.assertNotIn('synthetic-private-source-payload', repr(result))
        with self.assertRaises(FrozenInstanceError):
            result.state = 'unavailable'

    def test_invalid_state_and_payload_types_are_not_coerced(self):
        """Loose truthiness, bytes-like objects and equality spoofing are refused."""

        class PretendsEqual:
            __hash__ = None

            def __eq__(self, other):
                return True

        for state, payload in (
            (True, None),
            (PretendsEqual(), b'data'),
            ('empty', None),
            ('available', None),
            ('available', 'data'),
            ('available', bytearray(b'data')),
            ('unavailable', b'data'),
            ('unsupported', b'data'),
        ):
            with self.subTest(
                state=type(state).__name__, payload=type(payload).__name__
            ):
                with self.assertRaises(ValueError):
                    reader.S3ReadOutcome(state, payload)

    def test_unsupported_capabilities_do_not_touch_authority_or_source(self):
        """Unsupported controlled/shared/history reads cannot use native fallback."""
        for operation in (
            'controlled_document.content',
            'work.history',
            'work.population',
            'part_document.content',
        ):
            with self.subTest(operation=operation):
                with patch.object(reader, 'integration_actor_from_request') as intake:
                    result = reader.read_source(
                        None,
                        environment='literal-source',
                        operation=operation,
                        source_pk=1,
                    )
                self.assertEqual(result.state, 'unsupported')
                self.assertIsNone(result.payload)
                intake.assert_not_called()

    def test_malformed_coordinates_fail_before_request_intake(self):
        """No scope label or primary-key coercion is an authentication mechanism."""
        for environment, source_pk in (
            ('', 1),
            (' ', 1),
            (None, 1),
            ('source', True),
            ('source', '1'),
            ('source', 0),
            ('source', -1),
        ):
            with self.subTest(environment=environment, pk=source_pk):
                with patch.object(reader, 'integration_actor_from_request') as intake:
                    with self.assertRaises(ValueError):
                        reader.read_source(
                            None,
                            environment=environment,
                            operation='work.core',
                            source_pk=source_pk,
                        )
                intake.assert_not_called()

    def test_content_limits_fail_before_request_intake(self):
        """The adapter retains native bounded-IO input validation before any lookup."""
        for limit in (None, True, 0, -1, 1.5, MAX_MACHINE_ATTACHMENT_CONTENT_BYTES + 1):
            with self.subTest(limit=limit):
                with patch.object(
                    reader,
                    'integration_actor_from_request',
                    side_effect=AssertionError('Intake before limit validation'),
                ):
                    with self.assertRaises(ValueError):
                        reader.read_source(
                            None,
                            environment='source',
                            operation='work_document.content',
                            source_pk=1,
                            max_bytes=limit,
                        )
