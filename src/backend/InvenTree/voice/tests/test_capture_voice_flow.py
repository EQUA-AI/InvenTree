"""E7/E8 live capture integration; isolated database, no provider or email."""

import os
import re
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from tasks.closeout_models import CloseoutCapture
from tasks.models import WorkOrderLifecycle
from tasks.tests.test_workorder_voice_readback import WorkOrderVoiceFixture

from ai.core.decisions.adapters.capture_adapter import require_enabled
from ai.core.decisions.receipts import verified_work_order_receipt
from aichat.models import ChatActionProposal
from aichat.services.proposals import ProposalError
from voice.models import (
    VoiceCaptureSession,
    VoiceTranscriptAcceptance,
    VoiceTranscriptRevision,
)

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
        previous = MigrationExecutor(connection).loader.project_state(
            [('voice', '0007_voicepresentation')]
        ).apps.get_model('voice', 'VoiceTranscriptRevision')
        row = previous.objects.create(
            capture_id=capture.pk,
            revision=1,
            full_text='VOICE-TEST old ORM compatibility only.',
            content_hash='a' * 64,
            segments=[],
            created_by_id=self.actor.pk,
        )
        self.assertEqual(VoiceTranscriptRevision.objects.get(pk=row.pk).source_turn_id, '')

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
