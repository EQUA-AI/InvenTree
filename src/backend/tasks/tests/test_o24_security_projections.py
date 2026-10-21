"""Shared Work must not disclose a foreign machine's live location."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from rest_framework.test import APIClient

from assets.models import AssetMachine, Client, ClientScopeGrant
from company.models import Company
from tasks.models import WorkOrder
from tasks.serializers import WorkOrderBoardSerializer


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='no-location-fallback',
)
class SharedWorkMachineLocationTests(TestCase):
    """Exercise every response that inherits the Board's machine fields."""

    def setUp(self):
        """Grant Work view and client A, then share Work on client B's machine."""
        self.actor = get_user_model().objects.create_user(username='location-reader')
        group = Group.objects.create(name='Shared Work location readers')
        self.rule = group.rule_sets.get(name='work_order')
        self.rule.can_view = True
        self.rule.save()
        self.actor.groups.add(group)
        own = Client.objects.create(name='Location own', code='location-own')
        foreign = Client.objects.create(name='Location foreign', code='location-foreign')
        self.own_grant = ClientScopeGrant.objects.create(user=self.actor, client=own)
        self.machine = AssetMachine.objects.create(
            name='Shared Work machine', client=foreign, location='Private bay 17'
        )
        self.work = WorkOrder.objects.create(
            title='Shared location Work',
            customer=Company.objects.create(name='Location customer', is_customer=True),
            machine=self.machine,
        )
        self.api = APIClient()
        self.api.force_authenticate(self.actor)

    def assert_locations(self, expected):
        """Check collections, detail and overview without hiding Work identity."""
        for path, shape in [
            ('/api/kanban/work-orders/', 'board'),
            ('/api/kanban/work-orders/list/', 'table'),
            (f'/api/kanban/work-orders/{self.work.pk}/', 'detail'),
            (reverse('kanban-card-overview', kwargs={'pk': self.work.pk}), 'overview'),
        ]:
            with self.subTest(surface=shape, expected=expected):
                response = self.api.get(path)
                self.assertEqual(response.status_code, 200)
                rows = response.data['results'] if shape == 'table' else response.data
                row = (
                    next(r for r in rows if r['id'] == self.work.pk)
                    if shape in ('board', 'table')
                    else rows
                )
                self.assertEqual(row['machine'], self.machine.pk)
                self.assertEqual(row['machine_name'], self.machine.name)
                self.assertEqual(row['machine_location'], expected)

    def test_location_requires_current_machine_scope_on_every_work_surface(self):
        """Shared Work is readable before and after a live client grant is revoked."""
        self.assert_locations(None)
        grant = ClientScopeGrant.objects.create(user=self.actor, client=self.machine.client)
        self.assert_locations('Private bay 17')
        grant.delete()
        self.machine.location = 'Private bay changed after revocation'
        self.machine.save(update_fields=['location'])
        self.assert_locations(None)
        self.own_grant.delete()
        self.assert_locations(None)

    def test_serializer_without_actor_does_not_disclose_location(self):
        """A caller omitting request context has no machine authority."""
        self.assertIsNone(WorkOrderBoardSerializer(self.work).data['machine_location'])

    def test_window_and_command_responses_preserve_scoped_location(self):
        """Manual serializer callers pass the actor and recheck fresh scope."""
        self.rule.can_change = True
        self.rule.save()
        grant = ClientScopeGrant.objects.create(user=self.actor, client=self.machine.client)
        for allowed in (True, False):
            if not allowed:
                grant.delete()
            expected = self.machine.location if allowed else None
            response = self.api.get(reverse('kanban-schedule-window'))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['cards'][0]['machine_location'], expected)
            self.work.refresh_from_db()
            response = self.api.post(
                reverse('kanban-command-update', kwargs={'pk': self.work.pk}),
                {
                    'expected_version': self.work.lifecycle_version,
                    'idempotency_key': f'location-command-{allowed}',
                    'fields': {'title': f'Shared location Work {allowed}'},
                },
                format='json',
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['machine_location'], expected)
