"""Tests for the health summary projection and immutable evidence capture."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from assets.health_models import (
    AnomalySeverity,
    HealthEvidenceSnapshot,
    HealthState,
    SignalQuality,
    SnapshotReason,
)
from machine_health.services.anomalies import fingerprint_for, record_anomaly
from machine_health.services.snapshots import (
    SnapshotError,
    capture_anomaly_evidence,
    capture_current_signal,
)
from machine_health.services.summary import health_summary, signal_rows

from .fixtures import HealthEnvMixin


class HealthSummaryTest(HealthEnvMixin, TestCase):
    """The current-condition projection never presents stale data as current."""

    def setUp(self):
        """Build a machine with one bounded signal and a 15-minute budget."""
        self.build_health_env(freshness=900)
        self.now = timezone.now()

    def test_unmapped_machine_reports_unknown_not_healthy(self):
        """A machine with no source is unknown, and says it is unconfigured."""
        self.binding.delete()

        summary = health_summary(self.machine, now=self.now)

        self.assertEqual(summary['state'], HealthState.UNKNOWN)
        self.assertFalse(summary['configured'])
        self.assertEqual(summary['signal_count'], 0)

    def test_fresh_in_range_signal_reads_normal(self):
        """A recent value inside its limits is normal."""
        self.set_signal(3.0, observed_at=self.now)

        summary = health_summary(self.machine, now=self.now)

        self.assertEqual(summary['state'], HealthState.NORMAL)
        self.assertTrue(summary['configured'])
        self.assertFalse(summary['degraded_data'])
        self.assertEqual(summary['stale_signal_count'], 0)

    def test_every_signal_stale_reads_offline(self):
        """A connector outage reads as an outage, not as a healthy machine."""
        self.set_signal(3.0, observed_at=self.now - timedelta(hours=2))

        summary = health_summary(self.machine, now=self.now)

        self.assertEqual(summary['state'], HealthState.OFFLINE)
        self.assertTrue(summary['degraded_data'])
        self.assertEqual(summary['stale_signal_count'], 1)

    def test_stale_signal_reports_no_health_verdict(self):
        """A stale row does not classify: its state is unknown, not normal."""
        self.set_signal(3.0, observed_at=self.now - timedelta(hours=2))

        [row] = signal_rows(self.machine, now=self.now)

        self.assertTrue(row['stale'])
        self.assertEqual(row['state'], HealthState.UNKNOWN)

    def test_bad_quality_marks_the_data_degraded(self):
        """Uncertain or bad quality is surfaced even when the value is in range."""
        self.set_signal(3.0, observed_at=self.now, quality=SignalQuality.BAD)

        summary = health_summary(self.machine, now=self.now)

        self.assertTrue(summary['degraded_data'])

    def test_active_anomaly_drives_the_overall_state(self):
        """A critical anomaly outranks otherwise-normal signals."""
        self.set_signal(3.0, observed_at=self.now)
        record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('test', 'critical'),
            title='Seal alarm',
            severity=AnomalySeverity.CRITICAL,
        )

        summary = health_summary(self.machine, now=self.now)

        self.assertEqual(summary['state'], HealthState.CRITICAL)
        self.assertEqual(summary['active_anomaly_count'], 1)
        self.assertEqual(summary['anomaly_counts'][AnomalySeverity.CRITICAL], 1)

    def test_source_status_reports_redacted_errors_only(self):
        """Connection health is exposed; provider messages are not."""
        self.source.last_error_at = self.now
        self.source.last_error_code = 'TIMEOUT'
        self.source.save()

        summary = health_summary(self.machine, now=self.now)

        [source] = summary['sources']
        self.assertEqual(source['last_error_code'], 'TIMEOUT')
        self.assertEqual(source['mapped_tag_count'], 1)
        self.assertFalse(source['healthy'])


class EvidenceSnapshotTest(HealthEnvMixin, TestCase):
    """Snapshots are immutable citations, including when data was missing."""

    def setUp(self):
        """Build a machine with one bounded signal."""
        self.build_health_env()
        self.now = timezone.now()

    def test_capture_records_value_quality_and_freshness(self):
        """A capture freezes what was observed and how good it was."""
        self.set_signal(7.2, observed_at=self.now)

        snapshot = capture_current_signal(
            self.binding, reason=SnapshotReason.ANOMALY_REPAIR, now=self.now
        )

        self.assertEqual(snapshot.statistics['latest'], 7.2)
        self.assertEqual(snapshot.signal_label, self.binding.display_name)
        self.assertEqual(snapshot.unit, 'mm/s')
        self.assertFalse(snapshot.stale)
        self.assertTrue(snapshot.content_hash)

    def test_missing_reading_is_captured_as_evidence_of_absence(self):
        """No data at decision time is itself evidence, not a skipped capture."""
        snapshot = capture_current_signal(
            self.binding, reason=SnapshotReason.AI_DIAGNOSIS, now=self.now
        )

        self.assertTrue(snapshot.stale)
        self.assertEqual(snapshot.quality, SignalQuality.UNKNOWN)
        self.assertFalse(snapshot.statistics['available'])

    def test_stale_reading_is_captured_as_stale(self):
        """A capture over an aged window records that it was already stale."""
        self.set_signal(3.0, observed_at=self.now - timedelta(hours=3))

        snapshot = capture_current_signal(
            self.binding, reason=SnapshotReason.MANUAL_REPAIR, now=self.now
        )

        self.assertTrue(snapshot.stale)

    def test_snapshot_cannot_be_updated(self):
        """Evidence is immutable; a correction is a new capture."""
        self.set_signal(3.0, observed_at=self.now)
        snapshot = capture_current_signal(
            self.binding, reason=SnapshotReason.MANUAL_REPAIR, now=self.now
        )

        snapshot.statistics = {'latest': 999}
        with self.assertRaisesMessage(ValueError, 'immutable'):
            snapshot.save()

        snapshot.refresh_from_db()
        self.assertEqual(snapshot.statistics['latest'], 3.0)

    def test_unknown_reason_is_refused(self):
        """The capture reason comes from a closed vocabulary."""
        with self.assertRaises(SnapshotError):
            capture_current_signal(self.binding, reason='curiosity')

    def test_anomaly_capture_covers_every_implicated_signal(self):
        """One capture per signal behind the anomaly, in a stable order."""
        self.set_signal(9.5, observed_at=self.now)
        anomaly, _ = record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('test', 'evidence'),
            title='Vibration alarm',
            severity=AnomalySeverity.CRITICAL,
            bindings=[self.binding],
        )

        snapshots = capture_anomaly_evidence(anomaly, now=self.now)

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].anomaly, anomaly)
        self.assertEqual(HealthEvidenceSnapshot.objects.count(), 1)

    def test_anomaly_without_signals_yields_no_false_evidence(self):
        """An anomaly with no mapped signal produces no snapshots."""
        anomaly, _ = record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('test', 'no-signals'),
            title='Operator report',
            severity=AnomalySeverity.WARNING,
        )

        self.assertEqual(capture_anomaly_evidence(anomaly, now=self.now), [])
