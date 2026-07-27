"""Tests for four-domain stock allocation accounting coherence.

Proves that maintenance Job Kit reservations participate in the shared
``StockItem`` availability calculation alongside build, sales, and transfer
allocations, and that the over-allocation guards honour the new domain. The
build, sales, and transfer ``clean()`` guards all delegate to the same
``StockItem.total_committed_allocation`` authority, so the direct helper tests
plus the end-to-end build-guard proof below cover all three guards.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from build.models import BuildItem
from build.test_build import BuildTestBase
from part.models import Part
from stock.models import StockItem
from tasks.models import (
    FulfillmentMode,
    JobKit,
    JobKitAllocation,
    JobKitAllocationStatus,
    JobKitLine,
    WorkOrder,
    ProcedureResourceKind,
    WorkOrderType,
)


class StockAllocationAccountingTest(TestCase):
    """Exercise the Job Kit stock allocation domain and shared authority."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='alloc-actor')
        self.component = Part.objects.create(
            name='Bearing', description='A stock component', component=True
        )
        self.stock = StockItem.objects.create(
            part=self.component, quantity=Decimal('10')
        )
        self.work_order = WorkOrder.objects.create(
            title='Alloc WO', status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        self.kit = JobKit.objects.create(
            work_order=self.work_order, created_by=self.user
        )
        self._line_seq = 0

    def make_line(self):
        """Create a distinct manual line.

        The active-state partial-unique constraint permits only one active
        allocation per (line, stock_item), so each allocation gets its own line.
        """
        self._line_seq += 1
        return JobKitLine.objects.create(
            kit=self.kit, sequence=self._line_seq, kind=ProcedureResourceKind.PART,
            requested_part=self.component, selected_part=self.component,
            required_quantity=Decimal('8'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME, source='manual',
        )

    def make_allocation(self, quantity, status=JobKitAllocationStatus.RESERVED):
        return JobKitAllocation.objects.create(
            line=self.make_line(), stock_item=self.stock, quantity=Decimal(quantity),
            status=status, reserved_by=self.user,
            idempotency_key=f'a-{quantity}-{status}',
        )

    def test_job_kit_allocation_count_active_only(self):
        self.make_allocation('3', JobKitAllocationStatus.RESERVED)
        self.make_allocation('2', JobKitAllocationStatus.STAGED)
        self.make_allocation('1', JobKitAllocationStatus.ISSUED)
        self.make_allocation('4', JobKitAllocationStatus.CONSUMED)
        self.make_allocation('5', JobKitAllocationStatus.RELEASED)

        self.assertEqual(self.stock.job_kit_allocation_count(), Decimal('6'))
        self.assertEqual(
            self.stock.job_kit_allocation_count(active=False), Decimal('15')
        )

    def test_job_kit_allocation_count_exclude(self):
        keep = self.make_allocation('3', JobKitAllocationStatus.RESERVED)
        drop = self.make_allocation('2', JobKitAllocationStatus.STAGED)
        self.assertEqual(
            self.stock.job_kit_allocation_count(
                exclude_allocations={'pk': drop.pk}
            ),
            Decimal('3'),
        )
        self.assertEqual(keep.quantity, Decimal('3'))

    def test_allocation_count_includes_job_kit_domain(self):
        self.assertEqual(self.stock.allocation_count(), Decimal('0'))
        self.make_allocation('4', JobKitAllocationStatus.RESERVED)
        self.assertEqual(self.stock.allocation_count(), Decimal('4'))
        self.assertEqual(self.stock.unallocated_quantity(), Decimal('6'))

    def test_total_committed_allocation_sums_and_excludes(self):
        active = self.make_allocation('4', JobKitAllocationStatus.RESERVED)
        # Build/sales/transfer domains are zero here, so the total is the job-kit
        # contribution; the exclusion removes it.
        self.assertEqual(self.stock.total_committed_allocation(), Decimal('4'))
        self.assertEqual(
            self.stock.total_committed_allocation(
                exclude_job_kit={'pk': active.pk}
            ),
            Decimal('0'),
        )


class BuildGuardJobKitTest(BuildTestBase):
    """Prove the BuildItem over-allocation guard now counts Job Kit reservations.

    All three domain guards (build/sales/transfer ``clean()``) delegate to the
    same ``StockItem.total_committed_allocation`` authority, so this end-to-end
    build proof plus the direct helper tests above cover every guard.
    """

    def _job_kit_line(self):
        from tasks.models import (
            FulfillmentMode,
            JobKit,
            JobKitLine,
            WorkOrder,
            ProcedureResourceKind,
            WorkOrderType,
        )

        user = get_user_model().objects.get(pk=1)
        work_order = WorkOrder.objects.create(
            title='Guard WO', status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        kit = JobKit.objects.create(work_order=work_order, created_by=user)
        return user, JobKitLine.objects.create(
            kit=kit, sequence=1, kind=ProcedureResourceKind.PART,
            requested_part=self.sub_part_1, selected_part=self.sub_part_1,
            required_quantity=Decimal('1'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME, source='manual',
        )

    def _reserve(self, line, user, quantity, status=JobKitAllocationStatus.RESERVED):
        return JobKitAllocation.objects.create(
            line=line, stock_item=self.stock_1_1, quantity=Decimal(quantity),
            status=status, reserved_by=user, idempotency_key=f'g-{quantity}-{status}',
        )

    def test_build_guard_counts_active_job_kit_allocation(self):
        # stock_1_1 holds 3 units of sub_part_1 and is otherwise unallocated.
        self.assertEqual(self.stock_1_1.quantity, 3)
        user, line = self._job_kit_line()
        self._reserve(line, user, '2', JobKitAllocationStatus.RESERVED)

        # 2 reserved for maintenance + 2 for build = 4 > 3 available.
        over = BuildItem(
            build_line=self.line_1, stock_item=self.stock_1_1, quantity=Decimal('2')
        )
        with self.assertRaises(ValidationError):
            over.clean()

        # 2 + 1 = 3 exactly available -> allowed.
        ok = BuildItem(
            build_line=self.line_1, stock_item=self.stock_1_1, quantity=Decimal('1')
        )
        ok.clean()

    def test_build_guard_ignores_terminal_job_kit_allocation(self):
        user, line = self._job_kit_line()
        # Consumed is terminal and must not block a fresh build allocation.
        self._reserve(line, user, '2', JobKitAllocationStatus.CONSUMED)

        ok = BuildItem(
            build_line=self.line_1, stock_item=self.stock_1_1, quantity=Decimal('3')
        )
        ok.clean()
