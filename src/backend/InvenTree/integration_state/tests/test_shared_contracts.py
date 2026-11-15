"""Pure generic-state contracts; no application startup or source authority."""

from typing import Any
from unittest import TestCase
from uuid import uuid4


class SharedContractTests(TestCase):
    """Typed source coordinates cannot invent authority or unknown revisions."""

    def test_integer_and_string_keys_preserve_distinct_identity_and_unknowns(self):
        """Opaque text is not repaired into an integer or an incarnation."""
        from integration_state.shared_contracts import (
            SharedSourceKey,
            SourceObservation,
        )

        values: dict[str, Any] = {
            'namespace_id': 1,
            'environment': 'synthetic-shared',
            'epoch': uuid4(),
            'entity_kind': 'synthetic_document',
            'aspect': 'metadata',
        }
        integer = SharedSourceKey(**values, source_id=7)
        text = SharedSourceKey(**values, source_id='7')
        self.assertNotEqual(integer, text)
        self.assertEqual(len({integer, text}), 2)
        self.assertEqual(integer.source_id, 7)
        self.assertEqual(text.source_id, '7')
        self.assertIsNone(integer.declared_incarnation)
        observation = SourceObservation(
            state='available',
            source_revision=None,
            client_ids=None,
            content_digest=None,
        )
        self.assertIsNone(observation.source_revision)
        self.assertIsNone(observation.client_ids)
        self.assertFalse(observation.coverage_complete)
        self.assertFalse(observation.source_deleted)

    def test_key_refuses_boolean_and_hostile_scalar_aliases(self):
        """A private source key still needs exact non-coercible coordinates."""
        from uuid import UUID

        from integration_state.shared_contracts import SharedSourceKey

        class Alias(str):
            def __eq__(self, other):
                return True

            __hash__ = str.__hash__

        original: dict[str, Any] = {
            'namespace_id': 1,
            'environment': 'synthetic-shared',
            'epoch': uuid4(),
            'entity_kind': 'synthetic_document',
            'aspect': 'metadata',
            'source_id': 7,
        }
        invalid = (
            {'namespace_id': True},
            {'namespace_id': 0},
            {'source_id': True},
            {'source_id': -1},
            {'source_id': 2**63},
            {'source_id': Alias('7')},
            {'source_id': ''},
            {'environment': Alias('other-environment')},
            {'entity_kind': Alias('other-kind')},
            {'aspect': Alias('other-aspect')},
            {'epoch': UUID(int=0)},
            {'epoch': str(original['epoch'])},
            {'declared_incarnation': True},
            {'declared_incarnation': ''},
        )
        for change in invalid:
            with self.subTest(change=tuple(change)):
                with self.assertRaises(ValueError):
                    SharedSourceKey(**(original | change))
        self.assertEqual(SharedSourceKey(**original).source_id, 7)

    def test_observation_refuses_scalar_aliases_and_mutable_scope(self):
        """Unknown metadata is not zero, a mutable owner list or a false proof."""
        from integration_state.shared_contracts import SourceObservation

        original: dict[str, Any] = {
            'state': 'available',
            'source_revision': 0,
            'client_ids': (1, 2),
            'content_digest': 'a' * 64,
        }
        invalid = (
            {'state': True},
            {'state': 'complete'},
            {'source_revision': True},
            {'source_revision': -1},
            {'source_revision': ''},
            {'source_revision': 2**63},
            {'client_ids': [1, 2]},
            {'client_ids': (True,)},
            {'client_ids': (1, 1)},
            {'client_ids': (0,)},
            {'content_digest': 'z' * 64},
            {'content_digest': 'a' * 63},
        )
        for change in invalid:
            with self.subTest(change=tuple(change)):
                with self.assertRaises(ValueError):
                    SourceObservation(**(original | change))
        known = SourceObservation(**original)
        self.assertEqual(known.source_revision, 0)
        self.assertEqual(known.client_ids, (1, 2))
        empty = SourceObservation(state='available', client_ids=())
        self.assertEqual(empty.client_ids, ())
        self.assertFalse(empty.coverage_complete)
        self.assertFalse(empty.source_deleted)

    def test_identity_rejects_database_incompatible_literals(self):
        """Reject NUL/surrogates, preserve otherwise literal opaque key spelling."""
        from integration_state.shared_contracts import SharedSourceKey

        values: dict[str, Any] = {
            'namespace_id': 1,
            'environment': 'synthetic-shared',
            'epoch': uuid4(),
            'entity_kind': 'synthetic_document',
            'aspect': 'metadata',
        }
        for key in ('x\x00y', 'x\ud800y'):
            with self.subTest(kind='database_incompatible'):
                with self.assertRaises(ValueError):
                    SharedSourceKey(**values, source_id=key)
        literal = SharedSourceKey(**values, source_id=' 007 ')
        self.assertEqual(literal.source_id, ' 007 ')
