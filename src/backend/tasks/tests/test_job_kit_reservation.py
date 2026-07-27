"""Tests for the reserve_job_kit orchestration service."""

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
    JobKitShortage,
    JobKitStatus,
    WorkOrder,
    ProcedureResourceKind,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope
from tasks.services.job_kits import JobKitStateError, reserve_job_kit


class ReserveJobKitTest(TestCase):
    """Exercise atomic reservation, shortages, readiness, and idempotency."""

    def setUp(self):
        self.customer = Company.objects.create(name='Res Customer', is_customer=True)
        self.actor = get_user_model().objects.create_superuser(
            username='reserve-sup', email='res@example.com', password='pw'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.work_order = WorkOrder.objects.create(
            title='Reserve WO', status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM, customer=self.customer,
            assigned_to=self.actor, work_order_type=WorkOrderType.PREVENTIVE,
        )
        self.kit = JobKit.objects.create(
            work_order=self.work_order, created_by=self.actor
        )
        self._seq = 0

    def make_line(self, part, quantity, required=True,
                  mode=FulfillmentMode.RESERVE_CONSUME):
        self._seq += 1
        return JobKitLine.objects.create(
            kit=self.kit, sequence=self._seq, kind=ProcedureResourceKind.PART,
            requested_part=part, selected_part=part,
            required_quantity=Decimal(quantity), required=required,
            fulfillment_mode=mode, source='manual',
        )

    def make_part_with_stock(self, name, stock_quantity):
        part = Part.objects.create(name=name, description=name, component=True)
        StockItem.objects.create(part=part, quantity=Decimal(stock_quantity))
        return part

    def reserve(self, key='reserve-1'):
        return reserve_job_kit(
            work_order_id=self.work_order.pk, actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
        )

    def test_full_reservation_makes_kit_ready(self):
        part = self.make_part_with_stock('Filter', '10')
        line = self.make_line(part, '5')

        kit = self.reserve()

        self.assertEqual(kit.status, JobKitStatus.READY)
        self.assertEqual(
            sum(a.quantity for a in line.allocations.all()), Decimal('5')
        )
        self.assertFalse(JobKitShortage.objects.filter(line=line, status='open').exists())

    def test_insufficient_stock_records_shortage_and_short_status(self):
        part = self.make_part_with_stock('Gasket', '3')
        line = self.make_line(part, '5')

        kit = self.reserve()

        self.assertEqual(kit.status, JobKitStatus.SHORT)
        self.assertEqual(
            sum(a.quantity for a in line.allocations.all()), Decimal('3')
        )
        shortage = JobKitShortage.objects.get(line=line, status='open')
        self.assertEqual(shortage.quantity, Decimal('2'))

    def test_reservation_is_idempotent_on_replay(self):
        part = self.make_part_with_stock('Seal', '10')
        self.make_line(part, '4')

        first = self.reserve(key='same')
        replay = self.reserve(key='same')

        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(JobKitAllocation.objects.count(), 1)
        self.assertEqual(
            self.work_order.commands.filter(command='reserve_job_kit').count(), 1
        )

    def test_optional_and_verify_only_lines_are_not_reserved(self):
        required = self.make_part_with_stock('Bolt', '10')
        optional = self.make_part_with_stock('Grease', '10')
        tool = self.make_part_with_stock('Torque wrench', '10')
        req_line = self.make_line(required, '2')
        opt_line = self.make_line(optional, '2', required=False)
        tool_line = self.make_line(
            tool, '1', mode=FulfillmentMode.VERIFY_ONLY
        )

        self.reserve()

        self.assertEqual(req_line.allocations.count(), 1)
        self.assertEqual(opt_line.allocations.count(), 0)
        self.assertEqual(tool_line.allocations.count(), 0)

    def test_short_kit_can_be_re_reserved_after_restock(self):
        part = self.make_part_with_stock('Bearing', '3')
        line = self.make_line(part, '5')
        kit = self.reserve(key='r1')
        self.assertEqual(kit.status, JobKitStatus.SHORT)

        # Restock and retry with a fresh command.
        StockItem.objects.create(part=part, quantity=Decimal('5'))
        self.work_order.refresh_from_db()
        kit = reserve_job_kit(
            work_order_id=self.work_order.pk, actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='r2',
        )

        self.assertEqual(kit.status, JobKitStatus.READY)
        self.assertEqual(
            sum(a.quantity for a in line.allocations.all()), Decimal('5')
        )
        self.assertFalse(JobKitShortage.objects.filter(line=line, status='open').exists())

    def test_permission_is_required(self):
        part = self.make_part_with_stock('Nut', '10')
        self.make_line(part, '2')
        planner = get_user_model().objects.create_user(username='res-noperm')
        planner.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        with self.assertRaises(PermissionDenied):
            reserve_job_kit(
                work_order_id=self.work_order.pk, actor=planner,
                expected_version=self.work_order.lifecycle_version,
                idempotency_key='noperm',
            )

    def test_released_kit_cannot_be_reserved(self):
        part = self.make_part_with_stock('Washer', '10')
        self.make_line(part, '2')
        self.kit.status = JobKitStatus.RELEASED
        self.kit.save(update_fields=['status'])
        with self.assertRaises(JobKitStateError):
            self.reserve()
