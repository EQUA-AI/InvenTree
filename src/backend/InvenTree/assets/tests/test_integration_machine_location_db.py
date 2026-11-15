"""Synthetic controls for current machine-to-location assignment."""

from dataclasses import fields
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from tasks.scope import MaintenanceScope, scoped_machines

from assets.integration_contracts import LocationSourceIdentity, MachineSourceIdentity
from assets.integration_machine_location import (
    MachineLocationSourceRecord,
    read_machine_location,
)
from assets.models import AssetLocation, AssetMachine, Client, ClientScopeGrant
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='unavailable-in-test',
)
class MachineLocationSourceTests(TestCase):
    """Current machine and location authority are checked independently."""

    def setUp(self):
        """Create synthetic actors, clients, locations and a machine."""
        self.actor = get_user_model().objects.create_user(username='placement-reader')
        group = Group.objects.create(name='Placement readers')
        self.actor.groups.add(group)
        self.rule, _ = RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.own = Client.objects.create(name='Synthetic own', code='placement-own')
        self.other = Client.objects.create(
            name='Synthetic other', code='placement-other'
        )
        self.grant = ClientScopeGrant.objects.create(user=self.actor, client=self.own)
        self.location = self.node(self.own, 'own')
        self.foreign = self.node(self.other, 'foreign')
        self.machine = AssetMachine.objects.create(
            name='Synthetic machine', client=self.own, physical_location=self.location
        )

    def node(self, client, code):
        """Create a location with an explicitly client-qualified identity."""
        return AssetLocation.objects.create(
            client=client, name=code, code=code, name_key=code, sibling_key='root'
        )

    def read(self):
        """Read the machine's current direct placement."""
        return read_machine_location(
            self.actor, environment='synthetic', machine_pk=self.machine.pk
        )

    def test_unassigned_and_authorized_placement_are_distinct(self):
        """Available exposes only an authorized location identity; null is unassigned."""
        placement = self.read()
        self.assertEqual(placement.schema_version, 'aimms.machine.location.v1')
        self.assertEqual(placement.state, 'available')
        self.assertEqual(placement.machine.pk, self.machine.pk)
        self.assertEqual(placement.location.pk, self.location.pk)
        self.assertEqual(
            {item.name for item in fields(placement)},
            {'machine', 'state', 'location', 'schema_version'},
        )
        self.machine.physical_location = None
        self.machine.save(update_fields=['physical_location'])
        placement = self.read()
        self.assertEqual((placement.state, placement.location), ('unassigned', None))

    def test_foreign_location_is_unavailable_even_with_both_client_grants(self):
        """A cross-client placement is invalid even when both clients are granted."""
        self.machine.physical_location = self.foreign
        self.machine.save(update_fields=['physical_location'])
        self.assertEqual(
            (self.read().state, self.read().location), ('unavailable', None)
        )
        ClientScopeGrant.objects.create(user=self.actor, client=self.other)
        self.assertEqual(
            (self.read().state, self.read().location), ('unavailable', None)
        )

    def test_location_native_scope_is_separate_from_machine_scope(self):
        """A scoped machine and client grant do not replace target native scope."""
        self.assertEqual(self.read().state, 'available')
        with patch(
            'assets.locations.locations_for', return_value=AssetLocation.objects.none()
        ):
            self.assertEqual(
                (self.read().state, self.read().location), ('unavailable', None)
            )
        self.assertEqual(self.read().state, 'available')

    def test_source_grant_and_role_are_required(self):
        """A denied machine cannot produce even an unassigned/unavailable result."""
        self.grant.delete()
        self.assertIsNone(self.read())
        ClientScopeGrant.objects.create(user=self.actor, client=self.own)
        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])
        self.assertIsNone(self.read())

    def test_explicit_machine_grant_is_required_with_native_scope_intact(self):
        """Native machine scope remains visible while integration grant is removed."""
        from assets.integration_machine_location import _authorized_machine

        scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.own.pk)
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: scopes):
            self.assertTrue(
                scoped_machines(self.actor).filter(pk=self.machine.pk).exists()
            )
            self.assertIsNotNone(_authorized_machine(self.actor.pk, self.machine.pk))
            self.assertIsNotNone(self.read())
            self.grant.delete()
            self.assertTrue(
                scoped_machines(self.actor).filter(pk=self.machine.pk).exists()
            )
            self.assertIsNone(_authorized_machine(self.actor.pk, self.machine.pk))
            self.assertIsNone(self.read())
            ClientScopeGrant.objects.create(user=self.actor, client=self.own)
            self.assertIsNotNone(self.read())

    def test_superuser_still_needs_an_explicit_client_grant(self):
        """Superuser role bypass does not bypass the integration client grant."""
        superuser = get_user_model().objects.create_superuser(
            username='synthetic-placement-admin', email='admin@example.invalid'
        )
        scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.own.pk)
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: scopes):
            self.assertTrue(
                scoped_machines(superuser).filter(pk=self.machine.pk).exists()
            )
            self.assertIsNone(
                read_machine_location(
                    superuser, environment='synthetic', machine_pk=self.machine.pk
                )
            )
            ClientScopeGrant.objects.create(user=superuser, client=self.own)
            self.assertIsNotNone(
                read_machine_location(
                    superuser, environment='synthetic', machine_pk=self.machine.pk
                )
            )

    def test_inactive_actor_and_client_are_denied_but_inactive_machine_is_not(self):
        """Machine inactive state is distinct from actor and client authorization."""
        self.machine.active = False
        self.machine.save(update_fields=['active'])
        self.assertEqual(self.read().state, 'available')
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        self.assertIsNone(self.read())
        self.actor.is_active = True
        self.actor.save(update_fields=['is_active'])
        self.own.active = False
        self.own.save(update_fields=['active'])
        self.assertIsNone(self.read())

    def test_contract_rejects_inconsistent_and_cross_environment_edges(self):
        """Withheld and unassigned placements cannot carry a location ID."""

        class PretendsEqual:
            __hash__ = None

            def __eq__(self, other):
                return True

        machine = MachineSourceIdentity(environment='synthetic', pk=1)
        location = LocationSourceIdentity(environment='synthetic', pk=1)
        foreign = LocationSourceIdentity(environment='elsewhere', pk=1)
        for state, value in (
            ('unassigned', location),
            ('unavailable', location),
            ('available', None),
            ('available', foreign),
            ('invalid', None),
            (PretendsEqual(), location),
        ):
            with self.subTest(state=state, value=value), self.assertRaises(ValueError):
                MachineLocationSourceRecord(machine, state, value)

    def test_revocation_during_location_read_prevents_result(self):
        """Current machine authorization is rechecked after location selection."""
        import assets.integration_machine_location as placement

        original = placement._authorized_location

        def revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            ClientScopeGrant.objects.filter(pk=self.grant.pk).delete()
            return result

        with patch.object(placement, '_authorized_location', side_effect=revoke):
            self.assertIsNone(self.read())

    def test_reassignment_during_location_read_prevents_stale_result(self):
        """A changed machine location cannot return the old identity."""
        import assets.integration_machine_location as placement

        alternate = self.node(self.own, 'alternate')
        original = placement._authorized_location

        def reassign(*args, **kwargs):
            result = original(*args, **kwargs)
            AssetMachine.objects.filter(pk=self.machine.pk).update(
                physical_location=alternate
            )
            return result

        with patch.object(placement, '_authorized_location', side_effect=reassign):
            self.assertIsNone(self.read())
        self.assertEqual(self.read().location.pk, alternate.pk)

    def test_reassignment_after_final_location_check_is_denied(self):
        """The final machine check detects a change after the second target read."""
        import assets.integration_machine_location as placement

        initial = self.read()
        assert initial is not None and initial.location is not None
        self.assertEqual(initial.location.pk, self.location.pk)
        alternate = self.node(self.own, 'final-alternate')
        original = placement._authorized_location
        calls = []

        def reassign(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(result)
            if len(calls) == 2:
                AssetMachine.objects.filter(pk=self.machine.pk).update(
                    physical_location=alternate
                )
            return result

        with patch.object(placement, '_authorized_location', side_effect=reassign):
            self.assertIsNone(self.read())
        self.assertEqual(len(calls), 2)
        current = self.read()
        assert current is not None and current.location is not None
        self.assertEqual(current.location.pk, alternate.pk)

    def test_machine_client_transfer_during_location_read_is_denied(self):
        """A transfer between two granted clients changes the observed placement."""
        import assets.integration_machine_location as placement

        ClientScopeGrant.objects.create(user=self.actor, client=self.other)
        initial = self.read()
        assert initial is not None
        self.assertEqual(initial.state, 'available')
        original = placement._authorized_location
        calls = []

        def transfer(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(result)
            if len(calls) == 1:
                machine = AssetMachine.objects.get(pk=self.machine.pk)
                machine.client = self.other
                machine.save(update_fields=['client'])
            return result

        with patch.object(placement, '_authorized_location', side_effect=transfer):
            self.assertIsNone(self.read())
        self.assertEqual(len(calls), 1)

    def test_location_client_changes_between_checks_prevents_stale_result(self):
        """A location moved across client scope cannot return its previous edge."""
        import assets.integration_machine_location as placement

        original = placement._authorized_location
        calls = []

        def transfer(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(result)
            if len(calls) == 1:
                AssetLocation.objects.filter(pk=self.location.pk).update(
                    client=self.other
                )
            return result

        with patch.object(placement, '_authorized_location', side_effect=transfer):
            self.assertIsNone(self.read())
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.read().state, 'unavailable')
