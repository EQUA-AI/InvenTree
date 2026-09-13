"""Assigned inbox and voice review safety on isolated records; no email calls."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.test import override_settings

from ai.core.auth import principal_for_user
from ai.core.decisions.adapters.approval_adapter import ApprovalAdapter
from ai.core.decisions.adapters.proposal_adapter import ProposalAdapter
from ai.core.decisions.coordinator import (
    DecisionConflict,
    DecisionCoordinator,
    estimated_playback_seconds,
)
from ai.core.decisions.store import InMemoryPendingDecisionStore
from assets.models import AssetMachine, Client
from voice.models import VoiceOperation, VoiceUtterance
from voice.services.realtime import SessionLimits, create_session

from . import services
from .models import Approval, ApprovalExecution, ApprovalReviewAcknowledgment
from .review_evidence import review_units
from .review_sections import compute_review_hash
from .serializers import ApprovalCreateSerializer
from .tests import ApprovalTestBase


def inbox_scopes(actor):
    """Resolve the test user's actual tenant, without trusting payload fields."""
    client = Client.objects.filter(code=f'voice-reviewer-{actor.pk}').first()
    return [{'client_id': client.pk}] if client else []


@override_settings(
    APPROVAL_INBOX_SCOPED=True,
    APPROVAL_REVIEW_REVISION_BOUND=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER=inbox_scopes,
)
class VoiceInboxTests(ApprovalTestBase):
    """REST, service and coordinator must all honor the same current assignment."""

    def setUp(self):
        """Create two local tenants and a provider-free voice session."""
        super().setUp()
        self.tenant = Client.objects.create(
            name='Voice review tenant', code=f'voice-reviewer-{self.user.pk}'
        )
        self.other_tenant = Client.objects.create(
            name='Other review tenant', code=f'voice-reviewer-{self.user2.pk}'
        )
        self.machine = AssetMachine.objects.create(
            name='VOICE-TEST pump', client=self.tenant
        )
        self.other_machine = AssetMachine.objects.create(
            name='Not in scope', client=self.other_tenant
        )
        self.principal = principal_for_user(self.user)
        self.session = create_session(
            owner=self.user,
            thread_id='voice-inbox',
            scope_key=self.principal.scope,
            policy_version='test',
            limits=SessionLimits(),
        )
        self.clock = [datetime.now(UTC)]
        self.store = InMemoryPendingDecisionStore()
        self.coordinator = DecisionCoordinator(
            store=self.store,
            adapter=ProposalAdapter(),
            approval_adapter=ApprovalAdapter(),
            now=lambda: self.clock[0],
        )
        from ai.core.config import get_settings

        self.config = get_settings().model_copy(
            update={
                'feature_voice_approvals': True,
                'feature_voice_auditory_review': True,
                'feature_voice_external_actions': True,
                'voice_action_dry_run': False,
            }
        )
        self.enterContext(
            patch('ai.core.config.get_settings', return_value=self.config)
        )

    def fixture(self, *, machine=None, assigned=None, approval_id=None, **overrides):
        """Create only a test approval; an actual service call still checks its scope."""
        key = str(uuid.uuid4())
        data = {
            'action_type': 'repair_work_package',
            'summary': f'VOICE-TEST {key[:8]}',
            'agent_run_id': key,
            'agent_checkpoint_id': key,
            'tool_call_id': key,
            'assigned_to_user_id': assigned or self.user.pk,
            'payload': {
                'machine_id': (machine or self.machine).pk,
                'title': 'Inspect pump',
                'origin': 'chat',
                'fault': {'summary': 'Test vibration', 'criticality': 'medium'},
            },
        }
        data.update(overrides)
        serializer = ApprovalCreateSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        if approval_id:
            with patch.object(
                Approval._meta.get_field('id'), 'get_default', return_value=approval_id
            ):
                approval = serializer.save()
            self.assertEqual(approval.pk, approval_id)
            return approval
        return serializer.save()

    def begin(self, content='What needs my approval?'):
        """Run the same deterministic coordinator entry as the voice pipeline."""
        return self.coordinator.begin(
            content,
            actor=self.principal,
            session_id=self.session.pk,
            thread_id=self.session.thread_id,
            nonce=str(uuid.uuid4()),
        )

    def say(self, content, **kwargs):
        """Supply the current public focus just as the client wire does."""
        current = self.store.read(self.session.thread_id)
        return self.coordinator.resolve(
            content,
            actor=self.principal,
            session_id=self.session.pk,
            thread_id=self.session.thread_id,
            nonce=str(uuid.uuid4()),
            context=kwargs.pop('context', current.to_public_dict()),
            **kwargs,
        )

    def deliver(self):
        """Recording-only exact playback: no real audio or provider connection."""
        current = self.store.read(self.session.thread_id)
        utterance = VoiceUtterance.objects.create(
            session=self.session,
            utterance_type='prompt',
            policy_version='test',
            spoken_summary=current.spoken_summary,
            spoken_summary_hash=hashlib.sha256(
                current.spoken_summary.encode()
            ).hexdigest(),
        )
        current = self.coordinator.bind_playback(
            current,
            utterance_id=utterance.pk,
            spoken_text=utterance.spoken_summary,
            spoken_hash=utterance.spoken_summary_hash,
        )
        current = self.coordinator.playback(
            current,
            event='playback-started',
            sequence=current.sequence,
            utterance_id=utterance.pk,
            spoken_hash=utterance.spoken_summary_hash,
        )
        self.clock[0] += timedelta(
            seconds=estimated_playback_seconds(current.spoken_summary) + 2
        )
        self.coordinator.playback(
            current,
            event='playback-completed',
            sequence=current.sequence,
            utterance_id=utterance.pk,
            spoken_hash=utterance.spoken_summary_hash,
        )
        utterance.playback_state = 'done'
        utterance.save(update_fields=['playback_state'])
        self.clock[0] += timedelta(seconds=2)

    def test_rest_list_count_detail_card_events_revisions_hide_other_assignments_and_scopes(
        self,
    ):
        """Enumeration, counts and object endpoints reveal only assigned scoped rows."""
        own = self.fixture()
        # A retained pre-E19 placeholder is not a valid new notification contract.
        legacy_notification = self.fixture()
        Approval.objects.filter(pk=legacy_notification.pk).update(
            action_type='notification'
        )
        hidden = [
            self.fixture(assigned=self.user2.pk),
            self.fixture(machine=self.other_machine),
            self.fixture(payload={'client_id': self.tenant.pk}),
            legacy_notification,
        ]
        rows = self.client.get('/api/approvals/').data
        self.assertEqual([str(row['id']) for row in rows], [str(own.pk)])
        self.assertEqual(self.client.get('/api/approvals/count/').data['count'], 1)
        for row in hidden:
            for suffix in ('', 'card-package/'):
                self.assertEqual(
                    self.client.get(f'/api/approvals/{row.pk}/{suffix}').status_code,
                    404,
                )
            for suffix in ('events/', 'revisions/'):
                self.assertEqual(
                    self.client.get(f'/api/approvals/{row.pk}/{suffix}').data, []
                )
            with self.assertRaises(services.ApprovalNotFoundError):
                services.open_approval(row.pk, actor=self.user)

    def test_selection_is_not_approval_and_bare_yes_never_selects(self):
        """An ordinal names a request but does not acknowledge or execute it."""
        approval = self.fixture()
        self.begin()
        self.assertEqual(self.say('yes').decision.kind, 'selection')
        selected = self.say('the first one')
        self.assertEqual(selected.decision.source_id, str(approval.pk))
        self.assertEqual(selected.decision.kind, 'approval_review')
        self.assertFalse(ApprovalExecution.objects.exists())
        self.assertFalse(ApprovalReviewAcknowledgment.objects.exists())

    def test_exact_reference_selects_from_active_inbox(self):
        """The displayed reference works while the frozen numbered menu is open."""
        self.fixture()
        approval = self.fixture()
        self.begin()
        selected = self.say(f'Review request {str(approval.pk)[:8]}')
        self.assertEqual(selected.decision.source_id, str(approval.pk))
        self.assertEqual(selected.decision.kind, 'approval_review')
        self.assertFalse(ApprovalExecution.objects.exists())
        self.assertFalse(ApprovalReviewAcknowledgment.objects.exists())

    def test_spoken_reference_selects_from_active_inbox(self):
        """Digit names and conventional letter names retain the exact target."""
        approval = self.fixture(
            approval_id=uuid.UUID('08298de6-1111-4111-8111-111111111111')
        )
        self.begin()
        selected = self.say('Read request zero eight two nine eight dee ee six')
        self.assertEqual(selected.decision.source_id, str(approval.pk))
        self.assertEqual(selected.decision.kind, 'approval_review')
        self.assertFalse(ApprovalExecution.objects.exists())

    def test_reference_cannot_select_outside_frozen_inbox_page(self):
        """Knowing an off-page reference does not bypass the frozen selection."""
        rows = [self.fixture() for _ in range(10)]
        inbox = self.begin().decision
        outside = next(a for a in rows if str(a.pk) not in inbox.executable['ids'])
        result = self.say(f'Review request {str(outside.pk)[:8]}')
        self.assertEqual(result.decision.decision_id, inbox.decision_id)
        self.assertEqual(result.decision.kind, 'selection')
        outside.refresh_from_db()
        self.assertEqual(outside.status, 'pending')

    def test_ambiguous_reference_does_not_select_from_inbox(self):
        """An eight-character collision is never resolved by first-match order."""
        self.fixture(approval_id=uuid.UUID('aaaaaaaa-1111-4111-8111-111111111111'))
        self.fixture(approval_id=uuid.UUID('aaaaaaaa-2222-4222-8222-222222222222'))
        inbox = self.begin().decision
        result = self.say('Review request aaaaaaaa')
        self.assertEqual(result.decision.decision_id, inbox.decision_id)
        self.assertEqual(result.decision.kind, 'selection')
        self.assertFalse(ApprovalExecution.objects.exists())

    def test_email_is_excluded_from_voice_inbox(self):
        """Paused email work cannot enter this voice campaign even with email roles."""
        self.fixture(
            action_type='email',
            payload={
                'to': 'no-send@invalid.test',
                'subject': 'recording only',
                'body': 'never send',
            },
        )
        self.assertIsNone(self.begin().decision)

    def test_explicit_reference_rejects_inaccessible_and_ambiguous_targets(self):
        """No reference lookup can escape assignment or current tenant scope."""
        hidden = self.fixture(machine=self.other_machine)
        self.assertEqual(
            self.begin(f'read request {str(hidden.pk)[:8]}').event, 'refused'
        )

    def test_reassignment_invalidates_active_focus_before_any_write(self):
        """A cached decision is not a durable permission grant."""
        approval = self.fixture()
        self.begin()
        self.say('1')
        approval.assigned_to_user = self.user2
        approval.save(update_fields=['assigned_to_user'])
        with self.assertRaises(DecisionConflict):
            self.say('deny: test rejection')
        self.assertEqual(self.store.read(self.session.thread_id).state, 'disarmed')
        self.assertFalse(VoiceOperation.objects.exists())

    def test_missing_or_partial_delivery_cannot_acknowledge(self):
        """The client cannot assert that undelivered review pages were heard."""
        self.fixture()
        self.begin()
        self.say('1')
        self.assertEqual(
            self.say('I have reviewed this request').event, 'review_incomplete'
        )
        self.deliver()
        self.assertEqual(
            self.say('I have reviewed this request').event, 'review_incomplete'
        )
        self.assertFalse(ApprovalReviewAcknowledgment.objects.exists())

    def test_all_exact_pages_allow_acknowledgment_but_not_execution(self):
        """Acknowledgment remains separate from a strict final confirmation."""
        approval = self.fixture()
        self.begin()
        self.say('1')
        for index, _ in enumerate(review_units(approval)):
            if index:
                self.say('next section')
            self.deliver()
        result = self.say('I have reviewed this request')
        self.assertEqual(result.event, 'acknowledged')
        self.assertEqual(ApprovalReviewAcknowledgment.objects.count(), 1)
        self.assertFalse(ApprovalExecution.objects.exists())
        self.assertFalse(VoiceOperation.objects.exists())

    def test_reason_readback_strict_rejection_and_terminal_replay(self):
        """Rejection is recorded once and never invokes a business executor."""
        approval = self.fixture()
        self.begin()
        self.say('1')
        result = self.say('deny: use the scheduled shutdown instead')
        self.assertIn('use the scheduled shutdown instead', result.spoken)
        self.assertEqual(result.decision.required_phrase, 'confirm rejection')
        self.say('yes')
        approval.refresh_from_db()
        self.assertEqual(approval.status, 'in_review')
        self.deliver()
        result = self.say('confirm rejection')
        self.assertEqual(result.decision.execution_state, 'succeeded')
        self.say('confirm rejection')
        approval.refresh_from_db()
        self.assertEqual(approval.status, 'denied')
        self.assertEqual(approval.events.filter(event_type='denied').count(), 1)
        self.assertEqual(VoiceOperation.objects.count(), 1)
        self.assertFalse(ApprovalExecution.objects.exists())

    def test_bare_cancel_only_disarms_and_explicit_cancel_has_own_preview(self):
        """Canceling interaction focus cannot cancel the underlying request."""
        approval = self.fixture()
        self.begin()
        self.say('1')
        self.say('cancel')
        approval.refresh_from_db()
        self.assertEqual(approval.status, 'in_review')
        self.begin(f'read request {str(approval.pk)[:8]}')
        result = self.say('cancel the request')
        self.assertEqual(result.decision.required_phrase, 'confirm cancel request')
        self.deliver()
        self.say('confirm cancel request')
        approval.refresh_from_db()
        self.assertEqual(approval.status, 'canceled')

    def test_revision_changes_refuse_old_confirmation(self):
        """An updated request cannot be decided using the earlier hash."""
        approval = self.fixture()
        self.begin()
        self.say('1')
        self.say('request changes: reduce the quantity')
        approval.payload['title'] = 'Changed by another reviewer'
        approval.save(update_fields=['payload'])
        with self.assertRaises(DecisionConflict):
            self.say('confirm request changes', touch=True)
        self.assertFalse(VoiceOperation.objects.exists())

    def test_service_focus_is_rechecked_under_transaction(self):
        """Direct screen callers cannot omit or reuse stale decision focus."""
        approval = self.fixture()
        services.open_approval(approval.pk, actor=self.user)
        for data in (
            {'reason': 'test'},
            {'reason': 'test', 'revision': 0, 'review_hash': '0' * 64},
        ):
            with self.assertRaises(services.ApprovalConflictError):
                services.deny(approval.pk, actor=self.user, data=data)
        services.deny(
            approval.pk,
            actor=self.user,
            data={
                'reason': 'test',
                'revision': 0,
                'review_hash': compute_review_hash(approval),
            },
        )

    def test_scope_revocation_hides_receipts(self):
        """Previously successful decisions cannot leak after assignment is revoked."""
        approval = self.fixture()
        self.begin()
        self.say('1')
        self.say('deny: test only')
        self.say('confirm rejection', touch=True)
        operation = VoiceOperation.objects.get()
        approval.assigned_to_user = self.user2
        approval.save(update_fields=['assigned_to_user'])
        from ai.core.decisions.receipts import lookup_operation

        self.assertIsNone(
            lookup_operation(
                actor=self.principal,
                thread_id=self.session.thread_id,
                operation_id=operation.pk,
            )
        )
        self.assertIsNone(
            self.coordinator.history(
                self.store.read(self.session.thread_id), self.principal
            ).decision
        )

    def test_stale_context_cannot_disarm_a_newer_review(self):
        """A late tab cannot change current focus, even to disarm it."""
        self.fixture()
        old = self.begin().decision.to_public_dict()
        current = self.say('1').decision
        with self.assertRaises(DecisionConflict):
            self.say('cancel', context=old)
        self.assertEqual(self.store.read(self.session.thread_id), current)

    def test_inbox_content_changes_require_new_selection(self):
        """The ordinal never silently names a changed preview."""
        approval = self.fixture()
        self.begin()
        approval.summary = 'New summary'
        approval.save(update_fields=['summary'])
        with self.assertRaises(DecisionConflict):
            self.say('1')
        self.assertFalse(VoiceOperation.objects.exists())

    def test_late_delivery_binding_rechecks_assignment(self):
        """Provider output cannot attach a new review after access is revoked."""
        approval = self.fixture()
        self.begin()
        self.say('1')
        approval.assigned_to_user = self.user2
        approval.save(update_fields=['assigned_to_user'])
        with self.assertRaises(DecisionConflict):
            self.deliver()
        self.assertFalse(approval.review_deliveries.exists())

    def test_reconnected_terminal_focus_is_receipt_only_and_does_not_trap_intents(self):
        """The same owner's new session can read a receipt, never rearm old authority."""
        approval = self.fixture()
        self.begin(f'Read request {str(approval.pk)[:8]}')
        self.say('deny: synthetic request')
        self.deliver()
        self.say('confirm rejection')
        current = self.store.read(self.session.thread_id)
        arguments = {
            'actor': self.principal,
            'session_id': str(uuid.uuid4()),
            'thread_id': self.session.thread_id,
            'nonce': str(uuid.uuid4()),
            'context': current.to_public_dict(),
        }
        self.assertIsNone(self.coordinator.resolve('Show my approvals', **arguments))
        receipt = self.coordinator.resolve('last action status', **arguments)
        self.assertEqual(receipt.event, 'receipt')
        self.assertEqual(receipt.decision.operation_id, current.operation_id)
        self.assertEqual(VoiceOperation.objects.count(), 1)
        with self.assertRaises(DecisionConflict):
            self.coordinator.resolve(
                'last action status',
                **{**arguments, 'actor': principal_for_user(self.user2)},
            )

    def test_reconnected_disarmed_focus_never_confirms_the_previous_session(self):
        """An old non-active prompt cannot block a fresh explicit inbox request."""
        approval = self.fixture()
        self.begin(f'Read request {str(approval.pk)[:8]}')
        active = self.store.read(self.session.thread_id)
        with self.assertRaises(DecisionConflict):
            self.coordinator.resolve(
                'deny: different session',
                actor=self.principal,
                session_id=str(uuid.uuid4()),
                thread_id=self.session.thread_id,
                nonce=str(uuid.uuid4()),
                context=active.to_public_dict(),
            )
        self.say('cancel')
        current = self.store.read(self.session.thread_id)
        arguments = {
            'actor': self.principal,
            'session_id': str(uuid.uuid4()),
            'thread_id': self.session.thread_id,
            'nonce': str(uuid.uuid4()),
            'context': current.to_public_dict(),
        }
        self.assertIsNone(self.coordinator.resolve('Show my approvals', **arguments))
        self.assertEqual(self.coordinator.resolve('yes', **arguments).event, 'disarmed')
        self.assertFalse(VoiceOperation.objects.exists())
        approval.assigned_to_user = self.user2
        approval.save(update_fields=['assigned_to_user'])
        with self.assertRaises(DecisionConflict):
            self.coordinator.resolve('yes', **arguments)

    def test_qualified_yes_disarms_and_final_result_does_not_trap_new_intents(self):
        """Corrections never execute, and a receipt is not a new confirmation."""
        self.fixture()
        self.begin()
        self.say('1')
        self.say('deny: duplicate')
        self.assertEqual(
            self.say('yes but change the reason').decision.state, 'disarmed'
        )
        self.assertIsNone(self.say('show my approvals'))
        self.begin()
        self.say('1')
        self.say('deny: duplicate')
        self.say('confirm rejection', touch=True)
        self.assertIsNone(self.say('show my approvals'))

    def test_decision_throttle_shared_actor_bucket_fails_closed(self):
        """The 31st decision in one minute is refused on either surface."""
        from types import SimpleNamespace

        from django.core.cache import cache

        from approvals.permissions import ApprovalDecisionThrottle

        throttle = ApprovalDecisionThrottle()
        throttle.timer = lambda: 123000000
        key = f'approval:decisions:{self.user.pk}:2050000'
        cache.delete(key)
        with override_settings(TESTING=False):
            for _ in range(15):
                self.assertTrue(throttle.allow_actor(self.user))
                self.assertTrue(
                    throttle.allow_request(SimpleNamespace(user=self.user), None)
                )
            self.assertFalse(throttle.allow_actor(self.user))
            with patch.object(cache, 'add', side_effect=ConnectionError):
                self.assertFalse(throttle.allow_actor(self.user2))
        cache.delete(key)
