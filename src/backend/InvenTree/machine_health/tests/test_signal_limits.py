"""Applying reviewed alarm limits from a file.

Limits decide when somebody is woken up. The rules here are the ones the
existing review arrived at the hard way: a bound needs a citation, a figure
derived in one unit must never land on a signal measured in another, and a
channel whose own data breaches the limit is left disarmed with its reason
recorded rather than quietly armed.
"""

from __future__ import annotations

import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from assets.health_models import HealthSource, MachineSignalBinding
from assets.models import AssetMachine, Client, DictionaryPoint

LIMITS_FILE = (
    Path(__file__).resolve().parents[5] / 'contrib/cosmos/limits/pumphouse.limits.json'
)


def write(tmp, document):
    """Put a limits document somewhere the command can read it."""
    path = Path(tmp) / 'limits.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    return path


class LimitsFileTests(TestCase):
    """The committed file must say what the database already holds."""

    def test_the_committed_file_is_valid_and_carries_its_citation(self):
        """A bound without a citation is a number nobody can defend."""
        document = json.loads(LIMITS_FILE.read_text(encoding='utf-8'))

        self.assertEqual(document['schema_version'], 1)
        for entry in document['limits']:
            with self.subTest(family=entry['family']):
                self.assertTrue(entry['citation'].strip())
                self.assertTrue(entry['reasoning'].strip())
                self.assertTrue(entry['unit'].strip())
                for item in entry.get('exclude', []):
                    self.assertTrue(item['reason'].strip())
                    self.assertTrue(
                        item['station'].strip(),
                        'bay numbering repeats across stations',
                    )


class ApplyLimitsTests(TestCase):
    """What the command does to bindings."""

    def setUp(self):
        """One station, one bay, two winding channels and one pressure."""
        client = Client.objects.create(name='Estate', code='estate')
        self.source = HealthSource.objects.create(
            name='probe', source_type='iot', connector_type='cosmos_pumphouse',
            client=client, active=True,
            config={'endpoint': 'https://x.documents.azure.com:443/',
                    'database': 'aimms', 'readings_container': 'r'},
        )
        self.station = AssetMachine.objects.create(
            name='Station', asset_type='pumphouse', client=client,
            source_namespace='klsw', source_key='PH_9',
            source_entity_uuid='11111111-1111-4111-8111-111111111111',
        )
        self.bindings = {}
        for key, unit in (
            ('/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1', 'degC'),
            ('/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED2', 'degC'),
            ('/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED3', 'mH2O'),
        ):
            point = DictionaryPoint.objects.create(
                station=self.station, machine=self.station, path=key,
                raw_tag=key, status='approved', data_type='number', unit=unit,
            )
            self.bindings[key] = MachineSignalBinding.objects.create(
                source=self.source, machine=self.station, dictionary_point=point,
                external_key=key, unit=unit, active=True,
                display_name=key.rsplit('_', 1)[-1],
            )

    def document(self, **overrides):
        """A one-family limits document over the winding tags."""
        entry = {
            'family': 'Stator winding ETDs',
            'match': r'^/dex/PUMP\d+_PUMP_MOTOR_WINDING_TEMPERATURED\d+$',
            'unit': 'degC',
            'warn_max': 125,
            'critical_max': 145,
            'citation': 'IS/IEC 60034-1 Table 7 item 1a',
            'reasoning': 'the design ceiling, not an operating band',
        }
        entry.update(overrides)
        return {'schema_version': 1, 'limits': [entry]}

    def run_command(self, document, **options):
        """Apply a document and hand back what the command printed."""
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            call_command(
                'apply_signal_limits', limits=write(tmp, document),
                stdout=out, **options
            )
        return out.getvalue()

    def test_limits_land_only_on_the_unit_they_were_derived_for(self):
        """A degC ceiling on a pressure signal would be silently wrong."""
        self.run_command(self.document())

        for key in (
            '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1',
            '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED2',
        ):
            self.bindings[key].refresh_from_db()
            self.assertEqual(self.bindings[key].warn_max, 125)
            self.assertEqual(self.bindings[key].critical_max, 145)

        pressure = self.bindings['/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED3']
        pressure.refresh_from_db()
        self.assertIsNone(pressure.warn_max, 'mH2O must not take a degC ceiling')

    def test_an_excluded_channel_is_left_disarmed(self):
        """A channel whose own data breaches the limit raises a false alarm."""
        self.run_command(self.document(exclude=[{
            'station': 'PH_9',
            'key': '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED2',
            'reason': 'pegged at the over-range marker',
        }]))

        kept = self.bindings['/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1']
        skipped = self.bindings['/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED2']
        kept.refresh_from_db()
        skipped.refresh_from_db()
        self.assertEqual(kept.warn_max, 125)
        self.assertIsNone(skipped.warn_max)

    def test_a_dry_run_writes_nothing(self):
        """An operator previews a change that wakes people up."""
        output = self.run_command(self.document(), dry_run=True)

        self.assertIn('Rolled back preview.', output)
        binding = self.bindings['/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1']
        binding.refresh_from_db()
        self.assertIsNone(binding.warn_max)

    def test_applying_twice_changes_nothing_the_second_time(self):
        """Re-applying after a re-review is the normal case, not an edge one."""
        self.run_command(self.document())
        output = self.run_command(self.document())

        self.assertIn('applied   : 0', output)
        self.assertIn('unchanged : 2', output)

    def test_a_bound_without_a_citation_is_refused(self):
        """The rule the existing review was built on."""
        with self.assertRaises(CommandError):
            self.run_command(self.document(citation=''))

    def test_an_exclusion_without_a_reason_is_refused(self):
        """A channel nobody can explain is forgotten, not excluded."""
        with self.assertRaises(CommandError):
            self.run_command(self.document(exclude=[{
                'station': 'PH_9',
                'key': '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED2',
            }]))

    def test_a_warning_above_its_critical_is_refused(self):
        """It could never be reached, so it is a typo, not a policy."""
        with self.assertRaises(CommandError):
            self.run_command(self.document(warn_max=150))
