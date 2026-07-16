"""Tests for Job Kit custody lifecycle services (stage/issue/consume/return)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from company.models import Company
from part.models import Part
from stock.models import StockItem
from tasks.models import (
    FulfillmentMode,
    JobKit,
    JobKitAllocation,
    JobKitAllocationStatus,
    JobKitLine,
    KanbanCard,
    ProcedureResourceKind,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope
from tasks.services.job_kit_custody import (
    JobKitCustodyError,
    consume_allocation,
    issue_allocation,
    return_allocation,
    stage_allocation,
)
from tasks.services.job_kits import JobKitStateError, reserve_job_kit


class JobKitCustodyTest(TestCase):
    """Exercise staging, issue, real consumption, and return of allocations."""

    def setUp(self):
        self.customer = Company.objects.create(name='Custody Cust', is_customer=True)
        self.actor = get_user_model().objects.create_superuser(
            username='custody-sup', email='cust@example.com', password='pw'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.work_order = KanbanCard.objects.create(
            title='Custody WO', status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_MEDIUM, customer=self.customer,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        self.kit = JobKit.objects.create(
            work_order=self.work_order, created_by=self.actor
        )
        self.part = Part.objects.create(name='Filter', description='f', component=True)
        self.stock = StockItem.objects.create(part=self.part, quantity=Decimal('10'))
        self.line = JobKitLine.objects.create(
            kit=self.kit, sequence=1, kind=ProcedureResourceKind.PART,
            requested_part=self.part, selected_part=self.part,
            required_quantity=Decimal('4'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME, source='manual',
        )
        reserve_job_kit(
            work_order_id=self.work_order.pk, actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='reserve',
        )
        self.allocation = JobKitAllocation.objects.get(line=self.line)

    def stage(self):
        return stage_allocation(
            work_order_id=self.work_order.pk, allocation_id=self.allocation.pk,
            actor=self.actor,
        )

    def test_stage_then_issue(self):
        staged = self.stage()
        self.assertEqual(staged.status, JobKitAllocationStatus.STAGED)
        self.assertIsNotNone(staged.staged_at)

        issued = issue_allocation(
            work_order_id=self.work_order.pk, allocation_id=self.allocation.pk,
            actor=self.actor,
        )
        self.assertEqual(issued.status, JobKitAllocationStatus.ISSUED)

    def test_consume_removes_real_stock_and_records_tracking(self):
        before = self.stock.quantity
        consumed = consume_allocation(
            work_order_id=self.work_order.pk, allocation_id=self.allocation.pk,
            actor=self.actor,
        )
        self.assertEqual(consumed.status, JobKitAllocationStatus.CONSUMED)
        self.assertIsNotNone(consumed.stock_tracking_id)

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, before - Decimal('4'))
        # Consumed is terminal: it no longer counts against availability, and the
        # physical stock was already reduced, so there is no double count.
        self.assertEqual(self.stock.job_kit_allocation_count(), Decimal('0'))
        self.assertEqual(self.stock.unallocated_quantity(), Decimal('6'))

    def test_double_consume_is_rejected(self):
        consume_allocation(
            work_order_id=self.work_order.pk, allocation_id=self.allocation.pk,
            actor=self.actor,
        )
        with self.assertRaises(JobKitStateError):
            consume_allocation(
                work_order_id=self.work_order.pk, allocation_id=self.allocation.pk,
                actor=self.actor,
            )
        # Stock was only decremented once.
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('6'))

    def test_return_frees_reservation_without_consuming(self):
        before = self.stock.quantity
        returned = return_allocation(
            work_order_id=self.work_order.pk, allocation_id=self.allocation.pk,
            actor=self.actor,
        )
        self.assertEqual(returned.status, JobKitAllocationStatus.RETURNED)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, before)  # nothing consumed
        self.assertEqual(self.stock.job_kit_allocation_count(), Decimal('0'))

    def test_issue_requires_active_state(self):
        # Consume first -> terminal, then issue must fail.
        consume_allocation(
            work_order_id=self.work_order.pk, allocation_id=self.allocation.pk,
            actor=self.actor,
        )
        with self.assertRaises(JobKitStateError):
            issue_allocation(
                work_order_id=self.work_order.pk, allocation_id=self.allocation.pk,
                actor=self.actor,
            )

    def test_scan_required_line_blocks_unscanned_stage(self):
        self.line.requires_scan = True
        self.line.save(update_fields=['requires_scan'])
        with self.assertRaises(JobKitCustodyError):
            self.stage()
        # With scan proof it succeeds.
        staged = stage_allocation(
            work_order_id=self.work_order.pk, allocation_id=self.allocation.pk,
            actor=self.actor, scan_proof={'barcode': 'ABC123'},
        )
        self.assertEqual(staged.status, JobKitAllocationStatus.STAGED)
        self.assertEqual(staged.scan_proof, {'barcode': 'ABC123'})
