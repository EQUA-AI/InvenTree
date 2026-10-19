"""R6 UI01: the Machines list shows each machine's open Work from canonical scope."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from tasks.models import WorkOrder
from tasks.scope import ScopeError

from assets.models import AssetMachine, Client, ClientScopeGrant
from company.models import Company
from users.models import RuleSet

URL = '/api/assets/locations/machines/'


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class LocationWorkContextTests(TestCase):
    """Normal reader, stored role and native client grant; real Work rows."""

    def setUp(self):
        """One granted client with two machines, and work in several states."""
        self.reader = get_user_model().objects.create_user(username='work-context')
        group = Group.objects.create(name='Work context readers')
        self.reader.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.tenant = Client.objects.create(name='Context client', code='ctx-client')
        ClientScopeGrant.objects.create(user=self.reader, client=self.tenant)
        self.busy = AssetMachine.objects.create(name='Busy pump', client=self.tenant)
        self.quiet = AssetMachine.objects.create(name='Quiet pump', client=self.tenant)

        def work(priority, status='planned', machine=None, **extra):
            return WorkOrder.objects.create(
                title=f'{priority} {status}',
                machine=machine or self.busy,
                priority=priority,
                lifecycle_status=status,
                **extra,
            )

        work('low')
        work('high', 'on_hold')
        work('medium', 'in_progress')
        # Not open, not active, or outside the reader's Work boundary.
        work('high', 'completed')
        work('high', 'cancelled')
        work('high', is_active=False)
        work(
            'high',
            customer=Company.objects.create(name='Other customer', is_customer=True),
        )
        self.client.force_login(self.reader)

    def rows(self, **params):
        """The page of machines as the Machines list reads it."""
        response = self.client.get(URL, params)
        self.assertEqual(response.status_code, 200, response.content)
        return {row['name']: row for row in response.json()['results']}

    def test_open_work_is_counted_with_its_highest_priority(self):
        """Only open, active Work inside the canonical boundary counts."""
        rows = self.rows()
        self.assertEqual(
            rows['Busy pump']['open_work'], {'count': 3, 'highest_priority': 'high'}
        )
        self.assertEqual(
            rows['Quiet pump']['open_work'], {'count': 0, 'highest_priority': None}
        )

    def test_highest_priority_follows_native_order_not_text(self):
        """'medium' outranks 'low' although it sorts after it alphabetically."""
        WorkOrder.objects.filter(machine=self.busy, priority='high').update(
            lifecycle_status='completed'
        )
        self.assertEqual(
            self.rows()['Busy pump']['open_work'],
            {'count': 2, 'highest_priority': 'medium'},
        )

    def test_unresolved_work_scope_is_unknown_not_zero(self):
        """A missing Work scope is reported as unknown for every row."""
        with mock.patch(
            'assets.location_api.work_order_scope_filter',
            side_effect=ScopeError('unavailable'),
        ):
            rows = self.rows()
        self.assertIsNone(rows['Busy pump']['open_work'])
        self.assertIsNone(rows['Quiet pump']['open_work'])

    def test_counts_cost_one_query_for_the_page(self):
        """More machines on the page add no per-row Work queries."""
        self.rows()
        with CaptureQueriesContext(connection) as two_machines:
            self.rows()
        for index in range(4):
            machine = AssetMachine.objects.create(
                name=f'Extra pump {index}', client=self.tenant
            )
            WorkOrder.objects.create(
                title='extra', machine=machine, priority='low', lifecycle_status='ready'
            )
        with CaptureQueriesContext(connection) as six_machines:
            rows = self.rows()
        self.assertEqual(len(rows), 6)
        self.assertEqual(len(six_machines), len(two_machines))

    def test_search_and_paging_keep_counts_for_the_rows_shown(self):
        """The counts belong to the page actually returned."""
        rows = self.rows(search='Busy')
        self.assertEqual(list(rows), ['Busy pump'])
        self.assertEqual(rows['Busy pump']['open_work']['count'], 3)
        second_page = self.rows(limit=1, offset=1)
        self.assertEqual(list(second_page), ['Quiet pump'])
        self.assertEqual(second_page['Quiet pump']['open_work']['count'], 0)
