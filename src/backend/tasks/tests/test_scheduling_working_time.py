"""Working-time integration into the schedule/resize commands (S6).

Runs under USE_TZ=True so the derived instants are real (the naive test default
would mask timezone/DST behaviour).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from assets.models import AssetMachine
from tasks.models import KanbanCard, WorkingCalendar
from tasks.services import scheduling


def _dt(y, m, d, h=9, tz='UTC'):
    return datetime(y, m, d, h, tzinfo=ZoneInfo(tz))


@override_settings(USE_TZ=True)
class ScheduleDerivesEndFromDurationTest(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username='wt-actor', email='wt@example.com', password='pw'
        )
        self.machine = AssetMachine.objects.create(name='WT Press')
        # A Mon–Fri 09:00–17:00 UTC calendar for this machine.
        WorkingCalendar.objects.create(name='WT cal', machine=self.machine)

    def _card(self, **kw):
        return KanbanCard.objects.create(
            title='WT WO', status='backlog', priority='low',
            machine=self.machine, **kw,
        )

    def test_move_derives_end_from_working_minutes(self):
        # 4h job starting Friday 16:00: 1h Fri (16:00–17:00) + 3h Mon (09:00–12:00)
        # -> Mon 12:00. The weekend contributes no working time.
        card = self._card(estimated_minutes=240)

        scheduling.schedule_work_order(
            work_order_id=card.pk,
            actor=self.actor,
            expected_version=card.lifecycle_version,
            idempotency_key='wt1',
            scheduled_start=_dt(2026, 8, 7, 16),  # Friday 16:00
            scheduled_end=None,
        )

        card.refresh_from_db()
        end = card.scheduled_end.astimezone(ZoneInfo('UTC'))
        self.assertEqual((end.month, end.day, end.hour), (8, 10, 12))

    def test_explicit_end_is_honoured_verbatim(self):
        # Even with a duration, an explicit end wins (literal-window behaviour).
        card = self._card(estimated_minutes=240)

        scheduling.schedule_work_order(
            work_order_id=card.pk,
            actor=self.actor,
            expected_version=card.lifecycle_version,
            idempotency_key='wt2',
            scheduled_start=_dt(2026, 8, 3, 9),
            scheduled_end=_dt(2026, 8, 3, 10),
        )

        card.refresh_from_db()
        end = card.scheduled_end.astimezone(ZoneInfo('UTC'))
        self.assertEqual((end.day, end.hour), (3, 10))

    def test_move_without_duration_leaves_end_null(self):
        card = self._card()  # no estimated_minutes

        scheduling.schedule_work_order(
            work_order_id=card.pk,
            actor=self.actor,
            expected_version=card.lifecycle_version,
            idempotency_key='wt3',
            scheduled_start=_dt(2026, 8, 3, 9),
            scheduled_end=None,
        )

        card.refresh_from_db()
        self.assertIsNone(card.scheduled_end)


@override_settings(USE_TZ=True)
class ResizeDerivesDurationFromSpanTest(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username='rz-actor', email='rz@example.com', password='pw'
        )
        self.machine = AssetMachine.objects.create(name='RZ Press')
        WorkingCalendar.objects.create(name='RZ cal', machine=self.machine)

    def test_dragging_end_across_weekend_counts_only_working_minutes(self):
        # Start Fri 16:00, drag end to Mon 10:00: working span = 1h Fri + 1h Mon
        # = 120 min, NOT the ~66h of wall-clock the span covers.
        card = KanbanCard.objects.create(
            title='RZ WO', status='backlog', priority='low',
            machine=self.machine,
            scheduled_start=_dt(2026, 8, 7, 16),
        )

        scheduling.resize_work_order(
            work_order_id=card.pk,
            actor=self.actor,
            expected_version=card.lifecycle_version,
            idempotency_key='rz1',
            scheduled_end=_dt(2026, 8, 10, 10),
        )

        card.refresh_from_db()
        self.assertEqual(card.estimated_minutes, 120)

    def test_explicit_minutes_still_wins(self):
        card = KanbanCard.objects.create(
            title='RZ WO2', status='backlog', priority='low',
            machine=self.machine,
            scheduled_start=_dt(2026, 8, 3, 9),
        )

        scheduling.resize_work_order(
            work_order_id=card.pk,
            actor=self.actor,
            expected_version=card.lifecycle_version,
            idempotency_key='rz2',
            estimated_minutes=90,
            scheduled_end=_dt(2026, 8, 3, 16),
        )

        card.refresh_from_db()
        self.assertEqual(card.estimated_minutes, 90)
