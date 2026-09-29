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
    MachineSignalBinding,
    MachineSignalState,
    SignalQuality,
)
from machine_health.services.anomalies import (
    RESOLUTION_IN_LIMITS,
    RESOLUTION_UNASSESSABLE,
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


class UnusableReadingTest(HealthEnvMixin, TestCase):
    """A pegged channel is not evidence, in either direction."""

    def setUp(self):
        """One bounded signal; the environment's limits warn at 6 and trip at 9."""
        super().setUp()
        self.build_health_env()
        self.now = timezone.now()

    def test_a_bad_quality_breach_raises_nothing(self):
        """The source's over-range marker must not become a plant condition.

        3276.7 is the signed 16-bit maximum at 0.1 resolution and reaches 3.3%
        of readings on approved temperature points. Classified rather than
        refused, roughly one reading in thirty would open a critical anomaly
        about a measurement that never happened.
        """
        self.set_signal(3276.7, observed_at=self.now, quality=SignalQuality.BAD)

        self.assertEqual(evaluate_thresholds(self.machine, now=self.now), [])
        self.assertFalse(MachineAnomaly.objects.filter(machine=self.machine).exists())

    def test_a_channel_going_bad_does_not_resolve_an_open_anomaly(self):
        """"The sensor stopped reporting" is not "the signal returned inside its limits".

        Auto-resolution closes any open threshold anomaly whose fingerprint was
        not seen this pass, with exactly that note. So skipping an unusable
        reading outright would silently clear a real condition and attribute a
        recovery to it that nobody observed.
        """
        self.set_signal(10.0, observed_at=self.now)
        [raised] = evaluate_thresholds(self.machine, now=self.now)
        self.assertEqual(raised.status, AnomalyStatus.OPEN)

        self.set_signal(3276.7, observed_at=self.now, quality=SignalQuality.BAD)
        evaluate_thresholds(self.machine, now=self.now)

        raised.refresh_from_db()
        self.assertEqual(raised.status, AnomalyStatus.OPEN)
        self.assertEqual(raised.resolution_note, '')

    def test_a_good_reading_inside_limits_still_resolves(self):
        """The guard must not break recovery for readings that are usable."""
        self.set_signal(10.0, observed_at=self.now)
        [raised] = evaluate_thresholds(self.machine, now=self.now)

        self.set_signal(3.0, observed_at=self.now)
        evaluate_thresholds(self.machine, now=self.now)

        raised.refresh_from_db()
        self.assertEqual(raised.status, AnomalyStatus.RESOLVED)


class ThresholdResolutionNoteTest(HealthEnvMixin, TestCase):
    """A threshold anomaly is closed with a note that is true.

    ``_auto_resolve_threshold_anomalies`` is the only code in the backend that
    writes ``RESOLVED``, so it has to close everything it stops matching - and
    "the reading came back inside its limits" is only one of the reasons it can
    stop matching. Writing that sentence about a rule somebody deleted is how a
    condition nobody looked at reads as a condition that ended.
    """

    def setUp(self):
        """One bounded signal, breaching."""
        self.build_health_env()
        self.now = timezone.now()

    def _open_one(self):
        self.set_signal(10.0, observed_at=self.now)
        [raised] = evaluate_thresholds(self.machine, now=self.now)
        self.assertEqual(raised.status, AnomalyStatus.OPEN)
        return raised

    def test_a_clearing_reading_says_the_signal_returned(self):
        """The affirmative case keeps the affirmative note."""
        raised = self._open_one()

        self.set_signal(3.0, observed_at=self.now)
        evaluate_thresholds(self.machine, now=self.now)

        raised.refresh_from_db()
        self.assertEqual(raised.status, AnomalyStatus.RESOLVED)
        self.assertEqual(raised.resolution_note, RESOLUTION_IN_LIMITS)

    def test_removing_the_limits_does_not_claim_the_signal_returned(self):
        """Activation wipes all six bounds when a point's meaning changes.

        The binding then classifies UNKNOWN on a perfectly good reading. Before
        this, that closed the anomaly with the recovery note - asserting a
        recovery on a channel still reading 10.0 against limits of 6 and 9.
        """
        raised = self._open_one()

        for bound in ('normal_max', 'warn_max', 'critical_max'):
            setattr(self.binding, bound, None)
        self.binding.save()
        evaluate_thresholds(self.machine, now=self.now)

        raised.refresh_from_db()
        self.assertEqual(raised.status, AnomalyStatus.RESOLVED)
        self.assertEqual(raised.resolution_note, RESOLUTION_UNASSESSABLE)

    def test_a_vanished_state_row_does_not_claim_the_signal_returned(self):
        """Activation deletes the cached state along with the bounds."""
        raised = self._open_one()

        MachineSignalState.objects.filter(binding=self.binding).delete()
        evaluate_thresholds(self.machine, now=self.now)

        raised.refresh_from_db()
        self.assertEqual(raised.status, AnomalyStatus.RESOLVED)
        self.assertEqual(raised.resolution_note, RESOLUTION_UNASSESSABLE)

    def test_nothing_is_left_in_a_state_no_code_can_close(self):
        """Refusing to close would hold the machine's one open slot for ever.

        ``machine_health_anomaly_open_unique`` is partial on active statuses, and
        acknowledgement only moves OPEN to ACKNOWLEDGED. So an anomaly this
        function declines to resolve can never be resolved by anything.
        """
        raised = self._open_one()

        MachineSignalState.objects.filter(binding=self.binding).delete()
        self.binding.active = False
        self.binding.save(update_fields=['active'])
        evaluate_thresholds(self.machine, now=self.now)

        raised.refresh_from_db()
        self.assertEqual(raised.status, AnomalyStatus.RESOLVED)
        self.assertIsNotNone(raised.resolved_at)

    def test_a_bad_reading_still_holds_the_condition_open(self):
        """The one case that must NOT be closed, re-pinned against the rewrite.

        A sensor going bad is not evidence either way. Closing it with either
        note would be wrong, so it stays open.
        """
        raised = self._open_one()

        self.set_signal(3276.7, observed_at=self.now, quality=SignalQuality.BAD)
        evaluate_thresholds(self.machine, now=self.now)

        raised.refresh_from_db()
        self.assertEqual(raised.status, AnomalyStatus.OPEN)
        self.assertEqual(raised.resolution_note, '')


class DetectorVotingTest(HealthEnvMixin, TestCase):
    """A single detector is not an alarm, and is not nothing either.

    IEEE Std 3004.8-2016 cl. 8.5.2.2 recommends RTD voting so damaged and
    open-circuit inputs are ignored; API Std 670 cl. 5.4.6.4 makes dual voting
    standard where two sensors share a bearing's load zone, while keeping
    single-violation logic everywhere else - which is why an unset vote_minimum
    must mean no voting rather than a default of two.

    The rule de-escalates rather than suppresses. A stator hot spot in one slot
    is real, and silencing it to avoid nuisance trips would discard exactly the
    signal a detector array exists to catch.
    """

    def setUp(self):
        """Three detectors on one machine, voting as one group."""
        self.build_health_env()
        self.now = timezone.now()
        self.detectors = [self.binding]
        for index in (2, 3):
            self.detectors.append(
                MachineSignalBinding.objects.create(
                    machine=self.machine,
                    source=self.source,
                    external_key=f'{self.binding.external_key}-{index}',
                    display_name=f'Winding detector {index}',
                    unit='mm/s',
                    warn_max=6.0,
                    critical_max=9.0,
                )
            )
        for binding in self.detectors:
            binding.vote_group = 'Stator winding ETDs'
            binding.vote_minimum = 2
            binding.save(update_fields=['vote_group', 'vote_minimum'])

    def read(self, binding, value):
        """Give one detector a current reading."""
        MachineSignalState.objects.update_or_create(
            binding=binding,
            defaults={
                'value': {'value': value},
                'observed_at': self.now,
                'quality': SignalQuality.GOOD,
            },
        )

    def test_one_detector_past_critical_warns_instead(self):
        """Unconfirmed, so it is not yet the machine's condition."""
        self.read(self.detectors[0], 10.0)
        self.read(self.detectors[1], 3.0)
        self.read(self.detectors[2], 3.0)

        [raised] = evaluate_thresholds(self.machine, now=self.now)

        self.assertEqual(raised.severity, AnomalySeverity.WARNING)
        self.assertIn('alone', raised.evidence_summary)
        self.assertEqual(raised.metrics['vote_confirmed'], 1)
        self.assertEqual(raised.metrics['vote_minimum'], 2)

    def test_two_detectors_past_critical_confirm_it(self):
        """The corroborated case is the machine's condition."""
        self.read(self.detectors[0], 10.0)
        self.read(self.detectors[1], 11.0)
        self.read(self.detectors[2], 3.0)

        raised = evaluate_thresholds(self.machine, now=self.now)

        self.assertEqual(len(raised), 2)
        for anomaly in raised:
            self.assertEqual(anomaly.severity, AnomalySeverity.CRITICAL)
            self.assertNotIn('alone', anomaly.evidence_summary)

    def test_a_lone_breach_is_still_raised(self):
        """De-escalated, never suppressed - the distinction is the design."""
        self.read(self.detectors[0], 10.0)
        self.read(self.detectors[1], 3.0)
        self.read(self.detectors[2], 3.0)

        evaluate_thresholds(self.machine, now=self.now)

        self.assertTrue(
            MachineAnomaly.objects.filter(machine=self.machine).exists(),
            'a single hot detector must still reach somebody',
        )

    def test_without_a_vote_minimum_one_detector_is_critical(self):
        """API 670 keeps single-violation logic wherever voting is not asked for."""
        for binding in self.detectors:
            binding.vote_group, binding.vote_minimum = '', None
            binding.save(update_fields=['vote_group', 'vote_minimum'])
        self.read(self.detectors[0], 10.0)

        [raised] = evaluate_thresholds(self.machine, now=self.now)

        self.assertEqual(raised.severity, AnomalySeverity.CRITICAL)

    def test_a_warning_level_breach_is_untouched_by_voting(self):
        """Voting gates the critical only; a warning is already the lower call."""
        self.read(self.detectors[0], 7.0)

        [raised] = evaluate_thresholds(self.machine, now=self.now)

        self.assertEqual(raised.severity, AnomalySeverity.WARNING)
        self.assertNotIn('alone', raised.evidence_summary)

    def test_a_confirmed_condition_is_not_de_escalated_when_a_detector_fails(self):
        """record_anomaly never silently de-escalates, and that matters here.

        Two detectors confirm a critical; one then goes bad-quality. The vote can
        no longer be met, but the machine did not get better - and a sensor
        dropping out must not quietly downgrade a standing critical.
        """
        self.read(self.detectors[0], 10.0)
        self.read(self.detectors[1], 11.0)
        first = evaluate_thresholds(self.machine, now=self.now)
        self.assertTrue(all(a.severity == AnomalySeverity.CRITICAL for a in first))

        MachineSignalState.objects.filter(binding=self.detectors[1]).update(
            quality=SignalQuality.BAD
        )
        evaluate_thresholds(self.machine, now=self.now)

        standing = MachineAnomaly.objects.get(
            machine=self.machine, bindings=self.detectors[0]
        )
        self.assertEqual(standing.severity, AnomalySeverity.CRITICAL)

    def test_a_detector_in_another_group_does_not_corroborate(self):
        """Winding and core measure different things and must not vote together."""
        self.detectors[1].vote_group = 'Motor / stator core RTDs (backstop)'
        self.detectors[1].save(update_fields=['vote_group'])
        self.read(self.detectors[0], 10.0)
        self.read(self.detectors[1], 11.0)

        raised = evaluate_thresholds(self.machine, now=self.now)

        self.assertEqual(len(raised), 2)
        for anomaly in raised:
            self.assertEqual(
                anomaly.severity,
                AnomalySeverity.WARNING,
                'each is alone in its own group',
            )
