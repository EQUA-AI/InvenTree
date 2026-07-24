"""Tests for scheduling dependencies: service + API (S6b)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from assets.models import AssetMachine
from tasks.models import KanbanCard, KanbanCardDependency
from tasks.services import scheduling
from users.models import RuleSet
from users.ruleset import RuleSetEnum


class DependencyServiceTest(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username='dep-actor', email='d@example.com', password='pw'
        )
        self.machine = AssetMachine.objects.create(name='Dep Press')
        self.a = self._card('A')
        self.b = self._card('B')
        self.c = self._card('C')

    def _card(self, title):
        return KanbanCard.objects.create(
            title=title, status='backlog', priority='low', machine=self.machine
        )

    def test_create_dependency(self):
        dep = scheduling.create_dependency(
            from_card_id=self.a.pk, to_card_id=self.b.pk, actor=self.actor
        )
        self.assertEqual(dep.from_card_id, self.a.pk)
        self.assertEqual(dep.to_card_id, self.b.pk)
        self.assertEqual(dep.dependency_type, 'FS')

    def test_create_records_an_event_on_the_successor(self):
        scheduling.create_dependency(
            from_card_id=self.a.pk, to_card_id=self.b.pk, actor=self.actor
        )
        self.assertTrue(
            self.b.events.filter(event_type='DEPENDENCY_ADDED').exists()
        )

    def test_self_dependency_is_rejected(self):
        with self.assertRaises(scheduling.InvalidDependency):
            scheduling.create_dependency(
                from_card_id=self.a.pk, to_card_id=self.a.pk, actor=self.actor
            )

    def test_direct_cycle_is_rejected(self):
        scheduling.create_dependency(
            from_card_id=self.a.pk, to_card_id=self.b.pk, actor=self.actor
        )
        with self.assertRaises(scheduling.DependencyCycle):
            scheduling.create_dependency(
                from_card_id=self.b.pk, to_card_id=self.a.pk, actor=self.actor
            )

    def test_transitive_cycle_is_rejected(self):
        # A -> B -> C, then C -> A would close the loop.
        scheduling.create_dependency(
            from_card_id=self.a.pk, to_card_id=self.b.pk, actor=self.actor
        )
        scheduling.create_dependency(
            from_card_id=self.b.pk, to_card_id=self.c.pk, actor=self.actor
        )
        with self.assertRaises(scheduling.DependencyCycle):
            scheduling.create_dependency(
                from_card_id=self.c.pk, to_card_id=self.a.pk, actor=self.actor
            )

    def test_diamond_is_allowed(self):
        # A -> B, A -> C, B -> D, C -> D is a DAG, not a cycle.
        d = self._card('D')
        scheduling.create_dependency(
            from_card_id=self.a.pk, to_card_id=self.b.pk, actor=self.actor
        )
        scheduling.create_dependency(
            from_card_id=self.a.pk, to_card_id=self.c.pk, actor=self.actor
        )
        scheduling.create_dependency(
            from_card_id=self.b.pk, to_card_id=d.pk, actor=self.actor
        )
        # This must not raise.
        scheduling.create_dependency(
            from_card_id=self.c.pk, to_card_id=d.pk, actor=self.actor
        )
        self.assertEqual(KanbanCardDependency.objects.count(), 4)

    def test_create_is_idempotent(self):
        first = scheduling.create_dependency(
            from_card_id=self.a.pk, to_card_id=self.b.pk, actor=self.actor
        )
        second = scheduling.create_dependency(
            from_card_id=self.a.pk, to_card_id=self.b.pk, actor=self.actor
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(KanbanCardDependency.objects.count(), 1)

    def test_all_four_types_between_same_pair(self):
        for dep_type in ('FS', 'SS', 'FF', 'SF'):
            scheduling.create_dependency(
                from_card_id=self.a.pk,
                to_card_id=self.b.pk,
                actor=self.actor,
                dependency_type=dep_type,
            )
        self.assertEqual(KanbanCardDependency.objects.count(), 4)

    def test_lag_minutes_can_be_negative(self):
        dep = scheduling.create_dependency(
            from_card_id=self.a.pk,
            to_card_id=self.b.pk,
            actor=self.actor,
            lag_minutes=-30,
        )
        self.assertEqual(dep.lag_minutes, -30)

    def test_unknown_card_is_rejected(self):
        with self.assertRaises(scheduling.UnknownWorkOrder):
            scheduling.create_dependency(
                from_card_id=self.a.pk, to_card_id=999999, actor=self.actor
            )

    def test_delete_dependency(self):
        dep = scheduling.create_dependency(
            from_card_id=self.a.pk, to_card_id=self.b.pk, actor=self.actor
        )
        self.assertTrue(
            scheduling.delete_dependency(dependency_id=dep.pk, actor=self.actor)
        )
        self.assertFalse(KanbanCardDependency.objects.filter(pk=dep.pk).exists())
        self.assertTrue(
            self.b.events.filter(event_type='DEPENDENCY_REMOVED').exists()
        )

    def test_delete_missing_dependency_returns_false(self):
        self.assertFalse(
            scheduling.delete_dependency(dependency_id=999999, actor=self.actor)
        )


class DependencyApiTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='dep-sup', email='ds@example.com', password='pw'
        )
        self.client.force_login(self.user)
        self.machine = AssetMachine.objects.create(name='Dep API Press')
        self.a = self._card('A')
        self.b = self._card('B')

    def _card(self, title):
        return KanbanCard.objects.create(
            title=title, status='backlog', priority='low', machine=self.machine
        )

    def test_create_via_api(self):
        response = self.client.post(
            reverse('kanban-dependency-create'),
            data={'from_card': self.a.pk, 'to_card': self.b.pk},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(KanbanCardDependency.objects.exists())

    def test_cycle_via_api_is_409(self):
        self.client.post(
            reverse('kanban-dependency-create'),
            data={'from_card': self.a.pk, 'to_card': self.b.pk},
            content_type='application/json',
        )
        response = self.client.post(
            reverse('kanban-dependency-create'),
            data={'from_card': self.b.pk, 'to_card': self.a.pk},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'DEPENDENCY_CYCLE')

    def test_delete_via_api(self):
        dep = KanbanCardDependency.objects.create(
            from_card=self.a, to_card=self.b
        )
        response = self.client.delete(
            reverse('kanban-dependency-detail', kwargs={'pk': dep.pk})
        )
        self.assertEqual(response.status_code, 204)

    def test_window_includes_dependencies(self):
        self.a.scheduled_start = '2026-08-03T09:00:00Z'
        self.a.save()
        self.b.scheduled_start = '2026-08-04T09:00:00Z'
        self.b.save()
        KanbanCardDependency.objects.create(from_card=self.a, to_card=self.b)

        response = self.client.get(
            reverse('kanban-schedule-window'),
            {'min_date': '2026-08-01', 'max_date': '2026-08-31'},
        )
        self.assertEqual(response.status_code, 200)
        deps = response.json()['dependencies']
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]['from_card'], self.a.pk)


class DependencyPermissionTest(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name='dep-techs')
        self.user = get_user_model().objects.create_user(
            username='dep-tech', email='dt@example.com', password='pw'
        )
        self.user.groups.add(self.group)
        self.client.force_login(self.user)
        self.machine = AssetMachine.objects.create(name='Dep Perm Press')
        self.a = KanbanCard.objects.create(
            title='A', status='backlog', priority='low', machine=self.machine
        )
        self.b = KanbanCard.objects.create(
            title='B', status='backlog', priority='low', machine=self.machine
        )
        self.ruleset = RuleSet.objects.get(
            group=self.group, name=RuleSetEnum.WORK_ORDER
        )

    def test_create_requires_change(self):
        self.ruleset.can_view = True
        self.ruleset.can_change = False
        self.ruleset.save()

        response = self.client.post(
            reverse('kanban-dependency-create'),
            data={'from_card': self.a.pk, 'to_card': self.b.pk},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
