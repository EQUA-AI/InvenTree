"""Tests for the tasks application."""

from django.urls import reverse

from InvenTree.unit_test import InvenTreeAPITestCase

from .models import KanbanCard


class KanbanCardAPITest(InvenTreeAPITestCase):
    """API behaviour for Kanban cards."""

    roles = 'all'

    def setUp(self):
        """Ensure a clean slate for each test."""

        super().setUp()
        KanbanCard.objects.all().delete()

    def _create_card(self, **overrides):
        """Helper to create a card with sensible defaults."""

        data = {
            'title': 'Test card',
            'description': 'Initial description',
            'status': KanbanCard.STATUS_BACKLOG,
            'priority': KanbanCard.PRIORITY_MEDIUM,
            'assignee': 'Jordan Example',
            'tags': ['alpha', 'beta'],
            'company': 'Example Co',
            'company_contact_name': 'Alex Smith',
            'company_contact_phone': '+1 555 0100',
            'job_number': 'JOB-1234',
            'service_quote': 'SQ-9876',
        }
        data.update(overrides)
        return KanbanCard.objects.create(**data)

    def test_create_card(self):
        """Cards can be created through the API."""

        url = reverse('kanban-card-list')
        payload = {
            'title': 'Persisted Card',
            'description': 'Created through the API',
            'status': KanbanCard.STATUS_IN_PROGRESS,
            'priority': KanbanCard.PRIORITY_HIGH,
            'due_date': '2025-01-05',
            'assignee': 'Taylor Example',
            'tags': ['urgent', 'backend'],
            'company': 'Example Co',
            'company_contact_name': 'Jamie Rivera',
            'company_contact_phone': '+1 555 0101',
            'job_number': 'J-0091',
            'service_quote': 'SQ-001',
        }

        response = self.post(url, payload, expected_code=201)

        self.assertEqual(response.data['title'], payload['title'])
        self.assertTrue(response.data['is_active'])
        self.assertEqual(KanbanCard.objects.count(), 1)

    def test_list_excludes_inactive(self):
        """Inactive cards are hidden from the default listing."""

        active = self._create_card(title='Active Card')
        inactive = self._create_card(title='Inactive Card', is_active=False)

        url = reverse('kanban-card-list')
        response = self.get(url, expected_code=200)

        titles = [entry['title'] for entry in response.data]

        self.assertIn(active.title, titles)
        self.assertNotIn(inactive.title, titles)

    def test_soft_delete(self):
        """Deleting a card toggles the active flag instead of removing it."""

        card = self._create_card()
        url = reverse('kanban-card-detail', kwargs={'pk': card.pk})

        self.delete(url, expected_code=204)

        card.refresh_from_db()

        self.assertFalse(card.is_active)

    def test_restore_card(self):
        """Soft deleted cards can be restored via the dedicated endpoint."""

        card = self._create_card(is_active=False)
        url = reverse('kanban-card-restore', kwargs={'pk': card.pk})

        response = self.post(url, expected_code=200)

        card.refresh_from_db()

        self.assertTrue(card.is_active)
        self.assertEqual(response.data['id'], card.pk)

    def test_tag_filter(self):
        """Filtering by a tag returns matching cards."""

        card = self._create_card(tags=['priority', 'backend'])
        self._create_card(title='Other Card', tags=['frontend'])

        url = reverse('kanban-card-list')
        response = self.get(url, {'tags': 'backend'}, expected_code=200)

        ids = [entry['id'] for entry in response.data]
        self.assertIn(card.pk, ids)

        response = self.get(url, {'tags': 'frontend'}, expected_code=200)
        ids = [entry['id'] for entry in response.data]
        self.assertNotIn(card.pk, ids)
