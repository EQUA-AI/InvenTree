"""Tests for governed Job Kit substitution (propose/decide)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from company.models import Company
from part.models import Part
from stock.models import StockItem
from tasks.models import (
    FulfillmentMode,
    JobKit,
    JobKitAllocation,
    JobKitLine,
    JobKitSubstitutionStatus,
    KanbanCard,
    ProcedureResourceKind,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope
from tasks.services.job_kits import (
    JobKitLineError,
    JobKitStateError,
    decide_substitution,
    propose_substitution,
    reserve_job_kit,
)


class JobKitSubstitutionTest(TestCase):
    """Exercise proposal, separation of duties, and the approve effect."""

    def setUp(self):
        self.customer = Company.objects.create(name='Sub Cust', is_customer=True)
        self.proposer = get_user_model().objects.create_superuser(
            username='proposer', email='p@example.com', password='pw'
        )
        self.decider = get_user_model().objects.create_superuser(
            username='decider', email='d@example.com', password='pw'
        )
        for user in (self.proposer, self.decider):
            user.maintenance_scopes = {
                MaintenanceScope(customer_id=self.customer.pk, site_key=None)
            }
        self.work_order = KanbanCard.objects.create(
            title='Sub WO', status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_MEDIUM, customer=self.customer,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        self.kit = JobKit.objects.create(
            work_order=self.work_order, created_by=self.proposer
        )
        self.requested = Part.objects.create(name='OEM', description='o', component=True)
        self.alternate = Part.objects.create(name='Alt', description='a', component=True)
        self.line = JobKitLine.objects.create(
            kit=self.kit, sequence=1, kind=ProcedureResourceKind.PART,
            requested_part=self.requested, selected_part=self.requested,
            required_quantity=Decimal('2'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME, source='manual',
            substitution_policy='supervisor',
        )

    def propose(self, actor=None):
        return propose_substitution(
            work_order_id=self.work_order.pk, line_id=self.line.pk,
            proposed_part_id=self.alternate.pk, actor=actor or self.proposer,
            basis={'reason': 'equivalent spec'},
        )

    def test_approve_sets_selected_part(self):
        sub = self.propose()
        self.assertEqual(sub.status, JobKitSubstitutionStatus.PROPOSED)

        decided = decide_substitution(
            work_order_id=self.work_order.pk, substitution_id=sub.pk,
            actor=self.decider, approve=True,
        )
        self.assertEqual(decided.status, JobKitSubstitutionStatus.APPROVED)
        self.line.refresh_from_db()
        self.assertEqual(self.line.selected_part_id, self.alternate.pk)

    def test_reject_leaves_selected_part(self):
        sub = self.propose()
        decide_substitution(
            work_order_id=self.work_order.pk, substitution_id=sub.pk,
            actor=self.decider, approve=False, reason='not equivalent',
        )
        self.line.refresh_from_db()
        self.assertEqual(self.line.selected_part_id, self.requested.pk)

    def test_proposer_cannot_decide_own(self):
        sub = self.propose()
        with self.assertRaises(JobKitLineError):
            decide_substitution(
                work_order_id=self.work_order.pk, substitution_id=sub.pk,
                actor=self.proposer, approve=True,
            )

    def test_policy_none_blocks_proposal(self):
        self.line.substitution_policy = 'none'
        self.line.save(update_fields=['substitution_policy'])
        with self.assertRaises(JobKitLineError):
            self.propose()

    def test_approve_blocked_with_active_reservation(self):
        StockItem.objects.create(part=self.requested, quantity=Decimal('10'))
        reserve_job_kit(
            work_order_id=self.work_order.pk, actor=self.proposer,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='reserve',
        )
        self.assertTrue(JobKitAllocation.objects.filter(line=self.line).exists())
        sub = self.propose()
        with self.assertRaises(JobKitStateError):
            decide_substitution(
                work_order_id=self.work_order.pk, substitution_id=sub.pk,
                actor=self.decider, approve=True,
            )

    def test_decide_requires_permission(self):
        sub = self.propose()
        planner = get_user_model().objects.create_user(username='sub-noperm')
        planner.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        with self.assertRaises(PermissionDenied):
            decide_substitution(
                work_order_id=self.work_order.pk, substitution_id=sub.pk,
                actor=planner, approve=True,
            )
