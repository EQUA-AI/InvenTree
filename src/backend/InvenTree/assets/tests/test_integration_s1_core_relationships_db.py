"""Synthetic independent endpoint and bounded location hierarchy controls."""

from integration_state.tests.assertions import assert_literal_bool

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from assets.models import AssetLocation, Client, ClientScopeGrant
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s1-relationships-fallback',
)
class CoreRelationshipTests(TestCase):
    """Current relationships do not imply related field or historical authority."""

    def setUp(self):
        """Create synthetic current clients, grants, roles and location nodes."""
        self.actor = get_user_model().objects.create_user(username='s1-edge-actor')
        group = Group.objects.create(name='S1 relationship readers')
        self.actor.groups.add(group)
        self.work, _ = RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.admin = RuleSet.objects.get(group=group, name='admin')
        RuleSet.objects.filter(pk=self.admin.pk).update(can_view=True)
        self.tenant = Client.objects.create(name='S1 edge tenant', code='s1-edges')
        self.other = Client.objects.create(name='S1 edge other', code='s1-edges-other')
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.tenant
        )
        self.root = self.node('root', timezone='Europe/Berlin')
        self.child = self.node('child', parent=self.root)

    def node(self, code, *, parent=None, timezone='', client=None):
        """Create only synthetic rows; corruption tests label bypassed writers."""
        return AssetLocation.objects.create(
            client=client or self.tenant,
            parent=parent,
            name=f'S1 {code}',
            code=code,
            name_key=f's1 {code}',
            sibling_key=str(parent.pk) if parent else 'root',
            timezone=timezone,
        )

    def test_location_client_requires_independent_current_endpoint_authority(self):
        """Work-only native context cannot stand in for admin client authority."""
        from assets import integration_location_parents as links

        reader = getattr(links, 'read_location_client', None)
        self.assertTrue(callable(reader), 'A location-client reader is required')
        assert callable(reader)
        result = reader(self.actor, environment='s1-test', location_pk=self.child.pk)
        self.assertEqual(result.schema_version, 'aimms.location.client.v1')
        self.assertEqual(result.location.pk, self.child.pk)
        self.assertEqual(result.client.pk, self.tenant.pk)
        RuleSet.objects.filter(pk=self.admin.pk).update(can_view=False)
        self.assertIsNone(
            reader(self.actor, environment='s1-test', location_pk=self.child.pk)
        )
        RuleSet.objects.filter(pk=self.admin.pk).update(can_view=True)
        self.assertIsNotNone(
            reader(self.actor, environment='s1-test', location_pk=self.child.pk)
        )
        RuleSet.objects.filter(pk=self.work.pk).update(can_view=False)
        self.assertIsNone(
            reader(self.actor, environment='s1-test', location_pk=self.child.pk)
        )

    def test_hierarchy_matches_authorized_native_path_timezone_archive_and_child_hint(
        self,
    ):
        """A bounded path is not an entire tree, count or client identity grant."""
        from assets import integration_location_parents as links
        from assets.locations import graph_for, location_data

        reader = getattr(links, 'read_location_hierarchy', None)
        self.assertTrue(callable(reader), 'A bounded hierarchy reader is required')
        assert callable(reader)
        self.node('grandchild', parent=self.child)
        AssetLocation.objects.filter(pk=self.root.pk).update(archived=True)
        self.child.refresh_from_db()
        native = location_data(self.child, graph_for(self.actor))
        result = reader(self.actor, environment='s1-test', location_pk=self.child.pk)
        self.assertEqual(result.schema_version, 'aimms.location.hierarchy.v1')
        self.assertEqual(result.location.pk, self.child.pk)
        self.assertEqual(
            [{'pk': node.identity.pk, 'name': node.name} for node in result.path],
            native['path'],
        )
        self.assertEqual(result.effective_timezone, native['effective_timezone'])
        self.assertEqual(result.has_children, native['has_children'])
        self.assertTrue(result.path[0].archived)
        self.assertEqual(result.path[-1].timezone, '')
        assert_literal_bool(self, result.population_complete, False)
        assert_literal_bool(self, result.revision_complete, False)
        self.assertFalse(hasattr(result, 'client'))

    def readers(self):
        """Exercise each direct/property/current-edge/hierarchy reader."""
        from assets.integration_location_parents import (
            read_location_client,
            read_location_hierarchy,
            read_location_parent,
        )
        from assets.integration_sources import (
            read_location,
            read_location_details,
            read_location_properties,
        )

        return (
            read_location,
            read_location_details,
            read_location_properties,
            read_location_parent,
            read_location_client,
            read_location_hierarchy,
        )

    def assert_results(self, actor, *, available):
        """Check real ORM outcomes individually, not just helper return values."""
        for reader in self.readers():
            with self.subTest(reader=reader.__name__, available=available):
                value = reader(actor, environment='s1-test', location_pk=self.child.pk)
                if available:
                    self.assertIsNotNone(value)
                else:
                    self.assertIsNone(value)

    def test_each_location_reader_intersects_native_scope_with_explicit_grants(self):
        """Isolate each grant requirement while the other authority stays valid."""
        from tasks.scope import MaintenanceScope

        from assets.locations import locations_for

        scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: scopes):
            self.assert_results(self.actor, available=True)
            self.grant.delete()
            ClientScopeGrant.objects.create(user=self.actor, client=self.other)
            self.assertTrue(locations_for(self.actor).filter(pk=self.child.pk).exists())
            self.assert_results(self.actor, available=False)
            ClientScopeGrant.objects.create(user=self.actor, client=self.tenant)
            self.assert_results(self.actor, available=True)
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: set()):
            self.assertTrue(
                ClientScopeGrant.objects.filter(
                    user=self.actor, client=self.tenant
                ).exists()
            )
            self.assert_results(self.actor, available=False)
        self.assert_results(self.actor, available=True)

    def test_each_location_reader_tracks_current_actor_role_client_and_transfer(self):
        """Inactive/deleted actors and transferred-out nodes cannot reuse a DTO."""
        self.assert_results(self.actor, available=True)
        for model, pk, field in (
            (get_user_model(), self.actor.pk, 'is_active'),
            (RuleSet, self.work.pk, 'can_view'),
            (Client, self.tenant.pk, 'active'),
        ):
            with self.subTest(model=model.__name__, field=field):
                model.objects.filter(pk=pk).update(**{field: False})
                self.assert_results(self.actor, available=False)
                model.objects.filter(pk=pk).update(**{field: True})
                self.assert_results(self.actor, available=True)
        AssetLocation.objects.filter(pk=self.child.pk).update(client=self.other)
        self.assert_results(self.actor, available=False)
        AssetLocation.objects.filter(pk=self.child.pk).update(client=self.tenant)
        self.assert_results(self.actor, available=True)
        self.assert_results(None, available=False)
        get_user_model().objects.filter(pk=self.actor.pk).delete()
        self.assert_results(self.actor, available=False)

    def test_location_superuser_native_visibility_is_not_an_explicit_grant(self):
        """A granted superuser succeeds; a native-visible ungranted one cannot."""
        from tasks.scope import MaintenanceScope

        from assets.locations import locations_for

        actor = get_user_model().objects.create_superuser(username='s1-edge-admin')
        scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda user: scopes):
            self.assertTrue(locations_for(actor).filter(pk=self.child.pk).exists())
            self.assert_results(actor, available=False)
            ClientScopeGrant.objects.create(user=actor, client=self.tenant)
            self.assert_results(actor, available=True)

    def test_location_client_final_source_check_refuses_reassignment(self):
        """Even a newly granted other client cannot preserve the old source edge."""
        from assets import integration_location_parents as links

        ClientScopeGrant.objects.create(user=self.actor, client=self.other)
        original = links._authorized_client
        calls = []

        def change(actor, identity):
            value = original(actor, identity)
            calls.append(identity.pk)
            if len(calls) == 2:
                AssetLocation.objects.filter(pk=self.child.pk).update(client=self.other)
            return value

        with patch.object(links, '_authorized_client', side_effect=change):
            self.assertIsNone(
                links.read_location_client(
                    self.actor, environment='s1-test', location_pk=self.child.pk
                )
            )
        self.assertEqual(calls, [self.tenant.pk, self.tenant.pk])

    def test_hierarchy_rechecks_names_timezones_archive_and_version_without_id_changes(
        self,
    ):
        """Checking only IDs misses mutations in the actual exported path values."""
        from assets import integration_location_parents as links

        original = links._authorized_child_hint
        for field, value in (
            ('name', 'S1 renamed root'),
            ('timezone', 'Europe/Paris'),
            ('archived', True),
            ('version', 15),
        ):
            self.root.refresh_from_db()
            previous = getattr(self.root, field)
            calls = []

            def change(*args, calls=calls, field=field, value=value, **kwargs):
                result = original(*args, **kwargs)
                calls.append(result)
                if len(calls) == 1:
                    AssetLocation.objects.filter(pk=self.root.pk).update(**{
                        field: value
                    })
                return result

            with self.subTest(field=field):
                with patch.object(links, '_authorized_child_hint', side_effect=change):
                    self.assertIsNone(
                        links.read_location_hierarchy(
                            self.actor, environment='s1-test', location_pk=self.child.pk
                        )
                    )
                self.assertEqual(
                    getattr(
                        links.read_location_hierarchy(
                            self.actor, environment='s1-test', location_pk=self.child.pk
                        ).path[0],
                        field,
                    ),
                    value,
                )
                AssetLocation.objects.filter(pk=self.root.pk).update(**{
                    field: previous
                })

    def test_hierarchy_child_hint_is_scoped_and_changes_are_not_complete_empty(self):
        """Foreign children or an excluded descendant cannot become a raw count."""
        from assets import integration_location_parents as links

        self.node('foreign-child', parent=self.child, client=self.other)
        ClientScopeGrant.objects.create(user=self.actor, client=self.other)
        read = lambda: links.read_location_hierarchy(
            self.actor, environment='s1-test', location_pk=self.child.pk
        )
        assert_literal_bool(self, read().has_children, False)
        grandchild = self.node('own-grandchild', parent=self.child)
        assert_literal_bool(self, read().has_children, True)
        with patch(
            'assets.locations.locations_for',
            return_value=AssetLocation.objects.filter(
                pk__in=[self.root.pk, self.child.pk]
            ),
        ):
            assert_literal_bool(self, read().has_children, False)
        AssetLocation.objects.filter(pk=grandchild.pk).delete()
        original = links._authorized_child_hint
        calls = []

        def insert(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(result)
            if len(calls) == 1:
                self.node('new-child', parent=self.child)
            return result

        with patch.object(links, '_authorized_child_hint', side_effect=insert):
            self.assertIsNone(read())
        self.assertEqual(calls, [False, True])
        assert_literal_bool(self, read().has_children, True)

    def test_hierarchy_rejects_denied_ancestors_cross_client_cycles_and_over_depth(
        self,
    ):
        """Unavailable paths do not turn into roots or silently truncated paths."""
        from assets import integration_location_parents as links
        from assets.integration_sources import read_location
        from assets.locations import MAX_DEPTH

        def read(node):
            return links.read_location_hierarchy(
                self.actor, environment='s1-test', location_pk=node.pk
            )

        with patch(
            'assets.locations.locations_for',
            return_value=AssetLocation.objects.filter(pk=self.child.pk),
        ):
            self.assertIsNotNone(
                read_location(
                    self.actor, environment='s1-test', location_pk=self.child.pk
                )
            )
            self.assertIsNone(read(self.child))
        foreign = self.node('foreign-root', client=self.other)
        ClientScopeGrant.objects.create(user=self.actor, client=self.other)
        AssetLocation.objects.filter(pk=self.child.pk).update(parent=foreign)
        self.assertIsNone(read(self.child))
        AssetLocation.objects.filter(pk=self.child.pk).update(parent=self.root)
        AssetLocation.objects.filter(pk=self.root.pk).update(parent=self.child)
        self.assertIsNone(read(self.child))
        AssetLocation.objects.filter(pk=self.root.pk).update(parent=None)
        node = self.root
        for index in range(MAX_DEPTH - 1):
            node = self.node(f'depth-{index}', parent=node)
        self.assertEqual(len(read(node).path), MAX_DEPTH)
        node = self.node('too-deep', parent=node)
        self.assertIsNone(read(node))

    def test_blank_timezone_root_is_observed_not_defaulted_or_inferred_from_denial(
        self,
    ):
        """Historical blank values remain null-effective, not configured UTC."""
        from assets.integration_location_parents import read_location_hierarchy

        AssetLocation.objects.filter(pk=self.root.pk).update(timezone='')
        result = read_location_hierarchy(
            self.actor, environment='s1-test', location_pk=self.root.pk
        )
        self.assertEqual(len(result.path), 1)
        self.assertEqual(result.path[0].timezone, '')
        self.assertIsNone(result.effective_timezone)

    def test_native_context_work_role_behavior_is_preserved_and_intentionally_broader(
        self,
    ):
        """Direct native view remains usable; integration client edge stays stricter."""
        from rest_framework.test import APIRequestFactory, force_authenticate

        from assets.integration_location_parents import read_location_client
        from assets.location_api import LocationContext

        def native():
            request = APIRequestFactory().get('/synthetic-s1-context/')
            force_authenticate(
                request, user=get_user_model().objects.get(pk=self.actor.pk)
            )
            return LocationContext.as_view()(request)

        self.assertEqual(native().status_code, 200)
        RuleSet.objects.filter(pk=self.admin.pk).update(can_view=False)
        response = native()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item['pk'] for item in response.data['workspaces']], [self.tenant.pk]
        )
        self.assertIsNone(
            read_location_client(
                self.actor, environment='s1-test', location_pk=self.child.pk
            )
        )
        RuleSet.objects.filter(pk=self.work.pk).update(can_view=False)
        self.assertEqual(native().status_code, 403)

    def test_hierarchy_contract_rejects_malformed_path_value_envelopes(self):
        """Typed path constructors cannot accept missing or inconsistent values."""
        from assets.integration_contracts import LocationSourceIdentity
        from assets.integration_location_parents import (
            LocationHierarchyNode,
            LocationHierarchySourceRecord,
        )

        root = LocationSourceIdentity('s1-test', self.root.pk)
        child = LocationSourceIdentity('s1-test', self.child.pk)
        root_node = LocationHierarchyNode(root, 'root', 'Europe/Berlin', False, 1)
        child_node = LocationHierarchyNode(child, 'child', '', False, 1)
        good = {
            'location': child,
            'path': (root_node, child_node),
            'effective_timezone': 'Europe/Berlin',
            'has_children': False,
        }
        self.assertEqual(LocationHierarchySourceRecord(**good).path[-1].identity, child)
        for changed in (
            {'path': ()},
            {'path': [root_node, child_node]},
            {'path': (root_node,)},
            {'path': (root_node, root_node, child_node)},
            {'effective_timezone': 'UTC'},
            {'has_children': 'false'},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                LocationHierarchySourceRecord(**(good | changed))
        for changed in (
            {'identity': None},
            {'name': None},
            {'timezone': False},
            {'archived': 'false'},
            {'version': True},
            {'version': -1},
        ):
            params = {
                'identity': root,
                'name': 'root',
                'timezone': '',
                'archived': False,
                'version': 1,
            }
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                LocationHierarchyNode(**(params | changed))

    def test_registered_machine_native_writes_and_direct_edge_limits_are_distinct(self):
        """A direct edge is not certification of the full registered machine graph."""
        from uuid import UUID

        from django.core.exceptions import ValidationError

        from assets.integration_machine_parent import read_machine_parent
        from assets.integration_sources import read_machine_registration
        from assets.models import AssetMachine

        station = AssetMachine.objects.create(
            name='S1 valid station',
            client=self.tenant,
            asset_type='pumphouse',
            source_namespace='s1-registry',
            source_entity_uuid=UUID('12345678-1234-5678-1234-567812345678'),
        )
        pump = AssetMachine.objects.create(
            name='S1 valid pump',
            client=self.tenant,
            asset_type='pump',
            parent=station,
            source_key='s1-pump',
        )
        result = read_machine_parent(
            self.actor, environment='s1-test', machine_pk=pump.pk
        )
        self.assertEqual(result.parent.pk, station.pk)
        registration = read_machine_registration(
            self.actor, environment='s1-test', machine_pk=station.pk
        )
        assert registration is not None
        self.assertEqual(registration.uuid, station.uuid)
        self.assertEqual(registration.source_entity_uuid, station.source_entity_uuid)
        self.assertEqual(registration.source_namespace, station.source_namespace)
        pump.parent = None
        with self.assertRaises(ValidationError):
            pump.save()
        pump.refresh_from_db()
        self.assertEqual(pump.parent_id, station.pk)
        equipment = AssetMachine.objects.create(
            name='S1 non-station equipment', client=self.tenant
        )
        # Deliberately bypass native save/clean to characterize a corrupt row.
        AssetMachine.objects.filter(pk=pump.pk).update(parent=equipment)
        pump.refresh_from_db()
        with self.assertRaises(ValidationError):
            pump.clean()
        observed = read_machine_parent(
            self.actor, environment='s1-test', machine_pk=pump.pk
        )
        self.assertEqual(observed.parent.pk, equipment.pk)
        self.assertFalse(hasattr(observed, 'graph_valid'))
