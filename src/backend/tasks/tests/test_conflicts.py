"""Tests for scheduling conflict detection (S6e)."""

from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assets.models import AssetMachine
from tasks.models import WorkOrder
from tasks.services.conflicts import detect_conflicts


def _utc(y, m, d, h=9):
    return datetime(y, m, d, h, tzinfo=dt_timezone.utc)


class DetectConflictsTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='conf', email='c@example.com', password='pw'
        )
        self.m1 = AssetMachine.objects.create(name='M1')
        self.m2 = AssetMachine.objects.create(name='M2')

    def _card(self, machine, start, end, assignee=None):
        return WorkOrder.objects.create(
            title='WO',
            status='backlog',
            priority='low',
            machine=machine,
            assigned_to=assignee,
            scheduled_start=start,
            scheduled_end=end,
        )

    def test_same_machine_overlap_is_flagged(self):
        a = self._card(self.m1, _utc(2026, 8, 3, 9), _utc(2026, 8, 3, 12))
        b = self._card(self.m1, _utc(2026, 8, 3, 11), _utc(2026, 8, 3, 14))

        warnings = detect_conflicts([a, b])

        machine_warnings = [w for w in warnings if w['code'] == 'machine_overlap']
        self.assertEqual(len(machine_warnings), 1)
        self.assertEqual(machine_warnings[0]['card_ids'], sorted([a.pk, b.pk]))
        self.assertEqual(machine_warnings[0]['machine_id'], self.m1.pk)

    def test_different_machines_do_not_conflict(self):
        a = self._card(self.m1, _utc(2026, 8, 3, 9), _utc(2026, 8, 3, 12))
        b = self._card(self.m2, _utc(2026, 8, 3, 9), _utc(2026, 8, 3, 12))

        self.assertEqual(detect_conflicts([a, b]), [])

    def test_adjacent_windows_do_not_conflict(self):
        # a ends exactly when b starts: half-open, no overlap.
        a = self._card(self.m1, _utc(2026, 8, 3, 9), _utc(2026, 8, 3, 12))
        b = self._card(self.m1, _utc(2026, 8, 3, 12), _utc(2026, 8, 3, 14))

        self.assertEqual(detect_conflicts([a, b]), [])

    def test_same_assignee_overlap_is_flagged(self):
        a = self._card(
            self.m1, _utc(2026, 8, 3, 9), _utc(2026, 8, 3, 12), assignee=self.user
        )
        b = self._card(
            self.m2, _utc(2026, 8, 3, 10), _utc(2026, 8, 3, 13), assignee=self.user
        )

        warnings = detect_conflicts([a, b])
        assignee_warnings = [w for w in warnings if w['code'] == 'assignee_overlap']
        self.assertEqual(len(assignee_warnings), 1)
        self.assertEqual(assignee_warnings[0]['assigned_to_id'], self.user.pk)

    def test_assignee_can_be_excluded(self):
        a = self._card(
            self.m1, _utc(2026, 8, 3, 9), _utc(2026, 8, 3, 12), assignee=self.user
        )
        b = self._card(
            self.m2, _utc(2026, 8, 3, 10), _utc(2026, 8, 3, 13), assignee=self.user
        )

        warnings = detect_conflicts([a, b], include_assignee=False)
        self.assertEqual(warnings, [])

    def test_unscheduled_cards_never_conflict(self):
        a = self._card(self.m1, None, None)
        b = self._card(self.m1, _utc(2026, 8, 3, 9), _utc(2026, 8, 3, 12))
        # a has no window; only a full-window pair can clash.
        self.assertEqual(detect_conflicts([a, b]), [])

    def test_three_way_overlap_reports_each_pair(self):
        a = self._card(self.m1, _utc(2026, 8, 3, 9), _utc(2026, 8, 3, 15))
        b = self._card(self.m1, _utc(2026, 8, 3, 10), _utc(2026, 8, 3, 12))
        c = self._card(self.m1, _utc(2026, 8, 3, 11), _utc(2026, 8, 3, 13))

        machine_warnings = [
            w for w in detect_conflicts([a, b, c]) if w['code'] == 'machine_overlap'
        ]
        # a-b, a-c, b-c all overlap.
        self.assertEqual(len(machine_warnings), 3)


class ScheduleWindowConflictApiTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='conf-sup', email='cs@example.com', password='pw'
        )
        self.client.force_login(self.user)
        self.machine = AssetMachine.objects.create(name='Conf Press')

    def test_window_surfaces_machine_conflict_as_warning(self):
        for start, end in [(9, 12), (11, 14)]:
            WorkOrder.objects.create(
                title='WO', status='backlog', priority='low',
                machine=self.machine,
                scheduled_start=_utc(2026, 8, 3, start),
                scheduled_end=_utc(2026, 8, 3, end),
            )

        response = self.client.get(
            reverse('kanban-schedule-window'),
            {'min_date': '2026-08-01', 'max_date': '2026-08-31'},
        )
        self.assertEqual(response.status_code, 200)
        warnings = response.json()['warnings']
        self.assertTrue(any(w['code'] == 'machine_overlap' for w in warnings))
