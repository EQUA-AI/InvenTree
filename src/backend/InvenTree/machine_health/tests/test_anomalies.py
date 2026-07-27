"""Tests for deterministic anomaly detection, dedupe and lifecycle."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from assets.health_models import (
    AnomalySeverity,
    AnomalyStatus,
    HealthState,
    MachineAnomaly,
)
from machine_health.services.anomalies import (
    AnomalyError,
    acknowledge_anomaly,
    evaluate_thresholds,
    fingerprint_for,
    ingest_source_alarm,
    record_anomaly,
)

from .fixtures import HealthEnvMixin


class ThresholdDetectionTest(HealthEnvMixin, TestCase):
    """Threshold rules raise and clear anomalies deterministically."""

    def setUp(self):
        """Build a machine with one bounded vibration signal."""
        self.build_health_env()

    def test_reading_inside_limits_raises_nothing(self):
        """A healthy signal produces no anomaly."""
        self.set_signal(3.0)
        self.assertEqual(evaluate_thresholds(self.machine), [])
        self.assertFalse(MachineAnomaly.objects.exists())

    def test_warning_and_critical_bounds_are_distinguished(self):
        """Severity follows the configured band the value falls into."""
        self.set_signal(7.0)
        [warning] = evaluate_thresholds(self.machine)
        self.assertEqual(warning.severity, AnomalySeverity.WARNING)

        self.set_signal(12.0)
        [critical] = evaluate_thresholds(self.machine)
        self.assertEqual(critical.pk, warning.pk)
        self.assertEqual(critical.severity, AnomalySeverity.CRITICAL)

    def test_severity_escalates_but_never_silently_de_escalates(self):
        """A brief dip cannot downgrade a standing critical condition."""
        self.set_signal(12.0)
        [critical] = evaluate_thresholds(self.machine)

        self.set_signal(7.0)
        [refreshed] = evaluate_thresholds(self.machine)

        self.assertEqual(refreshed.pk, critical.pk)
        self.assertEqual(refreshed.severity, AnomalySeverity.CRITICAL)

    def test_repeated_evaluation_updates_one_row(self):
        """Re-running detection is idempotent, not a flood of anomalies."""
        self.set_signal(7.0)
        evaluate_thresholds(self.machine)
        evaluate_thresholds(self.machine)
        evaluate_thresholds(self.machine)

        self.assertEqual(MachineAnomaly.objects.count(), 1)

    def test_signal_returning_to_normal_resolves_its_own_anomaly(self):
        """A threshold anomaly clears when the value comes back inside limits."""
        self.set_signal(7.0)
        [anomaly] = evaluate_thresholds(self.machine)

        self.set_signal(3.0)
        evaluate_thresholds(self.machine)

        anomaly.refresh_from_db()
        self.assertEqual(anomaly.status, AnomalyStatus.RESOLVED)
        self.assertIsNotNone(anomaly.resolved_at)

    def test_acknowledged_anomaly_is_not_auto_resolved(self):
        """An operator's acknowledgement is not closed on their behalf."""
        self.set_signal(7.0)
        [anomaly] = evaluate_thresholds(self.machine)
        actor = get_user_model().objects.create_user(
            username='ack-user', email='ack@example.com', password='pw'
        )
        acknowledge_anomaly(anomaly.pk, actor=actor)

        self.set_signal(3.0)
        evaluate_thresholds(self.machine)

        anomaly.refresh_from_db()
        self.assertEqual(anomaly.status, AnomalyStatus.ACKNOWLEDGED)

    def test_unbounded_signal_has_no_opinion(self):
        """A binding with no limits never manufactures a health verdict."""
        self.binding.normal_max = None
        self.binding.warn_max = None
        self.binding.critical_max = None
        self.binding.save()

        self.set_signal(999.0)

        self.assertEqual(self.binding.classify(999.0), HealthState.UNKNOWN)
        self.assertEqual(evaluate_thresholds(self.machine), [])


class SourceAlarmTest(HealthEnvMixin, TestCase):
    """Alarms declared by the source system."""

    def setUp(self):
        """Build the health environment."""
        self.build_health_env()

    def test_repeated_alarm_is_idempotent(self):
        """Re-sending one alarm refreshes the open anomaly."""
        first, created = ingest_source_alarm(
            machine=self.machine,
            source=self.source,
            alarm_code='VIB-HI',
            title='High vibration',
            severity=AnomalySeverity.CRITICAL,
            external_key=self.binding.external_key,
        )
        self.assertTrue(created)

        later = timezone.now() + timedelta(minutes=5)
        second, created_again = ingest_source_alarm(
            machine=self.machine,
            source=self.source,
            alarm_code='VIB-HI',
            title='High vibration',
            severity=AnomalySeverity.CRITICAL,
            observed_at=later,
            external_key=self.binding.external_key,
        )

        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(MachineAnomaly.objects.count(), 1)
        self.assertEqual(second.last_observed_at, later)

    def test_alarm_links_the_signal_it_names(self):
        """A mapped tag is attached so the anomaly can cite its signal."""
        anomaly, _ = ingest_source_alarm(
            machine=self.machine,
            source=self.source,
            alarm_code='VIB-HI',
            title='High vibration',
            severity=AnomalySeverity.WARNING,
            external_key=self.binding.external_key,
        )
        self.assertEqual(list(anomaly.bindings.all()), [self.binding])

    def test_resolved_alarm_can_open_again(self):
        """Once resolved, the same condition may legitimately recur."""
        anomaly, _ = ingest_source_alarm(
            machine=self.machine,
            source=self.source,
            alarm_code='VIB-HI',
            title='High vibration',
            severity=AnomalySeverity.WARNING,
        )
        anomaly.status = AnomalyStatus.RESOLVED
        anomaly.save(update_fields=['status'])

        reopened, created = ingest_source_alarm(
            machine=self.machine,
            source=self.source,
            alarm_code='VIB-HI',
            title='High vibration',
            severity=AnomalySeverity.WARNING,
        )

        self.assertTrue(created)
        self.assertNotEqual(reopened.pk, anomaly.pk)
        self.assertEqual(MachineAnomaly.objects.count(), 2)


class AcknowledgementTest(HealthEnvMixin, TestCase):
    """Acknowledging records that a human saw the condition."""

    def setUp(self):
        """Build a machine with one open anomaly."""
        self.build_health_env()
        self.actor = get_user_model().objects.create_user(
            username='acker', email='acker@example.com', password='pw'
        )
        self.anomaly, _ = record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('test', 'ack'),
            title='Bearing temperature rising',
            severity=AnomalySeverity.WARNING,
        )

    def test_acknowledge_records_actor_and_note(self):
        """The acknowledgement is attributed and time-stamped."""
        acknowledged = acknowledge_anomaly(
            self.anomaly.pk, actor=self.actor, note='Route tech notified'
        )

        self.assertEqual(acknowledged.status, AnomalyStatus.ACKNOWLEDGED)
        self.assertEqual(acknowledged.acknowledged_by, self.actor)
        self.assertIsNotNone(acknowledged.acknowledged_at)
        self.assertEqual(acknowledged.acknowledgement_note, 'Route tech notified')

    def test_acknowledging_twice_is_a_no_op(self):
        """A repeated acknowledgement does not rewrite the original record."""
        first = acknowledge_anomaly(self.anomaly.pk, actor=self.actor)
        second = acknowledge_anomaly(self.anomaly.pk, actor=self.actor)
        self.assertEqual(first.acknowledged_at, second.acknowledged_at)

    def test_resolved_anomaly_cannot_be_acknowledged(self):
        """Acknowledgement applies to open conditions only."""
        self.anomaly.status = AnomalyStatus.RESOLVED
        self.anomaly.save(update_fields=['status'])

        with self.assertRaises(AnomalyError):
            acknowledge_anomaly(self.anomaly.pk, actor=self.actor)

    def test_unknown_severity_is_refused(self):
        """Severity comes from a closed vocabulary."""
        with self.assertRaises(AnomalyError):
            record_anomaly(
                machine=self.machine,
                fingerprint=fingerprint_for('test', 'bad-severity'),
                title='x',
                severity='catastrophic',
            )
