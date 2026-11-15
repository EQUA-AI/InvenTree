"""Synthetic controls for the direct, independently authorized machine-client edge."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from assets.models import AssetMachine, Client, ClientScopeGrant
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='unavailable-in-test',
)
class MachineClientIntegrationTests(TestCase):
    """Current machine access does not automatically authorize client identity."""

    def setUp(self):
        """Set up a scoped actor with separately removable native role grants."""
        self.actor = get_user_model().objects.create_user(
            username='synthetic-client-edge'
        )
        group = Group.objects.create(name='Synthetic client edge readers')
        self.actor.groups.add(group)
        self.work, _ = RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.admin = RuleSet.objects.get(group=group, name='admin')
        RuleSet.objects.filter(pk=self.admin.pk).update(can_view=True)
        self.tenant = Client.objects.create(
            name='Synthetic client edge', code='client-edge'
        )
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.tenant
        )
        self.machine = AssetMachine.objects.create(
            name='Synthetic client edge machine', client=self.tenant
        )

    def read(self):
        """Read the current edge using a trusted synthetic actor."""
        from assets.integration_machine_client import read_machine_client

        return read_machine_client(
            self.actor, environment='synthetic', machine_pk=self.machine.pk
        )

    def test_current_edge_requires_both_visible_endpoints(self):
        """The client can be named only with machine and client role authority."""
        result = self.read()
        assert result is not None
        self.assertEqual(result.schema_version, 'aimms.machine.client.v1')
        self.assertEqual(result.machine.pk, self.machine.pk)
        self.assertEqual(result.client.pk, self.tenant.pk)
        RuleSet.objects.filter(pk=self.admin.pk).update(can_view=False)
        self.assertIsNone(self.read())
        RuleSet.objects.filter(pk=self.admin.pk).update(can_view=True)
        self.assertIsNotNone(self.read())
        RuleSet.objects.filter(pk=self.work.pk).update(can_view=False)
        self.assertIsNone(self.read())

    def test_client_revocation_during_client_read_returns_no_edge(self):
        """Recheck machine authority after the independently scoped client read."""
        import assets.integration_machine_client as client_edge

        self.assertIsNotNone(self.read())
        original = client_edge._authorized_client
        calls = []

        def revoke(actor, identity):
            result = original(actor, identity)
            calls.append(identity.pk)
            self.grant.delete()
            return result

        with patch.object(client_edge, '_authorized_client', side_effect=revoke):
            self.assertIsNone(self.read())
        self.assertEqual(calls, [self.tenant.pk])

    def test_change_after_final_client_check_returns_no_stale_edge(self):
        """A transfer after the second target check is caught by the last source read."""
        import assets.integration_machine_client as client_edge

        initial = self.read()
        assert initial is not None
        self.assertEqual(initial.client.pk, self.tenant.pk)
        other = Client.objects.create(
            name='Synthetic new tenant', code='new-client-edge'
        )
        ClientScopeGrant.objects.create(user=self.actor, client=other)
        original = client_edge._authorized_client
        calls = []

        def transfer(actor, identity):
            result = original(actor, identity)
            calls.append(identity.pk)
            if len(calls) == 2:
                AssetMachine.objects.filter(pk=self.machine.pk).update(client=other)
            return result

        with patch.object(client_edge, '_authorized_client', side_effect=transfer):
            self.assertIsNone(self.read())
        self.assertEqual(calls, [self.tenant.pk, self.tenant.pk])
        current = self.read()
        assert current is not None
        self.assertEqual(current.client.pk, other.pk)

    def test_clientless_or_inactive_client_never_becomes_empty_success(self):
        """Missing and inactive target authority return no relationship."""
        self.assertIsNotNone(self.read())
        Client.objects.filter(pk=self.tenant.pk).update(active=False)
        self.assertIsNone(self.read())
        Client.objects.filter(pk=self.tenant.pk).update(active=True)
        self.assertIsNotNone(self.read())
        AssetMachine.objects.filter(pk=self.machine.pk).update(client=None)
        self.assertIsNone(self.read())

    def test_contract_refuses_cross_environment_endpoints(self):
        """Caller-created coordinates cannot mix environments or identity types."""
        from assets.integration_contracts import (
            ClientSourceIdentity,
            MachineSourceIdentity,
        )
        from assets.integration_machine_client import MachineClientSourceRecord

        machine = MachineSourceIdentity('synthetic', 1)
        with self.assertRaises(ValueError):
            MachineClientSourceRecord(machine, ClientSourceIdentity('other', 1))

    def test_explicit_grant_is_required_with_native_scope_positive(self):
        """Native machine visibility does not substitute for a current grant."""
        from tasks.scope import MaintenanceScope, scoped_machines

        scope = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: scope):
            self.assertTrue(
                scoped_machines(self.actor).filter(pk=self.machine.pk).exists()
            )
            self.assertIsNotNone(self.read())
            self.grant.delete()
            self.assertTrue(
                scoped_machines(self.actor).filter(pk=self.machine.pk).exists()
            )
            self.assertIsNone(self.read())
            ClientScopeGrant.objects.create(user=self.actor, client=self.tenant)
            self.assertIsNotNone(self.read())

    def test_superuser_requires_explicit_grant(self):
        """Superuser status is not an integration client grant."""
        from tasks.scope import MaintenanceScope, scoped_machines

        from assets.integration_machine_client import read_machine_client

        admin = get_user_model().objects.create_superuser(
            username='synthetic-client-edge-admin', email='admin@example.invalid'
        )
        scope = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: scope):
            self.assertTrue(scoped_machines(admin).filter(pk=self.machine.pk).exists())
            self.assertIsNone(
                read_machine_client(
                    admin, environment='synthetic', machine_pk=self.machine.pk
                )
            )
            ClientScopeGrant.objects.create(user=admin, client=self.tenant)
            self.assertIsNotNone(
                read_machine_client(
                    admin, environment='synthetic', machine_pk=self.machine.pk
                )
            )
