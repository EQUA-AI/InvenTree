"""Synthetic controls for a bounded, authorized current location-parent edge."""

from dataclasses import fields
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from tasks.scope import MaintenanceScope

from assets.integration_contracts import LocationSourceIdentity
from assets.integration_location_parents import (
    LocationParentSourceRecord,
    read_location_parent,
)
from assets.locations import locations_for
from assets.models import AssetLocation, Client, ClientScopeGrant
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='unavailable-in-test',
)
class LocationParentSourceTests(TestCase):
    """Only a currently authorized, same-client, valid path yields an edge."""

    def setUp(self):
        """Create synthetic own/foreign clients and an actor with only own grant."""
        self.actor = get_user_model().objects.create_user(username='parent-reader')
        group = Group.objects.create(name='Parent readers')
        self.actor.groups.add(group)
        self.rule, _ = RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.own = Client.objects.create(name='Synthetic own', code='parent-own')
        self.other = Client.objects.create(name='Synthetic other', code='parent-other')
        self.grant = ClientScopeGrant.objects.create(user=self.actor, client=self.own)
        self.root = self.node(self.own, 'root')
        self.child = self.node(self.own, 'child', parent=self.root)

    def node(self, client, code, *, parent=None):
        """Create a synthetic native node without invoking writes on live data."""
        return AssetLocation.objects.create(
            client=client,
            parent=parent,
            name=code,
            code=code,
            name_key=code,
            sibling_key=str(parent.pk) if parent else 'root',
        )

    def read(self, location):
        """Read one location's current parent edge."""
        return read_location_parent(
            self.actor, environment='synthetic', location_pk=location.pk
        )

    def test_root_and_authorized_child_have_distinct_states(self):
        """Known root has no parent; child contains only the authorized parent ID."""
        root = self.read(self.root)
        self.assertEqual((root.state, root.parent), ('root', None))
        edge = self.read(self.child)
        self.assertEqual(edge.schema_version, 'aimms.location.parent.v1')
        self.assertEqual(edge.location.pk, self.child.pk)
        self.assertEqual(edge.parent.pk, self.root.pk)
        self.assertEqual(edge.parent.environment, 'synthetic')
        self.assertEqual(edge.state, 'available')
        self.assertEqual(
            {item.name for item in fields(edge)},
            {'location', 'state', 'parent', 'schema_version'},
        )

    def test_no_source_grant_and_role_returns_no_result(self):
        """Native role and an explicit source grant are independently required."""
        self.grant.delete()
        self.assertIsNone(self.read(self.child))
        ClientScopeGrant.objects.create(user=self.actor, client=self.own)
        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])
        self.assertIsNone(self.read(self.child))

    def test_explicit_grant_is_required_with_native_location_scope_intact(self):
        """A custom native location scope does not supply an integration grant."""
        scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.own.pk)
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: scopes):
            self.assertTrue(locations_for(self.actor).filter(pk=self.child.pk).exists())
            self.assertIsNotNone(self.read(self.child))
            self.grant.delete()
            self.assertTrue(locations_for(self.actor).filter(pk=self.child.pk).exists())
            self.assertIsNone(self.read(self.child))
            ClientScopeGrant.objects.create(user=self.actor, client=self.own)
            self.assertIsNotNone(self.read(self.child))

    def test_foreign_parent_is_withheld_even_if_actor_has_both_grants(self):
        """An invalid cross-client edge is never projected as hierarchy."""
        foreign = self.node(self.other, 'foreign')
        AssetLocation.objects.filter(pk=self.child.pk).update(parent=foreign)
        self.assertIsNone(self.read(self.child))
        ClientScopeGrant.objects.create(user=self.actor, client=self.other)
        self.assertIsNone(self.read(self.child))

    def test_cross_client_ancestor_is_withheld_even_with_both_grants(self):
        """A same-client direct parent cannot conceal a foreign intermediate node."""
        foreign = self.node(self.other, 'foreign-middle')
        AssetLocation.objects.filter(pk=self.root.pk).update(parent=foreign)
        ClientScopeGrant.objects.create(user=self.actor, client=self.other)
        self.assertIsNone(self.read(self.child))
        AssetLocation.objects.filter(pk=self.root.pk).update(parent=None)
        restored = self.read(self.child)
        assert restored is not None and restored.parent is not None
        self.assertEqual(restored.parent.pk, self.root.pk)

    def test_parent_native_scope_is_checked_independently(self):
        """A visible child and explicit grant do not substitute for parent scope."""
        self.assertIsNotNone(self.read(self.child))
        with patch(
            'assets.locations.locations_for',
            return_value=AssetLocation.objects.filter(pk=self.child.pk),
        ):
            self.assertTrue(ClientScopeGrant.objects.filter(pk=self.grant.pk).exists())
            self.assertIsNone(self.read(self.child))
        self.assertIsNotNone(self.read(self.child))

    def test_cycles_and_over_depth_do_not_look_like_root(self):
        """Cyclic and over-depth paths are unavailable, not truncated or root."""
        AssetLocation.objects.filter(pk=self.root.pk).update(parent=self.child)
        self.assertIsNone(self.read(self.child))
        AssetLocation.objects.filter(pk=self.root.pk).update(parent=None)
        node = self.root
        for index in range(31):
            node = self.node(self.own, f'depth-{index}', parent=node)
        self.assertIsNotNone(self.read(node))
        node = self.node(self.own, 'depth-over-limit', parent=node)
        self.assertIsNone(self.read(node))

    def test_contract_rejects_invalid_or_cross_source_edges(self):
        """A root or available edge cannot carry an inconsistent identity."""

        class PretendsEqual:
            __hash__ = None

            def __eq__(self, other):
                return True

        location = LocationSourceIdentity(environment='synthetic', pk=1)
        parent = LocationSourceIdentity(environment='synthetic', pk=2)
        foreign = LocationSourceIdentity(environment='elsewhere', pk=2)
        for state, value in (
            ('root', parent),
            ('available', None),
            ('available', foreign),
            ('available', location),
            ('invalid', None),
            (PretendsEqual(), None),
        ):
            with self.subTest(state=state, value=value), self.assertRaises(ValueError):
                LocationParentSourceRecord(location, state, value)

    def test_reparent_between_walks_is_withheld(self):
        """Do not emit an old parent after the selected node is reparented."""
        import assets.integration_location_parents as links

        alternate = self.node(self.own, 'alternate')
        original = links._authorized_location
        calls = []

        def reparent(actor, identity):
            result = original(actor, identity)
            calls.append(identity.pk)
            if len(calls) == 2:
                AssetLocation.objects.filter(pk=self.child.pk).update(parent=alternate)
            return result

        with patch.object(links, '_authorized_location', side_effect=reparent):
            self.assertIsNone(self.read(self.child))
        self.assertEqual(self.read(self.child).parent.pk, alternate.pk)

    def test_grant_revoked_during_walk_is_denied(self):
        """Revoking the source grant mid-walk prevents an edge result."""
        import assets.integration_location_parents as links

        original = links._authorized_location

        def revoke(actor, identity):
            result = original(actor, identity)
            ClientScopeGrant.objects.filter(pk=self.grant.pk).delete()
            return result

        with patch.object(links, '_authorized_location', side_effect=revoke):
            self.assertIsNone(self.read(self.child))
