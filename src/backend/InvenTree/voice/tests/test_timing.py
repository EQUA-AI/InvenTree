"""V3 reporting is scoped telemetry, never delivery or execution authority."""

# ruff: noqa: D102

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from ai.core.voice.timing import VoiceTimingReport
from voice.models import VoiceSession, VoiceUtterance
from voice.services import realtime, timing


class VoiceTimingTests(TestCase):
    """Use real ORM ownership, generation and first-write semantics."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='timing-owner')
        self.session = realtime.create_session(
            owner=self.user,
            thread_id='timing',
            scope_key='test',
            policy_version='v1',
            limits=realtime.SessionLimits(),
        )
        self.kwargs = {
            'owner': self.user,
            'scope_key': 'test',
            'session_id': self.session.pk,
            'limits': realtime.SessionLimits(),
        }
        self.epoch = timing.begin_epoch(**self.kwargs)['epoch']
        self.utterance = realtime.persist_utterance(
            session=self.session,
            utterance_type='completed_answer',
            spoken_summary='PRIVATE_SENTINEL',
            turn_id='1',
            response_id='1',
        )
        self.before = VoiceSession.objects.values().get(pk=self.session.pk)

    def observation(self, **overrides):
        values = {
            'epoch': self.epoch,
            'utterance_id': str(self.utterance.pk),
            'spoken_hash': self.utterance.spoken_summary_hash,
            'provenance': 'rtp_energy_proxy',
            'first_playback_epoch_ms': timezone.now().timestamp() * 1000,
            'timing': {'submit_to_observed_playback_ms': 17.5},
        }
        return VoiceTimingReport(**{**values, **overrides})

    def test_first_write_wins_without_authority_or_activity_change(self):
        initial = self.observation()
        timing.report(**self.kwargs, observation=initial)
        first = VoiceUtterance.objects.values().get(pk=self.utterance.pk)
        timing.report(
            **self.kwargs,
            observation=self.observation(timing={'submit_to_observed_playback_ms': 99}),
        )
        after = VoiceUtterance.objects.values().get(pk=self.utterance.pk)
        self.assertEqual(first, after)
        self.assertEqual(after['playback_state'], self.utterance.playback_state)
        self.assertEqual(after['updated_at'], self.utterance.updated_at)
        self.assertEqual(
            VoiceSession.objects.values().get(pk=self.session.pk), self.before
        )
        self.assertEqual(
            after['timing_metrics'], {'submit_to_observed_playback_ms': 17.5}
        )

    def test_owner_scope_hash_generation_and_old_utterance_are_refused(self):
        other = get_user_model().objects.create_user(username='timing-other')
        for change in ({'owner': other}, {'scope_key': 'other'}):
            with self.assertRaises(realtime.VoiceSessionForbidden):
                timing.report(
                    **{**self.kwargs, **change}, observation=self.observation()
                )
        for change in ({'epoch': 'f' * 32}, {'spoken_hash': 'f' * 64}):
            with self.assertRaises(timing.TimingRejected):
                timing.report(**self.kwargs, observation=self.observation(**change))
        self.epoch = timing.begin_epoch(**self.kwargs)['epoch']
        with self.assertRaises(timing.TimingRejected):
            timing.report(**self.kwargs, observation=self.observation())

    def test_suspension_terminal_and_clock_bounds(self):
        for stamp in (0, (timezone.now() + timedelta(days=1)).timestamp() * 1000):
            with self.assertRaises(timing.TimingRejected):
                timing.report(
                    **self.kwargs,
                    observation=self.observation(first_playback_epoch_ms=stamp),
                )
        timing.invalidate_epoch(self.session)
        with self.assertRaises(timing.TimingRejected):
            timing.report(**self.kwargs, observation=self.observation())
        VoiceSession.objects.filter(pk=self.session.pk).update(
            state='ended', ended_at=timezone.now()
        )
        with self.assertRaises(timing.TimingRejected):
            timing.begin_epoch(**self.kwargs)

    def test_rate_limit_and_no_historical_backfill(self):
        for _ in range(5):
            timing.begin_epoch(**self.kwargs)
        with self.assertRaises(timing.TimingRejected):
            timing.begin_epoch(**self.kwargs)
        self.utterance.refresh_from_db()
        self.assertIsNone(self.utterance.first_playback_at)
        self.assertIsNone(self.utterance.timing_reported_at)
        self.assertIsNone(self.utterance.timing_metrics)

    def test_pause_report_does_not_invent_playback(self):
        timing.report(
            **self.kwargs,
            observation=self.observation(
                provenance='local_pause_proxy',
                first_playback_epoch_ms=None,
                timing={'local_stop_ms': 2},
            ),
        )
        self.utterance.refresh_from_db()
        self.assertIsNone(self.utterance.first_playback_at)
        self.assertEqual(self.utterance.timing_metrics, {'local_stop_ms': 2})
