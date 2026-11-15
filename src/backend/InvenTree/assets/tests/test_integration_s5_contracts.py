"""Placement expectation contracts; no database or external serving claim."""

import unittest
from uuid import UUID

from assets.integration_contracts import (
    ClientSourceIdentity,
    LocationSourceIdentity,
    MachineSourceIdentity,
)


class PlacementContractTests(unittest.TestCase):
    """Source-qualified coordinates remain literal and independently typed."""

    def test_valid_expectation_preserves_coordinates(self):
        """A valid native placement has explicit source and projection fences."""
        from assets.integration_s5_contracts import PlacementExpectation

        expected = PlacementExpectation(
            machine=MachineSourceIdentity('synthetic-s5', 7),
            client=ClientSourceIdentity('synthetic-s5', 3),
            location=LocationSourceIdentity('synthetic-s5', 11),
            native_version=2,
            fence=4,
            namespace_epoch=UUID('00000000-0000-0000-0000-000000000001'),
        )
        self.assertEqual(expected.machine.pk, 7)
        self.assertEqual(expected.client.pk, 3)
        self.assertEqual(expected.location, LocationSourceIdentity('synthetic-s5', 11))
        self.assertEqual(expected.native_version, 2)
        self.assertEqual(expected.fence, 4)

    def test_boolean_cannot_alias_a_native_version_or_change_fence(self):
        """Exact integers are required even though Python equates True and one."""
        from assets.integration_s5_contracts import PlacementExpectation

        values = {
            'machine': MachineSourceIdentity('synthetic-s5', 7),
            'client': ClientSourceIdentity('synthetic-s5', 3),
            'location': None,
            'native_version': 0,
            'fence': 1,
            'namespace_epoch': UUID('00000000-0000-0000-0000-000000000001'),
        }
        for field in ('native_version', 'fence'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                PlacementExpectation(**{**values, field: True})

    def test_nested_coordinates_and_epoch_cannot_alias_valid_identity(self):
        """Valid scalar counters do not make foreign or malformed bindings valid."""
        from assets.integration_s5_contracts import PlacementExpectation

        values = {
            'machine': MachineSourceIdentity('synthetic-s5', 7),
            'client': ClientSourceIdentity('synthetic-s5', 3),
            'location': None,
            'native_version': 0,
            'fence': 1,
            'namespace_epoch': UUID('00000000-0000-0000-0000-000000000001'),
        }
        malformed = MachineSourceIdentity('synthetic-s5', 7)
        object.__setattr__(malformed, 'pk', True)
        for field, value in (
            ('machine', ClientSourceIdentity('synthetic-s5', 7)),
            ('machine', malformed),
            ('client', MachineSourceIdentity('synthetic-s5', 3)),
            ('client', ClientSourceIdentity('other-environment', 3)),
            ('location', LocationSourceIdentity('other-environment', 11)),
            ('location', {}),
            ('namespace_epoch', str(values['namespace_epoch'])),
            ('namespace_epoch', UUID(int=0)),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                PlacementExpectation(**{**values, field: value})
