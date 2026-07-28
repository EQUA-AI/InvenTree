"""Concurrency and correctness proof for the shared stock-allocation authority.

Satisfies success criterion SC-004: concurrent reservation requests for the last
units produce zero aggregate over-allocation. These use ``TransactionTestCase``
so each worker thread runs a real committed transaction and the
``SELECT ... FOR UPDATE`` row lock genuinely serializes contenders on PostgreSQL.
"""

import threading
from decimal import Decimal
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase, tag

from part.models import Part
from stock.models import StockItem
from tasks.models import (
    FulfillmentMode,
    JobKit,
    JobKitAllocation,
    JobKitLine,
    WorkOrder,
    ProcedureResourceKind,
    WorkOrderType,
)
from tasks.services.stock_allocation import StockOverAllocation, mutate_stock_allocation


class _AllocationFixtureMixin:
    """Shared fixture builders for a stock item and a Job Kit with lines."""

    def build_fixture(self, stock_quantity):
        self.user = get_user_model().objects.create_user(username='reserve-actor')
        self.component = Part.objects.create(
            name='Bearing', description='A component', component=True
        )
        self.stock = StockItem.objects.create(
            part=self.component, quantity=Decimal(stock_quantity)
        )
        self.work_order = WorkOrder.objects.create(
            title='Reserve WO', status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        self.kit = JobKit.objects.create(
            work_order=self.work_order, created_by=self.user
        )
        self._seq = 0

    def make_line(self):
        self._seq += 1
        return JobKitLine.objects.create(
            kit=self.kit, sequence=self._seq, kind=ProcedureResourceKind.PART,
            requested_part=self.component, selected_part=self.component,
            required_quantity=Decimal('1'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME, source='manual',
        )


class MutateStockAllocationTest(_AllocationFixtureMixin, TestCase):
    """Single-threaded correctness: idempotency, merge, and the availability guard."""

    def setUp(self):
        self.build_fixture(stock_quantity='10')
        self.line = self.make_line()

    def reserve(self, quantity, key, line=None):
        return mutate_stock_allocation(
            stock_item_id=self.stock.pk, line_id=(line or self.line).pk,
            requested_quantity=Decimal(quantity), actor=self.user,
            idempotency_key=key,
        )

    def test_idempotent_replay_returns_same_row(self):
        first = self.reserve('2', 'same-key')
        replay = self.reserve('2', 'same-key')
        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(JobKitAllocation.objects.count(), 1)
        self.assertEqual(self.stock.job_kit_allocation_count(), Decimal('2'))

    def test_second_key_merges_active_reservation(self):
        self.reserve('2', 'k1')
        merged = self.reserve('3', 'k2')
        self.assertEqual(JobKitAllocation.objects.count(), 1)
        self.assertEqual(merged.quantity, Decimal('5'))
        self.assertEqual(self.stock.job_kit_allocation_count(), Decimal('5'))

    def test_over_allocation_raises_and_reserves_nothing(self):
        self.stock.quantity = Decimal('1')
        self.stock.save(update_fields=['quantity'])
        with self.assertRaises(StockOverAllocation):
            self.reserve('2', 'too-much')
        self.assertEqual(JobKitAllocation.objects.count(), 0)

    def test_reservation_reduces_unallocated_quantity(self):
        self.reserve('4', 'k')
        self.assertEqual(self.stock.unallocated_quantity(), Decimal('6'))


@tag('migration_test')
@skipUnless(
    connection.vendor == 'postgresql',
    'Row-level locking is what is under test; SQLite serializes writes with a '
    'whole-database lock and reports "database table is locked" instead.',
)
class ConcurrentReservationTest(_AllocationFixtureMixin, TransactionTestCase):
    """Threaded proof that concurrent reservations never over-allocate.

    Tagged ``migration_test`` so the default ``invoke dev.test`` runs exclude
    it: a ``TransactionTestCase`` teardown flush truncates
    ``django_content_type`` and post_migrate regenerates the rows at new ids,
    corrupting the shared ``--keepdb`` database for every fixture-replaying
    suite that runs afterwards (users_owner FK violations). Run it explicitly
    with ``manage.py test tasks.tests.test_stock_allocation_concurrency`` on a
    disposable database. (``serialized_rollback`` does not help: with
    ``--keepdb`` Django skips the serialization it would restore from.)
    """

    def _worker(self, line_id, key, quantity, barrier, results, errors):
        barrier.wait()
        try:
            allocation = mutate_stock_allocation(
                stock_item_id=self.stock.pk, line_id=line_id,
                requested_quantity=Decimal(quantity), actor=self.user,
                idempotency_key=key,
            )
            results.append(allocation.pk)
        except StockOverAllocation:
            errors.append('over')
        except Exception as exc:  # pragma: no cover - surfaces real defects
            errors.append(f'UNEXPECTED:{exc!r}')
        finally:
            connection.close()

    def _run_contention(self, worker_count, quantity_each):
        lines = [self.make_line() for _ in range(worker_count)]
        barrier = threading.Barrier(worker_count)
        results, errors = [], []
        threads = [
            threading.Thread(
                target=self._worker,
                args=(line.pk, f'key-{i}', quantity_each, barrier, results, errors),
            )
            for i, line in enumerate(lines)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return results, errors

    def test_last_unit_is_reserved_exactly_once(self):
        self.build_fixture(stock_quantity='1')
        results, errors = self._run_contention(worker_count=8, quantity_each='1')

        self.assertNotIn(
            True, [e.startswith('UNEXPECTED') for e in errors], msg=errors
        )
        # Exactly one of eight contenders wins the single available unit.
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results) + len(errors), 8)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.job_kit_allocation_count(), Decimal('1'))
        self.assertEqual(self.stock.unallocated_quantity(), Decimal('0'))

    def test_partial_availability_is_respected_exactly(self):
        self.build_fixture(stock_quantity='3')
        results, errors = self._run_contention(worker_count=7, quantity_each='1')

        self.assertNotIn(
            True, [e.startswith('UNEXPECTED') for e in errors], msg=errors
        )
        # Exactly three of seven contenders win; aggregate reserved == available.
        self.assertEqual(len(results), 3)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.job_kit_allocation_count(), Decimal('3'))
        self.assertLessEqual(
            self.stock.job_kit_allocation_count(), self.stock.quantity
        )
