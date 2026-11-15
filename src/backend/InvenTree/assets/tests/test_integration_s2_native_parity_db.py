"""Actual asset-route controls under isolated synthetic ORM settings."""

from dataclasses import replace
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from assets.models import AssetLocation, AssetMachine, Client, ClientScopeGrant
from users.models import RuleSet

BASE = '/api/assets/locations/'


class S2SourceFixture:
    """Real roles, native client scopes and explicit grants; no client data."""

    def build_source(self):
        """Two granted clients, an ungranted client and mixed placement state."""
        self.owner = Client.objects.create(name='S2 Alpha', code='s2-alpha')
        self.second_owner = Client.objects.create(name='S2 Beta', code='s2-beta')
        self.foreign = Client.objects.create(name='S2 Foreign', code='s2-foreign')
        self.reader = get_user_model().objects.create_user(username='s2-reader')
        group = Group.objects.create(name='S2 readers')
        self.reader.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        # The reduced scratch app registry omits optional admin-role models.
        # Set only the existing fixture relation, not role provisioning signals.
        RuleSet.objects.filter(group=group, name='admin').update(can_view=True)
        self.reader_group = group
        for client in (self.owner, self.second_owner):
            ClientScopeGrant.objects.create(user=self.reader, client=client)
        self.site = self.node('Alpha site')
        self.room = self.node('Alpha room', parent=self.site)
        self.other_site = self.node('Beta site', client=self.second_owner)
        self.archived = self.node('Retired branch', archived=True)
        self.archived_child = self.node('Retired child', parent=self.archived)
        self.direct = self.machine('A direct', self.site)
        self.nested = self.machine('B nested', self.room, serial='Needle serial')
        self.other = self.machine(
            'C second client', self.other_site, client=self.second_owner
        )
        self.unassigned = self.machine('D unassigned', manufacturer='Needle maker')
        self.inactive = self.machine('E inactive', self.site, active=False)
        self.machine('F archived', self.archived)
        self.machine('G below archived', self.archived_child)
        self.hidden = self.machine('H foreign', client=self.foreign)
        self.api = APIClient()
        self.api.force_authenticate(self.reader)

    def node(self, name, *, parent=None, client=None, archived=False):
        """A synthetic physical location, distinct from stock locations."""
        return AssetLocation.objects.create(
            client=client or self.owner,
            parent=parent,
            name=name,
            code=name.lower().replace(' ', '-'),
            name_key=name.casefold(),
            sibling_key=str(parent.pk) if parent else 'root',
            timezone='' if parent else 'UTC',
            archived=archived,
        )

    def machine(self, name, location=None, *, client=None, **details):
        """Create only synthetic source rows in the owned test database."""
        return AssetMachine.objects.create(
            name=name,
            client=client or self.owner,
            physical_location=location,
            **details,
        )

    def simulated_adapter(
        self,
        rows,
        *,
        fields_allowed=lambda request, binding: True,
        page_allowed=lambda request, binding, page: True,
        transform=lambda page: page,
    ):
        """Only transport/field/proof fakes; real request/scope/grant queries remain."""
        from assets.integration_s2_native import NativeReadAdapter
        from assets.tests.test_integration_s2_provider_routing import FixtureSnapshot

        sources = []

        def select_plan(binding):
            source = FixtureSnapshot(binding, rows=rows)
            source.transform = transform
            sources.append(source)
            plan = source.plan()
            if binding.shape.domain == 'locations':
                plan = replace(
                    plan, row_key=lambda row: (row['name'].casefold(), row['pk'])
                )
            return plan

        return NativeReadAdapter(
            environment='synthetic',
            plan_for=select_plan,
            authorize_fields=fields_allowed,
            authorize_page=page_allowed,
        ), sources

    def assert_optional_parity(self, path, params):
        """The whole small native fixture is the independent fake source oracle."""
        from assets.location_api import LocationList, LocationMachines

        url = f'{BASE}{path}'
        expected = self.api.get(url, params)
        self.assertEqual(expected.status_code, 200)
        oracle = self.api.get(url, {**params, 'limit': 100, 'offset': 0}).json()
        self.assertEqual(oracle['count'], len(oracle['results']))
        adapter, sources = self.simulated_adapter(oracle['results'])
        view = LocationMachines if path == 'machines/' else LocationList
        original = view._get_native
        with (
            override_settings(AIMMS_ASSET_READ_ADAPTER=adapter),
            mock.patch.object(
                view, '_get_native', autospec=True, side_effect=original
            ) as native,
        ):
            response = self.api.get(url, params)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), expected.json())
        self.assertEqual(len(sources), 1)
        self.assertEqual(len(sources[0].calls), 1)
        self.assertEqual(native.call_count, 0)
        return sources[0].binding


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class NativeSeamParityTests(S2SourceFixture, TestCase):
    """Exercise actual views/permissions, not a direct selector alone."""

    def setUp(self):
        """Set up a nonempty source and ordinary authorized actor."""
        self.build_source()

    def test_both_real_get_routes_enter_the_native_default_seam(self):
        """The native implementation is the working default, not a separate stub."""
        from assets.integration_s2_provider import route_native_read

        with mock.patch(
            'assets.integration_s2_native.route_native_read', wraps=route_native_read
        ) as router:
            machines = self.api.get(f'{BASE}machines/', {'limit': 1})
            locations = self.api.get(BASE, {'parent': 'root', 'limit': 1})
        self.assertEqual(machines.status_code, 200)
        self.assertEqual(locations.status_code, 200)
        self.assertEqual(router.call_count, 2)
        self.assertEqual(machines.data['count'], 7)
        self.assertEqual(len(machines.data['results']), 1)
        self.assertEqual(machines['Cache-Control'], 'private, no-store')
        self.assertEqual(locations['Cache-Control'], 'private, no-store')

    def test_supported_fake_reaches_actual_route_without_native_execution(self):
        """Use real request authority but explicitly simulated field/source proof."""
        from assets.integration_s2_native import NativeReadAdapter
        from assets.location_api import LocationMachines
        from assets.tests.test_integration_s2_provider_routing import FixtureSnapshot

        url = f'{BASE}machines/'
        expected = self.api.get(url, {'limit': 1}).json()
        oracle = self.api.get(url, {'limit': 100}).json()
        self.assertEqual(oracle['count'], len(oracle['results']))
        sources = []

        def select_plan(binding):
            source = FixtureSnapshot(binding, rows=oracle['results'])
            sources.append(source)
            return source.plan()

        adapter = NativeReadAdapter(
            environment='synthetic',
            plan_for=select_plan,
            authorize_fields=lambda request, binding: True,
            authorize_page=lambda request, binding, page: True,
        )
        original = LocationMachines._get_native
        with (
            override_settings(AIMMS_ASSET_READ_ADAPTER=adapter),
            mock.patch.object(
                LocationMachines, '_get_native', autospec=True, side_effect=original
            ) as native,
        ):
            response = self.api.get(url, {'limit': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        self.assertEqual(len(sources), 1)
        self.assertEqual(len(sources[0].calls), 1)
        self.assertEqual(native.call_count, 0)

    def test_machine_filter_shapes_match_native_population_before_paging(self):
        """All native flags/filters, not just one facility or active equipment."""
        AssetMachine.objects.filter(pk=self.direct.pk).update(
            profile={'criticality': 'critical'}
        )
        AssetMachine.objects.filter(pk=self.nested.pk).update(
            profile={'criticality': 'critical', 'unknown': True}
        )
        null_client = self.machine('No client')
        AssetMachine.objects.filter(pk=null_client.pk).update(client_id=None)
        for params in (
            {'active': 'true'},
            {'active': 'false'},
            {'active_only': 'true'},
            {'active_only': 'false'},
            {'active': 'true', 'active_only': 'true'},
            {'unassigned': 'true'},
            {'unassigned': 'false'},
            {'location': self.site.pk},
            {'location': self.site.pk, 'include_descendants': 'false'},
            {'location': self.site.pk, 'include_descendants': 'true'},
            {'machine': self.nested.pk},
            {'search': '  Needle  '},
            {'search': 'direct'},
            {'search': 'not-a-match'},
            {'criticality': 'critical'},
            {'criticality': 'medium'},
            {'criticality': ''},
            {'has_alarms': 'true'},
            {'has_alarms': 'false'},
        ):
            with self.subTest(params=params):
                binding = self.assert_optional_parity(
                    'machines/', {**params, 'limit': 1}
                )
                self.assertEqual(binding.shape.ordering, 'native.machine.db-name-pk.v1')

    def test_location_shapes_match_native_hierarchy_counts_and_casefold_order(self):
        """Physical-location fields/counts are not stock-location DTOs."""
        self.node('zulu')
        self.node('alpha lower')
        self.node('Äland')
        for params in (
            {},
            {'parent': 'root'},
            {'parent': self.site.pk},
            {'client': self.second_owner.pk},
            {'search': '  ALPHA  '},
            {'active_only': 'true'},
            {'active_only': 'false'},
            {'include_counts': 'true'},
            {'include_counts': 'true', 'active': 'false'},
            {'include_counts': 'true', 'active': 'true', 'active_only': 'true'},
            {'include_counts': 'false', 'active': 'not-parsed-without-counts'},
        ):
            with self.subTest(params=params):
                binding = self.assert_optional_parity('', {**params, 'limit': 2})
                self.assertEqual(
                    binding.shape.ordering, 'native.location.casefold-name-pk.v1'
                )

    def test_random_offsets_and_native_page_controls_do_not_walk_from_start(self):
        """A late empty page is not a complete scan or an execution failure."""
        for path in ('machines/', ''):
            for params in (
                {'limit': 1, 'offset': 1},
                {'limit': 2, 'offset': 2},
                {'limit': 1, 'offset': 1000},
                {'limit': -1, 'offset': -1},
                {'limit': 'invalid', 'offset': 'invalid'},
                {'limit': 200},
            ):
                with self.subTest(path=path, params=params):
                    self.assert_optional_parity(path, params)

    def test_foreign_selected_location_or_parent_remains_native_404_before_provider(
        self,
    ):
        """A capability claim cannot authorize the caller-selected linked identity."""
        foreign = self.node('Foreign selected location', client=self.foreign)
        for path, params in (
            ('machines/', {'location': foreign.pk}),
            ('', {'parent': foreign.pk}),
        ):
            with self.subTest(path=path):
                native_rows = self.api.get(f'{BASE}{path}', {'limit': 100}).json()[
                    'results'
                ]
                adapter, sources = self.simulated_adapter(native_rows)
                self.assertEqual(self.api.get(f'{BASE}{path}', params).status_code, 404)
                with self.settings(AIMMS_ASSET_READ_ADAPTER=adapter):
                    response = self.api.get(f'{BASE}{path}', params)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(sources, [])
