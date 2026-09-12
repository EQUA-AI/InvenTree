"""B7: real canonical hold/correction/receipt journey with no provider mocks."""

import unittest
from datetime import UTC, datetime, timedelta

from django.apps import apps

if not apps.is_installed('tasks'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

from django.test import override_settings

from tasks.models import WorkOrder, WorkOrderCommand, WorkOrderLifecycle

from ai.core.decisions.adapters.proposal_adapter import ProposalAdapter
from ai.core.decisions.coordinator import DecisionConflict, DecisionCoordinator
from ai.core.decisions.store import InMemoryPendingDecisionStore
from ai.core.voice.speech import spoken_summary_hash
from aichat.models import ChatActionProposal
from aichat.services.proposals import ProposalPreviewChanged
from aichat.tests.test_voice_bridge import VoiceBridgeTests, _principal
from voice.models import VoiceOperation, VoiceSession


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='aichat.tests.test_voice_bridge._voice_scope_resolver'
)
class VoiceHoldJourneyTests(VoiceBridgeTests):
    """The real adapter, current scope, proposal lock, domain effect and receipt."""

    def setUp(self):
        """Set up only isolated, actor-scoped work orders and a voice ledger."""
        super().setUp()
        self.clock = [datetime.now(UTC)]
        self.coordinator = DecisionCoordinator(
            store=InMemoryPendingDecisionStore(),
            adapter=ProposalAdapter(),
            now=lambda: self.clock[0],
        )
        self.session = VoiceSession.objects.create(
            owner=self.actor,
            thread_id='hold-test',
            scope_key='test',
            scope_hash='a' * 64,
            policy_version='test',
        )
        self.target = WorkOrder.objects.create(
            title='Corrected target',
            customer=self.customer,
            machine=self.machine,
            lifecycle_status=WorkOrderLifecycle.IN_PROGRESS,
            status=WorkOrder.STATUS_REVIEW,
        )
        self.principal = _principal(self.actor)
        self.arguments = {
            'actor': self.principal,
            'session_id': str(self.session.pk),
            'thread_id': 'hold-test',
            'nonce': 'first',
        }

    def present(self):
        """Create the first authoritative hold preview."""
        return self.coordinator.begin(
            f'Put work order {self.work_order.pk} on hold because replacement seal is missing',
            **self.arguments,
        ).decision

    def deliver(self, d):
        """Bind the read-back and advance beyond its conservative echo guard."""
        d = self.coordinator.bind_playback(
            d,
            utterance_id='test-utterance-' + d.decision_id,
            spoken_text=d.spoken_summary,
            spoken_hash=spoken_summary_hash(d.spoken_summary),
        )
        self.clock[0] += timedelta(seconds=60)
        return d

    def respond(self, text, d, **kwargs):
        """Submit a reply carrying the exact reviewed focus."""
        return self.coordinator.resolve(
            text,
            **{**self.arguments, 'nonce': 'correction'},
            context=d.to_public_dict(),
            **kwargs,
        )

    def test_target_correction_then_confirm_and_duplicate_have_one_command(self):
        """Correction changes target, while stale and repeated confirms never double-write."""
        original = self.present()
        revised = self.respond(f'no, I meant {self.target.pk}', original).decision
        self.assertNotEqual(original.decision_id, revised.decision_id)
        self.assertEqual(revised.sections[2]['text'], 'replacement seal is missing')
        with self.assertRaises(DecisionConflict):
            self.respond('confirm hold', original)
        revised = self.deliver(revised)
        reply = self.respond('confirm hold', revised)
        self.assertEqual(reply.decision.execution_state, 'succeeded')
        self.assertIn('on hold', reply.spoken)
        self.respond('confirm hold', reply.decision)
        history = self.respond('what changed', reply.decision)
        self.assertIn('Corrected target', history.spoken)
        self.assertEqual(
            WorkOrderCommand.objects.filter(
                work_order=self.target, command='hold'
            ).count(),
            1,
        )
        self.work_order.refresh_from_db()
        self.assertEqual(
            self.work_order.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS
        )
        self.assertEqual(VoiceOperation.objects.filter(session=self.session).count(), 1)

    def test_echo_cannot_dispatch_and_touch_can_confirm_without_audio(self):
        """An acoustic echo is not authorization; an explicit bound touch action is."""
        d = self.present()
        reply = self.respond('confirm hold', d, provider_active=True)
        self.assertEqual(reply.event, 'echo_refused')
        self.assertFalse(VoiceOperation.objects.filter(session=self.session).exists())
        reply = self.respond('confirm hold', d, touch=True)
        self.assertEqual(reply.decision.execution_state, 'succeeded')

    def test_touch_reject_invalidates_before_voice_dispatch(self):
        """Authoritative touch state wins over an older voice preview."""
        d = self.present()
        ChatActionProposal.objects.filter(pk=d.source_id).update(state='rejected')
        with self.assertRaises(DecisionConflict):
            self.respond('yes', d)
        self.assertFalse(VoiceOperation.objects.filter(session=self.session).exists())

    def test_changed_preview_or_version_requires_fresh_review(self):
        """Source drift disarms the old focus before any operation is submitted."""
        d = self.present()
        WorkOrder.objects.filter(pk=self.work_order.pk).update(lifecycle_version=99)
        with self.assertRaises(ProposalPreviewChanged):
            self.respond('confirm hold', d, touch=True)
        self.assertEqual(self.coordinator.store.read('hold-test').state, 'disarmed')

    def test_unknown_result_is_reconciled_from_committed_proposal(self):
        """A response lost after commit is recovered from its durable receipt."""
        from unittest.mock import patch

        real_execute = self.coordinator.adapter.execute

        def lose_response(*args):
            real_execute(*args)
            raise TimeoutError('response lost after commit')

        d = self.present()
        with patch.object(
            self.coordinator.adapter, 'execute', side_effect=lose_response
        ):
            reply = self.respond('confirm hold', d, touch=True)
        self.assertEqual(reply.decision.execution_state, 'succeeded')
        self.assertIn('is now on hold', reply.spoken)

    def test_recovery_finds_ledger_after_lost_cache_operation_binding(self):
        """A crash after submission cannot hide the durable operation or re-execute."""
        from ai.core.decisions.receipts import start_operation

        d = self.coordinator.advance(
            self.present(), state='executing', execution_state='executing'
        )
        operation, _ = start_operation(d)
        reply = self.coordinator.history(d, self.principal)
        self.assertEqual(reply.decision.operation_id, str(operation.pk))
        self.assertIn('not yet verified', reply.spoken)
        self.assertFalse(
            WorkOrderCommand.objects.filter(work_order=self.work_order).exists()
        )

    def test_reconciliation_requires_matching_domain_audit_event(self):
        """A proposal receipt alone cannot prove the business effect."""
        from ai.core.decisions.receipts import verified_hold_receipt

        d = self.present()
        self.respond('confirm hold', d, touch=True)
        proposal = ChatActionProposal.objects.get(pk=d.source_id)
        self.assertTrue(verified_hold_receipt(proposal))
        proposal.receipt = {**proposal.receipt, 'event_id': 99999999}
        self.assertFalse(verified_hold_receipt(proposal))

    def test_cache_loss_recovers_only_a_receipt_not_a_confirmable_action(self):
        """A replacement session can reconcile without restoring authorization."""
        from ai.core.decisions.receipts import recover_latest

        d = self.present()
        self.respond('confirm hold', d, touch=True)
        self.coordinator.store.take('hold-test')
        recovered = recover_latest(
            actor=self.principal,
            thread_id='hold-test',
            session_id='replacement',
            now=self.clock[0],
        )
        self.assertEqual(recovered.execution_state, 'succeeded')
        self.assertEqual(recovered.state, 'resolved')
        self.assertIsNone(recovered.executable)
        self.assertNotIn('confirm hold', recovered.allowed_responses)
        self.assertIsNone(
            recover_latest(
                actor=self.principal,
                thread_id='foreign-thread',
                session_id='replacement',
                now=self.clock[0],
            )
        )

    def test_preview_hash_is_canonical_and_ignores_only_as_of(self):
        """Ordering and read timestamp are irrelevant; displayed changes are not."""
        from aichat.services.proposals import compute_preview_hash

        self.assertEqual(
            compute_preview_hash(
                {'reason': 'seal', 'as_of': 'old', 'target': {'pk': 1}}
            ),
            compute_preview_hash(
                {'target': {'pk': 1}, 'as_of': 'new', 'reason': 'seal'}
            ),
        )
        self.assertNotEqual(
            compute_preview_hash({'reason': 'seal'}),
            compute_preview_hash({'reason': 'bearing'}),
        )

    def test_changed_hash_rejects_even_an_executed_proposal_replay(self):
        """A caller cannot replay another reviewed preview under this source ID."""
        from aichat.services.proposals import confirm_proposal

        d = self.present()
        self.respond('confirm hold', d, touch=True)
        with self.assertRaises(ProposalPreviewChanged):
            confirm_proposal(
                owner=self.actor,
                scope_hash=d.scope_hash,
                proposal_id=d.source_id,
                expected_preview_hash='0' * 64,
                confirm_phrase='confirm hold',
            )
        self.assertEqual(
            WorkOrderCommand.objects.filter(
                work_order=self.work_order, command='hold'
            ).count(),
            1,
        )
