"""WS4-T3/T8/T9 service tests: authorization, limits, persist-before-speak, sweep."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from voice.models import PlaybackState, VoiceSessionState, VoiceUtteranceType
from voice.services import realtime
from voice.services.realtime import (
    ExactSpeechConflict,
    SessionLimits,
    VoiceSessionExpired,
    VoiceSessionForbidden,
    VoiceSessionLimit,
)

LIMITS = SessionLimits(
    max_active_per_user=1, idle_timeout_s=300, max_age_s=3600, max_turns=3
)


class RealtimeSessionServiceTests(TestCase):
    """RealtimeSessionServiceTests."""
    @classmethod
    def setUpTestData(cls):
        """SetUpTestData."""
        user_model = get_user_model()
        cls.tech = user_model.objects.create_user(username='tech', password='x')
        cls.other = user_model.objects.create_user(username='other', password='x')

    def _create(self, owner=None):
        """Create."""
        return realtime.create_session(
            owner=owner or self.tech,
            thread_id='thread-1',
            scope_key='site:pilot',
            policy_version='v1',
            limits=LIMITS,
        )

    def test_session_limit_is_enforced_per_owner(self):
        """Session limit is enforced per owner."""
        self._create()
        with self.assertRaises(VoiceSessionLimit):
            self._create()
        # A different owner is unaffected.
        self._create(owner=self.other)

    def test_ending_frees_the_slot(self):
        """Ending frees the slot."""
        session = self._create()
        realtime.end_session(session=session)
        self._create()

    def test_cross_owner_access_is_indistinguishable_from_missing(self):
        """Cross owner access is indistinguishable from missing."""
        session = self._create()
        with self.assertRaises(VoiceSessionForbidden):
            realtime.get_owned_session(
                owner=self.other,
                scope_key='site:pilot',
                session_id=session.id,
                limits=LIMITS,
            )
        with self.assertRaises(VoiceSessionForbidden):
            realtime.get_owned_session(
                owner=self.other,
                scope_key='site:pilot',
                session_id='not-a-uuid',
                limits=LIMITS,
            )

    def test_cross_scope_access_is_indistinguishable_from_missing(self):
        """Cross scope access is indistinguishable from missing."""
        session = self._create()
        with self.assertRaises(VoiceSessionForbidden):
            realtime.get_owned_session(
                owner=self.tech,
                scope_key='site:other',
                session_id=session.id,
                limits=LIMITS,
            )

    def test_idle_session_expires_lazily(self):
        """Idle session expires lazily."""
        session = self._create()
        stale = timezone.now() - timedelta(seconds=LIMITS.idle_timeout_s + 5)
        realtime.VoiceSession.objects.filter(id=session.id).update(
            last_activity_at=stale
        )
        with self.assertRaises(VoiceSessionExpired):
            realtime.get_owned_session(
                owner=self.tech,
                scope_key='site:pilot',
                session_id=session.id,
                limits=LIMITS,
            )
        session.refresh_from_db()
        self.assertEqual(session.state, VoiceSessionState.EXPIRED)
        self.assertEqual(session.terminal_reason, 'session_bounds')

    def test_turn_budget_terminates_the_session(self):
        """Turn budget terminates the session."""
        session = self._create()
        for _ in range(LIMITS.max_turns):
            session = realtime.touch_session(
                session=session, limits=LIMITS, count_turn=True
            )
        with self.assertRaises(VoiceSessionExpired):
            realtime.touch_session(session=session, limits=LIMITS, count_turn=True)
        session.refresh_from_db()
        self.assertTrue(session.is_terminal)

    def test_end_is_idempotent_and_cancels_playback(self):
        """End is idempotent and cancels playback."""
        session = self._create()
        utterance = realtime.persist_utterance(
            session=session,
            utterance_type=VoiceUtteranceType.COMPLETED_ANSWER,
            spoken_summary='Answer text.',
            response_id='resp-1',
        )
        realtime.end_session(session=session)
        realtime.end_session(session=session)
        utterance.refresh_from_db()
        self.assertEqual(utterance.playback_state, PlaybackState.CANCELED)

    def test_persist_utterance_replays_exactly(self):
        """Persist utterance replays exactly."""
        session = self._create()
        first = realtime.persist_utterance(
            session=session,
            utterance_type=VoiceUtteranceType.COMPLETED_ANSWER,
            spoken_summary='Answer text.',
            response_id='resp-1',
        )
        replay = realtime.persist_utterance(
            session=session,
            utterance_type=VoiceUtteranceType.COMPLETED_ANSWER,
            spoken_summary='Answer text.',
            response_id='resp-1',
        )
        self.assertEqual(first.id, replay.id)

    def test_persist_utterance_conflicts_on_different_text(self):
        """Persist utterance conflicts on different text."""
        session = self._create()
        realtime.persist_utterance(
            session=session,
            utterance_type=VoiceUtteranceType.COMPLETED_ANSWER,
            spoken_summary='Answer text.',
            response_id='resp-1',
        )
        with self.assertRaises(ExactSpeechConflict):
            realtime.persist_utterance(
                session=session,
                utterance_type=VoiceUtteranceType.COMPLETED_ANSWER,
                spoken_summary='A different answer.',
                response_id='resp-1',
            )

    def test_no_speech_persists_into_a_terminal_session(self):
        """No speech persists into a terminal session."""
        session = self._create()
        realtime.end_session(session=session)
        with self.assertRaises(VoiceSessionExpired):
            realtime.persist_utterance(
                session=session,
                utterance_type=VoiceUtteranceType.INTERIM_STATUS,
                spoken_summary='Checking the record.',
            )

    def test_orphan_sweep_expires_stale_sessions(self):
        """Orphan sweep expires stale sessions."""
        session = self._create()
        realtime.VoiceSession.objects.filter(id=session.id).update(
            created_at=timezone.now() - timedelta(seconds=LIMITS.max_age_s + 10)
        )
        swept = realtime.expire_stale_sessions(limits=LIMITS)
        self.assertEqual(swept, 1)
        session.refresh_from_db()
        self.assertEqual(session.terminal_reason, 'orphan_sweep')

    def test_transport_attempt_updates_session_on_connect(self):
        """Transport attempt updates session on connect."""
        session = self._create()
        realtime.record_transport_attempt(
            session=session, transport='webrtc', outcome='connected'
        )
        session.refresh_from_db()
        self.assertEqual(session.transport, 'webrtc')
