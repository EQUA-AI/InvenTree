"""Tests for persisted board columns.

Board columns used to be frontend ``useState``, so add/reorder/delete never
reached the server: a refresh reset the board to four hardcoded defaults and any
card in a custom column vanished. These tests cover the persisted model, the
seed migration's guarantees, and the CRUD/reorder API.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from tasks.models import WorkOrder, KanbanColumn

SEED_KEYS = ['backlog', 'in-progress', 'review', 'done']


class KanbanColumnSeedTest(TestCase):
    """The seed migration must reproduce the four original columns exactly."""

    def test_default_columns_are_seeded(self):
        keys = list(
            KanbanColumn.objects.order_by('order').values_list('key', flat=True)
        )
        self.assertEqual(keys, SEED_KEYS)

    def test_seeded_columns_match_the_former_frontend_defaults(self):
        expected = {
            'backlog': ('Backlog', 'gray'),
            'in-progress': ('In Progress', 'indigo'),
            'review': ('In Review', 'yellow'),
            'done': ('Done', 'green'),
        }

        for key, (label, color) in expected.items():
            column = KanbanColumn.objects.get(key=key)
            self.assertEqual(column.label, label)
            self.assertEqual(column.color, color)
            self.assertTrue(column.is_default)

    def test_every_card_status_resolves_to_a_seeded_column(self):
        """The whole point of freezing the keys: no card is orphaned."""
        for key in SEED_KEYS:
            WorkOrder.objects.create(title=f'c-{key}', status=key, priority='low')

        statuses = set(
            WorkOrder.objects.values_list('status', flat=True).distinct()
        )
        column_keys = set(KanbanColumn.objects.values_list('key', flat=True))

        self.assertTrue(statuses.issubset(column_keys))


class KanbanColumnModelTest(TestCase):
    """Model behaviour independent of the API."""

    def test_card_count_only_counts_active_cards_in_the_column(self):
        column = KanbanColumn.objects.get(key='backlog')
        WorkOrder.objects.create(title='a', status='backlog', priority='low')
        WorkOrder.objects.create(title='b', status='backlog', priority='low')
        WorkOrder.objects.create(
            title='archived', status='backlog', priority='low', is_active=False
        )
        WorkOrder.objects.create(title='elsewhere', status='done', priority='low')

        self.assertEqual(column.card_count(), 2)
        self.assertEqual(column.card_count(active_only=False), 3)


class KanbanColumnApiTest(TestCase):
    """CRUD and reorder over the column endpoints."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='col-sup', email='col@example.com', password='pw'
        )
        self.client.force_login(self.user)
        self.list_url = reverse('kanban-column-list')
        self.reorder_url = reverse('kanban-column-reorder')

    def _detail_url(self, key):
        return reverse(
            'kanban-column-detail',
            kwargs={'pk': KanbanColumn.objects.get(key=key).pk},
        )

    def test_list_returns_seeded_columns_in_order(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([c['key'] for c in response.json()], SEED_KEYS)

    def test_list_is_not_paginated(self):
        self.assertIsInstance(self.client.get(self.list_url).json(), list)

    def test_card_count_is_reported(self):
        WorkOrder.objects.create(title='x', status='review', priority='low')

        rows = {c['key']: c for c in self.client.get(self.list_url).json()}
        self.assertEqual(rows['review']['card_count'], 1)
        self.assertEqual(rows['backlog']['card_count'], 0)

    def test_create_appends_to_the_right(self):
        response = self.client.post(
            self.list_url,
            data={'key': 'blocked', 'label': 'Blocked', 'color': 'red'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        column = KanbanColumn.objects.get(key='blocked')
        self.assertEqual(column.order, 4)
        self.assertFalse(column.is_default)

    def test_create_rejects_a_duplicate_key(self):
        response = self.client.post(
            self.list_url,
            data={'key': 'backlog', 'label': 'Dup'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)

    def test_create_rejects_a_non_slug_key(self):
        response = self.client.post(
            self.list_url,
            data={'key': 'not a slug', 'label': 'Bad'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)

    def test_relabel_and_recolor_are_allowed(self):
        response = self.client.patch(
            self._detail_url('backlog'),
            data={'label': 'To Do', 'color': 'blue'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        column = KanbanColumn.objects.get(key='backlog')
        self.assertEqual(column.label, 'To Do')
        self.assertEqual(column.color, 'blue')

    def test_key_cannot_be_changed(self):
        """Renaming a key would orphan every card in the column."""
        response = self.client.patch(
            self._detail_url('backlog'),
            data={'key': 'renamed'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('key', response.json())
        self.assertTrue(KanbanColumn.objects.filter(key='backlog').exists())

    def test_default_columns_cannot_be_deleted(self):
        response = self.client.delete(self._detail_url('done'))

        self.assertEqual(response.status_code, 400)
        self.assertTrue(KanbanColumn.objects.filter(key='done').exists())

    def test_a_custom_empty_column_can_be_deleted(self):
        KanbanColumn.objects.create(key='temp', label='Temp', order=9)

        response = self.client.delete(
            reverse(
                'kanban-column-detail',
                kwargs={'pk': KanbanColumn.objects.get(key='temp').pk},
            )
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(KanbanColumn.objects.filter(key='temp').exists())

    def test_a_column_with_cards_cannot_be_deleted(self):
        column = KanbanColumn.objects.create(key='busy', label='Busy', order=9)
        WorkOrder.objects.create(title='held', status='busy', priority='low')

        response = self.client.delete(
            reverse('kanban-column-detail', kwargs={'pk': column.pk})
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(KanbanColumn.objects.filter(key='busy').exists())

    def test_an_archived_card_does_not_block_deletion(self):
        column = KanbanColumn.objects.create(key='q', label='Q', order=9)
        WorkOrder.objects.create(
            title='gone', status='q', priority='low', is_active=False
        )

        response = self.client.delete(
            reverse('kanban-column-detail', kwargs={'pk': column.pk})
        )

        self.assertEqual(response.status_code, 204)

    def test_reorder_persists_a_new_order(self):
        response = self.client.post(
            self.reorder_url,
            data={'order': ['done', 'review', 'in-progress', 'backlog']},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        keys = list(
            KanbanColumn.objects.order_by('order').values_list('key', flat=True)
        )
        self.assertEqual(keys, ['done', 'review', 'in-progress', 'backlog'])

    def test_reorder_rejects_a_partial_key_set(self):
        response = self.client.post(
            self.reorder_url,
            data={'order': ['done', 'backlog']},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        # Order is unchanged.
        self.assertEqual(
            list(
                KanbanColumn.objects.order_by('order').values_list(
                    'key', flat=True
                )
            ),
            SEED_KEYS,
        )

    def test_reorder_rejects_an_unknown_key(self):
        response = self.client.post(
            self.reorder_url,
            data={'order': ['backlog', 'in-progress', 'review', 'nonexistent']},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)

    def test_reorder_rejects_a_non_list_payload(self):
        response = self.client.post(
            self.reorder_url,
            data={'order': 'backlog'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)


class KanbanColumnPermissionTest(TestCase):
    """Columns are gated on the work_order ruleset like the rest of the board."""

    def setUp(self):
        from django.contrib.auth.models import Group

        from users.models import RuleSet
        from users.ruleset import RuleSetEnum

        self.group = Group.objects.create(name='col-techs')
        self.user = get_user_model().objects.create_user(
            username='col-tech', email='ct@example.com', password='pw'
        )
        self.user.groups.add(self.group)
        self.client.force_login(self.user)

        self._ruleset = RuleSet.objects.get(
            group=self.group, name=RuleSetEnum.WORK_ORDER
        )

    def _set(self, **permissions):
        for field, value in permissions.items():
            setattr(self._ruleset, field, value)
        self._ruleset.save()

    def test_view_permission_is_required_to_list(self):
        self._set(can_view=False)
        self.assertEqual(
            self.client.get(reverse('kanban-column-list')).status_code, 403
        )

        self._set(can_view=True)
        self.assertEqual(
            self.client.get(reverse('kanban-column-list')).status_code, 200
        )

    def test_add_permission_is_required_to_create(self):
        self._set(can_view=True, can_add=False)
        response = self.client.post(
            reverse('kanban-column-list'),
            data={'key': 'nope', 'label': 'Nope'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_change_permission_is_required_to_reorder(self):
        self._set(can_view=True, can_change=False)
        response = self.client.post(
            reverse('kanban-column-reorder'),
            data={'order': SEED_KEYS},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
