"""WS7: governed proposal rail — allow-list, exactly-once, real receipts.

Runs under the full InvenTree settings (PostgreSQL invoke runner); it is
skipped in the minimal aichat-only settings because it exercises the real
canonical work-order command service.
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

from tasks.models import KanbanCard, WorkOrder, WorkOrderLifecycle, WorkOrderType
from tasks.scope import MaintenanceScope

from aichat.models import ChatActionProposal, ProposalState
from aichat.services import proposals as svc
from assets.models import AssetMachine
from company.models import Company
from users.models import ApiToken


class ProposalRailTestCase(TestCase):
    """Shared fixture: one scoped superuser and one in-progress work order."""

    @classmethod
    def setUpTestData(cls):
        """SetUpTestData."""
        cls.customer = Company.objects.create(
            name='Proposal Cust', is_customer=True
        )
        cls.actor = get_user_model().objects.create_superuser(
            username='proposal-sup', email='p@example.com', password='pw'
        )
        cls.machine = AssetMachine.objects.create(
            name='Press 7', customer=cls.customer
        )

    def setUp(self):
        """SetUp."""
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.work_order = WorkOrder.objects.create(
            title='Hold me',
            status=WorkOrder.STATUS_REVIEW,
            priority=WorkOrder.PRIORITY_MEDIUM,
            customer=self.customer,
            machine=self.machine,
            assigned_to=self.actor,
            work_order_type=WorkOrderType.PREVENTIVE,
            lifecycle_status=WorkOrderLifecycle.IN_PROGRESS,
        )

    def _create(self, action='work_order.hold', key='intent-1', **over):
        """Create."""
        params = {
            'owner': self.actor,
            'scope_key': f'customer:{self.customer.pk}',
            'scope_hash': 'a' * 64,
            'action_type': action,
            'work_order_id': self.work_order.pk,
            'reason': 'Spoken request: put this on hold.',
            'idempotency_key': key,
            'policy_version': 'test-v1',
        }
        params.update(over)
        return svc.create_proposal(**params)


class ProposalServiceTests(ProposalRailTestCase):
    """ProposalServiceTests."""
    def test_allow_list_is_exactly_the_governed_actions(self):
        """The allow list is the complete set of governed executable actions."""
        self.assertEqual(
            svc.allowed_actions(),
            (
                'dependency.create',
                'dependency.delete',
                'repair_work_package.create',
                'schedule.optimize',
                'work_order.assign',
                'work_order.cancel',
                'work_order.create',
                'work_order.create_child',
                'work_order.delete',
                'work_order.generate_procurement',
                'work_order.hold',
                'work_order.resize',
                'work_order.resume',
                'work_order.schedule',
                'work_order.transition',
                'work_order.update',
            ),
        )
        with self.assertRaises(svc.CapabilityDenied):
            self._create(action='work_order.start')
        with self.assertRaises(svc.CapabilityDenied):
            self._create(action='stock.consume')

    def test_create_snapshots_server_derived_preview_and_version(self):
        """Create snapshots server derived preview and version."""
        proposal = self._create()
        self.assertEqual(proposal.state, ProposalState.PROPOSED)
        self.assertEqual(
            proposal.target_version, self.work_order.lifecycle_version
        )
        self.assertEqual(proposal.preview['current_status'], 'in_progress')
        self.assertEqual(proposal.preview['resulting_status'], 'on_hold')
        self.assertIn('does not change any safety status', proposal.preview['warning'])

    def test_create_replays_exactly_and_conflicts_on_changed_intent(self):
        """Create replays exactly and conflicts on changed intent."""
        first = self._create(key='same-key')
        replay = self._create(key='same-key')
        self.assertEqual(first.id, replay.id)
        with self.assertRaises(svc.ProposalStateConflict):
            self._create(action='work_order.resume', key='same-key')
        with self.assertRaises(svc.ProposalStateConflict):
            self._create(reason='Different hold reason.', key='same-key')
        with self.assertRaises(svc.ProposalStateConflict):
            self._create(scope_hash='b' * 64, key='same-key')

    def test_confirm_executes_the_canonical_hold_command_once(self):
        """Confirm executes the canonical hold command once."""
        proposal = self._create()
        confirmed = svc.confirm_proposal(
            owner=self.actor,
            scope_hash=proposal.scope_hash,
            proposal_id=proposal.id,
        )
        self.work_order.refresh_from_db()
        self.assertEqual(
            self.work_order.lifecycle_status, WorkOrderLifecycle.ON_HOLD
        )
        self.assertEqual(confirmed.state, ProposalState.EXECUTED)
        self.assertEqual(confirmed.receipt['command'], 'hold')
        self.assertEqual(confirmed.receipt['lifecycle_status'], 'on_hold')

        version_after_first = self.work_order.lifecycle_version
        replay = svc.confirm_proposal(
            owner=self.actor,
            scope_hash=proposal.scope_hash,
            proposal_id=proposal.id,
        )
        self.work_order.refresh_from_db()
        self.assertEqual(replay.receipt, confirmed.receipt)
        self.assertEqual(
            self.work_order.lifecycle_version,
            version_after_first,
            'replaying a confirmation must not execute a second effect',
        )

    def test_stale_target_version_fails_revalidation_without_effect(self):
        """Stale target version fails revalidation without effect."""
        proposal = self._create()
        # The work order moves on before the user confirms.
        from tasks.services.work_orders import hold_work_order

        hold_work_order(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='external-hold',
            reason='someone else held it first',
        )
        self.work_order.refresh_from_db()
        held_version = self.work_order.lifecycle_version

        with self.assertRaises(svc.ProposalRevalidationFailed):
            svc.confirm_proposal(
                owner=self.actor,
                scope_hash=proposal.scope_hash,
                proposal_id=proposal.id,
            )
        proposal.refresh_from_db()
        self.work_order.refresh_from_db()
        self.assertEqual(proposal.state, ProposalState.FAILED)
        self.assertEqual(proposal.failure_code, 'PROPOSAL_REVALIDATION_FAILED')
        self.assertEqual(self.work_order.lifecycle_version, held_version)

    def test_expired_proposal_cannot_execute(self):
        """Expired proposal cannot execute."""
        proposal = self._create()
        ChatActionProposal.objects.filter(id=proposal.id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        with self.assertRaises(svc.ProposalExpired):
            svc.confirm_proposal(
                owner=self.actor,
                scope_hash=proposal.scope_hash,
                proposal_id=proposal.id,
            )
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, ProposalState.EXPIRED)
        self.work_order.refresh_from_db()
        self.assertEqual(
            self.work_order.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS
        )

    def test_reject_is_idempotent_and_blocks_confirmation(self):
        """Reject is idempotent and blocks confirmation."""
        proposal = self._create()
        svc.reject_proposal(
            owner=self.actor,
            scope_hash=proposal.scope_hash,
            proposal_id=proposal.id,
        )
        svc.reject_proposal(
            owner=self.actor,
            scope_hash=proposal.scope_hash,
            proposal_id=proposal.id,
        )
        with self.assertRaises(svc.ProposalStateConflict):
            svc.confirm_proposal(
                owner=self.actor,
                scope_hash=proposal.scope_hash,
                proposal_id=proposal.id,
            )

    def test_cross_owner_access_is_indistinguishable_from_missing(self):
        """Cross owner access is indistinguishable from missing."""
        proposal = self._create()
        stranger = get_user_model().objects.create_user(
            username='stranger', password='pw'
        )
        for operation in (svc.get_owned_proposal, svc.reject_proposal):
            with self.assertRaises(svc.ProposalNotFound):
                operation(
                    owner=stranger,
                    scope_hash=proposal.scope_hash,
                    proposal_id=proposal.id,
                )
        with self.assertRaises(svc.ProposalNotFound):
            svc.confirm_proposal(
                owner=stranger,
                scope_hash=proposal.scope_hash,
                proposal_id=proposal.id,
            )

    def test_resume_round_trip_through_the_rail(self):
        """Resume round trip through the rail."""
        hold = self._create(key='hold-key')
        svc.confirm_proposal(
            owner=self.actor,
            scope_hash=hold.scope_hash,
            proposal_id=hold.id,
        )
        self.work_order.refresh_from_db()
        resume = self._create(
            action='work_order.resume',
            key='resume-key',
            reason='Spoken request: resume the job.',
        )
        confirmed = svc.confirm_proposal(
            owner=self.actor,
            scope_hash=resume.scope_hash,
            proposal_id=resume.id,
        )
        self.work_order.refresh_from_db()
        self.assertEqual(confirmed.state, ProposalState.EXECUTED)
        self.assertEqual(
            self.work_order.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS
        )

    def test_expiry_sweep_marks_only_stale_pending_rows(self):
        """Expiry sweep marks only stale pending rows."""
        stale = self._create(key='stale')
        fresh = self._create(key='fresh')
        ChatActionProposal.objects.filter(id=stale.id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        swept = svc.expire_stale_proposals()
        self.assertEqual(swept, 1)
        stale.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(stale.state, ProposalState.EXPIRED)
        self.assertEqual(fresh.state, ProposalState.PROPOSED)


class SchedulingProposalTests(ProposalRailTestCase):
    """WS7 (Phase 6c): the scheduling actions route the same governed rail.

    Each action creates a preview from a fresh read, then confirmation
    dispatches the identical ``tasks.services`` command the board UI uses —
    same version guard, same audit event, same exactly-once receipt.
    """

    def test_schedule_action_moves_the_window_through_the_rail(self):
        """A schedule proposal dispatches the canonical schedule command."""
        proposal = self._create(
            action='work_order.schedule',
            key='sched-1',
            reason='Spoken request: move this to Monday morning.',
            intent={
                'scheduled_start': '2026-08-03T09:00:00',
                'scheduled_end': '2026-08-03T13:00:00',
            },
        )
        self.assertEqual(proposal.preview['proposed_start'], '2026-08-03T09:00:00')
        confirmed = svc.confirm_proposal(
            owner=self.actor, scope_hash=proposal.scope_hash, proposal_id=proposal.id
        )
        self.assertEqual(confirmed.state, ProposalState.EXECUTED)
        self.assertEqual(confirmed.receipt['command'], 'schedule')
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.scheduled_start.hour, 9)
        self.assertEqual(self.work_order.scheduled_end.hour, 13)

    def test_resize_action_sets_the_estimate_through_the_rail(self):
        """A resize proposal dispatches the canonical resize command."""
        proposal = self._create(
            action='work_order.resize',
            key='resize-1',
            reason='Spoken request: this really takes three hours.',
            intent={'estimated_minutes': 180},
        )
        self.assertEqual(proposal.preview['proposed_estimated_minutes'], 180)
        confirmed = svc.confirm_proposal(
            owner=self.actor, scope_hash=proposal.scope_hash, proposal_id=proposal.id
        )
        self.assertEqual(confirmed.receipt['command'], 'resize')
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.estimated_minutes, 180)

    def test_update_action_edits_a_plan_field_through_the_rail(self):
        """An update proposal dispatches the canonical plan-update command."""
        proposal = self._create(
            action='work_order.update',
            key='update-1',
            reason='Spoken request: bump the priority.',
            intent={'fields': {'priority': WorkOrder.PRIORITY_HIGH}},
        )
        self.assertEqual(
            proposal.preview['changes']['priority']['to'], WorkOrder.PRIORITY_HIGH
        )
        confirmed = svc.confirm_proposal(
            owner=self.actor, scope_hash=proposal.scope_hash, proposal_id=proposal.id
        )
        self.assertEqual(confirmed.receipt['command'], 'update_plan')
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.priority, WorkOrder.PRIORITY_HIGH)

    def test_assign_action_reassigns_through_the_rail(self):
        """An assign proposal dispatches the canonical assign command."""
        other = get_user_model().objects.create_user(username='tech-2', password='pw')
        proposal = self._create(
            action='work_order.assign',
            key='assign-1',
            reason='Spoken request: give this to tech-2.',
            intent={'assigned_to': other.pk},
        )
        self.assertEqual(proposal.preview['proposed_assigned_to_id'], other.pk)
        confirmed = svc.confirm_proposal(
            owner=self.actor, scope_hash=proposal.scope_hash, proposal_id=proposal.id
        )
        self.assertEqual(confirmed.receipt['command'], 'assign')
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.assigned_to_id, other.pk)

    def test_delete_action_removes_the_card_and_keeps_a_record(self):
        """A delete proposal dispatches the governed delete and yields a record."""
        from tasks.models import WorkOrderDeletionRecord

        work_order_id = self.work_order.pk
        proposal = self._create(
            action='work_order.delete',
            key='delete-1',
            reason='Spoken request: this was created by mistake.',
        )
        self.assertTrue(proposal.preview['irreversible'])
        self.assertEqual(proposal.preview['confirm_phrase'], 'confirm delete')
        # Irreversible: a confirm without the strict phrase is refused.
        with self.assertRaises(svc.StrictConfirmationRequired):
            svc.confirm_proposal(
                owner=self.actor, scope_hash=proposal.scope_hash,
                proposal_id=proposal.id,
            )
        self.assertTrue(WorkOrder.objects.filter(pk=work_order_id).exists())
        confirmed = svc.confirm_proposal(
            owner=self.actor, scope_hash=proposal.scope_hash, proposal_id=proposal.id,
            confirm_phrase='confirm delete',
        )
        self.assertEqual(confirmed.state, ProposalState.EXECUTED)
        self.assertEqual(confirmed.receipt['command'], 'delete')
        self.assertFalse(WorkOrder.objects.filter(pk=work_order_id).exists())
        self.assertTrue(
            WorkOrderDeletionRecord.objects.filter(work_order_pk=work_order_id).exists()
        )

    def test_stale_version_fails_a_scheduling_action_without_effect(self):
        """A version drift between proposal and confirm fails, leaving no effect."""
        proposal = self._create(
            action='work_order.schedule',
            key='sched-stale',
            intent={
                'scheduled_start': '2026-08-03T09:00:00',
                'scheduled_end': '2026-08-03T13:00:00',
            },
        )
        # The card moves on before the user confirms.
        from tasks.services.work_orders import hold_work_order

        hold_work_order(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='drift-hold',
            reason='held first',
        )
        with self.assertRaises(svc.ProposalRevalidationFailed):
            svc.confirm_proposal(
                owner=self.actor,
                scope_hash=proposal.scope_hash,
                proposal_id=proposal.id,
            )
        proposal.refresh_from_db()
        self.work_order.refresh_from_db()
        self.assertEqual(proposal.state, ProposalState.FAILED)
        self.assertIsNone(self.work_order.scheduled_start)

    def test_confirm_replay_does_not_reschedule_twice(self):
        """Replaying a confirmed schedule returns the receipt with no second write."""
        proposal = self._create(
            action='work_order.schedule',
            key='sched-replay',
            intent={
                'scheduled_start': '2026-08-03T09:00:00',
                'scheduled_end': '2026-08-03T13:00:00',
            },
        )
        first = svc.confirm_proposal(
            owner=self.actor, scope_hash=proposal.scope_hash, proposal_id=proposal.id
        )
        self.work_order.refresh_from_db()
        version_after = self.work_order.lifecycle_version
        replay = svc.confirm_proposal(
            owner=self.actor, scope_hash=proposal.scope_hash, proposal_id=proposal.id
        )
        self.work_order.refresh_from_db()
        self.assertEqual(replay.receipt, first.receipt)
        self.assertEqual(self.work_order.lifecycle_version, version_after)

    def test_bad_intent_datetime_is_rejected_at_creation(self):
        """A malformed intent datetime fails create, before any row is written."""
        with self.assertRaises(svc.ProposalError):
            self._create(
                action='work_order.schedule',
                key='sched-bad',
                intent={'scheduled_start': 'not-a-datetime'},
            )
        self.assertFalse(
            ChatActionProposal.objects.filter(idempotency_key='sched-bad').exists()
        )


class GapClosingProposalTests(ProposalRailTestCase):
    """Phase 6e: the remaining board mutations route the same governed rail.

    Closes the §5.13 parity gap — create, create_child, generate_procurement,
    dependency create/delete, cancel, transition and bulk optimize.
    """

    def _confirm(self, proposal):
        return svc.confirm_proposal(
            owner=self.actor, scope_hash=proposal.scope_hash, proposal_id=proposal.id
        )

    def _set_lifecycle(self, status):
        self.work_order.lifecycle_status = status
        self.work_order.save(update_fields=['lifecycle_status'])

    def test_cancel_action_cancels_through_the_rail(self):
        """A cancel proposal dispatches the canonical cancel command."""
        self._set_lifecycle(WorkOrderLifecycle.PLANNED)
        proposal = self._create(action='work_order.cancel', key='cancel-1')
        self.assertEqual(proposal.preview['resulting_status'], 'canceled')
        confirmed = self._confirm(proposal)
        self.assertEqual(confirmed.receipt['command'], 'cancel')
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.CANCELED)

    def test_transition_action_moves_the_lifecycle_through_the_rail(self):
        """A transition proposal dispatches the canonical transition command."""
        self._set_lifecycle(WorkOrderLifecycle.DRAFT)
        proposal = self._create(
            action='work_order.transition',
            key='trans-1',
            intent={'to_status': WorkOrderLifecycle.PLANNED.value},
        )
        self.assertEqual(
            proposal.preview['resulting_status'], WorkOrderLifecycle.PLANNED.value
        )
        confirmed = self._confirm(proposal)
        self.assertEqual(confirmed.receipt['command'], 'transition')
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.PLANNED)

    def test_create_action_creates_a_card_through_the_rail(self):
        """A create proposal has no target and creates a fresh card on confirm."""
        before = WorkOrder.objects.count()
        proposal = self._create(
            action='work_order.create',
            key='create-1',
            reason='Spoken: open a corrective job for the press.',
            intent={
                'title': 'Fresh WO',
                'machine_id': self.machine.pk,
                'work_order_type': WorkOrderType.CORRECTIVE,
                'priority': WorkOrder.PRIORITY_LOW,
            },
        )
        self.assertIsNone(proposal.target_work_order_id)
        self.assertEqual(proposal.preview['proposed_title'], 'Fresh WO')
        confirmed = self._confirm(proposal)
        self.assertEqual(confirmed.receipt['command'], 'create')
        self.assertEqual(WorkOrder.objects.count(), before + 1)
        created = WorkOrder.objects.get(pk=confirmed.receipt['work_order_id'])
        self.assertEqual(created.title, 'Fresh WO')
        self.assertEqual(created.machine_id, self.machine.pk)

    def test_create_rejects_a_machine_outside_scope(self):
        """Create is scope-bound: an out-of-scope machine is not disclosed."""
        other = Company.objects.create(name='Other Co', is_customer=True)
        other_machine = AssetMachine.objects.create(name='Secret', customer=other)
        with self.assertRaises(svc.ProposalNotFound):
            self._create(
                action='work_order.create',
                key='create-bad',
                intent={'title': 'X', 'machine_id': other_machine.pk},
            )

    def test_create_child_action(self):
        """A create-child proposal targets the parent and makes a child."""
        proposal = self._create(
            action='work_order.create_child',
            key='child-1',
            intent={'title': 'Subtask A', 'card_kind': KanbanCard.KIND_SUBTASK},
        )
        self.assertEqual(proposal.preview['work_order_id'], self.work_order.pk)
        confirmed = self._confirm(proposal)
        child = KanbanCard.objects.get(pk=confirmed.receipt['card_id'])
        self.assertEqual(child.work_order_id, self.work_order.pk)
        self.assertEqual(child.title, 'Subtask A')

    def test_generate_procurement_action_with_no_parts_is_a_noop_child(self):
        """Generate-procurement returns a null child when nothing is needed."""
        proposal = self._create(
            action='work_order.generate_procurement', key='proc-1'
        )
        confirmed = self._confirm(proposal)
        self.assertEqual(confirmed.receipt['command'], 'generate_procurement')
        self.assertIsNone(confirmed.receipt['child_id'])

    def test_dependency_create_and_delete_actions(self):
        """Dependency create then delete both route the governed rail."""
        from tasks.models import WorkOrderDependency

        successor = WorkOrder.objects.create(
            title='Successor', status=WorkOrder.STATUS_REVIEW,
            priority=WorkOrder.PRIORITY_MEDIUM, customer=self.customer,
            machine=self.machine, lifecycle_status=WorkOrderLifecycle.IN_PROGRESS,
        )
        create = self._create(
            action='dependency.create',
            key='dep-create',
            work_order_id=successor.pk,
            intent={'predecessor_id': self.work_order.pk, 'dependency_type': 'FS'},
        )
        confirmed = self._confirm(create)
        dep_id = confirmed.receipt['dependency_id']
        self.assertTrue(WorkOrderDependency.objects.filter(pk=dep_id).exists())

        delete = self._create(
            action='dependency.delete',
            key='dep-delete',
            work_order_id=successor.pk,
            intent={'dependency_id': dep_id},
        )
        removed = self._confirm(delete)
        self.assertTrue(removed.receipt['removed'])
        self.assertFalse(WorkOrderDependency.objects.filter(pk=dep_id).exists())

    @override_settings(USE_TZ=True)
    def test_optimize_action_plans_and_applies_atomically(self):
        """A bulk optimize re-plans deterministically and applies through the rail."""
        self.work_order.estimated_minutes = 60
        self.work_order.scheduled_start = None
        self.work_order.scheduled_end = None
        self.work_order.save()
        second = WorkOrder.objects.create(
            title='Second', status=WorkOrder.STATUS_REVIEW,
            priority=WorkOrder.PRIORITY_MEDIUM, customer=self.customer,
            machine=self.machine, estimated_minutes=60,
            lifecycle_status=WorkOrderLifecycle.IN_PROGRESS,
        )
        proposal = self._create(
            action='schedule.optimize',
            key='opt-1',
            intent={'candidate_ids': [self.work_order.pk, second.pk]},
        )
        self.assertEqual(proposal.preview['candidate_count'], 2)
        confirmed = self._confirm(proposal)
        self.assertEqual(confirmed.receipt['command'], 'optimize')
        applied_ids = {row['work_order_id'] for row in confirmed.receipt['applied']}
        self.assertIn(self.work_order.pk, applied_ids)
        self.work_order.refresh_from_db()
        self.assertIsNotNone(self.work_order.scheduled_start)

    def test_optimize_requires_candidates(self):
        """An optimize with no candidates fails at creation, before any row."""
        with self.assertRaises(svc.ProposalError):
            self._create(
                action='schedule.optimize', key='opt-empty', intent={'candidate_ids': []}
            )


class PermissionParityTests(ProposalRailTestCase):
    """§5.13 permission parity: the rail enforces the UI's RBAC role."""

    def test_scheduling_action_requires_the_change_role(self):
        """A scoped actor without work_order.change cannot create a schedule proposal."""
        plain = get_user_model().objects.create_user(username='no-role', password='pw')
        plain.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        with self.assertRaises(svc.CapabilityDenied):
            svc.create_proposal(
                owner=plain,
                scope_key=f'customer:{self.customer.pk}',
                scope_hash='a' * 64,
                action_type='work_order.schedule',
                work_order_id=self.work_order.pk,
                reason='reschedule please',
                idempotency_key='no-role-sched',
                policy_version='test-v1',
                intent={'scheduled_start': '2026-08-03T09:00:00'},
            )

    def test_delete_requires_the_delete_role(self):
        """Delete needs work_order.delete specifically, not merely change."""
        plain = get_user_model().objects.create_user(username='no-del', password='pw')
        plain.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        with self.assertRaises(svc.CapabilityDenied):
            svc.create_proposal(
                owner=plain,
                scope_key=f'customer:{self.customer.pk}',
                scope_hash='a' * 64,
                action_type='work_order.delete',
                work_order_id=self.work_order.pk,
                reason='delete please',
                idempotency_key='no-del-1',
                policy_version='test-v1',
            )


class StrictConfirmationTests(ProposalRailTestCase):
    """§5.3 point 3: irreversible actions demand an exact phrase on the text rail."""

    def _delete_proposal(self, key='del-strict'):
        return self._create(
            action='work_order.delete', key=key, reason='remove it'
        )

    def test_wrong_phrase_is_refused_and_writes_nothing(self):
        """A mismatched phrase refuses without deleting."""
        proposal = self._delete_proposal()
        with self.assertRaises(svc.StrictConfirmationRequired):
            svc.confirm_proposal(
                owner=self.actor, scope_hash=proposal.scope_hash,
                proposal_id=proposal.id, confirm_phrase='yes',
            )
        self.assertTrue(WorkOrder.objects.filter(pk=self.work_order.pk).exists())
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, ProposalState.PROPOSED)

    def test_phrase_is_case_and_whitespace_insensitive(self):
        """The exact phrase matches regardless of case/surrounding whitespace."""
        proposal = self._delete_proposal(key='del-case')
        confirmed = svc.confirm_proposal(
            owner=self.actor, scope_hash=proposal.scope_hash,
            proposal_id=proposal.id, confirm_phrase='  Confirm Delete  ',
        )
        self.assertEqual(confirmed.state, ProposalState.EXECUTED)

    def test_reversible_action_needs_no_phrase(self):
        """A reversible action is unaffected by the strict-phrase gate."""
        proposal = self._create(action='work_order.hold', key='hold-nostrict')
        confirmed = svc.confirm_proposal(
            owner=self.actor, scope_hash=proposal.scope_hash, proposal_id=proposal.id
        )
        self.assertEqual(confirmed.state, ProposalState.EXECUTED)


def _test_scope_resolver(actor):
    """Deployment-seam resolver used by the API tests."""
    from company.models import Company

    customer = Company.objects.filter(name='Proposal Cust').first()
    if customer is None:
        return set()
    return {MaintenanceScope(customer_id=customer.pk, site_key=None)}


class ProposalApiTests(ProposalRailTestCase):
    """Session-authenticated REST rail over the same service."""

    RESOLVER = f'{__name__}._test_scope_resolver'

    def _client(self):
        """Client."""
        self.client.force_login(self.actor)
        return self.client

    def test_create_confirm_and_receipt_over_http(self):
        """Create confirm and receipt over http."""
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=self.RESOLVER):
            client = self._client()
            created = client.post(
                '/api/aichat/proposals/',
                {
                    'action_type': 'work_order.hold',
                    'work_order_id': self.work_order.pk,
                    'reason': 'hold it please',
                },
                content_type='application/json',
            )
            self.assertEqual(created.status_code, 201, created.content)
            body = created.json()
            self.assertEqual(body['state'], 'proposed')

            confirmed = client.post(
                f"/api/aichat/proposals/{body['id']}/confirm/"
            )
            self.assertEqual(confirmed.status_code, 200, confirmed.content)
            receipt = confirmed.json()['receipt']
            self.assertEqual(receipt['command'], 'hold')
            self.work_order.refresh_from_db()
            self.assertEqual(
                self.work_order.lifecycle_status, WorkOrderLifecycle.ON_HOLD
            )

    def test_delete_over_http_requires_the_strict_phrase(self):
        """An irreversible delete is 400 without the phrase, 200 with it."""
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=self.RESOLVER):
            client = self._client()
            created = client.post(
                '/api/aichat/proposals/',
                {
                    'action_type': 'work_order.delete',
                    'work_order_id': self.work_order.pk,
                    'reason': 'remove it',
                },
                content_type='application/json',
            )
            self.assertEqual(created.status_code, 201, created.content)
            pid = created.json()['id']
            self.assertEqual(
                created.json()['preview']['confirm_phrase'], 'confirm delete'
            )

            missing = client.post(f'/api/aichat/proposals/{pid}/confirm/')
            self.assertEqual(missing.status_code, 400, missing.content)
            self.assertEqual(
                missing.json()['error'], 'STRICT_CONFIRMATION_REQUIRED'
            )
            self.assertTrue(WorkOrder.objects.filter(pk=self.work_order.pk).exists())

            ok = client.post(
                f'/api/aichat/proposals/{pid}/confirm/',
                {'confirm_phrase': 'confirm delete'},
                content_type='application/json',
            )
            self.assertEqual(ok.status_code, 200, ok.content)
            self.assertFalse(WorkOrder.objects.filter(pk=self.work_order.pk).exists())

    def test_schedule_proposal_carries_intent_over_http(self):
        """The create view accepts a scheduling intent and confirmation dispatches it."""
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=self.RESOLVER):
            client = self._client()
            created = client.post(
                '/api/aichat/proposals/',
                {
                    'action_type': 'work_order.schedule',
                    'work_order_id': self.work_order.pk,
                    'reason': 'move to Monday',
                    'intent': {
                        'scheduled_start': '2026-08-03T09:00:00',
                        'scheduled_end': '2026-08-03T13:00:00',
                    },
                },
                content_type='application/json',
            )
            self.assertEqual(created.status_code, 201, created.content)
            body = created.json()
            self.assertEqual(
                body['intent']['scheduled_start'], '2026-08-03T09:00:00'
            )
            self.assertEqual(body['preview']['proposed_start'], '2026-08-03T09:00:00')

            confirmed = client.post(f"/api/aichat/proposals/{body['id']}/confirm/")
            self.assertEqual(confirmed.status_code, 200, confirmed.content)
            self.assertEqual(confirmed.json()['receipt']['command'], 'schedule')
            self.work_order.refresh_from_db()
            self.assertEqual(self.work_order.scheduled_start.hour, 9)

    def test_non_object_intent_is_rejected(self):
        """A non-object intent is a 400, never a server error."""
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=self.RESOLVER):
            client = self._client()
            response = client.post(
                '/api/aichat/proposals/',
                {
                    'action_type': 'work_order.schedule',
                    'work_order_id': self.work_order.pk,
                    'intent': 'not-an-object',
                },
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 400, response.content)

    def test_unauthenticated_requests_are_rejected(self):
        """Unauthenticated requests are rejected."""
        response = self.client.post(
            '/api/aichat/proposals/',
            {'action_type': 'work_order.hold', 'work_order_id': 1},
            content_type='application/json',
        )
        self.assertIn(response.status_code, (401, 403))

    def test_api_token_cannot_confirm_visual_proposal(self):
        """Api token cannot confirm visual proposal."""
        proposal = self._create()
        token = ApiToken.objects.create(user=self.actor, name='proposal-api-token')

        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=self.RESOLVER):
            response = self.client.post(
                f'/api/aichat/proposals/{proposal.id}/confirm/',
                HTTP_AUTHORIZATION=f'Token {token.key}',
            )

        self.assertIn(response.status_code, (401, 403))
        self.work_order.refresh_from_db()
        self.assertEqual(
            self.work_order.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS
        )

    def test_cross_scope_target_is_not_disclosed_on_create(self):
        """Cross scope target is not disclosed on create."""
        other_customer = Company.objects.create(
            name='Proposal Other Customer', is_customer=True
        )
        other_machine = AssetMachine.objects.create(
            name='Secret press', customer=other_customer
        )
        other_work_order = WorkOrder.objects.create(
            title='Secret work order',
            status=WorkOrder.STATUS_REVIEW,
            priority=WorkOrder.PRIORITY_MEDIUM,
            customer=other_customer,
            machine=other_machine,
            work_order_type=WorkOrderType.PREVENTIVE,
            lifecycle_status=WorkOrderLifecycle.IN_PROGRESS,
        )

        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=self.RESOLVER):
            response = self._client().post(
                '/api/aichat/proposals/',
                {
                    'action_type': 'work_order.hold',
                    'work_order_id': other_work_order.pk,
                    'reason': 'cross-scope probe',
                },
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error'], 'PROPOSAL_NOT_FOUND')
        self.assertFalse(
            ChatActionProposal.objects.filter(
                owner=self.actor, target_work_order_id=other_work_order.pk
            ).exists()
        )

    def test_scope_revocation_hides_existing_proposal(self):
        """Scope revocation hides existing proposal."""
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=self.RESOLVER):
            created = self._client().post(
                '/api/aichat/proposals/',
                {
                    'action_type': 'work_order.hold',
                    'work_order_id': self.work_order.pk,
                    'reason': 'scope revocation probe',
                },
                content_type='application/json',
            )
        self.assertEqual(created.status_code, 201)
        proposal_id = created.json()['id']

        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda _actor: set()):
            listed = self.client.get('/api/aichat/proposals/')
            detailed = self.client.get(f'/api/aichat/proposals/{proposal_id}/')
            replayed = self.client.post(
                f'/api/aichat/proposals/{proposal_id}/confirm/'
            )

        self.assertEqual(listed.status_code, 403)
        self.assertEqual(detailed.status_code, 404)
        self.assertEqual(replayed.status_code, 404)

    def test_disallowed_action_is_denied_over_http(self):
        """Disallowed action is denied over http."""
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=self.RESOLVER):
            client = self._client()
            response = client.post(
                '/api/aichat/proposals/',
                {
                    'action_type': 'work_order.complete',
                    'work_order_id': self.work_order.pk,
                },
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()['error'], 'CAPABILITY_DENIED')
