"""WS8/WS10 re-cut: transcript-only capture lifecycle, acceptance, handoff."""

from __future__ import annotations

import hashlib

from django.contrib.auth import get_user_model
from django.test import TestCase

from voice.models import CaptureState, VoiceCaptureSession, VoiceTranscriptRevision
from voice.services import capture as svc

ENABLED = ('fault_intake', 'closeout')


def _hash(text: str) -> str:
    """Hash."""
    return hashlib.sha256(text.encode()).hexdigest()


class CaptureServiceTests(TestCase):
    """CaptureServiceTests."""
    @classmethod
    def setUpTestData(cls):
        """SetUpTestData."""
        cls.tech = get_user_model().objects.create_user(
            username='cap-tech', password='x'
        )
        cls.other = get_user_model().objects.create_user(
            username='cap-other', password='x'
        )

    def _create(
        self,
        key='cap-1',
        purpose='fault_intake',
        enabled=ENABLED,
        work_order_version=3,
    ):
        """Create."""
        return svc.create_capture(
            owner=self.tech,
            scope_key='site:pilot',
            purpose=purpose,
            work_order_id=42,
            work_order_version=work_order_version,
            consent_version='consent-v1',
            idempotency_key=key,
            policy_version='v1',
            enabled_purposes=enabled,
        )

    def test_purpose_allow_list_fails_closed(self):
        """Purpose allow list fails closed."""
        with self.assertRaises(svc.PurposeUnavailable):
            self._create(enabled=())
        with self.assertRaises(svc.PurposeUnavailable):
            self._create(purpose='meeting_notes')

    def test_create_replays_exactly_and_conflicts_on_changed_intent(self):
        """Create replays exactly and conflicts on changed intent."""
        first = self._create(key='same')
        replay = self._create(key='same')
        self.assertEqual(first.id, replay.id)
        with self.assertRaises(svc.CaptureError):
            self._create(key='same', purpose='closeout')
        with self.assertRaises(svc.CaptureError):
            self._create(key='same', work_order_version=99)

    def test_no_audio_fields_exist_anywhere(self):
        """No audio fields exist anywhere."""
        for model in (VoiceCaptureSession, VoiceTranscriptRevision):
            for field in model._meta.get_fields():
                for fragment in ('audio', 'blob', 'media', 'recording', 'file'):
                    self.assertNotIn(fragment, field.name.lower())

    def test_revisions_append_immutably_and_move_state_to_review(self):
        """Revisions append immutably and move state to review."""
        cap = self._create()
        r1 = svc.append_revision(
            capture=cap, full_text='Pump is vibrating.', created_by=self.tech
        )
        self.assertEqual(cap.state, CaptureState.REVIEW)
        r2 = svc.append_revision(
            capture=cap,
            full_text='Pump is vibrating at 15 Hz.',
            created_by=self.tech,
            edit_reason='added measurement',
        )
        self.assertEqual((r1.revision, r2.revision), (1, 2))
        self.assertEqual(r2.supersedes_id, r1.id)
        r1.refresh_from_db()
        self.assertEqual(r1.full_text, 'Pump is vibrating.')

    def test_acceptance_binds_exact_latest_revision_by_hash(self):
        """Acceptance binds exact latest revision by hash."""
        cap = self._create()
        svc.append_revision(
            capture=cap, full_text='First text.', created_by=self.tech
        )
        r2 = svc.append_revision(
            capture=cap, full_text='Second text.', created_by=self.tech
        )
        acceptance = svc.accept_revision(
            capture=cap,
            revision_id=r2.id,
            content_hash=_hash('Second text.'),
            accepted_by=self.tech,
        )
        self.assertEqual(cap.state, CaptureState.ACCEPTED)
        self.assertEqual(acceptance.content_hash, r2.content_hash)

    def test_accepting_a_stale_revision_is_rejected(self):
        """Accepting a stale revision is rejected."""
        cap = self._create()
        r1 = svc.append_revision(
            capture=cap, full_text='First.', created_by=self.tech
        )
        svc.append_revision(
            capture=cap, full_text='Second.', created_by=self.tech
        )
        with self.assertRaises(svc.RevisionStale):
            svc.accept_revision(
                capture=cap,
                revision_id=r1.id,
                content_hash=r1.content_hash,
                accepted_by=self.tech,
            )

    def test_hash_mismatch_is_rejected(self):
        """Hash mismatch is rejected."""
        cap = self._create()
        r1 = svc.append_revision(
            capture=cap, full_text='Exact words.', created_by=self.tech
        )
        with self.assertRaises(svc.CaptureError):
            svc.accept_revision(
                capture=cap,
                revision_id=r1.id,
                content_hash=_hash('Different words.'),
                accepted_by=self.tech,
            )

    def test_cross_owner_lookup_is_indistinguishable_from_missing(self):
        """Cross owner lookup is indistinguishable from missing."""
        cap = self._create()
        with self.assertRaises(svc.CaptureNotFound):
            svc.get_owned_capture(
                owner=self.other, scope_key='site:pilot', capture_id=cap.id
            )
        with self.assertRaises(svc.CaptureNotFound):
            svc.get_owned_capture(
                owner=self.tech, scope_key='site:other', capture_id=cap.id
            )

    def test_handoffs_fail_closed_until_substrate_exists(self):
        """Handoffs fail closed until substrate exists."""
        for purpose in ('fault_intake', 'closeout'):
            cap = self._create(key=f'handoff-{purpose}', purpose=purpose)
            rev = svc.append_revision(
                capture=cap, full_text='Narrative.', created_by=self.tech
            )
            svc.accept_revision(
                capture=cap,
                revision_id=rev.id,
                content_hash=rev.content_hash,
                accepted_by=self.tech,
            )
            with self.assertRaises(svc.DestinationUnavailable):
                svc.handoff_capture(capture=cap)
            cap.refresh_from_db()
            self.assertEqual(
                cap.state,
                CaptureState.ACCEPTED,
                'a failed handoff must not consume the acceptance',
            )

    def test_cancel_preserves_revisions_and_blocks_progress(self):
        """Cancel preserves revisions and blocks progress."""
        cap = self._create()
        svc.append_revision(
            capture=cap, full_text='Some words.', created_by=self.tech
        )
        svc.cancel_capture(capture=cap)
        self.assertEqual(cap.revisions.count(), 1)
        with self.assertRaises(svc.CaptureError):
            svc.append_revision(
                capture=cap, full_text='More.', created_by=self.tech
            )

    def test_stale_instance_cannot_append_after_cancellation(self):
        """Stale instance cannot append after cancellation."""
        stale = self._create()
        current = VoiceCaptureSession.objects.get(pk=stale.pk)
        svc.cancel_capture(capture=current)

        with self.assertRaises(svc.CaptureError):
            svc.append_revision(
                capture=stale,
                full_text='After cancellation through stale state.',
                created_by=self.tech,
            )

        self.assertFalse(stale.revisions.exists())

    def test_stale_instance_cannot_cancel_committed_capture(self):
        """Stale instance cannot cancel committed capture."""
        stale = self._create()
        VoiceCaptureSession.objects.filter(pk=stale.pk).update(
            state=CaptureState.COMMITTED
        )

        with self.assertRaises(svc.CaptureError):
            svc.cancel_capture(capture=stale)

        stale.refresh_from_db()
        self.assertEqual(stale.state, CaptureState.COMMITTED)

    def test_correction_after_acceptance_requires_new_acceptance(self):
        """Correction after acceptance requires new acceptance."""
        cap = self._create()
        first = svc.append_revision(
            capture=cap, full_text='Original text.', created_by=self.tech
        )
        svc.accept_revision(
            capture=cap,
            revision_id=first.id,
            content_hash=first.content_hash,
            accepted_by=self.tech,
        )

        svc.append_revision(
            capture=cap, full_text='Corrected text.', created_by=self.tech
        )
        cap.refresh_from_db()

        self.assertEqual(cap.state, CaptureState.REVIEW)
        self.assertIsNone(cap.accepted_revision_id)
        self.assertTrue(hasattr(first, 'acceptance'))

    def test_segments_reject_audio_and_secret_fields(self):
        """Segments reject audio and secret fields."""
        cap = self._create()

        for forbidden in ('audio', 'provider_token', 'sdp_offer', 'ice_candidate'):
            with self.subTest(forbidden=forbidden), self.assertRaises(
                svc.CaptureError
            ):
                svc.append_revision(
                    capture=cap,
                    full_text='Transcript only.',
                    created_by=self.tech,
                    segments=[
                        {
                            'text': 'Transcript only.',
                            'start_ms': 0,
                            'end_ms': 100,
                            forbidden: 'forbidden-value',
                        }
                    ],
                )

    def test_revision_text_and_segments_are_bounded(self):
        """Revision text and segments are bounded."""
        cap = self._create()

        with self.assertRaises(svc.CaptureError):
            svc.append_revision(
                capture=cap, full_text='x' * 8_001, created_by=self.tech
            )
        with self.assertRaises(svc.CaptureError):
            svc.append_revision(
                capture=cap,
                full_text='Transcript only.',
                created_by=self.tech,
                segments={'not': 'a-list'},
            )
        with self.assertRaises(svc.CaptureError):
            svc.append_revision(
                capture=cap,
                full_text='Transcript only.',
                created_by=self.tech,
                segments=[{'text': 'x' * 8_001}],
            )

    def test_consent_is_required(self):
        """Consent is required."""
        with self.assertRaises(svc.CaptureError):
            svc.create_capture(
                owner=self.tech,
                scope_key='site:pilot',
                purpose='fault_intake',
                work_order_id=42,
                work_order_version=3,
                consent_version='',
                idempotency_key='no-consent',
                policy_version='v1',
                enabled_purposes=ENABLED,
            )
