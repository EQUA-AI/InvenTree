"""Every pending machine-bearing proposal reauthorizes without a preview hash."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from aichat.models import ProposalState
from aichat.services import proposals
from assets.models import AssetMachine, Client, ClientScopeGrant
from company.models import Company
from part.models import Part
from repair.models import RepairPacket
from tasks.models import (
    KanbanCard,
    WorkOrder,
    WorkOrderCommand,
    WorkOrderDependency,
    WorkOrderEvent,
    WorkOrderPart,
)
from tasks.services import scheduling
from tasks.services.work_orders import WorkOrderScopeError
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class MachineProposalReauthorizationTests(TestCase):
    """Use current native grants, not an unchanged proposal scope fingerprint."""

    def setUp(self):
        """Give one ordinary editor all planning actions and only client A."""
        self.actor = get_user_model().objects.create_user(username='machine-proposal-editor')
        self.group = Group.objects.create(name='Machine proposal editors')
        self.rules, _created = RuleSet.objects.update_or_create(
            group=self.group,
            name='work_order',
            defaults={
                'can_view': True,
                'can_add': True,
                'can_change': True,
                'can_delete': True,
            },
        )
        self.actor.groups.add(self.group)
        self.own = Client.objects.create(name='Proposal A', code='proposal-machine-a')
        self.foreign = Client.objects.create(name='Proposal B', code='proposal-machine-b')
        self.grant = ClientScopeGrant.objects.create(user=self.actor, client=self.own)
        self.machine = AssetMachine.objects.create(name='Originally allowed', client=self.own)
        self.work = WorkOrder.objects.create(title='Originally allowed Work', machine=self.machine)
        self.customer = Company.objects.create(name='Shared proposal customer', is_customer=True)

    def propose(self, action, *, intent=None, work=None, key=None):
        """Prepare an actual proposal through the canonical proposal service."""
        return proposals.create_proposal(
            owner=self.actor,
            scope_key=f'client:{self.own.pk}',
            scope_hash='b' * 64,
            action_type=action,
            work_order_id=(work or self.work).pk,
            reason='Review the planned change',
            idempotency_key=key or action,
            policy_version='o24-security-rereview',
            intent=intent or {},
        )

    def confirm(self, proposal, **kwargs):
        """Confirm through the legacy rail that omits expected_preview_hash."""
        return proposals.confirm_proposal(
            owner=self.actor,
            scope_hash=proposal.scope_hash,
            proposal_id=proposal.pk,
            **kwargs,
        )

    def revoke_machine(self):
        """Move the unchanged machine identity outside the actor's client grant."""
        self.machine.client = self.foreign
        self.machine.save(update_fields=['client'])

    def assert_no_effect(self, proposal, *, work_count=1):
        """Rejected dispatch saves no Work, packet, card, command or audit effect."""
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, ProposalState.PROPOSED)
        self.assertEqual(WorkOrder.objects.count(), work_count)
        self.assertFalse(RepairPacket.objects.exists())
        self.assertFalse(WorkOrderCommand.objects.exists())
        self.assertFalse(WorkOrderEvent.objects.exists())
        self.assertFalse(KanbanCard.objects.exclude(card_kind=KanbanCard.KIND_WORK_ORDER).exists())

    def create_proposal(self, action):
        """Both creation kinds store an explicit new machine assignment."""
        return self.propose(
            action,
            intent={'title': 'Proposed fresh Work', 'machine_id': self.machine.pk},
        )

    def test_create_confirm_without_hash_rechecks_machine_client(self):
        """Moving a previewed machine cannot create foreign Work on confirmation."""
        proposal = self.create_proposal('work_order.create')
        self.revoke_machine()
        with self.assertRaises(proposals.ProposalNotFound):
            self.confirm(proposal)
        self.assert_no_effect(proposal)

    def test_create_confirm_without_hash_rechecks_revoked_grant(self):
        """A stored scope hash is not authority after its actual grant is removed."""
        proposal = self.create_proposal('work_order.create')
        self.grant.delete()
        with self.assertRaises(proposals.ProposalNotFound):
            self.confirm(proposal)
        self.assert_no_effect(proposal)

    def test_repair_create_confirm_without_hash_rechecks_machine_client(self):
        """The compound repair create has the same fresh machine boundary."""
        proposal = self.create_proposal('repair_work_package.create')
        self.revoke_machine()
        with self.assertRaises(proposals.ProposalNotFound):
            self.confirm(proposal)
        self.assert_no_effect(proposal)

    def test_repair_create_confirm_without_hash_rechecks_revoked_grant(self):
        """Repair creation cannot use a revoked machine grant either."""
        proposal = self.create_proposal('repair_work_package.create')
        self.grant.delete()
        with self.assertRaises(proposals.ProposalNotFound):
            self.confirm(proposal)
        self.assert_no_effect(proposal)

    def test_child_confirm_without_hash_rechecks_inherited_work(self):
        """A card cannot be added after its machine-only parent becomes foreign."""
        proposal = self.propose('work_order.create_child', intent={'title': 'Tracked child'})
        self.revoke_machine()
        with self.assertRaises(proposals.ProposalNotFound):
            self.confirm(proposal)
        self.assert_no_effect(proposal)

    def test_procurement_confirm_without_hash_rechecks_inherited_work(self):
        """A generated card must reauthorize its parent before reading shortfalls."""
        part = Part.objects.create(name='Required proposal bearing')
        WorkOrderPart.objects.create(
            work_order=self.work,
            part=part,
            quantity=2,
            allocation_status=WorkOrderPart.ALLOCATION_INSUFFICIENT,
        )
        proposal = self.propose('work_order.generate_procurement')
        self.revoke_machine()
        with self.assertRaises(proposals.ProposalNotFound):
            self.confirm(proposal)
        self.assert_no_effect(proposal)

    def test_schedule_confirm_without_hash_rechecks_existing_work(self):
        """Moving an existing card also requires its current Work visibility."""
        proposal = self.propose(
            'work_order.schedule',
            intent={'scheduled_start': '2026-10-21T09:00:00Z'},
        )
        self.revoke_machine()
        with self.assertRaises(proposals.ProposalNotFound):
            self.confirm(proposal)
        self.assert_no_effect(proposal)

    def test_resize_confirm_without_hash_rechecks_existing_work(self):
        """A known Work identity does not preserve authority for a later resize."""
        proposal = self.propose('work_order.resize', intent={'estimated_minutes': 45})
        self.revoke_machine()
        with self.assertRaises(proposals.ProposalNotFound):
            self.confirm(proposal)
        self.assert_no_effect(proposal)

    def test_delete_confirm_without_hash_rechecks_existing_work(self):
        """The strict phrase confirms intent and never supplies missing scope."""
        proposal = self.propose('work_order.delete')
        self.revoke_machine()
        with self.assertRaises(proposals.ProposalNotFound):
            self.confirm(proposal, confirm_phrase='confirm delete')
        self.assert_no_effect(proposal)

    def test_optimize_confirm_without_hash_rechecks_every_candidate(self):
        """Targetless optimization reauthorizes its current candidate population."""
        proposal = self.propose(
            'schedule.optimize',
            intent={
                'candidate_ids': [self.work.pk],
                'horizon_start': '2026-10-21T09:00:00Z',
            },
        )
        self.revoke_machine()
        with self.assertRaises(proposals.ProposalNotFound):
            self.confirm(proposal)
        self.assert_no_effect(proposal)

    def test_dependency_create_without_hash_rechecks_predecessor(self):
        """A still-visible successor never authorizes its newly foreign predecessor."""
        successor = WorkOrder.objects.create(title='Shared successor', customer=self.customer)
        proposal = self.propose(
            'dependency.create', work=successor, intent={'predecessor_id': self.work.pk}
        )
        self.revoke_machine()
        with self.assertRaises(proposals.ProposalNotFound):
            self.confirm(proposal)
        self.assertFalse(WorkOrderDependency.objects.exists())
        self.assert_no_effect(proposal, work_count=2)

    def test_dependency_delete_without_hash_rechecks_both_work_endpoints(self):
        """A retained edge identity cannot bypass loss of predecessor visibility."""
        successor = WorkOrder.objects.create(title='Shared successor', customer=self.customer)
        edge = WorkOrderDependency.objects.create(predecessor=self.work, successor=successor)
        proposal = self.propose(
            'dependency.delete', work=successor, intent={'dependency_id': edge.pk}
        )
        self.revoke_machine()
        with self.assertRaises(proposals.ProposalNotFound):
            self.confirm(proposal)
        self.assertTrue(WorkOrderDependency.objects.filter(pk=edge.pk).exists())
        self.assert_no_effect(proposal, work_count=2)

    def test_shared_parent_child_keeps_existing_foreign_machine_identity(self):
        """Cards belong to shared Work and do not create or attach a new machine."""
        self.work.customer = self.customer
        self.work.save(update_fields=['customer'])
        self.revoke_machine()
        proposal = self.propose('work_order.create_child', intent={'title': 'Shared child'})
        confirmed = self.confirm(proposal)
        self.assertEqual(confirmed.state, ProposalState.EXECUTED)
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.assertEqual(KanbanCard.objects.exclude(card_kind=KanbanCard.KIND_WORK_ORDER).get().work_order_id, self.work.pk)
        self.assertEqual(WorkOrder.objects.get().machine_id, self.machine.pk)

    def test_shared_parent_procurement_keeps_existing_foreign_machine_identity(self):
        """Generating a card preserves O24 without conferring machine authority."""
        part = Part.objects.create(name='Shared procurement bearing')
        WorkOrderPart.objects.create(
            work_order=self.work,
            part=part,
            quantity=2,
            allocation_status=WorkOrderPart.ALLOCATION_INSUFFICIENT,
        )
        self.work.customer = self.customer
        self.work.save(update_fields=['customer'])
        self.revoke_machine()
        proposal = self.propose('work_order.generate_procurement')
        confirmed = self.confirm(proposal)
        self.assertEqual(confirmed.state, ProposalState.EXECUTED)
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.assertEqual(KanbanCard.objects.exclude(card_kind=KanbanCard.KIND_WORK_ORDER).get().work_order_id, self.work.pk)

    def test_create_with_hash_rechecks_machine_before_dispatch(self):
        """The existing reviewed-hash path and legacy confirmation agree."""
        proposal = self.create_proposal('work_order.create')
        self.revoke_machine()
        with self.assertRaises(proposals.ProposalNotFound):
            self.confirm(proposal, expected_preview_hash=proposal.preview_hash)
        self.assert_no_effect(proposal)

    def test_authorized_legacy_create_executes_once_and_replays_saved_receipt(self):
        """Fresh authorization keeps legitimate creation and exact receipt replay."""
        proposal = self.create_proposal('work_order.create')
        confirmed = self.confirm(proposal)
        self.revoke_machine()
        replay = self.confirm(proposal)
        self.assertEqual(confirmed.receipt, replay.receipt)
        self.assertEqual(WorkOrder.objects.count(), 2)
        self.assertEqual(WorkOrderCommand.objects.count(), 1)

    def test_direct_create_rejects_foreign_machine_before_writing(self):
        """The transactional service independently validates its machine input."""
        self.revoke_machine()
        with self.assertRaises(WorkOrderScopeError):
            scheduling.create_work_order(
                actor=self.actor,
                idempotency_key='direct-foreign-create',
                title='Forbidden direct Work',
                machine_id=self.machine.pk,
            )
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.assertFalse(WorkOrderCommand.objects.exists())
        self.assertFalse(WorkOrderEvent.objects.exists())

    def test_direct_create_replay_rechecks_current_machine_authority(self):
        """A command ledger replay cannot return an inaccessible Work result."""
        args = dict(
            actor=self.actor,
            idempotency_key='direct-replayed-create',
            title='Created before grant loss',
            machine_id=self.machine.pk,
        )
        scheduling.create_work_order(**args)
        self.revoke_machine()
        with self.assertRaises(WorkOrderScopeError):
            scheduling.create_work_order(**args)
        self.assertEqual(WorkOrder.objects.count(), 2)
        self.assertEqual(WorkOrderCommand.objects.count(), 1)
        self.assertEqual(WorkOrderEvent.objects.count(), 1)

    def test_direct_create_requires_its_own_action_role(self):
        """An authenticated Work viewer/editor is not necessarily a Work creator."""
        RuleSet.objects.filter(pk=self.rules.pk).update(can_add=False)
        with self.assertRaises(PermissionDenied):
            scheduling.create_work_order(
                actor=self.actor,
                idempotency_key='direct-no-add',
                title='Forbidden without add',
                machine_id=self.machine.pk,
            )
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.assertFalse(WorkOrderCommand.objects.exists())

    def test_direct_create_requires_view_even_if_add_remains(self):
        """A raw view revocation also closes the creation service boundary."""
        RuleSet.objects.filter(pk=self.rules.pk).update(can_view=False)
        with self.assertRaises(WorkOrderScopeError):
            scheduling.create_work_order(
                actor=self.actor,
                idempotency_key='direct-no-view',
                title='Forbidden without view',
                machine_id=self.machine.pk,
            )
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.assertFalse(WorkOrderCommand.objects.exists())

    def test_create_preview_requires_native_work_view(self):
        """An add flag alone cannot authorize a new machine Work preview."""
        RuleSet.objects.filter(pk=self.rules.pk).update(can_view=False)
        with self.assertRaises(proposals.ProposalNotFound):
            self.create_proposal('work_order.create')
        self.assertFalse(WorkOrderCommand.objects.exists())
        self.assertFalse(WorkOrderEvent.objects.exists())

    def test_repair_create_preview_requires_native_work_view(self):
        """Machine scope plus add cannot expose a repair preview without view."""
        RuleSet.objects.filter(pk=self.rules.pk).update(can_view=False)
        with self.assertRaises(proposals.ProposalNotFound):
            self.create_proposal('repair_work_package.create')
        self.assertFalse(WorkOrderCommand.objects.exists())
        self.assertFalse(RepairPacket.objects.exists())

    def test_direct_create_replay_rechecks_current_resulting_work(self):
        """Authorized original input cannot authorize a now foreign saved Work."""
        args = dict(
            actor=self.actor,
            idempotency_key='direct-reassigned-create',
            title='Created before Work reassignment',
            machine_id=self.machine.pk,
        )
        result = scheduling.create_work_order(**args)
        foreign_machine = AssetMachine.objects.create(name='Foreign reassigned result', client=self.foreign)
        WorkOrder.objects.filter(pk=result.work_order_id).update(machine=foreign_machine)
        with self.assertRaises(WorkOrderScopeError):
            scheduling.create_work_order(**args)
        self.assertEqual(WorkOrder.objects.count(), 2)
        self.assertEqual(WorkOrderCommand.objects.count(), 1)
        self.assertEqual(WorkOrderEvent.objects.count(), 1)
