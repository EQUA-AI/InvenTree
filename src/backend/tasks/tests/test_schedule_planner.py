"""Tests for the deterministic schedule planner (Phase 6b).

Under USE_TZ=True (the helpers are timezone-aware) and against the default
Mon–Fri 09:00–17:00 UTC fallback calendar, so no WorkingCalendar rows are needed.
"""

from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from assets.models import AssetMachine
from tasks.models import WorkOrder, WorkOrderDependency, WorkOrderLifecycle
from tasks.services.schedule_planner import PlanRequest, plan_schedule


def _utc(y, m, d, h=9, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=dt_timezone.utc)


# 2026-08-03 is a Monday.
HORIZON = _utc(2026, 8, 3, 9)


@override_settings(USE_TZ=True)
class PlanScheduleTest(TestCase):
    def setUp(self):
        self.m1 = AssetMachine.objects.create(name='P1')
        self.m2 = AssetMachine.objects.create(name='P2')

    def _card(self, machine=None, minutes=120, **kw):
        return WorkOrder.objects.create(
            title=kw.pop('title', 'WO'),
            status='backlog',
            priority=kw.pop('priority', 'medium'),
            machine=machine or self.m1,
            estimated_minutes=minutes,
            **kw,
        )

    def _plan(self, ids, **kw):
        return plan_schedule(
            PlanRequest(candidate_ids=ids, horizon_start=HORIZON, **kw)
        )

    def _op(self, result, work_order_id):
        return next(o for o in result.operations if o.work_order_id == work_order_id)

    def test_single_card_placed_at_horizon(self):
        work_order = self._card(minutes=120)
        result = self._plan([work_order.id])

        op = self._op(result, work_order.id)
        self.assertEqual(op.new_start, _utc(2026, 8, 3, 9))
        self.assertEqual(op.new_end, _utc(2026, 8, 3, 11))

    def test_card_without_duration_is_unscheduled(self):
        work_order = self._card(minutes=None)
        result = self._plan([work_order.id])

        self.assertIn(work_order.id, result.unscheduled)
        self.assertTrue(any('no estimated duration' in w for w in result.warnings))
        self.assertEqual(result.operations, [])

    def test_finish_to_start_dependency(self):
        a = self._card(machine=self.m1, minutes=120, title='A')
        b = self._card(machine=self.m2, minutes=120, title='B')
        WorkOrderDependency.objects.create(predecessor=a, successor=b)

        result = self._plan([a.id, b.id])

        # A at 09:00–11:00; B (FS) starts at or after A's end.
        self.assertEqual(self._op(result, a.id).new_start, _utc(2026, 8, 3, 9))
        self.assertGreaterEqual(
            self._op(result, b.id).new_start, _utc(2026, 8, 3, 11)
        )

    def test_start_to_start_dependency(self):
        a = self._card(machine=self.m1, minutes=120, title='A')
        b = self._card(machine=self.m2, minutes=60, title='B')
        WorkOrderDependency.objects.create(
            predecessor=a, successor=b, dependency_type='SS', lag_minutes=60
        )

        result = self._plan([a.id, b.id])
        # B starts 60 working minutes after A's start (10:00).
        self.assertEqual(self._op(result, b.id).new_start, _utc(2026, 8, 3, 10))

    def test_same_machine_cards_do_not_overlap(self):
        a = self._card(machine=self.m1, minutes=120, title='A')
        b = self._card(machine=self.m1, minutes=120, title='B')

        result = self._plan([a.id, b.id])
        a_op = self._op(result, a.id)
        b_op = self._op(result, b.id)

        # The two placements must not overlap on the shared machine.
        self.assertFalse(
            a_op.new_start < b_op.new_end and b_op.new_start < a_op.new_end
        )

    def test_different_machines_may_run_concurrently(self):
        a = self._card(machine=self.m1, minutes=120, title='A')
        b = self._card(machine=self.m2, minutes=120, title='B')

        result = self._plan([a.id, b.id])
        # Both can start at the horizon.
        self.assertEqual(self._op(result, a.id).new_start, _utc(2026, 8, 3, 9))
        self.assertEqual(self._op(result, b.id).new_start, _utc(2026, 8, 3, 9))

    def test_higher_priority_scheduled_first(self):
        low = self._card(machine=self.m1, minutes=120, title='low', priority='low')
        high = self._card(
            machine=self.m1, minutes=120, title='high', priority='high'
        )

        result = self._plan([low.id, high.id])
        # On the shared machine, the high-priority card takes the earlier slot.
        self.assertEqual(self._op(result, high.id).new_start, _utc(2026, 8, 3, 9))
        self.assertGreaterEqual(
            self._op(result, low.id).new_start, self._op(result, high.id).new_end
        )

    def test_locked_card_is_not_moved(self):
        locked = self._card(
            machine=self.m1, minutes=120, title='locked',
            scheduled_start=_utc(2026, 8, 3, 9),
            scheduled_end=_utc(2026, 8, 3, 11),
        )
        other = self._card(machine=self.m1, minutes=120, title='other')

        result = self._plan([locked.id, other.id], locked_ids=frozenset({locked.id}))

        # The locked card produces no operation; the other schedules around it.
        self.assertFalse(any(o.work_order_id == locked.id for o in result.operations))
        self.assertGreaterEqual(
            self._op(result, other.id).new_start, _utc(2026, 8, 3, 11)
        )

    def test_completed_card_is_skipped(self):
        done = self._card(
            machine=self.m1, minutes=120, title='done',
            lifecycle_status=WorkOrderLifecycle.COMPLETED,
        )
        result = self._plan([done.id])
        self.assertEqual(result.operations, [])

    def test_allow_move_existing_false_freezes_scheduled_cards(self):
        scheduled = self._card(
            machine=self.m1, minutes=120, title='scheduled',
            scheduled_start=_utc(2026, 8, 3, 9),
            scheduled_end=_utc(2026, 8, 3, 11),
        )
        result = self._plan([scheduled.id], allow_move_existing=False)
        self.assertEqual(result.operations, [])

    def test_no_operation_when_already_in_place(self):
        # A card already sitting exactly where the planner would put it: no-op.
        work_order = self._card(
            machine=self.m1, minutes=120,
            scheduled_start=_utc(2026, 8, 3, 9),
            scheduled_end=_utc(2026, 8, 3, 11),
        )
        result = self._plan([work_order.id])
        self.assertEqual(result.operations, [])

    def test_is_deterministic(self):
        a = self._card(machine=self.m1, minutes=120, title='A')
        b = self._card(machine=self.m1, minutes=90, title='B', priority='high')
        c = self._card(machine=self.m2, minutes=60, title='C')
        WorkOrderDependency.objects.create(predecessor=a, successor=c)

        first = self._plan([a.id, b.id, c.id])
        second = self._plan([a.id, b.id, c.id])

        def signature(r):
            return sorted(
                (o.work_order_id, o.new_start.isoformat(), o.new_end.isoformat())
                for o in r.operations
            )

        self.assertEqual(signature(first), signature(second))

    def test_assignee_conflict_optional(self):
        user = get_user_model().objects.create_user(
            username='tech', email='t@example.com', password='pw'
        )
        # Same assignee, different machines.
        a = self._card(machine=self.m1, minutes=120, title='A', assigned_to=user)
        b = self._card(machine=self.m2, minutes=120, title='B', assigned_to=user)

        without = self._plan([a.id, b.id], check_assignee=False)
        # Without the assignee check, both start at the horizon.
        self.assertEqual(
            self._op(without, a.id).new_start,
            self._op(without, b.id).new_start,
        )

        # Reset so the second plan sees them unscheduled again.
        WorkOrder.objects.filter(id__in=[a.id, b.id]).update(
            scheduled_start=None, scheduled_end=None
        )
        with_check = self._plan([a.id, b.id], check_assignee=True)
        a_op = self._op(with_check, a.id)
        b_op = self._op(with_check, b.id)
        # With the check, the same tech's jobs cannot overlap.
        self.assertFalse(
            a_op.new_start < b_op.new_end and b_op.new_start < a_op.new_end
        )
