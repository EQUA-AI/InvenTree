"""Tests for reconcile_job_kit and shortage procurement linkage."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from company.models import Company, SupplierPart
from order.models import PurchaseOrder, PurchaseOrderLineItem
from order.status_codes import PurchaseOrderStatus
from part.models import Part
from stock.models import StockItem
from tasks.models import (
    FulfillmentMode,
    JobKit,
    JobKitAllocation,
    JobKitLine,
    JobKitShortage,
    JobKitStatus,
    WorkOrder,
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
        self.work_order = WorkOrder.objects.create(
            title='Recon WO', status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM, customer=self.customer,
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
        po = PurchaseOrder.objects.create(reference='PO-0001', supplier=supplier, status=PurchaseOrderStatus.PLACED.value)
        po_line = PurchaseOrderLineItem.objects.create(
            part=supplier_part, order=po, quantity=2, purchase_price=10, purchase_price_currency='USD'
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

    def test_link_rejects_draft_wrong_part_and_insufficient_quantity(self):
        """Pending or unvalidated native rows never produce Ordered readiness."""
        self.reserve('r8-link-shortage')
        shortage = JobKitShortage.objects.get(line=self.line, status='open')
        supplier = Company.objects.create(name='R8 fixture supplier', is_supplier=True)
        sp = SupplierPart.objects.create(part=self.part, supplier=supplier, SKU='R8-fixture')
        po = PurchaseOrder.objects.create(reference='R8-FIXTURE-DRAFT', supplier=supplier)
        line = PurchaseOrderLineItem.objects.create(order=po, part=sp, quantity=5, purchase_price=1, purchase_price_currency='USD')

        def link():
            return link_po_to_shortage(work_order_id=self.work_order.pk, shortage_id=shortage.pk, purchase_order_line_id=line.pk, actor=self.actor)

        with self.assertRaises(JobKitLineError):
            link()
        po.status = PurchaseOrderStatus.PLACED.value
        po.save()
        line.quantity = 4
        line.save()
        with self.assertRaises(JobKitLineError):
            link()
        line.quantity = 5
        line.purchase_price = None
        line.save()
        with self.assertRaises(JobKitLineError):
            link()
        line.purchase_price = 1
        line.purchase_price_currency = 'USD'
        wrong = Part.objects.create(name='R8 fixture unrelated', purchaseable=True)
        sp.part = wrong
        sp.save()
        line.save()
        with self.assertRaises(JobKitLineError):
            link()
        shortage.refresh_from_db()
        self.assertEqual(shortage.status, 'open')
        self.assertIsNone(shortage.purchase_order_line_id)

    def test_link_respects_supplier_pack_conversion_and_replay(self):
        """Two order packs of three cover five base units without duplicate links."""
        self.reserve('r8-pack-shortage')
        shortage = JobKitShortage.objects.get(line=self.line, status='open')
        supplier = Company.objects.create(name='R8 fixture packs', is_supplier=True)
        sp = SupplierPart.objects.create(part=self.part, supplier=supplier, SKU='R8-pack', pack_quantity='3')
        sp.full_clean()
        sp.save()
        po = PurchaseOrder.objects.create(reference='R8-FIXTURE-PACK', supplier=supplier, status=PurchaseOrderStatus.PLACED.value)
        line = PurchaseOrderLineItem.objects.create(order=po, part=sp, quantity=2, purchase_price=1, purchase_price_currency='USD')
        for _ in range(2):
            result = link_po_to_shortage(work_order_id=self.work_order.pk, shortage_id=shortage.pk, purchase_order_line_id=line.pk, actor=self.actor)
            self.assertEqual(result.status, 'ordered')
        self.assertEqual(JobKitShortage.objects.filter(line=self.line).count(), 1)
