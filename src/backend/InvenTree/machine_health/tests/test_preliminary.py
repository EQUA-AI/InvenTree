"""Tests for evidence-cited preliminary results.

The point of these is that the service never overstates what it knows: missing
and stale telemetry produce explicit statuses and zero confidence, not a
plausible-sounding cause.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from repair.schema import (
    RELATION_SUPPORTS,
    RELATION_UNKNOWN,
    STATUS_AVAILABLE,
    STATUS_STALE,
    STATUS_UNAVAILABLE,
    is_preliminary,
    validate_diagnosis,
)

from assets.health_models import AnomalySeverity, SignalQuality
from machine_health.services.anomalies import fingerprint_for, record_anomaly
from machine_health.services.preliminary import analyze_anomaly

from .fixtures import HealthEnvMixin


class PreliminaryAnalysisTest(HealthEnvMixin, TestCase):
    """Preliminary results restate measurements and cite every one."""

    def setUp(self):
        """Build a machine with one bounded signal and an open anomaly."""
        self.build_health_env()
        self.now = timezone.now()
        self.anomaly, _ = record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('prelim', 'vibration'),
            title='Drive-end vibration above limit',
            severity=AnomalySeverity.CRITICAL,
            bindings=[self.binding],
        )

    def test_fresh_reading_produces_a_cited_supporting_result(self):
        """A usable reading is cited by snapshot id and marked supporting."""
        self.set_signal(9.5, observed_at=self.now)

        result = analyze_anomaly(self.anomaly, now=self.now)

        validate_diagnosis(result)
        self.assertEqual(result['status'], STATUS_AVAILABLE)
        self.assertGreater(result['confidence'], 0)

        [evidence] = result['evidence']
        self.assertTrue(evidence['snapshot_id'])
        self.assertEqual(evidence['relation'], RELATION_SUPPORTS)
        self.assertIn('9.5', evidence['observation'])

    def test_result_is_always_preliminary(self):
        """The service never marks its own output verified."""
        self.set_signal(9.5, observed_at=self.now)

        result = analyze_anomaly(self.anomaly, now=self.now)

        self.assertFalse(result['verified_by_user'])
        self.assertTrue(is_preliminary(result))
        self.assertEqual(result['provider'], 'machine_health.preliminary')

    def test_missing_telemetry_is_reported_not_guessed(self):
        """With no reading at all the status is unavailable and confidence zero."""
        result = analyze_anomaly(self.anomaly, now=self.now)

        validate_diagnosis(result)
        self.assertEqual(result['status'], STATUS_UNAVAILABLE)
        self.assertEqual(result['confidence'], 0.0)
        self.assertIn('needs a manual assessment', result['likely_cause'])
        self.assertEqual(result['alternatives'], [])

    def test_stale_telemetry_cannot_establish_a_cause(self):
        """Aged data is called out rather than presented as current."""
        self.set_signal(9.5, observed_at=self.now - timedelta(hours=3))

        result = analyze_anomaly(self.anomaly, now=self.now)

        self.assertEqual(result['status'], STATUS_STALE)
        self.assertEqual(result['confidence'], 0.0)
        self.assertTrue(result['freshness']['stale'])
        self.assertIn('stale', result['likely_cause'])
        # The stale observation is still cited - it just supports nothing.
        [evidence] = result['evidence']
        self.assertEqual(evidence['relation'], RELATION_UNKNOWN)

    def test_poor_quality_data_lowers_confidence(self):
        """A usable but uncertain reading is worth less than a clean one."""
        self.set_signal(9.5, observed_at=self.now, quality=SignalQuality.UNCERTAIN)
        degraded = analyze_anomaly(self.anomaly, now=self.now)

        self.set_signal(9.5, observed_at=self.now, quality=SignalQuality.GOOD)
        clean = analyze_anomaly(self.anomaly, now=self.now)

        self.assertLess(degraded['confidence'], clean['confidence'])
        self.assertEqual(degraded['quality']['summary'], 'degraded')

    def test_confirm_tests_are_proposed_for_gaps(self):
        """Missing data produces a concrete check rather than silence."""
        result = analyze_anomaly(self.anomaly, now=self.now)

        self.assertTrue(result['confirm_tests'])
        self.assertTrue(
            any('manual reading' in test for test in result['confirm_tests'])
        )

    def test_data_window_describes_what_was_seen(self):
        """The window and snapshot count are part of the provenance."""
        self.set_signal(9.5, observed_at=self.now)

        result = analyze_anomaly(self.anomaly, now=self.now)

        self.assertEqual(result['data_window']['snapshot_count'], 1)
        self.assertIsNotNone(result['data_window']['start'])
        self.assertIsNotNone(result['generated_at'])

    def test_analysis_can_re_read_evidence_without_capturing(self):
        """Re-reading an existing evidence set does not disturb it."""
        self.set_signal(9.5, observed_at=self.now)
        analyze_anomaly(self.anomaly, now=self.now)
        captured = self.anomaly.snapshots.count()

        analyze_anomaly(self.anomaly, capture=False, now=self.now)

        self.assertEqual(self.anomaly.snapshots.count(), captured)
