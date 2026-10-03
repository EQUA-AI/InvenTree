"""The test alarm is a real alarm, and leaves the machine as it found it."""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from assets.health_models import AnomalyStatus, MachineAnomaly, MachineSignalState

from .fixtures import HealthEnvMixin


class TestAlarmCommandTest(HealthEnvMixin, TestCase):
    """Raise through the real ingestion and detector; clear through the same."""

    def setUp(self):
        """One bounded signal reading calmly, with a critical limit of 9."""
        super().setUp()
        self.build_health_env()
        self.now = timezone.now()
        self.set_signal(3.2, observed_at=self.now)

    def run_command(self, *args):
        """Run the command with confirmation and return what it printed."""
        out = StringIO()
        call_command('test_alarm', '--machine', self.machine.pk, '--yes', *args, stdout=out)
        return out.getvalue()

    def test_it_refuses_without_confirmation(self):
        """Writing readings that are not measurements is never a default."""
        with self.assertRaises(CommandError):
            call_command('test_alarm', '--machine', self.machine.pk, stdout=StringIO())
        self.assertFalse(MachineAnomaly.objects.exists())

    def test_it_raises_a_real_critical_and_remembers_what_it_displaced(self):
        """The detector, not the command, decides there is an alarm."""
        printed = self.run_command()

        [anomaly] = MachineAnomaly.objects.all()
        self.assertEqual(anomaly.status, AnomalyStatus.OPEN)
        self.assertEqual(anomaly.severity, 'critical')
        self.assertEqual(anomaly.detector, 'threshold')
        self.assertIn('read 9.9', anomaly.evidence_summary)
        self.assertEqual(
            anomaly.metrics['test_alarm'][str(self.binding.pk)]['value'], 3.2
        )
        self.assertIn('raised : #', printed)
        self.assertIn('--clear --yes', printed)

        state = MachineSignalState.objects.get(binding=self.binding)
        self.assertEqual(state.value['value'], 9.9)

    def test_clearing_closes_the_condition_and_restores_the_row_as_found(self):
        """Closed by the detector's own recovery rule, then undone exactly.

        The recovery reading would sit five minutes past the plant's newest
        reading for ever on a station shown on a shifted clock, so the row is
        put back as it was - value, time, and how long it had held it.
        """
        before = MachineSignalState.objects.get(binding=self.binding)
        self.run_command()
        printed = self.run_command('--clear')

        [anomaly] = MachineAnomaly.objects.all()
        self.assertEqual(anomaly.status, AnomalyStatus.RESOLVED)
        self.assertIn('returned inside its configured limits', anomaly.resolution_note)
        self.assertIn('resolved', printed)
        after = MachineSignalState.objects.get(binding=self.binding)
        for name in (
            'value',
            'observed_at',
            'received_at',
            'value_changed_at',
            'quality',
            'payload_hash',
        ):
            self.assertEqual(getattr(after, name), getattr(before, name), name)

        with self.assertRaises(CommandError):
            self.run_command('--clear')
