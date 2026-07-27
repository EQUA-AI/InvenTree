"""Machine is required when creating a work order through the API (S3c).

Decision 5.6: every work order is anchored to a machine. This is enforced at the
serializer layer -- the create form, and every API/AI write path, must supply a
machine drawn from the assets list. The model column remains nullable for now, so
the DB-level non-null constraint and its data backfill are a separate follow-up;
these tests pin the API-level requirement that delivers the user-facing behaviour.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assets.models import AssetMachine
from tasks.models import WorkOrder


class MachineRequiredOnCreateTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='m-sup', email='m@example.com', password='pw'
        )
        self.client.force_login(self.user)
        self.machine = AssetMachine.objects.create(name='Required Press')
        self.list_url = reverse('kanban-card-list')

    def _create(self, **extra):
        payload = {'title': 'WO', 'status': 'backlog', 'priority': 'low', **extra}
        return self.client.post(
            self.list_url, data=payload, content_type='application/json'
        )

    def test_create_without_a_machine_is_rejected(self):
        response = self._create()

        self.assertEqual(response.status_code, 400)
        self.assertIn('machine', response.json())
        self.assertEqual(WorkOrder.objects.count(), 0)

    def test_create_with_a_machine_succeeds(self):
        response = self._create(machine=self.machine.pk)

        self.assertEqual(response.status_code, 201)
        work_order = WorkOrder.objects.get()
        self.assertEqual(work_order.machine, self.machine)

    def test_create_with_a_null_machine_is_rejected(self):
        response = self._create(machine=None)

        self.assertEqual(response.status_code, 400)
        self.assertIn('machine', response.json())

    def test_create_with_an_unknown_machine_is_rejected(self):
        response = self._create(machine=999999)

        self.assertEqual(response.status_code, 400)
        self.assertIn('machine', response.json())

    def test_partial_update_does_not_require_resending_the_machine(self):
        """A machineless PATCH of an existing card must still work.

        The requirement is on *creating* a work order, not on every edit; forcing
        the machine back through on each PATCH would break ordinary field edits.
        """
        work_order = WorkOrder.objects.create(
            title='existing',
            status='backlog',
            priority='low',
            machine=self.machine,
        )

        response = self.client.patch(
            reverse('kanban-card-detail', kwargs={'pk': work_order.pk}),
            data={'title': 'renamed'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        work_order.refresh_from_db()
        self.assertEqual(work_order.title, 'renamed')
        self.assertEqual(work_order.machine, self.machine)

    def test_machine_can_be_reassigned_on_patch(self):
        work_order = WorkOrder.objects.create(
            title='movable',
            status='backlog',
            priority='low',
            machine=self.machine,
        )
        other = AssetMachine.objects.create(name='Other Press')

        response = self.client.patch(
            reverse('kanban-card-detail', kwargs={'pk': work_order.pk}),
            data={'machine': other.pk},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        work_order.refresh_from_db()
        self.assertEqual(work_order.machine, other)
