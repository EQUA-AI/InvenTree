"""R6 UI01: the Machines list shows and filters each machine's declared criticality."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from assets.models import AssetMachine, Client, ClientScopeGrant
from users.models import RuleSet

URL = '/api/assets/locations/machines/'


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class LocationCriticalityTests(TestCase):
    """Normal reader, stored role and native client grant; real machine rows."""

    def setUp(self):
        """Declared, undeclared, drifted and out-of-scope machines."""
        self.reader = get_user_model().objects.create_user(username='criticality')
        group = Group.objects.create(name='Criticality readers')
        self.reader.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.tenant = Client.objects.create(name='Criticality client', code='crit')
        ClientScopeGrant.objects.create(user=self.reader, client=self.tenant)

        def machine(name, profile=None, client=None):
            return AssetMachine.objects.create(
                name=name, client=client or self.tenant, profile=profile or {}
            )

        machine('Critical pump', {'criticality': 'critical'})
        machine('Second critical pump', {'criticality': 'critical'})
        machine(
            'Low blower', {'criticality': 'low', 'maintenance_strategy': 'reactive'}
        )
        machine('Undeclared valve')
        # A stored profile that no longer validates (unknown key) declares
        # nothing, even though its criticality key alone looks valid.
        machine('Drifted pump', {'criticality': 'critical', 'fault_code': 'E1'})
        machine(
            'Elsewhere critical pump',
            {'criticality': 'critical'},
            client=Client.objects.create(name='Other client', code='other'),
        )
        self.client.force_login(self.reader)

    def page(self, **params):
        """The machines page as the Machines list reads it."""
        response = self.client.get(URL, params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def rows(self, **params):
        """Rows by name."""
        return {row['name']: row for row in self.page(**params)['results']}

    def test_each_row_carries_its_declared_criticality(self):
        """Declared values pass through; nothing declared is null, not low."""
        rows = self.rows()
        self.assertEqual(
            {name: row['criticality'] for name, row in rows.items()},
            {
                'Critical pump': 'critical',
                'Second critical pump': 'critical',
                'Low blower': 'low',
                'Undeclared valve': None,
                'Drifted pump': None,
            },
        )

    def test_filter_counts_before_paging(self):
        """Only declared matches count, and the count is the whole result."""
        page = self.page(criticality='critical', limit=1)
        self.assertEqual(page['count'], 2)
        self.assertEqual(len(page['results']), 1)
        names = set(self.rows(criticality='critical'))
        self.assertEqual(names, {'Critical pump', 'Second critical pump'})

    def test_filter_composes_with_search(self):
        """Search and criticality narrow the same scoped population."""
        self.assertEqual(
            list(self.rows(criticality='critical', search='Second')),
            ['Second critical pump'],
        )
        self.assertEqual(self.page(criticality='medium')['count'], 0)

    def test_only_supported_values_are_accepted(self):
        """An unknown value is refused, not ignored; empty means no filter."""
        for value in ['CRITICAL', 'severe', 'none']:
            with self.subTest(value=value):
                response = self.client.get(URL, {'criticality': value})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.page(criticality='')['count'], 5)

    def test_filtering_costs_one_query_whatever_the_population(self):
        """More declared machines add no per-row queries."""
        self.page(criticality='critical')
        with CaptureQueriesContext(connection) as few:
            self.page(criticality='critical')
        for index in range(4):
            AssetMachine.objects.create(
                name=f'Extra critical {index}',
                client=self.tenant,
                profile={'criticality': 'critical'},
            )
        with CaptureQueriesContext(connection) as more:
            page = self.page(criticality='critical')
        self.assertEqual(page['count'], 6)
        self.assertEqual(len(more), len(few))
