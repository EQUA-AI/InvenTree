"""The checked-in plant-flow layout uses the migrated, real cusec definition."""

from datetime import datetime, timezone
from types import SimpleNamespace

from django.test import TestCase

from common.models import CustomUnit
from InvenTree.conversion import reload_unit_registry
from machine_health.mimic_layout import expand_pointer, load_layout
from machine_health.services.mimic import empty_point, total_value


class CusecTotalTests(TestCase):
    """Exercise layout totals with Pint rebuilt from this test database's units."""

    def setUp(self):
        """Require migration 0015's row; do not create or mock the missing unit."""
        super().setUp()
        unit = CustomUnit.objects.get(name='cusec')
        self.assertEqual(unit.definition, 'foot**3/second')
        self.assertEqual(unit.symbol, 'cusec')
        reload_unit_registry()
        self.definition = next(
            total for total in load_layout()['totals'] if total['id'] == 'flow'
        )
        self.assertEqual(self.definition['unit'], 'cusec')
        self.pumps = [SimpleNamespace(source_key=key) for key in ('P1', 'P7')]
        self.pointers = [
            expand_pointer(self.definition['per_pump'], pump.source_key)
            for pump in self.pumps
        ]

    @staticmethod
    def reading(pointer, value, unit, *, reason=None):
        """Supply the same projected value fields used by the station service."""
        return {
            **empty_point(pointer, reason, unit=unit),
            'value': value,
            'observed_at': datetime(2026, 10, 22, 12, tzinfo=timezone.utc),
            'age_seconds': 5,
        }

    def test_direct_cusec_measurement_and_si_conversion(self):
        """A direct station measurement wins, retaining or converting its units."""
        pointer = self.definition['direct']
        for value, unit, expected in (
            (42, 'cusec', 42),
            (2, 'm**3/s', 2 / 0.3048**3),
        ):
            with self.subTest(unit=unit):
                total = total_value(
                    self.definition,
                    {pointer: self.reading(pointer, value, unit)},
                    self.pumps,
                    True,
                )
                self.assertAlmostEqual(total['value'], expected, places=10)
                self.assertEqual(total['unit'], 'cusec')
                self.assertFalse(total['derived'])
                self.assertEqual(total['contributors'], [pointer])
                self.assertIsNone(total['reason'])

    def test_derived_flow_normalizes_mixed_cusec_and_si_contributors(self):
        """Ten cusec plus one cubic metre per second is not eleven cusec."""
        first, second = self.pointers
        total = total_value(
            self.definition,
            {
                first: self.reading(first, 10, 'cusec'),
                second: self.reading(second, 1, 'm**3/s'),
            },
            self.pumps,
            True,
        )
        self.assertAlmostEqual(total['value'], 10 + 1 / 0.3048**3, places=10)
        self.assertEqual(total['unit'], 'cusec')
        self.assertTrue(total['derived'])
        self.assertEqual(total['contributors'], self.pointers)
        self.assertIsNone(total['reason'])

    def test_missing_or_unusable_contributor_never_becomes_zero(self):
        """A partial station flow remains unavailable even with a valid cusec."""
        first, second = self.pointers
        for missing in (
            None,
            self.reading(second, None, 'cusec', reason='not_bound'),
            self.reading(second, None, 'm**3/s', reason='stale'),
            self.reading(second, None, 'cusec', reason='bad_quality'),
        ):
            with self.subTest(reason=missing and missing['reason']):
                points = {first: self.reading(first, 10, 'cusec')}
                if missing is not None:
                    points[second] = missing
                total = total_value(self.definition, points, self.pumps, True)
                self.assertIsNone(total['value'])
                self.assertEqual(total['unit'], 'cusec')
                self.assertTrue(total['derived'])
                self.assertEqual(total['contributors'], self.pointers)
                self.assertEqual(
                    total['reason'], 'not_published' if missing is None else 'incomplete'
                )
