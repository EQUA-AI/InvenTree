"""Endpoint tests for the plan (preview) and optimize (apply) API (Phase 6b)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from assets.models import AssetMachine
from tasks.models import KanbanCard
from users.models import RuleSet
from users.ruleset import RuleSetEnum


@override_settings(USE_TZ=True)
class SchedulePlanApiTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='plan-sup', email='p@example.com', password='pw'
        )
        self.client.force_login(self.user)
        self.machine = AssetMachine.objects.create(name='Plan Press')

    def _card(self, minutes=120, **kw):
        return KanbanCard.objects.create(
            title=kw.pop('title', 'WO'),
            status='backlog',
            priority='medium',
            machine=self.machine,
            estimated_minutes=minutes,
            **kw,
        )

    def test_plan_returns_operations_without_saving(self):
        a = self._card(title='A')
        b = self._card(title='B')

        response = self.client.post(
            reverse('kanban-schedule-plan'),
            data={
                'candidate_ids': [a.id, b.id],
                'horizon_start': '2026-08-03T09:00:00Z'
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body['operations']), 2)
        # Nothing was persisted by a plan.
        a.refresh_from_db()
        self.assertIsNone(a.scheduled_start)

    def test_plan_reports_unscheduled_for_missing_duration(self):
        card = self._card(minutes=None)
        response = self.client.post(
            reverse('kanban-schedule-plan'),
            data={'candidate_ids': [card.id]},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(card.id, response.json()['unscheduled'])

    def test_plan_rejects_bad_candidate_ids(self):
        response = self.client.post(
            reverse('kanban-schedule-plan'),
            data={'candidate_ids': 'not-a-list'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_optimize_applies_the_plan(self):
        a = self._card(title='A')
        b = self._card(title='B')

        response = self.client.post(
            reverse('kanban-schedule-optimize'),
            data={
                'candidate_ids': [a.id, b.id],
                'horizon_start': '2026-08-03T09:00:00Z'
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['applied']), 2)
        a.refresh_from_db()
        b.refresh_from_db()
        # Both are now scheduled, and on the shared machine they do not overlap.
        self.assertIsNotNone(a.scheduled_start)
        self.assertIsNotNone(b.scheduled_start)
        self.assertFalse(
            a.scheduled_start < b.scheduled_end
            and b.scheduled_start < a.scheduled_end
        )

    def test_optimize_with_nothing_to_do_is_a_noop(self):
        response = self.client.post(
            reverse('kanban-schedule-optimize'),
            data={'candidate_ids': []},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['applied'], [])


@override_settings(USE_TZ=True)
class SchedulePlanPermissionTest(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name='plan-techs')
        self.user = get_user_model().objects.create_user(
            username='plan-tech', email='pt@example.com', password='pw'
        )
        self.user.groups.add(self.group)
        self.client.force_login(self.user)
        self.machine = AssetMachine.objects.create(name='Plan Perm Press')
        self.card = KanbanCard.objects.create(
            title='WO', status='backlog', priority='low',
            machine=self.machine, estimated_minutes=120,
        )
        self.ruleset = RuleSet.objects.get(
            group=self.group, name=RuleSetEnum.WORK_ORDER
        )

    def _set(self, **perms):
        for field, value in perms.items():
            setattr(self.ruleset, field, value)
        self.ruleset.save()

    def test_plan_needs_view(self):
        self._set(can_view=False)
        response = self.client.post(
            reverse('kanban-schedule-plan'),
            data={'candidate_ids': [self.card.id]},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_optimize_needs_change(self):
        self._set(can_view=True, can_change=False)
        response = self.client.post(
            reverse('kanban-schedule-optimize'),
            data={'candidate_ids': [self.card.id]},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
