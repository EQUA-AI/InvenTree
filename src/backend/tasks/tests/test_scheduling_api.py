"""Endpoint tests for the scheduling command + schedule-window API (S5)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from assets.models import AssetMachine
from tasks.models import WorkOrder, WorkOrderDeletionRecord
from tasks.tests.tz_support import iso
from users.models import RuleSet
from users.ruleset import RuleSetEnum


class SchedulingApiTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='cmd-sup', email='c@example.com', password='pw'
        )
        self.client.force_login(self.user)
        self.machine = AssetMachine.objects.create(name='Cmd Press')

    def _card(self, **kw):
        return WorkOrder.objects.create(
            title=kw.pop('title', 'WO'),
            status='backlog',
            priority='low',
            machine=self.machine,
            **kw,
        )

    def _post(self, name, body, **kwargs):
        return self.client.post(
            reverse(name, kwargs=kwargs), data=body, content_type='application/json'
        )

    def test_create_command_requires_a_machine(self):
        response = self._post('kanban-command-create', {'title': 'No machine'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('machine', response.json())

    def test_create_command_makes_a_card(self):
        response = self._post(
            'kanban-command-create',
            {'title': 'Made via command', 'machine': self.machine.pk},
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            WorkOrder.objects.filter(title='Made via command').exists()
        )

    def test_schedule_command_sets_window(self):
        work_order = self._card()
        response = self._post(
            'kanban-command-schedule',
            {
                'expected_version': work_order.lifecycle_version,
                'scheduled_start': iso('2026-08-03T09:00:00Z'),
                'scheduled_end': iso('2026-08-03T13:00:00Z'),
            },
            pk=work_order.pk,
        )
        self.assertEqual(response.status_code, 200)
        work_order.refresh_from_db()
        self.assertIsNotNone(work_order.scheduled_start)

    def test_schedule_command_stale_version_is_409(self):
        work_order = self._card()
        response = self._post(
            'kanban-command-schedule',
            {
                'expected_version': work_order.lifecycle_version + 5,
                'scheduled_start': iso('2026-08-03T09:00:00Z'),
                'scheduled_end': iso('2026-08-03T13:00:00Z'),
            },
            pk=work_order.pk,
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'STALE_VERSION')

    def test_schedule_command_inverted_window_is_400(self):
        work_order = self._card()
        response = self._post(
            'kanban-command-schedule',
            {
                'expected_version': work_order.lifecycle_version,
                'scheduled_start': iso('2026-08-03T13:00:00Z'),
                'scheduled_end': iso('2026-08-03T09:00:00Z'),
            },
            pk=work_order.pk,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'INVALID_SCHEDULE')

    def test_schedule_command_requires_expected_version(self):
        work_order = self._card()
        response = self._post(
            'kanban-command-schedule',
            {'scheduled_start': iso('2026-08-03T09:00:00Z')},
            pk=work_order.pk,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('expected_version', response.json())

    def test_resize_command_sets_duration(self):
        work_order = self._card()
        response = self._post(
            'kanban-command-resize',
            {'expected_version': work_order.lifecycle_version, 'estimated_minutes': 180},
            pk=work_order.pk,
        )
        self.assertEqual(response.status_code, 200)
        work_order.refresh_from_db()
        self.assertEqual(work_order.estimated_minutes, 180)

    def test_update_command_changes_fields(self):
        work_order = self._card()
        response = self._post(
            'kanban-command-update',
            {
                'expected_version': work_order.lifecycle_version,
                'fields': {'title': 'Updated', 'priority': 'high'},
            },
            pk=work_order.pk,
        )
        self.assertEqual(response.status_code, 200)
        work_order.refresh_from_db()
        self.assertEqual(work_order.title, 'Updated')

    def test_delete_command_removes_card_and_leaves_record(self):
        work_order = self._card(title='Doomed')
        work_order_pk = work_order.pk
        response = self._post(
            'kanban-command-delete',
            {'expected_version': work_order.lifecycle_version, 'reason': 'cleanup'},
            pk=work_order_pk,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        self.assertFalse(WorkOrder.objects.filter(pk=work_order_pk).exists())
        self.assertTrue(
            WorkOrderDeletionRecord.objects.filter(work_order_pk=work_order_pk).exists()
        )

    def test_batch_apply_schedules_all(self):
        a = self._card(title='A')
        b = self._card(title='B')
        response = self._post(
            'kanban-schedule-apply',
            {
                'operations': [
                    {
                        'card_id': a.pk,
                        'expected_version': a.lifecycle_version,
                        'scheduled_start': iso('2026-08-03T09:00:00Z'),
                        'scheduled_end': iso('2026-08-03T12:00:00Z'),
                    },
                    {
                        'card_id': b.pk,
                        'expected_version': b.lifecycle_version,
                        'scheduled_start': iso('2026-08-04T09:00:00Z'),
                        'scheduled_end': iso('2026-08-04T12:00:00Z'),
                    },
                ]
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['applied']), 2)

    def test_schedule_window_returns_stable_shape(self):
        work_order = self._card()
        work_order.scheduled_start = None
        work_order.due_date = '2026-08-15'
        work_order.save()

        response = self.client.get(
            reverse('kanban-schedule-window'),
            {'min_date': '2026-08-01', 'max_date': '2026-08-31'},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn('cards', body)
        self.assertEqual(body['dependencies'], [])
        self.assertEqual(body['warnings'], [])
        titles = {c['title'] for c in body['cards']}
        self.assertIn(work_order.title, titles)


class SchedulingApiPermissionTest(TestCase):
    """Command endpoints are gated per-action on the work_order ruleset."""

    def setUp(self):
        self.group = Group.objects.create(name='cmd-techs')
        self.user = get_user_model().objects.create_user(
            username='cmd-tech', email='ct@example.com', password='pw'
        )
        self.user.groups.add(self.group)
        self.client.force_login(self.user)
        self.machine = AssetMachine.objects.create(name='Perm Press')
        self.work_order = WorkOrder.objects.create(
            title='Perm', status='backlog', priority='low', machine=self.machine
        )
        self.ruleset = RuleSet.objects.get(
            group=self.group, name=RuleSetEnum.WORK_ORDER
        )

    def _set(self, **perms):
        for field, value in perms.items():
            setattr(self.ruleset, field, value)
        self.ruleset.save()

    def _post(self, name, body, **kwargs):
        return self.client.post(
            reverse(name, kwargs=kwargs), data=body, content_type='application/json'
        )

    def test_schedule_needs_change_permission(self):
        self._set(can_view=True, can_change=False)
        response = self._post(
            'kanban-command-schedule',
            {
                'expected_version': self.work_order.lifecycle_version,
                'scheduled_start': iso('2026-08-03T09:00:00Z'),
                'scheduled_end': iso('2026-08-03T13:00:00Z'),
            },
            pk=self.work_order.pk,
        )
        self.assertEqual(response.status_code, 403)

    def test_delete_needs_delete_permission(self):
        # change but not delete: schedule allowed, delete refused.
        self._set(can_view=True, can_change=True, can_delete=False)

        allowed = self._post(
            'kanban-command-schedule',
            {
                'expected_version': self.work_order.lifecycle_version,
                'scheduled_start': iso('2026-08-03T09:00:00Z'),
                'scheduled_end': iso('2026-08-03T13:00:00Z'),
            },
            pk=self.work_order.pk,
        )
        self.assertEqual(allowed.status_code, 200)
        self.work_order.refresh_from_db()

        refused = self._post(
            'kanban-command-delete',
            {'expected_version': self.work_order.lifecycle_version},
            pk=self.work_order.pk,
        )
        self.assertEqual(refused.status_code, 403)
        self.assertTrue(WorkOrder.objects.filter(pk=self.work_order.pk).exists())

    def test_create_needs_add_permission(self):
        # view+change but not add. add implies change, but change does not imply
        # add, so this is a representable "cannot create" state.
        self._set(can_view=True, can_change=True, can_add=False)
        response = self._post(
            'kanban-command-create',
            {'title': 'Nope', 'machine': self.machine.pk},
        )
        self.assertEqual(response.status_code, 403)
