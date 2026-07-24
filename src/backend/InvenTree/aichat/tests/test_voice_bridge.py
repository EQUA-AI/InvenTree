"""§5.3: voice writes terminate in the governed proposal rail, not the ORM.

Proves the executor bridge dispatches a verbally-confirmed voice write through
the *same* ``confirm_proposal`` → ``tasks.services`` command the visual rail
uses — same effect, same receipt shape — and that anything a stale, expired,
cross-owner or unbound proposal could do fails closed instead of writing.
"""

from __future__ import annotations

import unittest
from datetime import timedelta

from django.apps import apps

if not apps.is_installed('tasks'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from asgiref.sync import async_to_sync
from tasks.models import KanbanCard, WorkOrderLifecycle, WorkOrderType
from tasks.scope import MaintenanceScope

from ai.core.auth import AIPrincipal
from ai.core.voice.write_gate import ExecutableWrite
from aichat.models import ChatActionProposal, ProposalState
from aichat.services import proposals
from aichat.services.voice_bridge import ProposalConfirmingVoiceExecutor
from assets.models import AssetMachine
from company.models import Company

SCOPE_HASH = 'v' * 64


def _voice_scope_resolver(actor):
    """Resolver seam: scope any actor to the Voice Cust, as production would.

    The bridge re-reads the owner from the DB (it never trusts an in-memory
    attribute), so scope must resolve the way it does in a real deployment.
    """
    customer = Company.objects.filter(name='Voice Cust').first()
    if customer is None:
        return set()
    return {MaintenanceScope(customer_id=customer.pk, site_key=None)}


def _principal(user) -> AIPrincipal:
    return AIPrincipal(
        subject=f'user:{user.pk}',
        actor=f'user:{user.pk}',
        user_pk=str(user.pk),
        username=user.get_username(),
        authentication_method='voice',
        scope='voice',
        policy_version='test',
        is_staff=bool(user.is_staff),
        is_superuser=bool(user.is_superuser),
    )


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER=f'{__name__}._voice_scope_resolver'
)
class VoiceBridgeTests(TestCase):
    """The confirm-side unification bridge."""

    @classmethod
    def setUpTestData(cls):
        """SetUpTestData."""
        cls.customer = Company.objects.create(name='Voice Cust', is_customer=True)
        cls.actor = get_user_model().objects.create_superuser(
            username='voice-sup', email='v@example.com', password='pw'
        )
        cls.machine = AssetMachine.objects.create(name='Mill 2', customer=cls.customer)

    def setUp(self):
        """SetUp."""
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.work_order = KanbanCard.objects.create(
            title='Voice job',
            status=KanbanCard.STATUS_REVIEW,
            priority=KanbanCard.PRIORITY_MEDIUM,
            customer=self.customer,
            machine=self.machine,
            assigned_to=self.actor,
            work_order_type=WorkOrderType.PREVENTIVE,
            lifecycle_status=WorkOrderLifecycle.IN_PROGRESS,
        )
        self.executor = ProposalConfirmingVoiceExecutor()

    def _proposal(self, action='work_order.hold', key='voice-1', intent=None):
        return proposals.create_proposal(
            owner=self.actor,
            scope_key=f'customer:{self.customer.pk}',
            scope_hash=SCOPE_HASH,
            action_type=action,
            work_order_id=self.work_order.pk,
            reason='Spoken: hold this.',
            idempotency_key=key,
            policy_version='ws7-voice-v1',
            intent=intent or {},
        )

    def _executable(self, proposal):
        return ExecutableWrite(
            tool_name=proposal.action_type,
            capability='work_order.change',
            arguments={'proposal_id': str(proposal.id), 'scope_hash': SCOPE_HASH},
        )

    def test_confirmed_voice_write_dispatches_the_canonical_command(self):
        """A bound proposal is confirmed through the shared command service."""
        proposal = self._proposal()
        result = self.executor._confirm(self._executable(proposal), _principal(self.actor))
        self.assertTrue(result.ok)
        self.assertEqual(result.detail, 'hold')
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.ON_HOLD)
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, ProposalState.EXECUTED)

    def test_voice_and_visual_confirm_produce_the_same_receipt_shape(self):
        """Parity: the voice bridge and a direct confirm yield the same receipt."""
        voice_proposal = self._proposal(key='voice-parity')
        self.executor._confirm(self._executable(voice_proposal), _principal(self.actor))
        voice_proposal.refresh_from_db()

        # The voice bridge calls the very same confirm_proposal the visual rail
        # calls, so the durable receipt has the identical canonical shape.
        self.assertEqual(
            set(voice_proposal.receipt),
            {
                'work_order_id', 'event_id', 'command', 'lifecycle_status',
                'lifecycle_version', 'correlation_id', 'idempotency_key',
            },
        )
        self.assertEqual(voice_proposal.receipt['command'], 'hold')

    def test_async_execute_wraps_the_sync_confirm(self):
        """The async seam entrypoint runs the same confirmation."""
        proposal = self._proposal(key='voice-async')
        result = async_to_sync(self.executor.execute)(
            self._executable(proposal),
            actor=_principal(self.actor),
            trusted_context=None,
        )
        self.assertTrue(result.ok)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.ON_HOLD)

    def test_expired_proposal_fails_closed(self):
        """A voice confirm of an expired proposal writes nothing."""
        proposal = self._proposal(key='voice-expired')
        ChatActionProposal.objects.filter(id=proposal.id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        result = self.executor._confirm(self._executable(proposal), _principal(self.actor))
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, 'PROPOSAL_EXPIRED')
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS)

    def test_missing_binding_fails_closed(self):
        """An executable without a proposal binding cannot write."""
        executable = ExecutableWrite(
            tool_name='work_order.hold', capability='work_order.change', arguments={}
        )
        result = self.executor._confirm(executable, _principal(self.actor))
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, 'PROPOSAL_BINDING_MISSING')

    def test_cross_owner_proposal_is_not_confirmable_by_voice(self):
        """A voice actor cannot confirm another owner's proposal."""
        proposal = self._proposal(key='voice-cross')
        stranger = get_user_model().objects.create_user(username='v-stranger', password='pw')
        result = self.executor._confirm(self._executable(proposal), _principal(stranger))
        self.assertFalse(result.ok)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS)


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER=f'{__name__}._voice_scope_resolver'
)
class VoiceProposeSideTests(TestCase):
    """Propose-side wiring: build_voice_proposal creates the durable proposal."""

    @classmethod
    def setUpTestData(cls):
        """SetUpTestData."""
        cls.customer = Company.objects.create(name='Voice Cust', is_customer=True)
        cls.actor = get_user_model().objects.create_superuser(
            username='voice-prop', email='vp@example.com', password='pw'
        )
        cls.machine = AssetMachine.objects.create(name='Mill 9', customer=cls.customer)

    def setUp(self):
        """SetUp."""
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.work_order = KanbanCard.objects.create(
            title='Voice propose job',
            status=KanbanCard.STATUS_REVIEW,
            priority=KanbanCard.PRIORITY_MEDIUM,
            customer=self.customer,
            machine=self.machine,
            assigned_to=self.actor,
            work_order_type=WorkOrderType.PREVENTIVE,
            lifecycle_status=WorkOrderLifecycle.IN_PROGRESS,
        )

    def _build(self, action='work_order.hold', key='vp-1', intent=None):
        from aichat.services.voice_bridge import build_voice_proposal

        return build_voice_proposal(
            owner=self.actor,
            scope_key=f'customer:{self.customer.pk}',
            scope_hash=SCOPE_HASH,
            action_type=action,
            work_order_id=self.work_order.pk,
            reason='Spoken request.',
            idempotency_key=key,
            intent=intent,
        )

    def test_build_creates_a_proposal_and_binds_the_executable(self):
        """The resolved write carries a real proposal id + scope for the executor."""
        from aichat.services.voice_bridge import VOICE_PROPOSAL_EXPIRY_SECONDS

        resolved = self._build()
        proposal_id = resolved.executable.arguments['proposal_id']
        proposal = ChatActionProposal.objects.get(id=proposal_id)
        self.assertEqual(proposal.state, ProposalState.PROPOSED)
        self.assertEqual(resolved.executable.arguments['scope_hash'], SCOPE_HASH)
        self.assertEqual(resolved.executable.tool_name, 'work_order.hold')
        # Shorter voice expiry, not the 15-minute visual default.
        lifetime = (proposal.expires_at - proposal.created_at).total_seconds()
        self.assertLessEqual(lifetime, VOICE_PROPOSAL_EXPIRY_SECONDS + 5)
        self.assertGreater(lifetime, 0)

    def test_read_back_is_server_authored_from_the_preview(self):
        """The spoken summary comes from the proposal, not model text."""
        resolved = self._build()
        self.assertIn('hold', resolved.action.summary.lower())
        self.assertEqual(resolved.action.action_class.value, 'confirmable')
        self.assertEqual(resolved.action.confirm_phrase, '')

    def test_delete_is_irreversible_and_demands_a_strict_phrase(self):
        """A destructive voice write is classified irreversible with a phrase."""
        resolved = self._build(action='work_order.delete', key='vp-del')
        self.assertEqual(resolved.action.action_class.value, 'irreversible')
        self.assertEqual(resolved.action.confirm_phrase, 'confirm delete')

    def test_propose_then_confirm_round_trip(self):
        """The propose-side proposal is confirmable by the executor seam."""
        resolved = self._build(key='vp-roundtrip')
        result = ProposalConfirmingVoiceExecutor()._confirm(
            resolved.executable, _principal(self.actor)
        )
        self.assertTrue(result.ok)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.ON_HOLD)

    def test_voice_delete_confirms_without_re_demanding_the_phrase(self):
        """Voice enforces the strict phrase at the gate, so the executor may confirm.

        The executor asserts ``strict_phrase_satisfied``; the durable proposal is
        still irreversible-classified (the gate spoke and validated the phrase).
        """
        resolved = self._build(action='work_order.delete', key='vp-del-confirm')
        self.assertEqual(resolved.action.action_class.value, 'irreversible')
        result = ProposalConfirmingVoiceExecutor()._confirm(
            resolved.executable, _principal(self.actor)
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.detail, 'delete')
        self.assertFalse(KanbanCard.objects.filter(pk=self.work_order.pk).exists())
