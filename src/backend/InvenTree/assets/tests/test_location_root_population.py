"""By-location root populations and rollups come from the full authorized scope."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from assets.models import AssetLocation, AssetMachine, Client, ClientScopeGrant
from users.models import RuleSet

BASE = '/api/assets/locations/'


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class LocationRootPopulationTests(TestCase):
    """The root includes active placements and unassigned machines, never hidden sites."""

    def setUp(self):
        """Build two authorized sites, an archived branch and a foreign client."""
        self.owner = Client.objects.create(name='Root owner', code='root-owner')
        self.foreign = Client.objects.create(name='Foreign owner', code='root-foreign')
        self.reader = get_user_model().objects.create_user(username='root-reader')
        group = Group.objects.create(name='Root readers')
        self.reader.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        ClientScopeGrant.objects.create(user=self.reader, client=self.owner)
        self.api = APIClient()
        self.api.force_authenticate(self.reader)

        self.site = self.node('Alpha site')
        self.room = self.node('Alpha room', parent=self.site)
        self.second = self.node('Beta site')
        self.archived = self.node('Retired branch', parent=self.site, archived=True)
        self.archived_child = self.node('Retired room', parent=self.archived)
        self.foreign_site = self.node('Foreign site', client=self.foreign)
        self.direct = self.machine('Alpha direct', self.site)
        self.nested = self.machine('Alpha nested', self.room)
        self.other_site = self.machine('Beta direct', self.second)
        self.unassigned = self.machine('Unassigned')
        self.inactive = self.machine('Inactive', self.site, active=False)
        self.machine('Retired direct', self.archived)
        self.machine('Retired nested', self.archived_child)
        self.machine('Foreign machine', self.foreign_site, client=self.foreign)

    def node(self, name, *, parent=None, client=None, archived=False):
        """Create synthetic placement metadata, including inherited archive state."""
        return AssetLocation.objects.create(
            client=client or self.owner,
            name=name,
            code=name.lower().replace(' ', '-'),
            name_key=name.lower(),
            sibling_key=str(parent.pk) if parent else 'root',
            parent=parent,
            timezone='' if parent else 'UTC',
            archived=archived,
        )

    def machine(self, name, location=None, *, client=None, active=True):
        """Create a registered row without changing any external database."""
        return AssetMachine.objects.create(
            name=name,
            client=client or self.owner,
            physical_location=location,
            active=active,
        )

    def page(self, path='machines/', **params):
        """Read a real authorized endpoint and require success."""
        response = self.api.get(f'{BASE}{path}', params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def test_root_count_and_pages_include_all_active_locations_and_unassigned(self):
        """The count spans sites and unassigned rows before paging, not one site."""
        params = {'active': 'true', 'active_only': 'true', 'limit': 1}
        ids = set()
        for offset in range(4):
            page = self.page(**params, offset=offset)
            self.assertEqual(page['count'], 4)
            self.assertEqual(len(page['results']), 1)
            ids.add(page['results'][0]['pk'])
        self.assertEqual(
            ids,
            {self.direct.pk, self.nested.pk, self.other_site.pk, self.unassigned.pk},
        )
        self.assertEqual(
            self.page(**params, unassigned='true')['count'], 1
        )
        nested = self.page(**params, machine=self.nested.pk)['results'][0]
        self.assertEqual(
            [node['pk'] for node in nested['physical_location']['path']],
            [self.site.pk, self.room.pk],
        )

    def test_scope_filters_compose_before_counts(self):
        """Search, direct/descendant scopes and inactive inclusion use the same set."""
        params = {'active': 'true', 'active_only': 'true'}
        self.assertEqual(self.page(**params, search='Alpha')['count'], 2)
        self.assertEqual(self.page(**params, location=self.site.pk)['count'], 2)
        self.assertEqual(
            self.page(
                **params, location=self.site.pk, include_descendants='false'
            )['count'],
            1,
        )
        self.assertEqual(self.page(active_only='true')['count'], 5)
        self.assertEqual(self.page(active='true', active_only='false')['count'], 6)

    def test_root_site_counts_are_full_and_match_active_location_population(self):
        """Rollups ignore list pagination and include hidden descendants only on request."""
        params = {
            'parent': 'root',
            'active': 'true',
            'active_only': 'true',
            'include_counts': 'true',
            'limit': 1,
        }
        page = self.page('', **params)
        self.assertEqual(page['count'], 2)
        self.assertEqual(len(page['results']), 1)
        self.assertEqual(page['results'][0]['pk'], self.site.pk)
        self.assertEqual(
            page['results'][0]['counts'],
            {'direct_machines': 1, 'total_machines': 2},
        )
        second = self.page('', **params, offset=1)['results'][0]
        self.assertEqual(second['pk'], self.second.pk)
        self.assertEqual(
            second['counts'], {'direct_machines': 1, 'total_machines': 1}
        )
        archived = self.page(
            '', parent='root', active='true', include_counts='true'
        )['results'][0]
        self.assertEqual(archived['counts']['total_machines'], 4)
        inactive = self.page(
            '', parent='root', active_only='true', include_counts='true'
        )['results'][0]
        self.assertEqual(inactive['counts']['total_machines'], 3)
        self.assertNotIn('counts', self.page('', parent='root')['results'][0])

    def test_invalid_scope_flags_are_rejected(self):
        """A mistyped archive or count option never silently widens the population."""
        for path, params in (
            ('machines/', {'active_only': 'yes'}),
            ('', {'active_only': 'yes'}),
            ('', {'include_counts': 'yes'}),
            ('', {'include_counts': 'true', 'active': 'yes'}),
        ):
            with self.subTest(path=path, params=params):
                self.assertEqual(self.api.get(f'{BASE}{path}', params).status_code, 400)
