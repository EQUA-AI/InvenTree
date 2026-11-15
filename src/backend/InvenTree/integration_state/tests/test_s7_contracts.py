"""Pure S7 identity contracts; these values are not source or actor authority."""

import unittest
from uuid import UUID


class AspectIdentityTests(unittest.TestCase):
    """Actual identifier types survive without aliases or cross-domain collapse."""

    def test_integer_and_observation_string_remain_distinct(self):
        """An observation spelling is not silently converted into a native PK."""
        from integration_state.s7_contracts import SourceAspectIdentity

        coordinates = {
            'environment': 'synthetic-s7',
            'epoch': UUID(int=1),
            'entity_kind': 'machine',
            'aspect': 'placement',
        }
        integer = SourceAspectIdentity(**coordinates, source_id=41)
        literal = SourceAspectIdentity(**coordinates, source_id='41')
        self.assertEqual(integer.source_id, 41)
        self.assertEqual(literal.source_id, '41')
        self.assertNotEqual(integer, literal)
        self.assertEqual(len({integer, literal}), 2)
        self.assertNotEqual(
            integer,
            SourceAspectIdentity(
                **{**coordinates, 'entity_kind': 'document'}, source_id=41
            ),
        )

    def test_invalid_coordinates_do_not_normalize(self):
        """Exact scalar types are validated before any equality comparison."""
        from integration_state.s7_contracts import SourceAspectIdentity

        class EqualString(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

        values = {
            'environment': 'synthetic-s7',
            'epoch': UUID(int=1),
            'entity_kind': 'machine',
            'aspect': 'placement',
            'source_id': 41,
        }
        for field, value in (
            ('environment', EqualString('foreign')),
            ('environment', ''),
            ('environment', ' '),
            ('epoch', UUID(int=0)),
            ('epoch', str(UUID(int=1))),
            ('entity_kind', EqualString('document')),
            ('entity_kind', ''),
            ('aspect', EqualString('content')),
            ('aspect', ''),
            ('source_id', True),
            ('source_id', False),
            ('source_id', 0),
            ('source_id', -1),
            ('source_id', ''),
            ('source_id', EqualString('41')),
            ('source_id', None),
            ('source_id', 41.0),
        ):
            with (
                self.subTest(field=field, kind=type(value).__name__),
                self.assertRaises(ValueError),
            ):
                SourceAspectIdentity(**{**values, field: value})

    def test_literal_labels_and_private_identity_are_preserved(self):
        """Bounded labels are not repaired and repr omits source identifiers."""
        from integration_state.s7_contracts import SourceAspectIdentity

        identity = SourceAspectIdentity(
            environment=' synthetic-s7 ',
            epoch=UUID(int=1),
            entity_kind='telemetry',
            aspect='observation',
            source_id='synthetic-private-observation',
        )
        self.assertEqual(identity.environment, ' synthetic-s7 ')
        self.assertEqual(identity.source_id, 'synthetic-private-observation')
        self.assertNotIn(identity.source_id, repr(identity))
        for field, limit in (
            ('environment', 128),
            ('entity_kind', 32),
            ('aspect', 32),
            ('source_id', 256),
        ):
            values = {
                'environment': 'synthetic-s7',
                'epoch': UUID(int=1),
                'entity_kind': 'telemetry',
                'aspect': 'observation',
                'source_id': 'obs',
            }
            with self.subTest(field=field), self.assertRaises(ValueError):
                SourceAspectIdentity(**{**values, field: 'x' * (limit + 1)})
