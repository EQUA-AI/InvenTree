"""WS4-T2: realtime ledger constraints, redaction, and migration round-trip."""

from __future__ import annotations

import hashlib

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, tag
from django.utils import timezone

from voice.models import (
    VoiceSession,
    VoiceSessionState,
    VoiceTransport,
    VoiceTransportAttempt,
    VoiceUtterance,
    VoiceUtteranceType,
)

FORBIDDEN_FIELD_FRAGMENTS = (
    'sdp',
    'ice',
    'audio',
    'offer',
    'answer_payload',
    'recording',
    'blob',
    'media',
    'secret',
    'bearer',
)


def _session(owner, **overrides):
    """Session."""
    defaults = {
        'owner': owner,
        'thread_id': 'thread-1',
        'scope_key': 'site:pilot',
        'scope_hash': hashlib.sha256(b'site:pilot').hexdigest(),
        'policy_version': 'v1',
    }
    defaults.update(overrides)
    return VoiceSession.objects.create(**defaults)


class RealtimeModelConstraintTests(TestCase):
    """Database-level invariants for the realtime ledgers."""

    @classmethod
    def setUpTestData(cls):
        """SetUpTestData."""
        cls.user = get_user_model().objects.create_user(
            username='tech', password='unused'
        )

    def test_terminal_state_requires_ended_at(self):
        """Terminal state requires ended at."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            _session(self.user, state=VoiceSessionState.ENDED)

    def test_terminal_state_with_ended_at_is_valid(self):
        """Terminal state with ended at is valid."""
        session = _session(
            self.user,
            state=VoiceSessionState.ENDED,
            ended_at=timezone.now(),
            terminal_reason='user_ended',
        )
        self.assertTrue(session.is_terminal)

    def test_scope_hash_is_required(self):
        """Scope hash is required."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            _session(self.user, scope_hash='')

    def test_one_completed_answer_per_response(self):
        """One completed answer per response."""
        session = _session(self.user)
        text = 'I found two likely causes.'
        digest = hashlib.sha256(text.encode()).hexdigest()
        VoiceUtterance.objects.create(
            session=session,
            utterance_type=VoiceUtteranceType.COMPLETED_ANSWER,
            spoken_summary=text,
            spoken_summary_hash=digest,
            response_id='resp-1',
            policy_version='v1',
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            VoiceUtterance.objects.create(
                session=session,
                utterance_type=VoiceUtteranceType.COMPLETED_ANSWER,
                spoken_summary=text,
                spoken_summary_hash=digest,
                response_id='resp-1',
                policy_version='v1',
            )

    def test_interim_status_cannot_bind_a_response_id(self):
        """Interim status cannot bind a response id."""
        session = _session(self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            VoiceUtterance.objects.create(
                session=session,
                utterance_type=VoiceUtteranceType.INTERIM_STATUS,
                spoken_summary='Checking the machine record.',
                spoken_summary_hash=hashlib.sha256(b'x').hexdigest(),
                response_id='resp-1',
                policy_version='v1',
            )

    def test_spoken_text_and_hash_are_required(self):
        """Spoken text and hash are required."""
        session = _session(self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            VoiceUtterance.objects.create(
                session=session,
                utterance_type=VoiceUtteranceType.INTERIM_STATUS,
                spoken_summary='',
                spoken_summary_hash='',
                policy_version='v1',
            )

    def test_transport_attempt_records_metadata_only(self):
        """Transport attempt records metadata only."""
        session = _session(self.user)
        attempt = VoiceTransportAttempt.objects.create(
            session=session, transport=VoiceTransport.WEBRTC
        )
        self.assertEqual(attempt.outcome, 'started')

    def test_no_model_carries_signaling_or_audio_fields(self):
        """The redaction contract is structural: the columns cannot exist."""
        for model in (VoiceSession, VoiceTransportAttempt, VoiceUtterance):
            for field in model._meta.get_fields():
                name = field.name.lower()
                for fragment in FORBIDDEN_FIELD_FRAGMENTS:
                    self.assertNotIn(
                        fragment,
                        name,
                        f'{model.__name__}.{field.name} looks like it stores '
                        f'signaling/audio/credential material',
                    )


@tag('migration_test')
class VoiceMigrationRoundTripTests(TransactionTestCase):
    """The voice app must migrate backward and forward cleanly.

    Tagged per InvenTree convention: the default runner excludes migration
    tests because their flush regenerates content types with new ids and
    corrupts a shared --keepdb database for later fixture-loading suites.
    """

    def test_migration_round_trip(self):
        """Migration round trip."""
        call_command('migrate', 'voice', 'zero', verbosity=0, interactive=False)
        call_command('migrate', 'voice', verbosity=0, interactive=False)
        self.assertEqual(VoiceSession.objects.count(), 0)
