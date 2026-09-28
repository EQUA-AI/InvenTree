"""Applying reviewed alarm limits from a file.

Limits decide when somebody is woken up. The rules here are the ones the
existing review arrived at the hard way: a bound needs a citation, a figure
derived in one unit must never land on a signal measured in another, and a
channel whose own data breaches the limit is left disarmed with its reason
recorded rather than quietly armed.
"""

from __future__ import annotations

import json
import re
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from assets.health_models import (
    AnomalySeverity,
    HealthSource,
    MachineAnomaly,
    MachineSignalBinding,
    MachineSignalState,
    SignalQuality,
)
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
                        item['station'].strip(), 'bay numbering repeats across stations'
                    )


class ApplyLimitsEnv:
    """One station, one bay, two winding channels and one pressure."""

    def setUp(self):
        """One station, one bay, two winding channels and one pressure."""
        client = Client.objects.create(name='Estate', code='estate')
        self.source = HealthSource.objects.create(
            name='probe',
            source_type='iot',
            connector_type='cosmos_pumphouse',
            client=client,
            active=True,
            config={
                'endpoint': 'https://x.documents.azure.com:443/',
                'database': 'aimms',
                'readings_container': 'r',
            },
        )
        self.station = AssetMachine.objects.create(
            name='Station',
            asset_type='pumphouse',
            client=client,
            source_namespace='klsw',
            source_key='PH_9',
            source_entity_uuid='11111111-1111-4111-8111-111111111111',
        )
        self.bindings = {}
        for key, unit in (
            ('/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1', 'degC'),
            ('/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED2', 'degC'),
            ('/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED3', 'mH2O'),
        ):
            point = DictionaryPoint.objects.create(
                station=self.station,
                machine=self.station,
                path=key,
                raw_tag=key,
                status='approved',
                data_type='number',
                unit=unit,
            )
            self.bindings[key] = MachineSignalBinding.objects.create(
                source=self.source,
                machine=self.station,
                dictionary_point=point,
                external_key=key,
                unit=unit,
                active=True,
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
                'apply_signal_limits',
                limits=write(tmp, document),
                stdout=out,
                **options,
            )
        return out.getvalue()


class ApplyLimitsTests(ApplyLimitsEnv, TestCase):
    """What the command does to bindings."""

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
        self.run_command(
            self.document(
                exclude=[
                    {
                        'station': 'PH_9',
                        'key': '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED2',
                        'reason': 'pegged at the over-range marker',
                    }
                ]
            )
        )

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
            self.run_command(
                self.document(
                    exclude=[
                        {
                            'station': 'PH_9',
                            'key': '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED2',
                        }
                    ]
                )
            )

    def test_a_warning_above_its_critical_is_refused(self):
        """It could never be reached, so it is a typo, not a policy."""
        with self.assertRaises(CommandError):
            self.run_command(self.document(warn_max=150))


class ApplyLimitsEvaluationTests(ApplyLimitsEnv, TestCase):
    """Arming a limit also judges it, and says how many alarms it raises.

    Without this the command is inert on exactly this estate. The poller
    evaluates the machines a poll wrote state for, and every station here is
    parked at the end of a fully consumed recorded window, so no poll ever
    applies another document. A limit armed after the readings landed would
    therefore never be judged at all.
    """

    def set_reading(self, key, value, quality=SignalQuality.GOOD):
        """Give one of the fixture's bindings a current reading."""
        MachineSignalState.objects.update_or_create(
            binding=self.bindings[key],
            defaults={
                'value': {'value': value},
                'observed_at': timezone.now(),
                'quality': quality,
            },
        )

    def test_a_preview_reports_how_many_alarms_the_file_would_raise(self):
        """The count of people woken is the thing worth seeing before committing."""
        self.set_reading('/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1', 191.0)

        output = self.run_command(self.document(), dry_run=True)

        self.assertIn('evaluated : 1 machines, 1 breaching', output)
        # A preview must leave nothing behind, verdicts included.
        self.assertFalse(MachineAnomaly.objects.exists())
        self.assertIsNone(
            MachineSignalBinding.objects.get(
                external_key='/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1'
            ).critical_max
        )

    def test_applying_raises_the_anomaly_the_preview_predicted(self):
        """Same estate, same file, without --dry-run."""
        self.set_reading('/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1', 191.0)

        output = self.run_command(self.document())

        self.assertIn('evaluated : 1 machines, 1 breaching', output)
        anomaly = MachineAnomaly.objects.get(machine=self.station)
        self.assertEqual(anomaly.severity, AnomalySeverity.CRITICAL)
        self.assertEqual(anomaly.detector, 'threshold')

    def test_a_reading_inside_the_limit_raises_nothing(self):
        """The evaluation is the limit's, not the command's."""
        self.set_reading('/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1', 83.9)

        output = self.run_command(self.document())

        self.assertIn('0 breaching', output)
        self.assertFalse(MachineAnomaly.objects.exists())

    def test_a_re_run_on_an_already_armed_estate_still_evaluates(self):
        """The count must come from every matched binding, not the changed ones.

        On a re-run every binding takes the "unchanged" branch, so collecting
        machines after the save would evaluate nothing - and the 307 points
        already armed on this estate would stay unjudged for ever.
        """
        self.set_reading('/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1', 191.0)
        self.run_command(self.document())
        MachineAnomaly.objects.all().delete()

        output = self.run_command(self.document())

        self.assertIn('applied   : 0', output)
        self.assertIn('unchanged : 2', output)
        self.assertIn('evaluated : 1 machines, 1 breaching', output)
        self.assertTrue(MachineAnomaly.objects.exists())

    def test_an_unusable_reading_is_not_a_breach(self):
        """The over-range sentinel must not become an alarm about a measurement."""
        self.set_reading(
            '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1',
            3276.7,
            quality=SignalQuality.BAD,
        )

        output = self.run_command(self.document())

        self.assertIn('0 breaching', output)
        self.assertFalse(MachineAnomaly.objects.exists())


class BackstopRowTests(TestCase):
    """The core row is a backstop, and the file has to keep saying so.

    It carries the stator-winding figures onto motor and stator core detectors
    because no standard publishes a core temperature at all. That is defensible
    only as an upper bound - a core above the winding's own design ceiling is
    abnormal whatever the true core figure is - and it stops being defensible
    the moment someone reads the row as a core limit. So the things that make it
    honest are pinned here rather than left to a reviewer's memory.
    """

    def setUp(self):
        """Load the committed file and find the two families."""
        document = json.loads(LIMITS_FILE.read_text(encoding='utf-8'))
        self.families = {entry['family']: entry for entry in document['limits']}
        self.core = next(
            entry
            for name, entry in self.families.items()
            if name.startswith('Motor / stator core')
        )
        self.winding = self.families['Stator winding ETDs']

    def test_the_backstop_says_no_standard_fixes_a_core_temperature(self):
        """Its citation must disclaim itself, or the next reader will trust it."""
        citation = self.core['citation'].lower()
        self.assertIn('no standard fixes a core temperature', citation)
        self.assertIn('claims nothing about cores', citation)

    def test_the_backstop_carries_the_winding_figures_unchanged(self):
        """A different number here would be an invented core limit."""
        for bound in ('warn_max', 'critical_max'):
            self.assertEqual(self.core[bound], self.winding[bound])
        # And nothing else: a minimum would be a dead-channel gate in disguise.
        for bound in ('normal_min', 'normal_max', 'warn_min', 'critical_min'):
            self.assertIsNone(self.core.get(bound))

    def test_the_two_families_cannot_both_match_a_tag(self):
        """Overlap would make which limit a point gets depend on file order."""
        core = re.compile(self.core['match'])
        winding = re.compile(self.winding['match'])
        for path in (
            '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1',
            '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMP1',
            '/dex/PUMP4_MOTOR_CORE_RTD2_PROCESS_VALUE',
            '/dex/PUMP1_MOTOR_STATOR_CORE_RTD3',
        ):
            with self.subTest(path=path):
                self.assertFalse(
                    bool(core.match(path)) and bool(winding.match(path)),
                    'a tag matched by both families gets its limit by file order',
                )

    def test_the_backstop_does_not_reach_the_stator_winding_tags(self):
        """The winding row is screened per channel; the backstop must not widen it."""
        core = re.compile(self.core['match'])
        self.assertFalse(core.match('/dex/PUMP2_PUMP_MOTOR_WINDING_TEMPERATURED1'))
        self.assertTrue(core.match('/dex/PUMP4_MOTOR_CORE_RTD2_PROCESS_VALUE'))
        self.assertTrue(core.match('/dex/PUMP12_MOTOR_STATOR_CORE_RTD6'))

    def test_the_dead_channel_is_excluded_with_what_it_actually_reads(self):
        """A channel constant at an impossible value cannot carry a limit."""
        [excluded] = self.core['exclude']
        self.assertEqual(excluded['key'], '/dex/PUMP4_MOTOR_CORE_RTD2_PROCESS_VALUE')
        self.assertEqual(excluded['station'], 'PH_3')
        self.assertIn('-242.1', excluded['reason'])
        self.assertIn('75 of 75', excluded['reason'])
