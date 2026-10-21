"""P32 contract B holds for proposal previews and locked planning commands."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from aichat.models import ChatActionProposal, ProposalState
from aichat.services import proposals
from assets.models import AssetMachine, Client, ClientScopeGrant
from company.models import Company
from tasks.models import WorkOrder, WorkOrderCommand, WorkOrderEvent
from tasks.services import scheduling
from tasks.services.work_orders import WorkOrderScopeError
from users.models import RuleSet
from users.permissions import check_user_role


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class UpdateMachineScopeTests(TestCase):
    """Use native roles and grants; shared Work is never machine authority."""

    def setUp(self):
        """Give a normal editor client A and shared customer Work on client B."""
        self.actor = get_user_model().objects.create_user(username='p32-update-editor')
        group = Group.objects.create(name='P32 update editors')
        RuleSet.objects.update_or_create(
            group=group,
            name='work_order',
            defaults={'can_view': True, 'can_change': True},
        )
        self.actor.groups.add(group)
        self.own_client = Client.objects.create(name='Update A', code='p32-update-a')
        self.foreign_client = Client.objects.create(
            name='Update B', code='p32-update-b'
        )
        ClientScopeGrant.objects.create(user=self.actor, client=self.own_client)
        self.machine = AssetMachine.objects.create(
            name='Authorized update pump', client=self.own_client
        )
        self.foreign_machine = AssetMachine.objects.create(
            name='Foreign update pump', client=self.foreign_client
        )
        self.unresolved_machine = AssetMachine.objects.create(name='Unresolved pump')
        customer = Company.objects.create(name='Shared update customer', is_customer=True)
        self.work = WorkOrder.objects.create(
            title='Shared customer Work', customer=customer, machine=self.foreign_machine
        )

    def preview(self, machine_id=None, *, fields=None, key='machine-update'):
        """Prepare the same update intent used by the chat proposal API."""
        return proposals.create_proposal(
            owner=self.actor,
            scope_key=f'client:{self.own_client.pk}',
            scope_hash='a' * 64,
            action_type='work_order.update',
            work_order_id=self.work.pk,
            reason='Replace the recorded machine',
            idempotency_key=key,
            policy_version='p32-security-review',
            intent={'fields': fields if fields is not None else {'machine_id': machine_id}},
        )

    def update(self, *, fields, work=None, key='direct-update', version=None):
        """Invoke the locked service without any endpoint or proposal guard."""
        work = work or self.work
        return scheduling.update_work_order_plan(
            work_order_id=work.pk,
            actor=self.actor,
            expected_version=work.lifecycle_version if version is None else version,
            idempotency_key=key,
            fields=fields,
        )

    def assert_unchanged(self, machine_id=None):
        """A refusal creates no command, audit event, version bump or assignment."""
        self.work.refresh_from_db()
        self.assertEqual(
            self.work.machine_id,
            self.foreign_machine.pk if machine_id is None else machine_id,
        )
        self.assertEqual(self.work.lifecycle_version, 1)
        self.assertFalse(WorkOrderCommand.objects.exists())
        self.assertFalse(WorkOrderEvent.objects.exists())

    def test_preview_hides_foreign_missing_and_unresolved_machine_equally(self):
        """No preview or proposal is saved for a guessed unavailable target."""
        messages = []
        for machine_id in (
            self.foreign_machine.pk,
            self.unresolved_machine.pk,
            99999999,
        ):
            # Start with machineless shared Work so all cases are reassignment.
            self.work.machine = None
            self.work.save(update_fields=['machine'])
            with self.subTest(machine_id=machine_id):
                with self.assertRaises(proposals.ProposalNotFound) as denied:
                    self.preview(machine_id, key=f'guess-{machine_id}')
                messages.append(str(denied.exception))
        self.assertEqual(messages, ['no such machine'] * 3)
        self.assertFalse(ChatActionProposal.objects.exists())
        self.assertFalse(WorkOrderCommand.objects.exists())

    def test_direct_service_hides_foreign_missing_and_unresolved_machine_equally(self):
        """The command itself rejects newly attached targets before saving."""
        self.work.machine = None
        self.work.save(update_fields=['machine'])
        messages = []
        for machine_id in (
            self.foreign_machine.pk,
            self.unresolved_machine.pk,
            99999999,
        ):
            with self.subTest(machine_id=machine_id):
                with self.assertRaises(WorkOrderScopeError) as denied:
                    self.update(fields={'machine_id': machine_id}, key=f'guess-{machine_id}')
                messages.append(str(denied.exception))
                self.work.refresh_from_db()
                self.assertIsNone(self.work.machine_id)
                self.assertEqual(self.work.lifecycle_version, 1)
                self.assertFalse(WorkOrderCommand.objects.exists())
                self.assertFalse(WorkOrderEvent.objects.exists())
        self.assertEqual(len(set(messages)), 1)

    def test_service_checks_original_work_before_moving_it_into_actor_scope(self):
        """A permitted destination never authorizes editing inaccessible Work."""
        foreign_work = WorkOrder.objects.create(
            title='Foreign machine-only Work', machine=self.foreign_machine
        )
        with self.assertRaises(WorkOrderScopeError):
            self.update(work=foreign_work, fields={'machine_id': self.machine.pk})
        foreign_work.refresh_from_db()
        self.assertEqual(foreign_work.machine_id, self.foreign_machine.pk)
        self.assertEqual(foreign_work.lifecycle_version, 1)
        self.assertFalse(WorkOrderCommand.objects.exists())

    def test_reviewed_preview_reauthorizes_machine_after_grant_revocation(self):
        """The reviewed hash cannot preserve a revoked proposed-machine grant."""
        proposal = self.preview(self.machine.pk)
        ClientScopeGrant.objects.filter(user=self.actor).delete()
        with self.assertRaises(proposals.ProposalNotFound):
            proposals.confirm_proposal(
                owner=self.actor,
                scope_hash=proposal.scope_hash,
                proposal_id=proposal.pk,
                expected_preview_hash=proposal.preview_hash,
            )
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, ProposalState.PROPOSED)
        self.assert_unchanged()

    def test_confirmation_without_preview_hash_still_rechecks_machine_scope(self):
        """Fresh proposal authorization protects confirmations without hash checks."""
        proposal = self.preview(self.machine.pk)
        ClientScopeGrant.objects.filter(user=self.actor).delete()
        with self.assertRaises(proposals.ProposalNotFound):
            proposals.confirm_proposal(
                owner=self.actor, scope_hash=proposal.scope_hash, proposal_id=proposal.pk
            )
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, ProposalState.PROPOSED)
        self.assert_unchanged()

    def test_authorized_machine_assignment_succeeds_once(self):
        """An editor may attach an authorized machine and replay the receipt."""
        proposal = self.preview(self.machine.pk)
        confirmed = proposals.confirm_proposal(
            owner=self.actor,
            scope_hash=proposal.scope_hash,
            proposal_id=proposal.pk,
            expected_preview_hash=proposal.preview_hash,
        )
        replay = proposals.confirm_proposal(
            owner=self.actor,
            scope_hash=proposal.scope_hash,
            proposal_id=proposal.pk,
            expected_preview_hash=proposal.preview_hash,
        )
        self.work.refresh_from_db()
        self.assertEqual(self.work.machine_id, self.machine.pk)
        self.assertEqual(self.work.lifecycle_version, 2)
        self.assertEqual(confirmed.receipt, replay.receipt)
        self.assertEqual(WorkOrderCommand.objects.count(), 1)
        self.assertEqual(WorkOrderEvent.objects.count(), 1)

    def test_shared_metadata_edit_retains_existing_foreign_machine_identity(self):
        """O24 permits editing Work without changing its existing machine."""
        ClientScopeGrant.objects.filter(user=self.actor).delete()
        proposal = self.preview(
            fields={'title': 'Reviewed Work title', 'machine_id': self.foreign_machine.pk}
        )
        proposals.confirm_proposal(
            owner=self.actor,
            scope_hash=proposal.scope_hash,
            proposal_id=proposal.pk,
            expected_preview_hash=proposal.preview_hash,
        )
        self.work.refresh_from_db()
        self.assertEqual(self.work.title, 'Reviewed Work title')
        self.assertEqual(self.work.machine_id, self.foreign_machine.pk)
        self.assertEqual(self.work.lifecycle_version, 2)

    def test_machine_reassignment_rechecks_current_machine_client(self):
        """Moving the proposed machine to a foreign client invalidates preview."""
        proposal = self.preview(self.machine.pk)
        self.machine.client = self.foreign_client
        self.machine.save(update_fields=['client'])
        with self.assertRaises(proposals.ProposalNotFound):
            proposals.confirm_proposal(
                owner=self.actor,
                scope_hash=proposal.scope_hash,
                proposal_id=proposal.pk,
                expected_preview_hash=proposal.preview_hash,
            )
        self.assert_unchanged()

    def test_direct_replay_reauthorizes_current_machine_only_work(self):
        """A recorded result cannot bypass current Work authorization on replay."""
        self.work.customer = None
        self.work.machine = self.machine
        self.work.save(update_fields=['customer', 'machine'])
        fields = {'title': 'Previously authorized edit'}
        self.update(fields=fields, version=1)
        ClientScopeGrant.objects.filter(user=self.actor).delete()
        with self.assertRaises(WorkOrderScopeError):
            self.update(fields=fields, version=1)
        self.work.refresh_from_db()
        self.assertEqual(self.work.lifecycle_version, 2)
        self.assertEqual(WorkOrderCommand.objects.count(), 1)
        self.assertEqual(WorkOrderEvent.objects.count(), 1)

    def test_editor_can_detach_existing_machine_and_keep_shared_work(self):
        """Clearing a nullable machine keeps the pre-existing shared Work rule."""
        ClientScopeGrant.objects.filter(user=self.actor).delete()
        proposal = self.preview(fields={'machine_id': None})
        proposals.confirm_proposal(
            owner=self.actor,
            scope_hash=proposal.scope_hash,
            proposal_id=proposal.pk,
            expected_preview_hash=proposal.preview_hash,
        )
        self.work.refresh_from_db()
        self.assertIsNone(self.work.machine_id)
        self.assertEqual(self.work.lifecycle_version, 2)

    def test_direct_update_requires_change_role(self):
        """Work visibility alone never grants the planning command's action."""
        viewer = get_user_model().objects.create_user(username='p32-update-viewer')
        group = Group.objects.create(name='P32 update viewers')
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        viewer.groups.add(group)
        self.actor = viewer
        with self.assertRaises(PermissionDenied):
            self.update(fields={'title': 'Forbidden viewer update'})
        self.assert_unchanged()

    def test_shared_work_requires_view_even_with_change_role(self):
        """A raw view revocation must fail closed even if change remains granted."""
        editor = get_user_model().objects.create_user(username='p32-update-no-view')
        group = Group.objects.create(name='P32 update without view')
        rules, _created = RuleSet.objects.update_or_create(
            group=group,
            name='work_order',
            defaults={'can_view': True, 'can_change': True},
        )
        editor.groups.add(group)
        # Native RuleSet.save implies view from change. Deliberately represent a
        # raw database revocation instead, proving the scope gate independently
        # rejects a stored role matrix that ordinary checkbox saves normalize.
        RuleSet.objects.filter(pk=rules.pk).update(can_view=False)
        self.assertTrue(check_user_role(editor, 'work_order', 'change'))
        self.assertFalse(check_user_role(editor, 'work_order', 'view'))
        self.actor = editor
        with self.assertRaises(WorkOrderScopeError):
            self.update(fields={'title': 'Forbidden without view'})
        self.assert_unchanged()
