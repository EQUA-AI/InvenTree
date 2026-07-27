"""Tests for evidence-cited preliminary results.

The point of these is that the service never overstates what it knows: missing
and stale telemetry produce explicit statuses and zero confidence, not a
plausible-sounding cause.

The exception, pinned in :class:`SourceDeclaredAuthorityTest`, is an alarm the
source system declared itself. Understating that is its own failure mode: the
boundaries were configured in the hub the data comes from, so reporting its
alarm as our uncertain inference is wrong in the other direction.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from repair.schema import (
    AUTHORITY_DERIVED,
    AUTHORITY_SOURCE_DECLARED,
    RELATION_SUPPORTS,
    RELATION_UNKNOWN,
    STATUS_AVAILABLE,
    STATUS_STALE,
    STATUS_UNAVAILABLE,
    is_preliminary,
    validate_diagnosis,
)

from assets.health_models import AnomalySeverity, SignalQuality
from machine_health.services.anomalies import (
    fingerprint_for,
    ingest_source_alarm,
    record_anomaly,
)
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

    def test_a_threshold_anomaly_claims_no_external_authority(self):
        """What we inferred here is labelled as ours."""
        self.set_signal(9.5, observed_at=self.now)

        result = analyze_anomaly(self.anomaly, now=self.now)

        self.assertEqual(result['authority'], AUTHORITY_DERIVED)
        self.assertIsNone(result['authority_source'])


class SourceDeclaredAuthorityTest(HealthEnvMixin, TestCase):
    """An alarm the hub declared is reported as the hub's determination.

    Authority is scoped to this report. It changes how the condition is
    described and how much the result hedges - it does not let the alarm start
    work, satisfy a gate, or count as verified.
    """

    def setUp(self):
        """Build the environment and an alarm declared by the source."""
        self.build_health_env()
        self.now = timezone.now()
        self.anomaly, _ = ingest_source_alarm(
            machine=self.machine,
            source=self.source,
            alarm_code='VIB-HH',
            title='Drive-end vibration high-high',
            severity=AnomalySeverity.CRITICAL,
            observed_at=self.now,
            external_key=self.binding.external_key,
        )

    def test_a_declared_alarm_is_attributed_to_its_source(self):
        """The report names the hub whose boundaries were applied."""
        self.set_signal(9.5, observed_at=self.now)

        result = analyze_anomaly(self.anomaly, now=self.now)

        validate_diagnosis(result)
        self.assertEqual(result['authority'], AUTHORITY_SOURCE_DECLARED)
        self.assertEqual(result['authority_source'], self.source.name)
        self.assertIn(self.source.name, result['likely_cause'])
        self.assertIn('VIB-HH', result['likely_cause'])

    def test_a_declared_alarm_outranks_an_inference_from_the_same_data(self):
        """Restating the hub's call is worth more than deriving our own."""
        self.set_signal(9.5, observed_at=self.now)
        derived, _ = record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('prelim', 'threshold'),
            title='Drive-end vibration above limit',
            severity=AnomalySeverity.CRITICAL,
            bindings=[self.binding],
        )

        declared_result = analyze_anomaly(self.anomaly, now=self.now)
        derived_result = analyze_anomaly(derived, now=self.now)

        self.assertGreater(
            declared_result['confidence'], derived_result['confidence']
        )
        # Neither reaches the 'high' band: the cause is still unverified.
        self.assertLess(declared_result['confidence'], 0.8)

    def test_missing_telemetry_does_not_retract_the_declaration(self):
        """We hold no reading; the hub still declared the alarm.

        This is the case the whole ruling turns on. Reporting zero confidence
        here would read as "nothing is known", when what is actually true is
        that the system which owns the asset says it is in alarm.
        """
        result = analyze_anomaly(self.anomaly, now=self.now)

        validate_diagnosis(result)
        self.assertEqual(result['status'], STATUS_UNAVAILABLE)
        self.assertGreater(result['confidence'], 0.0)
        self.assertIn(self.source.name, result['likely_cause'])
        self.assertNotIn('needs a manual assessment', result['likely_cause'])

    def test_stale_telemetry_does_not_retract_the_declaration(self):
        """Our mirror going stale is our problem, not a reason to doubt the hub."""
        self.set_signal(9.5, observed_at=self.now - timedelta(hours=3))

        result = analyze_anomaly(self.anomaly, now=self.now)

        self.assertEqual(result['status'], STATUS_STALE)
        self.assertGreater(result['confidence'], 0.0)
        self.assertIn("source system's determination alone", result['likely_cause'])

    def test_a_declared_alarm_still_offers_alternatives_without_telemetry(self):
        """There is a stated condition to explain, so explanations are listed."""
        result = analyze_anomaly(self.anomaly, now=self.now)

        self.assertTrue(result['alternatives'])
        # Its limits are the hub's to change, and the report says where.
        self.assertTrue(
            any('source system' in item for item in result['alternatives'])
        )

    def test_an_unbound_alarm_points_at_the_source_not_at_mapping(self):
        """The gap is a missing signal binding, not a missing source."""
        self.binding.delete()

        result = analyze_anomaly(self.anomaly, now=self.now)

        self.assertTrue(
            any('VIB-HH' in test for test in result['confirm_tests']),
            result['confirm_tests'],
        )
        self.assertFalse(
            any('Map this machine' in test for test in result['confirm_tests'])
        )

    def test_authority_never_makes_a_result_verified(self):
        """Verification stays a human act, whoever declared the alarm."""
        self.set_signal(9.5, observed_at=self.now)

        result = analyze_anomaly(self.anomaly, now=self.now)

        self.assertFalse(result['verified_by_user'])
        self.assertTrue(is_preliminary(result))

    def test_a_deleted_source_cannot_lend_its_authority(self):
        """With the hub gone, nobody can be named, so the claim is dropped."""
        self.source.delete()
        self.anomaly.refresh_from_db()

        result = analyze_anomaly(self.anomaly, now=self.now)

        self.assertEqual(result['authority'], AUTHORITY_DERIVED)
        self.assertIsNone(result['authority_source'])
