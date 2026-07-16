"""Tests for structured work-order completion and asset-history writeback."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from assets.models import AssetMachine, AssetMaintenanceRecord
from company.models import Company
from part.models import Part
from stock.models import StockItem
from tasks.models import (
    FulfillmentMode,
    JobKit,
    JobKitAllocation,
    JobKitAllocationStatus,
    JobKitLine,
    JobKitStatus,
    KanbanCard,
    ProcedureResourceKind,
    WorkOrderCloseout,
    WorkOrderLifecycle,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope
from tasks.services.closeout import CloseoutError, complete_work_order
from tasks.services.job_kits import reserve_job_kit
from tasks.services.work_orders import IllegalTransition, ReadinessBlocked


class CompleteWorkOrderTest(TestCase):
    """Exercise the atomic completion, closeout, writeback, and kit closure."""

    def setUp(self):
        self.customer = Company.objects.create(name='Closeout Cust', is_customer=True)
        self.actor = get_user_model().objects.create_superuser(
            username='closeout-sup', email='co@example.com', password='pw'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.machine = AssetMachine.objects.create(
            name='Pump 1', customer=self.customer
        )
        self.work_order = KanbanCard.objects.create(
            title='Complete me', status=KanbanCard.STATUS_REVIEW,
            priority=KanbanCard.PRIORITY_MEDIUM, customer=self.customer,
            machine=self.machine, assigned_to=self.actor,
            work_order_type=WorkOrderType.PREVENTIVE,
            lifecycle_status=WorkOrderLifecycle.VERIFYING,
        )
        self.closeout = {
            'action': 'Replaced filter', 'result': 'Restored flow',
            'verification_summary': 'Flow verified at 20 GPM',
            'cause': 'Clogged filter',
        }

    def complete(self, key='complete-1', closeout=None):
        return complete_work_order(
            work_order_id=self.work_order.pk, actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=key, closeout=closeout or self.closeout,
        )

    def test_completion_writes_closeout_and_asset_record(self):
        result = self.complete()
        self.assertEqual(result.lifecycle_status, WorkOrderLifecycle.COMPLETED)

        self.work_order.refresh_from_db()
        self.assertEqual(
            self.work_order.lifecycle_status, WorkOrderLifecycle.COMPLETED
        )
        self.assertIsNotNone(self.work_order.actual_completed_at)

        closeout = WorkOrderCloseout.objects.get(work_order=self.work_order)
        self.assertEqual(closeout.action, 'Replaced filter')
        self.assertEqual(closeout.completed_by, self.actor)

        record = AssetMaintenanceRecord.objects.get(work_order=self.work_order)
        self.assertEqual(record.machine, self.machine)
        self.assertIn('Replaced filter', record.details)

    def test_missing_required_closeout_fields_fail(self):
        with self.assertRaises(CloseoutError):
            self.complete(closeout={'action': 'only action'})

    def test_completion_is_idempotent(self):
        first = self.complete(key='same')
        replay = self.complete(key='same')
        self.assertEqual(first.event_id, replay.event_id)
        self.assertEqual(WorkOrderCloseout.objects.count(), 1)
        self.assertEqual(AssetMaintenanceRecord.objects.count(), 1)

    def test_completion_closes_kit_and_releases_reservations(self):
        kit = JobKit.objects.create(
            work_order=self.work_order, created_by=self.actor
        )
        part = Part.objects.create(name='Seal', description='s', component=True)
        StockItem.objects.create(part=part, quantity=Decimal('10'))
        line = JobKitLine.objects.create(
            kit=kit, sequence=1, kind=ProcedureResourceKind.PART,
            requested_part=part, selected_part=part,
            required_quantity=Decimal('3'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME, source='manual',
        )
        reserve_job_kit(
            work_order_id=self.work_order.pk, actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='reserve',
        )
        # reserve_job_kit bumps kit.version but not work-order lifecycle_version.
        allocation = JobKitAllocation.objects.get(line=line)
        self.assertEqual(allocation.status, JobKitAllocationStatus.RESERVED)

        self.complete()

        allocation.refresh_from_db()
        kit.refresh_from_db()
        self.assertEqual(allocation.status, JobKitAllocationStatus.RELEASED)
        self.assertEqual(kit.status, JobKitStatus.CLOSED)
        self.assertIsNotNone(kit.closed_at)

    def test_missing_asset_blocks_completion(self):
        self.work_order.machine = None
        self.work_order.save(update_fields=['machine'])
        with self.assertRaises(ReadinessBlocked):
            self.complete()

    def test_illegal_lifecycle_state_is_rejected(self):
        self.work_order.lifecycle_status = WorkOrderLifecycle.PLANNED
        self.work_order.save(update_fields=['lifecycle_status'])
        with self.assertRaises(IllegalTransition):
            self.complete()
