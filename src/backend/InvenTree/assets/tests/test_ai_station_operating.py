"""Operating-state projection shares the mimic without widening AI authority."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from assets.ai_read import machine_operating_context


class MachineOperatingContextTests(SimpleTestCase):
    """Keep native status, unknown states and record authority intact."""

    def setUp(self):
        """Prepare a scoped station without database reads."""
        self.actor = object()
        self.station = SimpleNamespace(
            pk=20, asset_type='pumphouse', name='Test station', parent=None
        )
        self.pump = SimpleNamespace(
            pk=21, asset_type='pump', name='Pump 01', parent=self.station
        )
        self.projection = {
            'generated_at': timezone.now(),
            'last_observed_at': timezone.now(),
            'reporting': True,
            'read_failed': False,
            'telemetry_presentation': 'live',
            'source': {'name': 'TEST REPLAY — synthetic baseline', 'config': 'excluded'},
            'bays': [
                {
                    'machine': 21,
                    'name': 'Pump 01',
                    'state': 'running',
                    'alarm': 'critical',
                    'active': False,
                },
                {'machine': 22, 'name': 'Pump 02', 'state': 'idle'},
                {'machine': 23, 'name': 'Pump 03', 'state': 'stale'},
            ],
        }
        self.reader = patch(
            'machine_health.services.mimic.station_mimic',
            return_value=self.projection,
        ).start()
        self.authorized = patch(
            'assets.ai_read.authorized_machine', return_value=self.station
        ).start()
        self.analysis = patch(
            'ai.core.analysis.scope_context.scope_miss_for_machine',
            return_value=None,
        ).start()
        self.addCleanup(patch.stopall)

    def test_native_running_state_is_not_inventory_active(self):
        """Use actual operating state rather than inventory enablement."""
        result = machine_operating_context(self.actor, self.station)
        self.assertEqual(result['running_count'], 1)
        self.assertEqual(result['population_count'], 3)
        self.assertEqual(result['bays'][0]['operating_state'], 'running')
        self.assertEqual(result['bays'][0]['alarm'], 'critical')
        self.assertEqual(result['source'], 'machine_health.station_mimic')
        self.assertNotIn('active', result['bays'][0])
        self.assertIn('TEST REPLAY', result['source_name'])
        self.assertNotIn('excluded', str(result))

    def test_unknown_is_not_counted_as_idle(self):
        """Retain an explicit unknown count for stale readings."""
        result = machine_operating_context(self.actor, self.station)
        self.assertEqual(result['unknown_count'], 1)
        self.assertEqual(result['bays'][2]['operating_state'], 'stale')

    def test_unauthorized_station_never_reads_projection(self):
        """Fail closed before reading a forbidden station."""
        self.authorized.return_value = None
        result = machine_operating_context(self.actor, self.station)
        self.assertFalse(result['available'])
        self.reader.assert_not_called()

    def test_unauthorized_bay_is_excluded_from_counts_and_text(self):
        """Exclude forbidden pump identities and aggregated counts."""
        self.authorized.side_effect = (
            lambda _actor, machine_id: None if machine_id == 21 else self.station
        )
        result = machine_operating_context(self.actor, self.station)
        self.assertEqual(result['running_count'], 0)
        self.assertEqual(result['population_count'], 2)
        self.assertNotIn('Pump 01', str(result))

    def test_analysis_scope_is_not_widened(self):
        """Apply analysis scope on top of actor authority."""
        self.analysis.side_effect = (
            lambda machine_id: {'error': 'scope'} if machine_id == 21 else None
        )
        result = machine_operating_context(self.actor, self.station)
        self.assertEqual(result['running_count'], 0)
        self.assertNotIn('Pump 01', str(result))

    def test_pump_context_does_not_include_siblings(self):
        """A pump query never receives unrelated sibling readings."""
        result = machine_operating_context(self.actor, self.pump)
        self.assertEqual(result['population_count'], 1)
        self.assertEqual(result['bays'][0]['machine_id'], self.pump.pk)

    def test_missing_station_is_unavailable_not_zero_running(self):
        """Unavailable hierarchy does not establish a stopped pump."""
        self.pump.parent = None
        result = machine_operating_context(self.actor, self.pump)
        self.assertEqual(result, {'available': False, 'reason': 'station_unavailable'})
        self.reader.assert_not_called()
