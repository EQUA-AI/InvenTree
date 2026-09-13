"""E1/E2 cancellation: real proposals, canonical effects and verified receipts."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from ai.core.decisions.adapters.proposal_adapter import ProposalAdapter
from ai.core.decisions.coordinator import DecisionConflict, DecisionCoordinator
from ai.core.decisions.receipts import (
    lookup_operation,
    recover_latest,
    verified_work_order_receipt,
)
from ai.core.decisions.store import InMemoryPendingDecisionStore
from ai.core.voice.speech import spoken_summary_hash
from aichat.models import ChatActionProposal
from aichat.services import proposals
from aichat.tests.test_voice_bridge import _principal
from company.models import Company
from tasks.models import WorkOrder, WorkOrderCommand, WorkOrderEvent, WorkOrderLifecycle
from tasks.scope import MaintenanceScope
from voice.models import VoiceOperation, VoiceSession


def cancellation_scope(actor):
    """Provide only the isolated fixture's business scope after user rehydration."""
    customer = Company.objects.get(name='E1 cancellation customer')
    return {MaintenanceScope(customer_id=customer.pk, site_key=None)}


class WorkOrderVoiceFixture:
    """Shared disposable coordinator and domain fixtures."""

    def setUp(self):
        """Create disposable work orders, not live application fixtures."""
        self.customer = Company.objects.create(
            name='E1 cancellation customer', is_customer=True
        )
        self.actor = get_user_model().objects.create_superuser(
            username='e1-cancel', email='e1@example.invalid', password='test-only'
        )
        self.order = WorkOrder.objects.create(
            title='Cancel target',
            customer=self.customer,
            lifecycle_status=WorkOrderLifecycle.PLANNED,
        )
        self.other = WorkOrder.objects.create(
            title='Corrected cancellation',
            customer=self.customer,
            lifecycle_status=WorkOrderLifecycle.PLANNED,
        )
        self.session = VoiceSession.objects.create(
            owner=self.actor,
            thread_id='e1-cancellation',
            scope_key='test',
            scope_hash='a' * 64,
            policy_version='test',
        )
        self.clock = [datetime.now(UTC)]
        self.coordinator = DecisionCoordinator(
            store=InMemoryPendingDecisionStore(),
            adapter=ProposalAdapter(),
            now=lambda: self.clock[0],
        )
        self.principal = _principal(self.actor)
        self.arguments = {
            'actor': self.principal,
            'session_id': str(self.session.pk),
            'thread_id': self.session.thread_id,
            'nonce': 'initial',
        }

    def present(self, reason='pressure is fifteen PSI'):
        """Create a fresh cancellation preview without submitting an effect."""
        return self.coordinator.begin(
            f'Cancel work order {self.order.pk} because {reason}', **self.arguments
        ).decision

    def deliver(self, decision):
        """Supply delivery evidence and advance the conservative playback clock."""
        decision = self.coordinator.bind_playback(
            decision,
            utterance_id='e1-' + decision.decision_id,
            spoken_text=decision.spoken_summary,
            spoken_hash=spoken_summary_hash(decision.spoken_summary),
        )
        self.clock[0] += timedelta(seconds=90)
        return decision

    def respond(self, text, decision, **kwargs):
        """Reply with the exact current actor/session/focus binding."""
        return self.coordinator.resolve(
            text,
            **{**self.arguments, 'nonce': 'corrected'},
            context=decision.to_public_dict(),
            **kwargs,
        )


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=f'{__name__}.cancellation_scope')
class WorkOrderVoiceCancellationTests(WorkOrderVoiceFixture, TestCase):
    """Exercise the entire coordinator → proposal → command/event receipt path."""

    def test_full_reason_strict_phrase_and_no_compensation(self):
        """Read the reason and irreversibility before any effect, never offer bare cancel."""
        decision = self.present()
        self.assertIn('Reason: pressure is fifteen PSI.', decision.spoken_summary)
        self.assertIn('cannot be undone', decision.spoken_summary)
        self.assertIn('say no', decision.spoken_summary)
        self.assertNotIn('or cancel', decision.spoken_summary)
        self.assertNotIn('cancel', decision.allowed_responses)
        self.assertNotIn('yes', decision.allowed_responses)
        self.assertEqual(decision.required_phrase, 'confirm cancel order')
        self.assertFalse(WorkOrderCommand.objects.exists())
        reply = self.respond('confirm cancel order', self.deliver(decision))
        self.assertEqual(reply.decision.execution_state, 'succeeded')
        self.assertIn('canceled', reply.spoken)
        self.order.refresh_from_db()
        self.assertEqual(self.order.lifecycle_status, WorkOrderLifecycle.CANCELED)
        self.assertEqual(WorkOrderEvent.objects.get().reason, 'pressure is fifteen PSI')

    def test_missing_reason_does_not_create_a_proposal(self):
        """No generated placeholder can stand in for the user's cancellation reason."""
        with self.assertRaisesMessage(
            proposals.ProposalError, 'reason for the cancellation'
        ):
            self.coordinator.begin(
                f'Cancel work order {self.order.pk}', **self.arguments
            )
        self.assertFalse(ChatActionProposal.objects.exists())

    def test_out_of_scope_work_order_cannot_be_presented(self):
        """Knowing a work-order identifier does not grant its maintenance scope."""
        foreign = Company.objects.create(name='E1 other customer', is_customer=True)
        WorkOrder.objects.filter(pk=self.order.pk).update(customer=foreign)
        with self.assertRaises(proposals.ProposalError):
            self.present()
        self.assertFalse(ChatActionProposal.objects.exists())
        self.assertFalse(VoiceOperation.objects.exists())

    def test_long_reason_stays_complete_and_requires_screen_review(self):
        """Never truncate a reason to make it fit the voice confirmation bound."""
        reason = 'Review the full warning. ' * 20
        decision = self.present(reason)
        proposal = ChatActionProposal.objects.get(pk=decision.source_id)
        self.assertEqual(proposal.reason, reason.strip().rstrip('.'))
        self.assertEqual(decision.sections[2]['text'], proposal.reason)
        self.assertFalse(decision.voice_eligible)
        result = self.respond('confirm cancel order', self.deliver(decision))
        self.assertEqual(result.event, 'ineligible')
        self.assertFalse(VoiceOperation.objects.exists())

    def test_echo_including_optional_reference_cannot_execute(self):
        """An optional matching reference cannot evade the acoustic echo guard."""
        decision = self.present()
        for phrase in ('confirm cancel order', f'confirm cancel order {self.order.pk}'):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    self.respond(phrase, decision, provider_active=True).event,
                    'echo_refused',
                )
        self.assertFalse(VoiceOperation.objects.exists())

    def test_bare_yes_never_cancels(self):
        """A lenient affirmative is not the OD-3 phrase."""
        self.respond('yes', self.deliver(self.present()))
        self.assertFalse(VoiceOperation.objects.exists())
        self.assertFalse(WorkOrderCommand.objects.exists())

    def test_no_disarms_without_rejecting_source_or_changing_order(self):
        """Say no preserves the order and merely disarms the interaction."""
        decision = self.deliver(self.present())
        reply = self.respond('no', decision)
        self.assertEqual(reply.decision.state, 'disarmed')
        self.assertEqual(
            ChatActionProposal.objects.get(pk=decision.source_id).state, 'proposed'
        )
        self.assertFalse(VoiceOperation.objects.exists())

    def test_wrong_reference_is_a_fresh_cancellation_not_a_hold_or_effect(self):
        """Optional reference mismatch preserves reason/action but requires another review."""
        original = self.present()
        corrected = self.respond(
            f'confirm cancel order {self.other.pk}', original
        ).decision
        self.assertNotEqual(original.decision_id, corrected.decision_id)
        self.assertEqual(corrected.executable['action'], 'work_order.cancel')
        self.assertEqual(corrected.sections[2]['text'], original.sections[2]['text'])
        self.assertFalse(WorkOrderCommand.objects.exists())
        with self.assertRaises(DecisionConflict):
            self.respond('confirm cancel order', original)
        reply = self.respond('confirm cancel order', self.deliver(corrected))
        self.assertEqual(reply.decision.execution_state, 'succeeded')
        self.order.refresh_from_db()
        self.assertEqual(self.order.lifecycle_status, WorkOrderLifecycle.PLANNED)
        self.assertEqual(WorkOrderCommand.objects.get().work_order_id, self.other.pk)

    def test_reason_correction_is_bound_to_a_new_hash(self):
        """A unit-bearing correction must be reviewed, never appended to an assent."""
        original = self.present()
        corrected = self.respond(
            'change the reason to pressure is fifty PSI', original
        ).decision
        self.assertNotEqual(original.preview_hash, corrected.preview_hash)
        self.assertIn('pressure is fifty PSI', corrected.spoken_summary)
        self.assertEqual(corrected.executable['action'], 'work_order.cancel')
        self.assertFalse(WorkOrderCommand.objects.exists())

    def test_matching_reference_and_duplicate_confirmation_have_one_effect(self):
        """A matching optional identifier is safe, but never an idempotency bypass."""
        decision = self.deliver(self.present())
        reply = self.respond(f'confirm cancel order {self.order.pk}', decision)
        self.assertEqual(reply.decision.execution_state, 'succeeded')
        self.respond('confirm cancel order', reply.decision)
        self.assertEqual(WorkOrderCommand.objects.count(), 1)
        self.assertEqual(VoiceOperation.objects.count(), 1)

    def test_stale_version_disarms_before_submission(self):
        """A newer command invalidates the cancellation's old version."""
        decision = self.present()
        WorkOrder.objects.filter(pk=self.order.pk).update(lifecycle_version=99)
        with self.assertRaises(proposals.ProposalPreviewChanged):
            self.respond('confirm cancel order', decision, touch=True)
        self.assertEqual(
            self.coordinator.store.read(self.session.thread_id).state, 'disarmed'
        )
        self.assertFalse(VoiceOperation.objects.exists())

    def test_canonical_permission_is_checked_before_presenting(self):
        """Cancellation uses transition permission, not hold's execute permission."""
        with patch(
            'tasks.permissions.require_permission', side_effect=PermissionDenied
        ) as check:
            with self.assertRaises(PermissionDenied):
                self.present()
        self.assertEqual(check.call_args.args[1], 'tasks.transition_workorder')
        self.assertFalse(ChatActionProposal.objects.exists())

    def test_illegal_transition_is_failed_before_effect(self):
        """Cancellation cannot bypass the domain lifecycle graph."""
        WorkOrder.objects.filter(pk=self.order.pk).update(
            lifecycle_status='in_progress'
        )
        reply = self.respond('confirm cancel order', self.deliver(self.present()))
        self.assertEqual(reply.decision.execution_state, 'failed_before_effect')
        self.assertIn('not applied', reply.spoken)
        self.assertFalse(WorkOrderCommand.objects.exists())

    def test_revoked_permission_disarms_before_submitting_an_operation(self):
        """A previously granted transition permission is not cached as authority."""
        decision = self.present()
        self.actor.is_superuser = False
        self.actor.save(update_fields=['is_superuser'])
        with self.assertRaises(PermissionDenied):
            self.respond('confirm cancel order', decision, touch=True)
        self.assertEqual(
            self.coordinator.store.read(self.session.thread_id).state, 'disarmed'
        )
        self.assertFalse(VoiceOperation.objects.exists())

    def test_lost_response_reconciles_and_cache_recovery_never_rearms(self):
        """Verify the cancellation's canonical receipt without invoking it twice."""
        execute = self.coordinator.adapter.execute

        def lose_response(*args):
            execute(*args)
            raise TimeoutError('lost after commit')

        decision = self.deliver(self.present())
        with patch.object(
            self.coordinator.adapter, 'execute', side_effect=lose_response
        ):
            reply = self.respond('confirm cancel order', decision)
        self.assertEqual(reply.decision.execution_state, 'succeeded')
        self.assertIn(
            'canceled',
            self.respond('what happened to my last action', reply.decision).spoken,
        )
        self.coordinator.store.take(self.session.thread_id)
        recovered = recover_latest(
            actor=self.principal,
            thread_id=self.session.thread_id,
            session_id='replacement',
            now=self.clock[0],
        )
        self.assertEqual(recovered.execution_state, 'succeeded')
        self.assertIsNone(recovered.executable)
        self.assertNotIn('confirm cancel order', recovered.allowed_responses)
        self.assertEqual(WorkOrderCommand.objects.count(), 1)
        self.assertIsNone(
            lookup_operation(actor=self.principal, thread_id='other-thread')
        )

    def test_tampered_receipt_or_audit_does_not_prove_success(self):
        """Every positive receipt must join the right operation, actor and event."""
        decision = self.present()
        self.respond('confirm cancel order', decision, touch=True)
        proposal = ChatActionProposal.objects.get(pk=decision.source_id)
        self.assertTrue(verified_work_order_receipt(proposal))
        original = proposal.receipt
        for key, value in [
            ('event_id', 999999),
            ('command', 'hold'),
            ('work_order_id', self.other.pk),
            ('lifecycle_status', 'on_hold'),
            ('idempotency_key', 'another-proposal'),
            ('lifecycle_version', 99),
            ('correlation_id', 'unknown'),
        ]:
            with self.subTest(field=key):
                proposal.receipt = {**original, key: value}
                self.assertFalse(verified_work_order_receipt(proposal))
        WorkOrderEvent.objects.filter(pk=original['event_id']).update(event_type='HOLD')
        result = lookup_operation(
            actor=self.principal, thread_id=self.session.thread_id
        )
        self.assertEqual(result['execution_state'], 'unknown')
        self.assertIsNone(result['receipt_ref'])

    def test_text_strict_phrase_cannot_be_bypassed(self):
        """The text rail and obsolete bypass boolean cannot cancel on bare yes."""
        decision = self.present()
        for phrase, bypass in [('yes', False), ('confirm cancel', False), ('', True)]:
            with self.subTest(phrase=phrase, bypass=bypass):
                with self.assertRaises(proposals.StrictConfirmationRequired):
                    proposals.confirm_proposal(
                        owner=self.actor,
                        scope_hash=decision.scope_hash,
                        proposal_id=decision.source_id,
                        confirm_phrase=phrase,
                        strict_phrase_satisfied=bypass,
                    )
        self.assertFalse(WorkOrderCommand.objects.exists())

    def test_conflicting_or_oversized_reasons_are_not_silently_truncated(self):
        """The exact reviewed reason is the reason the command must receive."""
        for reason, intent in [('one', {'reason': 'two'}), ('x' * 2001, {}), ('', {})]:
            with self.subTest(reason_length=len(reason)):
                with self.assertRaises(proposals.ProposalError):
                    proposals.create_proposal(
                        owner=self.actor,
                        scope_key='test',
                        scope_hash='a' * 64,
                        action_type='work_order.cancel',
                        work_order_id=self.order.pk,
                        reason=reason,
                        intent=intent,
                        idempotency_key='invalid-reason',
                        policy_version='test',
                    )
        self.assertFalse(ChatActionProposal.objects.exists())

    def test_generic_transition_cannot_bypass_cancellation_review(self):
        """Cancellation is not a lenient transition proposal in disguise."""
        with self.assertRaisesMessage(proposals.ProposalError, 'work_order.cancel'):
            proposals.create_proposal(
                owner=self.actor,
                scope_key='test',
                scope_hash='a' * 64,
                action_type='work_order.transition',
                work_order_id=self.order.pk,
                reason='obsolete job',
                intent={'to_status': 'canceled'},
                idempotency_key='transition-bypass',
                policy_version='test',
            )


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=f'{__name__}.cancellation_scope')
class WorkOrderVoiceExtendedTests(WorkOrderVoiceFixture, TestCase):
    """E1 action-specific review, canonical commands and E2 recovery."""

    def begin_action(self, text):
        """Present a new, uniquely keyed action through the live resolver."""
        return self.coordinator.begin(
            text, **{**self.arguments, 'nonce': str(ChatActionProposal.objects.count())}
        ).decision

    def confirm_action(self, text):
        """Deliver the exact review, confirm it and verify canonical evidence."""
        decision = self.deliver(self.begin_action(text))
        failures = []
        execute = self.coordinator.adapter.execute

        def observed_execute(*args, **kwargs):
            try:
                return execute(*args, **kwargs)
            except Exception as exc:
                failures.append(repr(exc))
                raise

        with patch.object(
            self.coordinator.adapter, 'execute', side_effect=observed_execute
        ):
            reply = self.respond(decision.required_phrase or 'yes', decision)
        self.assertEqual(
            reply.decision.execution_state, 'succeeded', f'{reply.spoken} {failures}'
        )
        proposal = ChatActionProposal.objects.get(pk=decision.source_id)
        self.assertTrue(verified_work_order_receipt(proposal), proposal.receipt)
        result = lookup_operation(
            actor=self.principal, thread_id=self.session.thread_id
        )
        self.assertEqual(result['execution_state'], 'succeeded')
        self.respond('yes', reply.decision)
        self.assertEqual(
            WorkOrderCommand.objects.filter(
                idempotency_key=f'proposal:{proposal.pk}'
            ).count(),
            1,
        )
        return proposal

    def test_assign_reads_names_and_rejects_ambiguous_names(self):
        """Both sides use current display names, never merely model-supplied IDs."""
        assignee = get_user_model().objects.create_user(
            username='technician-e1', first_name='Test', last_name='Technician'
        )
        decision = self.begin_action(
            f'Assign work order {self.order.pk} to Test Technician'
        )
        self.assertIn('Unassigned', decision.spoken_summary)
        self.assertIn('Test Technician', decision.spoken_summary)
        reply = self.respond('yes', self.deliver(decision))
        self.assertEqual(reply.decision.execution_state, 'succeeded', reply.spoken)
        self.order.refresh_from_db()
        self.assertEqual(self.order.assigned_to_id, assignee.pk)
        get_user_model().objects.create_user(
            username='technician-e1-other', first_name='Test', last_name='Technician'
        )
        with self.assertRaisesMessage(proposals.ProposalError, 'ambiguous'):
            self.begin_action(f'Assign work order {self.order.pk} to Test Technician')

    def test_schedule_resize_and_update_verify_receipts(self):
        """Planning actions read all changed fields and each have one audited effect."""
        for text in (
            f'Schedule work order {self.order.pk} from 2030-01-01T10:00:00Z to 2030-01-01T11:00:00Z',
            f'Resize work order {self.order.pk} to 90 minutes',
            f'Update work order {self.order.pk} title to Reviewed new title',
        ):
            with self.subTest(text=text):
                self.confirm_action(text)

    def test_schedule_without_timezone_is_not_armed(self):
        """Unknown deployment timezone cannot silently change a reviewed appointment."""
        with self.assertRaisesMessage(proposals.ProposalError, 'timezone'):
            self.begin_action(
                f'Schedule work order {self.order.pk} from 2030-01-01T10:00 to 2030-01-01T11:00'
            )
        self.assertFalse(ChatActionProposal.objects.exists())

    def test_child_procurement_and_dependency_receipts(self):
        """Unversioned commands have proposal-specific event evidence, including no-op procurement."""
        from tasks.models import WorkOrderDependency

        self.confirm_action(
            f'Create a child for work order {self.order.pk} titled Inspection'
        )
        proposal = self.confirm_action(
            f'Generate procurement for work order {self.order.pk}'
        )
        self.assertIsNone(proposal.receipt['child_id'])
        self.confirm_action(
            f'Add dependency to work order {self.order.pk} from work order {self.other.pk} type FS lag 5 minutes'
        )
        edge = WorkOrderDependency.objects.get(
            successor=self.order, predecessor=self.other
        )
        self.confirm_action(
            f'Remove dependency {edge.pk} from work order {self.order.pk}'
        )
        self.assertFalse(WorkOrderDependency.objects.exists())

    def test_delete_receipt_survives_target_deletion(self):
        """The durable deletion record, not a missing object, proves deletion."""
        from tasks.models import WorkOrderDeletionRecord

        decision = self.deliver(
            self.begin_action(
                f'Delete work order {self.order.pk} because duplicate record'
            )
        )
        self.assertIn('duplicate record', decision.spoken_summary)
        self.assertTrue(decision.required_phrase)
        decision = self.respond('yes', decision).decision
        self.assertTrue(WorkOrder.objects.filter(pk=self.order.pk).exists())
        decision = self.deliver(
            self.begin_action(
                f'Delete work order {self.order.pk} because duplicate record'
            )
        )
        reply = self.respond(decision.required_phrase, decision)
        self.assertEqual(reply.decision.execution_state, 'succeeded', reply.spoken)
        proposal = ChatActionProposal.objects.get(pk=decision.source_id)
        self.assertTrue(verified_work_order_receipt(proposal))
        self.assertEqual(WorkOrderDeletionRecord.objects.count(), 1)
        self.assertFalse(WorkOrder.objects.filter(pk=self.order.pk).exists())
        proposal.receipt['deletion_record_id'] = 999999
        self.assertFalse(verified_work_order_receipt(proposal))

    def test_resume_reports_readiness_blockers_without_effect(self):
        """The resume command's canonical readiness, not assistant optimism, decides."""
        WorkOrder.objects.filter(pk=self.order.pk).update(lifecycle_status='on_hold')
        decision = self.deliver(self.begin_action(f'Resume work order {self.order.pk}'))
        reply = self.respond('yes', decision)
        self.assertEqual(
            reply.decision.execution_state, 'failed_before_effect', reply.spoken
        )
        self.assertIn('Readiness blocked:', reply.spoken)
        self.assertNotIn('ReadinessBlocked', reply.spoken)
        self.assertFalse(WorkOrderCommand.objects.exists())

    def test_transition_uses_dynamic_strict_phrase_without_legacy_bypass(self):
        """A one-way lifecycle transition requires the reviewed strict phrase on both rails."""
        WorkOrder.objects.filter(pk=self.order.pk).update(lifecycle_status='verifying')
        decision = self.begin_action(
            f'Transition work order {self.order.pk} to completed'
        )
        self.assertEqual(decision.required_phrase, 'confirm transition')
        proposal = ChatActionProposal.objects.get(pk=decision.source_id)
        with self.assertRaises(proposals.StrictConfirmationRequired):
            proposals.confirm_proposal(
                owner=self.actor,
                scope_hash=proposal.scope_hash,
                proposal_id=proposal.pk,
                strict_phrase_satisfied=True,
            )
        self.assertFalse(WorkOrderCommand.objects.exists())

    def test_assignment_name_drift_disarms_and_target_correction_preserves_parameters(
        self,
    ):
        """No stale identity label and no lost parameters across a fresh target review."""
        assignee = get_user_model().objects.create_user(
            username='named-e1', first_name='First'
        )
        decision = self.begin_action(f'Assign work order {self.order.pk} to named-e1')
        corrected = self.respond(
            f'change that to work order {self.other.pk}', decision
        ).decision
        self.assertEqual(
            corrected.executable['parameters'], {'assignee_name': 'named-e1'}
        )
        get_user_model().objects.filter(pk=assignee.pk).update(first_name='Changed')
        with self.assertRaises(proposals.ProposalPreviewChanged):
            self.respond('yes', corrected, touch=True)
        self.assertFalse(VoiceOperation.objects.exists())
