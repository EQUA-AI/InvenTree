"""E7/E8 live capture integration; isolated database, no provider or email."""

import os
import re
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from tasks.closeout_models import CloseoutCapture
from tasks.models import WorkOrderLifecycle
from tasks.tests.test_workorder_voice_readback import WorkOrderVoiceFixture

from ai.core.decisions.adapters.capture_adapter import DISCLOSURE, require_enabled
from ai.core.decisions.receipts import verified_work_order_receipt
from aichat.models import ChatActionProposal
from aichat.services.proposals import ProposalError
from voice.models import (
    VoiceCaptureReview,
    VoiceCaptureSession,
    VoiceTranscriptAcceptance,
    VoiceTranscriptRevision,
)
from voice.services import realtime

POLICY = {
    'AIMMS_SINGLE_SITE_POLICY_KEY': 'phase-e-test',
    'AIMMS_VOICE_CAPTURE_ENABLED': '1',
    'AIMMS_VOICE_PURPOSES': 'closeout',
    'AIMMS_VOICE_CONSENT_VERSION': 'consent-v2',
}


@override_settings(
    AIMMS_WORK_ORDERS_ENABLED=True,
    AIMMS_CLOSEOUT_WIZARD_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.tests.test_workorder_voice_readback.cancellation_scope',
)
class CaptureVoiceFlowTests(WorkOrderVoiceFixture, TestCase):
    """Consent and exact-note acceptance cannot silently perform handoff."""

    def setUp(self):
        """Only the dedicated fixture is writable."""
        super().setUp()
        self.flags = SimpleNamespace(feature_voice_closeout=True)
        self.enterContext(patch('ai.core.config.get_settings', return_value=self.flags))
        self.enterContext(patch.dict(os.environ, POLICY))
        self.session.consent_version = 'consent-v2'
        self.session.save(update_fields=['consent_version'])
        self.order.lifecycle_status = WorkOrderLifecycle.IN_PROGRESS
        self.order.save(update_fields=['lifecycle_status'])
        self.turn = 0

    def say(self, text):
        """A new server turn, never a reused transport submission."""
        self.turn += 1
        return self.coordinator.begin(
            text, **{**self.arguments, 'nonce': f'capture-{self.turn}'}
        )

    def consent(self):
        """Read disclosure and explicitly say yes."""
        decision = self.say(f'start closeout for work order {self.order.pk}').decision
        self.assertIn('does not store audio', decision.spoken_summary)
        self.assertEqual(decision.spoken_summary.count(DISCLOSURE), 1)
        self.assertEqual(
            next(
                section['text']
                for section in decision.sections
                if section['id'] == 'warning'
            ),
            DISCLOSURE,
        )
        self.assertFalse(VoiceCaptureSession.objects.exists())
        self.assertFalse(VoiceTranscriptRevision.objects.exists())
        reply = self.respond('yes', self.deliver(decision))
        self.assertEqual(reply.decision.execution_state, 'succeeded', reply.spoken)
        return VoiceCaptureSession.objects.get()

    def test_consent_before_any_revision(self):
        """Neither a start request nor premature dictation records a note."""
        with self.assertRaisesMessage(ProposalError, 'explicitly consent'):
            self.say('note pressure fifteen PSI')
        self.consent()
        self.assertFalse(VoiceTranscriptRevision.objects.exists())

    def test_previous_orm_can_insert_transcripts_after_additive_migrations(self):
        """Rollback code omits source_turn_id; the database supplies an empty key."""
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        capture = self.consent()
        previous = (
            MigrationExecutor(connection)
            .loader.project_state([('voice', '0007_voicepresentation')])
            .apps.get_model('voice', 'VoiceTranscriptRevision')
        )
        row = previous.objects.create(
            capture_id=capture.pk,
            revision=1,
            full_text='VOICE-TEST old ORM compatibility only.',
            content_hash='a' * 64,
            segments=[],
            created_by_id=self.actor.pk,
        )
        self.assertEqual(
            VoiceTranscriptRevision.objects.get(pk=row.pk).source_turn_id, ''
        )

    def test_exact_correction_acceptance_and_separate_handoff(self):
        """Canonical destination and voice receipt commit once with exact text."""
        capture = self.consent()
        self.say('note Replaced the seal. Tested at fifteen PSI and verified no leak.')
        self.say('change fifteen to fifty PSI')
        revisions = list(capture.revisions.all())
        self.assertEqual(len(revisions), 2)
        self.assertIn('fifteen PSI', revisions[0].full_text)
        self.assertIn('fifty PSI', revisions[1].full_text)
        self.assertNotIn('PSI PSI', revisions[1].full_text)
        self.assertEqual(revisions[1].edit_reason, 'voice_correction')
        self.assertIn(revisions[1].full_text, self.say('read the whole note').spoken)
        decision = self.say('accept this note').decision
        self.assertIn(revisions[1].full_text, decision.spoken_summary)
        reply = self.respond('accept this note', self.deliver(decision))
        self.assertEqual(reply.decision.execution_state, 'succeeded', reply.spoken)
        self.assertEqual(VoiceTranscriptAcceptance.objects.count(), 1)
        self.assertFalse(CloseoutCapture.objects.exists())
        decision = self.say('handoff this note').decision
        self.assertEqual(decision.required_phrase, 'confirm handoff')
        reply = self.respond('confirm handoff', self.deliver(decision))
        self.assertEqual(reply.decision.execution_state, 'succeeded', reply.spoken)
        row = CloseoutCapture.objects.get()
        self.assertEqual(row.current_revision.narrative, revisions[1].full_text)
        self.assertIn('not closed', reply.spoken)
        proposal = ChatActionProposal.objects.get(pk=decision.source_id)
        self.assertTrue(verified_work_order_receipt(proposal))
        self.respond('confirm handoff', reply.decision)
        self.assertEqual(CloseoutCapture.objects.count(), 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS)

    def test_asr_punctuation_and_spacing_preserve_literal_note_payloads(self):
        """Command punctuation is syntax; dictation punctuation remains evidence."""
        decision = self.say(f'Start close out for work order {self.order.pk}.').decision
        self.respond('yes', self.deliver(decision))
        capture = VoiceCaptureSession.objects.get()
        self.say(
            'Note Synthetic test: pressure fifteen PSI. No physical work performed!'
        )
        self.say('Change fifteen to fifty PSI.')
        self.assertEqual(
            capture.revisions.order_by('-revision').first().full_text,
            'Synthetic test: pressure fifty PSI. No physical work performed!',
        )
        self.assertIn('fifty PSI.', self.say('Read the whole note.').spoken)
        decision = self.say('Accept this note.').decision
        self.respond('accept this note.', self.deliver(decision))
        self.assertEqual(VoiceTranscriptAcceptance.objects.count(), 1)
        self.assertEqual(
            self.say('Hand off this note.').decision.required_phrase, 'confirm handoff'
        )
        self.assertFalse(CloseoutCapture.objects.exists())

    def test_correction_invalidates_old_acceptance_review(self):
        """Old hashes cannot accept newly dictated evidence."""
        self.consent()
        self.say('note Checked pressure fifteen PSI.')
        decision = self.deliver(self.say('accept this note').decision)
        self.say('replace note with Checked pressure fifty PSI.')
        with self.assertRaises(ProposalError):
            self.respond('accept this note', decision)
        self.assertFalse(VoiceTranscriptAcceptance.objects.exists())
        self.assertFalse(CloseoutCapture.objects.exists())

    def test_long_note_pages_are_exact_and_do_not_qualify_voice_acceptance(self):
        """Paging never truncates a note or invents a full-note auditory certificate."""
        capture = self.consent()
        text = '\n'.join(
            f'Check {index}: pressure 50 PSI, clearance 0.25 mm. Retained exact observation.'
            for index in range(40)
        )
        self.say(f'note {text}')
        first = self.say('read the whole note').spoken
        total = int(re.match(r'Note revision 1, page 1 of (\d+)\.', first)[1])
        self.assertGreater(total, 1)
        pages = []
        for page in range(1, total + 1):
            spoken = self.say(f'read the whole note page {page}').spoken
            prefix = f'Note revision 1, page {page} of {total}. '
            suffix = (
                f' Say read the whole note page {page + 1} for the next page.'
                if page < total
                else ' End of note.'
            )
            self.assertTrue(spoken.startswith(prefix))
            self.assertTrue(spoken.endswith(suffix))
            pages.append(spoken[len(prefix) : -len(suffix)])
        self.assertEqual(''.join(pages), text)
        self.assertEqual(capture.revisions.get().full_text, text)
        decision = self.say('accept this note').decision
        self.assertFalse(decision.voice_eligible)
        proposal = ChatActionProposal.objects.get(pk=decision.source_id)
        self.assertEqual(proposal.preview['full_text'], text)
        reply = self.respond('accept this note', self.deliver(decision))
        self.assertEqual(reply.event, 'ineligible')
        self.assertFalse(VoiceTranscriptAcceptance.objects.exists())
        self.assertFalse(CloseoutCapture.objects.exists())

    def deliver_note_page(self, decision):
        """Exercise the exact production playback transitions with private test text."""
        from ai.core.decisions.coordinator import estimated_playback_seconds

        utterance = realtime.persist_utterance(
            session=self.session,
            utterance_type='prompt',
            spoken_summary=decision.spoken_summary,
        )
        decision = self.coordinator.bind_playback(
            decision,
            utterance_id=str(utterance.pk),
            spoken_text=utterance.spoken_summary,
            spoken_hash=utterance.spoken_summary_hash,
        )
        self.clock[0] += timedelta(seconds=1)
        decision = self.coordinator.playback(
            decision,
            event='playback-started',
            sequence=decision.sequence,
            utterance_id=str(utterance.pk),
            spoken_hash=utterance.spoken_summary_hash,
        )
        self.clock[0] += timedelta(
            seconds=estimated_playback_seconds(utterance.spoken_summary)
        )
        decision = self.coordinator.playback(
            decision,
            event='playback-completed',
            sequence=decision.sequence,
            utterance_id=str(utterance.pk),
            spoken_hash=utterance.spoken_summary_hash,
        )
        # The HTTP route persists this only AFTER the coordinator accepted the callback.
        realtime.mark_playback(utterance=utterance, state='done')
        self.clock[0] += timedelta(seconds=2)
        return decision

    def review_long_note(self, *, skip=None):
        """Build a multi-page private note with an optional playback gap."""
        self.consent()
        text = '\n'.join(
            f'Observation {index}: pressure 50 PSI, clearance 0.25 mm. Synthetic test only.'
            for index in range(35)
        )
        self.say(f'note {text}')
        first = self.say('read the whole note').decision
        review = VoiceCaptureReview.objects.get(pk=first.source_id)
        self.assertGreater(len(review.page_hashes), 1)
        for page in range(1, len(review.page_hashes) + 1):
            decision = (
                first
                if page == 1
                else self.say(f'read the whole note page {page}').decision
            )
            if page != skip:
                self.deliver_note_page(decision)
        return text, review

    def test_every_exact_page_then_new_acceptance_and_separate_handoff(self):
        """Full review can span more than one action TTL, but never arms a write itself."""
        text, review = self.review_long_note()
        self.assertFalse(VoiceTranscriptAcceptance.objects.exists())
        decision = self.say('accept this note').decision
        self.assertTrue(decision.voice_eligible)
        self.assertIn('finished playing', decision.spoken_summary)
        self.assertEqual(decision.sections[1]['text'], text)
        proposal = ChatActionProposal.objects.get(pk=decision.source_id)
        self.assertEqual(proposal.preview['auditory_review_id'], str(review.pk))
        reply = self.respond('accept this note', self.deliver(decision))
        self.assertEqual(reply.decision.execution_state, 'succeeded', reply.spoken)
        self.assertFalse(CloseoutCapture.objects.exists())
        handoff = self.say('handoff this note').decision
        reply = self.respond('confirm handoff', self.deliver(handoff))
        self.assertEqual(reply.decision.execution_state, 'succeeded', reply.spoken)
        self.assertEqual(CloseoutCapture.objects.get().current_revision.narrative, text)
        self.respond('confirm handoff', reply.decision)
        self.assertEqual(CloseoutCapture.objects.count(), 1)

    def test_missing_page_cannot_qualify_acceptance(self):
        """A last-page callback cannot stand in for an earlier missing page."""
        self.review_long_note(skip=2)
        decision = self.say('accept this note').decision
        self.assertFalse(decision.voice_eligible)
        self.assertEqual(
            self.respond('accept this note', self.deliver(decision)).event, 'ineligible'
        )
        self.assertFalse(VoiceTranscriptAcceptance.objects.exists())

    def test_reconnect_invalidates_full_review_and_never_rearms(self):
        """Set-aside recovery discards auditory authority, not transcript history."""
        self.review_long_note()
        decision = self.say('accept this note').decision
        self.assertTrue(decision.voice_eligible)
        self.coordinator.disarm(self.session.thread_id, 'reconnected', set_aside=True)
        self.assertTrue(VoiceCaptureReview.objects.get().invalidated)
        self.assertFalse(self.say('accept this note').decision.voice_eligible)
        self.assertFalse(VoiceTranscriptAcceptance.objects.exists())

    def test_replacement_or_scope_drift_cannot_reuse_page_evidence(self):
        """Neither a new note nor a new scope inherits an old certificate."""
        self.review_long_note()
        with patch(
            'ai.core.decisions.adapters.capture_review.scope_strings',
            return_value=('changed', 'b' * 64),
        ):
            with self.assertRaisesMessage(ProposalError, 'review changed'):
                self.say('accept this note')
        self.say('replace note with ' + 'New exact observation at 55 PSI. ' * 80)
        self.assertFalse(self.say('accept this note').decision.voice_eligible)
        self.assertFalse(VoiceTranscriptAcceptance.objects.exists())

    def test_replayed_and_tampered_page_rows_do_not_fill_review_gaps(self):
        """Verify each distinct ledger row and its complete original hash."""
        from ai.core.decisions.adapters.capture_review import completed
        from voice.models import VoiceUtterance

        _, review = self.review_long_note()
        review.refresh_from_db()
        self.assertTrue(completed(review))
        review.utterance_ids[1] = review.utterance_ids[0]
        self.assertFalse(completed(review))
        review.refresh_from_db()
        VoiceUtterance.objects.filter(pk=review.utterance_ids[0]).update(
            spoken_summary='Different text'
        )
        self.assertFalse(completed(review))

    def test_page_navigation_never_accepts_even_with_touch_confirmation(self):
        """The read-only page decision has no executable acceptance action."""
        self.consent()
        self.say('note ' + 'Synthetic page at 15 PSI. ' * 90)
        decision = self.deliver_note_page(self.say('read the whole note').decision)
        next_page = self.respond('next page', decision).decision
        self.assertEqual(next_page.executable['page'], 2)
        self.assertFalse(next_page.voice_eligible)
        self.respond('yes', next_page, touch=True)
        self.assertFalse(VoiceTranscriptAcceptance.objects.exists())

    def test_dictation_replay_and_conflicting_turn_are_safe(self):
        """Same nonce appends once; changed content on that nonce is refused."""
        self.consent()
        args = {**self.arguments, 'nonce': 'dictation-once'}
        self.coordinator.begin('note Pressure fifty PSI.', **args)
        self.coordinator.begin('note Pressure fifty PSI.', **args)
        self.assertEqual(VoiceTranscriptRevision.objects.count(), 1)
        with self.assertRaisesMessage(ProposalError, 'different text'):
            self.coordinator.begin('note Pressure five PSI.', **args)

    def test_each_policy_dependency_fails_closed(self):
        """Missing web/worker deployment policy never silently falls back."""
        for key in POLICY:
            with (
                self.subTest(key=key),
                patch.dict(os.environ, {key: ''}),
                self.assertRaises(ProposalError),
            ):
                require_enabled()
        for key in ('AIMMS_WORK_ORDERS_ENABLED', 'AIMMS_CLOSEOUT_WIZARD_ENABLED'):
            with (
                self.subTest(key=key),
                override_settings(**{key: False}),
                self.assertRaises(ProposalError),
            ):
                require_enabled()
        self.flags.feature_voice_closeout = False
        with self.assertRaises(ProposalError):
            require_enabled()

    def test_stale_consent_and_revoked_flag(self):
        """No capture survives new consent requirements or a disabled workflow."""
        self.session.consent_version = 'consent-v1'
        self.session.save(update_fields=['consent_version'])
        with self.assertRaisesMessage(ProposalError, 'current consent'):
            self.say(f'start closeout for work order {self.order.pk}')
        self.session.consent_version = 'consent-v2'
        self.session.save(update_fields=['consent_version'])
        self.consent()
        self.say('note Verified repaired seal at fifty PSI.')
        decision = self.deliver(self.say('accept this note').decision)
        self.flags.feature_voice_closeout = False
        with self.assertRaisesMessage(ProposalError, 'disabled'):
            self.respond('accept this note', decision)
        self.assertFalse(VoiceTranscriptAcceptance.objects.exists())

    def test_fault_intake_refusal_has_no_destination(self):
        """Fault notes remain unavailable even with the closeout family enabled."""
        reply = self.say('file a fault note')
        self.assertEqual(reply.spoken, 'Fault notes cannot be filed by voice yet.')
        self.assertFalse(VoiceCaptureSession.objects.exists())
        self.assertFalse(ChatActionProposal.objects.exists())
