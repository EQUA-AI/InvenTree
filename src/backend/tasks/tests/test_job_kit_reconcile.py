"""Tests for reconcile_job_kit and shortage procurement linkage."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from company.models import Company, SupplierPart
from order.models import PurchaseOrder, PurchaseOrderLineItem
from part.models import Part
from stock.models import StockItem
from tasks.models import (
    FulfillmentMode,
    JobKit,
    JobKitAllocation,
    JobKitLine,
    JobKitShortage,
    JobKitStatus,
    KanbanCard,
    ProcedureResourceKind,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope
from tasks.services.job_kit_custody import consume_allocation
from tasks.services.job_kits import (
    JobKitLineError,
    link_po_to_shortage,
    reconcile_job_kit,
    reserve_job_kit,
)


class ReconcileJobKitTest(TestCase):
    """Exercise safe rerunnable reconciliation and PO linkage."""

    def setUp(self):
        self.customer = Company.objects.create(name='Recon Cust', is_customer=True)
        self.actor = get_user_model().objects.create_superuser(
            username='recon-sup', email='r@example.com', password='pw'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.work_order = KanbanCard.objects.create(
            title='Recon WO', status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_MEDIUM, customer=self.customer,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        self.kit = JobKit.objects.create(
            work_order=self.work_order, created_by=self.actor
        )
        self.part = Part.objects.create(
            name='Filter', description='f', component=True, purchaseable=True
        )
        self.line = JobKitLine.objects.create(
            kit=self.kit, sequence=1, kind=ProcedureResourceKind.PART,
            requested_part=self.part, selected_part=self.part,
            required_quantity=Decimal('5'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME, source='manual',
        )

    def reserve(self, key):
        return reserve_job_kit(
            work_order_id=self.work_order.pk, actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
        )

    def reconcile(self):
        return reconcile_job_kit(
            work_order_id=self.work_order.pk, actor=self.actor,
        )

    def test_reconcile_is_idempotent_for_shortage(self):
        StockItem.objects.create(part=self.part, quantity=Decimal('3'))
        self.reserve('r1')
        kit = self.reconcile()
        self.assertEqual(kit.status, JobKitStatus.SHORT)
        self.assertEqual(
            JobKitShortage.objects.filter(line=self.line, status='open').count(), 1
        )
        # Rerun changes nothing material.
        kit = self.reconcile()
        self.assertEqual(kit.status, JobKitStatus.SHORT)
        self.assertEqual(
            JobKitShortage.objects.filter(line=self.line, status='open').count(), 1
        )

    def test_reconcile_clears_shortage_after_restock(self):
        StockItem.objects.create(part=self.part, quantity=Decimal('3'))
        self.reserve('r1')
        self.reconcile()
        StockItem.objects.create(part=self.part, quantity=Decimal('5'))
        self.reserve('r2')

        kit = self.reconcile()
        self.assertEqual(kit.status, JobKitStatus.READY)
        self.assertFalse(
            JobKitShortage.objects.filter(line=self.line, status='open').exists()
        )
        self.assertTrue(
            self.work_order.events.filter(event_type='JOB_KIT_RECONCILED').exists()
        )

    def test_reconcile_counts_consumed_as_fulfilled(self):
        StockItem.objects.create(part=self.part, quantity=Decimal('10'))
        self.reserve('r1')
        allocation = JobKitAllocation.objects.get(line=self.line)
        consume_allocation(
            work_order_id=self.work_order.pk, allocation_id=allocation.pk,
            actor=self.actor,
        )
        kit = self.reconcile()
        # Consumed quantity still fulfils the required line -> no shortage.
        self.assertEqual(kit.status, JobKitStatus.READY)
        self.assertFalse(
            JobKitShortage.objects.filter(line=self.line, status='open').exists()
        )

    def test_link_po_to_shortage_marks_ordered(self):
        StockItem.objects.create(part=self.part, quantity=Decimal('3'))
        self.reserve('r1')
        shortage = JobKitShortage.objects.get(line=self.line, status='open')

        supplier = Company.objects.create(name='Acme Supply', is_supplier=True)
        supplier_part = SupplierPart.objects.create(
            part=self.part, supplier=supplier, SKU='SKU-1'
        )
        po = PurchaseOrder.objects.create(reference='PO-0001', supplier=supplier)
        po_line = PurchaseOrderLineItem.objects.create(
            part=supplier_part, order=po, quantity=2
        )

        linked = link_po_to_shortage(
            work_order_id=self.work_order.pk, shortage_id=shortage.pk,
            purchase_order_line_id=po_line.pk, actor=self.actor,
        )
        self.assertEqual(linked.status, 'ordered')
        self.assertEqual(linked.purchase_order_line_id, po_line.pk)

    def test_link_po_rejects_foreign_shortage(self):
        with self.assertRaises(JobKitLineError):
            link_po_to_shortage(
                work_order_id=self.work_order.pk, shortage_id=999999,
                purchase_order_line_id=1, actor=self.actor,
            )
