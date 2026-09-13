"""E9-E11 completion requires a reviewed coordinator decision, never bare done."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from ai.core.decisions.adapters.proposal_adapter import ProposalAdapter
from ai.core.decisions.coordinator import DecisionCoordinator
from ai.core.decisions.receipts import verified_work_order_receipt
from ai.core.decisions.store import InMemoryPendingDecisionStore
from ai.core.voice.speech import spoken_summary_hash
from aichat.models import ChatActionProposal
from aichat.services import proposals
from aichat.tests.test_voice_bridge import _principal
from company.models import Company
from tasks.models import WorkOrderStepExecution
from tasks.scope import MaintenanceScope
from tasks.tests.test_procedure_walkthrough import WalkthroughFlowTests
from voice.models import VoiceSession


def procedure_scope(actor):
    """Rehydrate the isolated fixture scope for receipt reconciliation too."""
    return {
        MaintenanceScope(
            customer_id=Company.objects.get(name='B7 Customer').pk, site_key=None
        )
    }


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=f'{__name__}.procedure_scope')
class VoiceProcedureCompletionTests(TestCase):
    """Real scoped application, proposal, canonical command and immutable receipt."""

    def setUp(self):
        """Reuse the isolated applied-procedure fixture without inheriting its tests."""
        WalkthroughFlowTests.setUp(self)
        self.step = WorkOrderStepExecution.objects.order_by('sequence').first()
        self.settings = SimpleNamespace(
            feature_voice_procedure_complete=True, feature_guided_procedures=True
        )
        self.enterContext(
            patch('ai.core.config.get_settings', return_value=self.settings)
        )
        adapter = ProposalAdapter()
        self.clock = [datetime.now(UTC)]
        self.coordinator = DecisionCoordinator(
            store=InMemoryPendingDecisionStore(),
            adapter=adapter,
            now=lambda: self.clock[0],
        )
        self.session = VoiceSession.objects.create(
            owner=self.actor,
            thread_id='e-procedure',
            scope_key='procedure-test',
            scope_hash='p' * 64,
            policy_version='test',
        )
        self.arguments = {
            'actor': _principal(self.actor),
            'session_id': str(self.session.pk),
            'thread_id': self.session.thread_id,
            'nonce': 'procedure-turn',
        }

    def present(self, suffix=''):
        """A fully identified request creates a review, not a completed step."""
        return self.coordinator.begin(
            f'Complete step {self.step.step_key} of application {self.step.application_id} for work order {self.work_order.pk}{suffix}',
            **self.arguments,
        ).decision

    def deliver(self, decision):
        """Bind persisted text evidence before allowing spoken assent."""
        decision = self.coordinator.bind_playback(
            decision,
            utterance_id='test-utterance',
            spoken_text=decision.spoken_summary,
            spoken_hash=spoken_summary_hash(decision.spoken_summary),
        )
        self.clock[0] += timedelta(seconds=120)
        return decision

    def respond(self, decision, text='yes'):
        """Only the exact reviewed focus can be confirmed."""
        return self.coordinator.resolve(
            text, **self.arguments, context=decision.to_public_dict()
        )

    def test_full_review_then_exactly_one_completion(self):
        """Instructions are retained verbatim and no effect occurs until separate assent."""
        decision = self.present()
        self.assertIn('Lock out the main breaker.', decision.spoken_summary)
        self.step.refresh_from_db()
        self.assertEqual(self.step.status, 'pending')
        reply = self.respond(self.deliver(decision))
        self.assertEqual(reply.decision.execution_state, 'succeeded', reply.spoken)
        self.step.refresh_from_db()
        self.assertEqual(self.step.status, 'completed')
        self.assertEqual(self.step.version, decision.revision + 1)
        self.assertTrue(
            verified_work_order_receipt(
                ChatActionProposal.objects.get(pk=decision.source_id)
            )
        )
        self.respond(reply.decision)
        self.assertEqual(
            self.work_order.commands.filter(command='complete_step').count(), 1
        )

    def test_value_and_derived_failed_result_are_reviewed(self):
        """A measurement outside stored limits is honestly recorded as failed."""
        self.step.step_snapshot.update(
            value_type='number', min_value='10', max_value='20', required=True
        )
        self.step.save(update_fields=['step_snapshot'])
        decision = self.present(' with value 25')
        self.assertIn('25', decision.spoken_summary)
        self.assertIn('failed', decision.spoken_summary)
        reply = self.respond(self.deliver(decision))
        self.assertEqual(reply.decision.execution_state, 'succeeded', reply.spoken)
        self.step.refresh_from_db()
        self.assertEqual(self.step.status, 'failed')
        self.assertFalse(self.step.passed)

    def test_stale_step_version_disarms_before_effect(self):
        """Screen edits cannot be overwritten by an old voice review."""
        decision = self.deliver(self.present())
        WorkOrderStepExecution.objects.filter(pk=self.step.pk).update(version=42)
        with self.assertRaises(proposals.ProposalPreviewChanged):
            self.respond(decision)
        self.assertFalse(
            self.work_order.commands.filter(command='complete_step').exists()
        )

    def test_flag_revocation_prevents_confirmation(self):
        """The completion kill switch is checked again for a pending decision."""
        decision = self.deliver(self.present())
        self.settings.feature_voice_procedure_complete = False
        with self.assertRaisesMessage(proposals.ProposalError, 'disabled'):
            self.respond(decision)
        self.assertFalse(
            self.work_order.commands.filter(command='complete_step').exists()
        )

    def test_required_value_and_visual_evidence_refuse_without_effect(self):
        """Missing or visually required evidence cannot be waived by speech."""
        self.step.step_snapshot.update(required=True)
        self.step.save(update_fields=['step_snapshot'])
        with self.assertRaisesMessage(proposals.ProposalError, 'explicit result'):
            self.present()
        self.step.step_snapshot.update(evidence_policy={'required': True})
        self.step.save(update_fields=['step_snapshot'])
        with self.assertRaisesMessage(proposals.ProposalError, 'visual evidence'):
            self.present(' passed true')
        self.assertFalse(ChatActionProposal.objects.exists())

    def test_tampered_receipt_value_is_not_verified(self):
        """An event ID alone cannot vouch for an invented measurement."""
        decision = self.deliver(self.present())
        self.respond(decision)
        proposal = ChatActionProposal.objects.get(pk=decision.source_id)
        self.assertTrue(verified_work_order_receipt(proposal))
        proposal.receipt['value'] = 'invented value'
        self.assertFalse(verified_work_order_receipt(proposal))

    def test_walkthrough_route_persists_exact_text_without_decision_flag(self):
        """Read-only guided navigation does not depend on the mutation coordinator."""
        from asgiref.sync import async_to_sync
        from unittest.mock import AsyncMock
        from ai.core.voice import routes
        from voice.models import VoiceUtterance

        self.settings.feature_voice_decision_coordinator = False
        self.settings.feature_voice_procedure_complete = False
        with patch.object(routes, '_require_voice_enabled', return_value=self.settings), \
             patch.object(routes, '_principal', return_value=_principal(self.actor)), \
             patch.object(routes, '_owned_session', AsyncMock(return_value=self.session)), \
             patch.object(routes, '_provider_channel_factory', None):
            result = async_to_sync(routes.procedure_walkthrough)(routes.WalkthroughRequest(
                work_order_id=self.work_order.pk, session_id=str(self.session.pk)))
        utterance = VoiceUtterance.objects.get()
        self.assertEqual(result['speak_text'], utterance.spoken_summary)
        self.assertEqual(result['spoken']['spoken_summary'], utterance.spoken_summary)
        self.assertFalse(result['audio_available'])
        self.assertFalse(ChatActionProposal.objects.exists())
