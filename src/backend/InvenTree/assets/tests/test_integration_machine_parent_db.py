"""Synthetic native-authority controls for a machine's direct parent edge."""

import uuid
from typing import Literal, cast
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
class MachineParentIntegrationTests(TestCase):
    """A visible child is not by itself authority for a parent."""

    def setUp(self):
        """Create same-client registered station and pump with a scoped actor."""
        self.actor = get_user_model().objects.create_user(
            username='synthetic-parent-reader'
        )
        group = Group.objects.create(name='Synthetic parent readers')
        self.actor.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.client = Client.objects.create(
            name='Synthetic parent tenant', code='parent-own'
        )
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.client
        )
        self.station = AssetMachine.objects.create(
            name='Synthetic station',
            asset_type='pumphouse',
            client=self.client,
            source_namespace='synthetic',
            source_entity_uuid=uuid.uuid4(),
        )
        self.pump = AssetMachine.objects.create(
            name='Synthetic pump',
            asset_type='pump',
            parent=self.station,
            client=self.client,
            source_key='synthetic-pump',
        )

    def read(self, pk):
        """Read a direct edge with the synthetic test actor."""
        from assets.integration_machine_parent import read_machine_parent

        return read_machine_parent(self.actor, environment='synthetic', machine_pk=pk)

    def test_authorized_root_and_direct_parent_are_distinct(self):
        """Return the independently visible direct edge or an explicit root."""
        root = self.read(self.station.pk)
        edge = self.read(self.pump.pk)
        self.assertEqual((root.state, root.parent), ('root', None))
        self.assertEqual(edge.state, 'available')
        self.assertEqual(edge.machine.pk, self.pump.pk)
        self.assertEqual(edge.parent.pk, self.station.pk)
        self.assertEqual(edge.schema_version, 'aimms.machine.parent.v1')

    def test_parent_scope_denial_withholds_the_entire_edge(self):
        """An admitted child cannot expose an independently denied parent."""
        self.assertIsNotNone(self.read(self.pump.pk))
        from tasks.scope import scoped_machines

        real = scoped_machines(self.actor)
        with patch(
            'tasks.scope.scoped_machines', return_value=real.exclude(pk=self.station.pk)
        ):
            self.assertIsNone(self.read(self.pump.pk))
        self.assertIsNotNone(self.read(self.pump.pk))

    def test_grant_revocation_during_parent_lookup_withholds_edge(self):
        """Re-resolve the child's authority after the selected target read."""
        import assets.integration_machine_parent as parent_edge

        self.assertIsNotNone(self.read(self.pump.pk))
        original = parent_edge._authorized_machine_source
        calls = []

        def revoke(actor, identity):
            result = original(actor, identity)
            calls.append(identity.pk)
            if identity.pk == self.station.pk:
                self.grant.delete()
            return result

        with patch.object(
            parent_edge, '_authorized_machine_source', side_effect=revoke
        ):
            self.assertIsNone(self.read(self.pump.pk))
        self.assertIn(self.station.pk, calls)

    def test_parent_change_after_final_target_check_is_withheld(self):
        """The final child lookup sees an edge change made after parent recheck."""
        import assets.integration_machine_parent as parent_edge

        initial = self.read(self.pump.pk)
        assert initial is not None and initial.parent is not None
        self.assertEqual(initial.parent.pk, self.station.pk)
        original = parent_edge._authorized_machine_source
        parent_reads = []

        def change(actor, identity):
            result = original(actor, identity)
            if identity.pk == self.station.pk:
                parent_reads.append(identity.pk)
                if len(parent_reads) == 2:
                    # Explicit bulk write simulates a concurrent bypass of model validation.
                    AssetMachine.objects.filter(pk=self.pump.pk).update(parent=None)
            return result

        with patch.object(
            parent_edge, '_authorized_machine_source', side_effect=change
        ):
            self.assertIsNone(self.read(self.pump.pk))
        self.assertEqual(len(parent_reads), 2)
        current = self.read(self.pump.pk)
        assert current is not None
        self.assertEqual(current.state, 'root')

    def test_cross_client_or_self_parent_is_withheld_even_when_scope_allows(self):
        """An inconsistent edge is never relabeled as an authorized relation."""
        other = Client.objects.create(
            name='Synthetic other tenant', code='parent-other'
        )
        ClientScopeGrant.objects.create(user=self.actor, client=other)
        foreign = AssetMachine.objects.create(name='Synthetic foreign', client=other)
        self.assertIsNotNone(self.read(self.pump.pk))
        # These bulk updates deliberately bypass the native model clean method.
        AssetMachine.objects.filter(pk=self.pump.pk).update(parent=foreign)
        self.assertIsNone(self.read(self.pump.pk))
        AssetMachine.objects.filter(pk=self.pump.pk).update(parent_id=self.pump.pk)
        self.assertIsNone(self.read(self.pump.pk))

    def test_source_grant_role_and_actor_are_independently_required(self):
        """Loss of any source authority refuses even the parentless result."""
        from tasks.scope import MaintenanceScope, scoped_machines

        scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.client.pk)
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: scopes):
            self.assertTrue(
                scoped_machines(self.actor).filter(pk=self.station.pk).exists()
            )
            self.assertIsNotNone(self.read(self.station.pk))
            self.grant.delete()
            self.assertTrue(
                scoped_machines(self.actor).filter(pk=self.station.pk).exists()
            )
            self.assertIsNone(self.read(self.station.pk))
            ClientScopeGrant.objects.create(user=self.actor, client=self.client)
            self.assertIsNotNone(self.read(self.station.pk))
            RuleSet.objects.filter(group__user=self.actor, name='work_order').update(
                can_view=False
            )
            self.assertIsNone(self.read(self.station.pk))
            RuleSet.objects.filter(group__user=self.actor, name='work_order').update(
                can_view=True
            )
            self.assertIsNotNone(self.read(self.station.pk))
            get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
            self.assertIsNone(self.read(self.station.pk))

    def test_parent_record_rejects_inconsistent_states(self):
        """No malformed edge may carry a cross-source or self reference."""
        from assets.integration_contracts import MachineSourceIdentity
        from assets.integration_machine_parent import MachineParentSourceRecord

        identity = MachineSourceIdentity('synthetic', 1)
        other = MachineSourceIdentity('synthetic', 2)
        foreign = MachineSourceIdentity('other', 2)
        for state, parent in (
            ('root', other),
            ('available', None),
            ('available', identity),
            ('available', foreign),
            ('unknown', None),
        ):
            with (
                self.subTest(state=state, parent=parent),
                self.assertRaises(ValueError),
            ):
                MachineParentSourceRecord(
                    identity, cast(Literal['root', 'available'], state), parent
                )

    def test_superuser_requires_explicit_client_grant(self):
        """Both endpoints remain grant-bound even for a native superuser."""
        from tasks.scope import MaintenanceScope, scoped_machines

        from assets.integration_machine_parent import read_machine_parent

        admin = get_user_model().objects.create_superuser(
            username='synthetic-parent-admin', email='admin@example.invalid'
        )
        scope = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.client.pk)
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: scope):
            self.assertTrue(scoped_machines(admin).filter(pk=self.pump.pk).exists())
            self.assertIsNone(
                read_machine_parent(
                    admin, environment='synthetic', machine_pk=self.pump.pk
                )
            )
            ClientScopeGrant.objects.create(user=admin, client=self.client)
            self.assertIsNotNone(
                read_machine_parent(
                    admin, environment='synthetic', machine_pk=self.pump.pk
                )
            )
