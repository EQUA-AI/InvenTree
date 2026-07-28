"""API behaviour for the board's work-order surface.

Previously ``tasks/tests.py``, a sibling of the ``tasks/tests/`` package. Python
prefers the package, so this module was unreachable *and* it made
``manage.py test tasks`` fail outright with "'tests' module incorrectly
imported". Moving it into the package fixes both.
"""

from django.urls import reverse

from tasks.models import WorkOrder

from assets.models import AssetMachine
from InvenTree.unit_test import InvenTreeAPITestCase


class WorkOrderBoardAPITest(InvenTreeAPITestCase):
    """API behaviour for the work orders the board lists."""

    # Named explicitly rather than 'all': upstream's assignRole(assign_all=True)
    # only sets can_view, so 'all' silently leaves every write forbidden.
    roles = [
        'work_order.view',
        'work_order.add',
        'work_order.change',
        'work_order.delete',
    ]

    def setUp(self):
        """Ensure a clean slate for each test."""
        super().setUp()
        WorkOrder.objects.all().delete()
        self.machine = AssetMachine.objects.create(name='Kanban API machine')

    def _create_card(self, **overrides):
        """Helper to create a card with sensible defaults."""
        data = {
            'title': 'Test card',
            'description': 'Initial description',
            'status': WorkOrder.STATUS_BACKLOG,
            'priority': WorkOrder.PRIORITY_MEDIUM,
            'assignee': 'Jordan Example',
            'tags': ['alpha', 'beta'],
            'company': 'Example Co',
            'company_contact_name': 'Alex Smith',
            'company_contact_phone': '+1 555 0100',
            'job_number': 'JOB-1234',
            'service_quote': 'SQ-9876',
            'machine': self.machine,
        }
        data.update(overrides)
        return WorkOrder.objects.create(**data)

    def test_create_card(self):
        """Cards can be created through the API."""
        url = reverse('kanban-card-list')
        payload = {
            'title': 'Persisted Card',
            'description': 'Created through the API',
            'status': WorkOrder.STATUS_IN_PROGRESS,
            'priority': WorkOrder.PRIORITY_HIGH,
            'due_date': '2025-01-05',
            'assignee': 'Taylor Example',
            'tags': ['urgent', 'backend'],
            'company': 'Example Co',
            'company_contact_name': 'Jamie Rivera',
            'company_contact_phone': '+1 555 0101',
            'job_number': 'J-0091',
            'service_quote': 'SQ-001',
            # Every work order is anchored to a machine; the API enforces it.
            'machine': self.machine.pk,
        }

        response = self.post(url, payload, expected_code=201)

        self.assertEqual(response.data['title'], payload['title'])
        self.assertTrue(response.data['is_active'])
        self.assertEqual(WorkOrder.objects.count(), 1)

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
        work_order = self._create_card()
        url = reverse('kanban-card-detail', kwargs={'pk': work_order.pk})

        self.delete(url, expected_code=204)

        work_order.refresh_from_db()

        self.assertFalse(work_order.is_active)

    def test_restore_card(self):
        """Soft deleted cards can be restored via the dedicated endpoint."""
        work_order = self._create_card(is_active=False)
        url = reverse('kanban-card-restore', kwargs={'pk': work_order.pk})

        response = self.post(url, expected_code=200)

        work_order.refresh_from_db()

        self.assertTrue(work_order.is_active)
        self.assertEqual(response.data['id'], work_order.pk)

    def test_tag_filter(self):
        """Filtering by a tag returns matching cards."""
        work_order = self._create_card(tags=['priority', 'backend'])
        self._create_card(title='Other Card', tags=['frontend'])

        url = reverse('kanban-card-list')
        response = self.get(url, {'tags': 'backend'}, expected_code=200)

        ids = [entry['id'] for entry in response.data]
        self.assertIn(work_order.pk, ids)

        response = self.get(url, {'tags': 'frontend'}, expected_code=200)
        ids = [entry['id'] for entry in response.data]
        self.assertNotIn(work_order.pk, ids)
