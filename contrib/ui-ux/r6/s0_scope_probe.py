"""Read-only observations of canonical and legacy maintenance scope parity."""

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from rest_framework.test import APIClient
from tasks.models import WorkOrder

from assets.models import AssetMachine, Client, ClientScopeGrant
from users.models import RuleSet


@override_settings(
    AIMMS_WORK_ORDERS_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
)
class S0ScopeObservation(TestCase):
    """Use real role/grant rows to retain observable scope and revocation proof."""

    def setUp(self):
        """Create a normal reader, two clients and one job per client."""
        self.actor = get_user_model().objects.create_user(
            username='s0-scope-reader', password='synthetic-only'
        )
        group = Group.objects.create(name='S0 work readers')
        RuleSet.objects.update_or_create(
            name='work_order', group=group, defaults={'can_view': True}
        )
        self.actor.groups.add(group)
        self.owned_client = Client.objects.create(name='S0 A', code='s0-a')
        other_client = Client.objects.create(name='S0 B', code='s0-b')
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.owned_client
        )
        self.jobs = []
        for client in (self.owned_client, other_client):
            machine = AssetMachine.objects.create(name=client.name, client=client)
            self.jobs.append(
                WorkOrder.objects.create(title=client.name, machine=machine)
            )
        self.api = APIClient()
        self.api.force_authenticate(get_user_model().objects.get(pk=self.actor.pk))

    def observe(self, phase):
        """Record collection and object results for the same authenticated actor."""
        rows = []
        for prefix in ('/api/tasks/work-orders/', '/api/kanban/work-orders/'):
            response = self.api.get(prefix)
            self.assertEqual(response.status_code, 200)
            data = response.data
            items = data['results'] if isinstance(data, dict) else data
            rows.append({
                'phase': phase,
                'endpoint': prefix,
                'status': response.status_code,
                'own_job_visible': any(item['id'] == self.jobs[0].pk for item in items),
                'other_job_visible': any(
                    item['id'] == self.jobs[1].pk for item in items
                ),
            })
        for job in self.jobs:
            for prefix, suffix in (
                ('/api/tasks/work-orders/', ''),
                ('/api/kanban/work-orders/', 'overview/'),
            ):
                response = self.api.get(f'{prefix}{job.pk}/{suffix}')
                rows.append({
                    'phase': phase,
                    'endpoint': f'{prefix}:id/{suffix}',
                    'job_scope': 'own' if job == self.jobs[0] else 'other',
                    'status': response.status_code,
                })
        print('S0_SCOPE_OBSERVATION ' + json.dumps(rows), flush=True)
        return rows

    def test_scope_and_revocation_observations(self):
        """Require both HTTP surfaces to honor the same current grant."""
        before = self.observe('grant present')
        self.assertTrue(before[0]['own_job_visible'])
        self.assertFalse(before[0]['other_job_visible'])
        self.assertTrue(before[1]['own_job_visible'])
        self.assertFalse(before[1]['other_job_visible'])
        self.assertEqual(before[2]['status'], 200)
        self.assertEqual(before[4]['status'], 404)
        self.assertEqual(before[5]['status'], 404)
        self.grant.delete()
        self.api.force_authenticate(get_user_model().objects.get(pk=self.actor.pk))
        after = self.observe(
            'grant revoked; single-site fallback excludes both clients'
        )
        self.assertFalse(after[0]['own_job_visible'])
        self.assertFalse(after[0]['other_job_visible'])
        self.assertFalse(after[1]['own_job_visible'])
        self.assertFalse(after[1]['other_job_visible'])
        self.assertEqual(after[2]['status'], 404)
        self.assertEqual(after[4]['status'], 404)
        self.assertEqual(after[3]['status'], 404)
        self.assertEqual(after[5]['status'], 404)
