"""Paginated native work reads retain the Board's exact ownership and roles."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient

from assets.models import AssetMachine, Client, ClientScopeGrant
from company.models import Company
from tasks.models import WorkOrder, WorkOrderLifecycle
from users.models import RuleSet

BASE = '/api/kanban/work-orders/list/'


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class WorkOrderTableAPITest(TestCase):
    """Use stored reader roles, real test login and explicit client grants."""

    def setUp(self):
        """Create fifty authorized jobs, shared (O24) jobs and a foreign match."""
        self.actor = get_user_model().objects.create_user(
            username='work-table-reader', password='synthetic-only'
        )
        self.other = get_user_model().objects.create_user(username='other-technician')
        group = Group.objects.create(name='Work table readers')
        self.rules, _created = RuleSet.objects.update_or_create(
            name='work_order', group=group, defaults={'can_view': True}
        )
        self.actor.groups.add(group)
        tenant = Client.objects.create(name='Table scope', code='table-scope')
        foreign = Client.objects.create(name='Other scope', code='table-foreign')
        self.grant = ClientScopeGrant.objects.create(user=self.actor, client=tenant)
        self.machine = AssetMachine.objects.create(
            name='Synthetic table pump', client=tenant
        )
        foreign_machine = AssetMachine.objects.create(
            name='Foreign table pump', client=foreign
        )
        self.works = [
            WorkOrder.objects.create(
                title=f'Synthetic work {index:02d}',
                machine=self.machine,
                status=WorkOrder.STATUS_BACKLOG,
                priority=WorkOrder.PRIORITY_HIGH
                if index % 3 == 0
                else WorkOrder.PRIORITY_MEDIUM,
                assigned_to=self.actor if index % 2 == 0 else self.other,
                lifecycle_status=WorkOrderLifecycle.PLANNED
                if index % 2 == 0
                else WorkOrderLifecycle.DRAFT,
                work_order_type='inspection',
            )
            for index in range(50)
        ]
        # Machine-only Work on another client's machine stays hidden.
        self.hidden = [
            WorkOrder.objects.create(title='Foreign work', machine=foreign_machine)
        ]
        # O24: customer-attributed and unattributed Work are shared with every
        # Work viewer; they are older than the fifty jobs, so they sort last.
        self.customer_work = WorkOrder.objects.create(
            title='Shared customer work',
            machine=self.machine,
            customer=Company.objects.create(name='Other customer', is_customer=True),
        )
        self.unattributed = WorkOrder.objects.create(title='Unattributed work')
        self.shared = [self.customer_work, self.unattributed]
        WorkOrder.objects.filter(pk__in=[work.pk for work in self.works]).update(
            created_at=timezone.now()
        )
        self.api = APIClient()
        self.assertTrue(
            self.api.login(username=self.actor.username, password='synthetic-only')
        )

    def test_pagination_is_scoped_stable_and_non_overlapping(self):
        """Scope and deterministic ordering precede the two result pages."""
        first = self.api.get(BASE, {'limit': 25})
        second = self.api.get(BASE, {'limit': 25, 'offset': 25})
        third = self.api.get(BASE, {'limit': 25, 'offset': 50})
        for page in (first, second, third):
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.data['count'], 52)
        actual = [row['id'] for row in first.data['results'] + second.data['results']]
        self.assertEqual(actual, [work.pk for work in reversed(self.works)])
        self.assertEqual(
            {row['id'] for row in third.data['results']},
            {work.pk for work in self.shared},
        )
        self.assertIsNotNone(second.data['next'])
        self.assertIsNone(third.data['next'])

    def test_original_board_collection_remains_an_unpaginated_array(self):
        """The additive collection never changes existing Board consumers."""
        response = self.api.get('/api/kanban/work-orders/', {'limit': 1})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 52)
        self.assertNotIn(self.hidden[0].pk, {row['id'] for row in response.data})

    def test_filters_apply_before_count_and_pagination(self):
        """Typed assignment, priority, lifecycle and type share one population."""
        response = self.api.get(
            BASE,
            {
                'assigned_to': self.actor.pk,
                'priority': 'high',
                'lifecycle_status': 'planned',
                'work_order_type': 'inspection',
                'limit': 5,
            },
        )
        self.assertEqual(response.status_code, 200)
        expected = [
            work
            for work in self.works
            if work.assigned_to_id == self.actor.pk and work.priority == 'high'
        ]
        self.assertEqual(response.data['count'], len(expected))
        self.assertEqual(len(response.data['results']), 5)
        self.assertTrue(
            all(row['assigned_to'] == self.actor.pk for row in response.data['results'])
        )

    def test_search_matches_reference_machine_and_typed_owner(self):
        """Search follows real native fields without leaking hidden matches."""
        for search, expected in [
            (self.works[0].reference, 1),
            # The shared customer job is on the same machine (O24).
            (self.machine.name, 51),
            ('Foreign work', 0),
            (self.actor.username, 25),
            ('missing-reference', 0),
        ]:
            with self.subTest(search=search):
                response = self.api.get(BASE, {'search': search})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['count'], expected)

    def test_grant_revocation_removes_rows_and_counts(self):
        """A previously authorized session does not cache client authority.

        The revoked client's machine-only jobs leave at once; the shared (O24)
        customer job on that machine stays readable to the Work viewer.
        """
        self.grant.delete()
        response = self.api.get(BASE, {'search': self.machine.name})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(
            [row['id'] for row in response.data['results']], [self.customer_work.pk]
        )

    def test_view_role_revocation_denies_the_collection(self):
        """Client grants alone do not grant the native work view role."""
        self.rules.can_view = False
        self.rules.save()
        response = self.api.get(BASE)
        self.assertEqual(response.status_code, 403)

    def test_collection_is_read_only_even_for_native_editors(self):
        """No create, update, delete or lifecycle command lives on the list."""
        self.rules.can_add = True
        self.rules.can_change = True
        self.rules.can_delete = True
        self.rules.save()
        count = WorkOrder.objects.count()
        for method in ('post', 'patch', 'put', 'delete'):
            with self.subTest(method=method):
                response = getattr(self.api, method)(BASE, {}, format='json')
                self.assertEqual(response.status_code, 405)
        self.assertEqual(WorkOrder.objects.count(), count)

    def test_page_limit_is_capped(self):
        """A caller cannot request an unbounded table page."""
        WorkOrder.objects.bulk_create([
            WorkOrder(
                title=f'Limit work {index}',
                reference=f'WO-TABLE-LIMIT-{index}',
                machine=self.machine,
            )
            for index in range(170)
        ])
        response = self.api.get(BASE, {'limit': 10000})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 222)
        self.assertEqual(len(response.data['results']), 200)
        self.assertIsNotNone(response.data['next'])

    def test_native_identity_and_assignment_are_not_reinterpreted(self):
        """The list returns the owning work ID and persisted lifecycle/version."""
        response = self.api.get(BASE, {'search': self.works[0].reference})
        self.assertEqual(response.status_code, 200)
        row = response.data['results'][0]
        self.assertEqual(row['id'], self.works[0].pk)
        self.assertEqual(row['machine'], self.machine.pk)
        self.assertEqual(row['machine_name'], self.machine.name)
        self.assertEqual(row['assigned_to'], self.actor.pk)
        self.assertEqual(row['assigned_to_username'], self.actor.username)
        self.assertEqual(row['lifecycle_status'], self.works[0].lifecycle_status)
        self.assertEqual(row['lifecycle_version'], self.works[0].lifecycle_version)
        self.assertNotIn('assignment_accepted', row)
