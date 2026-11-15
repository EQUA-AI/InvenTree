"""Synthetic current-core direct-field and source evidence controls."""

from integration_state.tests.assertions import assert_literal_bool

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from assets.models import AssetMachine, Client, ClientScopeGrant
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s1-fields-fallback',
)
class CoreFieldTests(TestCase):
    """Direct field projections do not implicitly authorize related records."""

    def setUp(self):
        """Create a granted synthetic actor and native machine."""
        self.actor = get_user_model().objects.create_user(username='s1-field-actor')
        group = Group.objects.create(name='S1 direct field readers')
        self.actor.groups.add(group)
        self.work, _ = RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.admin = RuleSet.objects.get(group=group, name='admin')
        RuleSet.objects.filter(pk=self.admin.pk).update(can_view=True)
        self.tenant = Client.objects.create(name='S1 field tenant', code='s1-fields')
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.tenant
        )
        self.machine = AssetMachine.objects.create(
            name='S1 direct field machine',
            client=self.tenant,
            source_namespace='s1-native-source',
            source_key='',
            source_context={'private-sentinel': 's1-withheld-context'},
        )

    def test_registration_preserves_native_identity_without_raw_source_context(self):
        """Native UUID/key nulls survive without exporting unapproved raw JSON."""
        from assets import integration_sources
        from assets.registry_api import EquipmentSerializer

        reader = getattr(integration_sources, 'read_machine_registration', None)
        self.assertTrue(callable(reader), 'A registration reader is required')
        assert callable(reader)
        result = reader(
            self.actor, environment=' s1-source ', machine_pk=self.machine.pk
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.schema_version, 'aimms.machine.registration.v1')
        self.assertEqual(result.identity.environment, ' s1-source ')
        native = EquipmentSerializer(self.machine).data
        self.assertEqual(str(result.uuid), native['uuid'])
        self.assertEqual(result.source_namespace, native['source_namespace'])
        self.assertEqual(result.source_key, '')
        self.assertIsNone(result.source_entity_uuid)
        self.assertEqual(result.source_context_state, 'withheld')
        self.assertFalse(hasattr(result, 'source_context'))
        self.assertNotIn('s1-withheld-context', repr(result))
        self.assertFalse(hasattr(result, 'client'))
        self.assertFalse(hasattr(result, 'parent'))

    def test_client_structure_exposes_native_counter_without_completeness(self):
        """A native structural counter is not an export generation or watermark."""
        from assets import integration_sources

        reader = getattr(integration_sources, 'read_client_revision', None)
        self.assertTrue(callable(reader), 'A client structure reader is required')
        assert callable(reader)
        Client.objects.filter(pk=self.tenant.pk).update(location_revision=7)
        self.tenant.refresh_from_db()
        result = reader(self.actor, environment='s1-test', client_pk=self.tenant.pk)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.schema_version, 'aimms.client.structure.v1')
        self.assertEqual(result.location_revision, self.tenant.location_revision)
        assert_literal_bool(self, result.revision_complete, False)
        self.assertFalse(hasattr(result, 'generation'))

    def test_barcode_preserves_native_hash_links_but_withholds_routing_bytes(self):
        """Barcode lookup material is not raw routing content or byte identity."""
        from assets import integration_sources

        reader = getattr(integration_sources, 'read_machine_barcode', None)
        self.assertTrue(callable(reader), 'A machine barcode reader is required')
        assert callable(reader)
        self.machine.assign_barcode(barcode_data='s1-private-routing-sentinel')
        result = reader(self.actor, environment='s1-test', machine_pk=self.machine.pk)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.schema_version, 'aimms.machine.barcode.v1')
        self.assertEqual(result.barcode_hash, self.machine.barcode_hash)
        self.assertEqual(
            result.api_url, f'{self.machine.get_api_url()}{self.machine.pk}/'
        )
        self.assertEqual(result.web_url, self.machine.get_absolute_url())
        self.assertEqual(result.barcode_data_state, 'withheld')
        self.assertFalse(hasattr(result, 'barcode_data'))
        self.assertNotIn('s1-private-routing-sentinel', repr(result))

    def test_placement_carries_observed_version_without_upgrading_v1(self):
        """Observed unassigned/available placement never claims a complete feed."""
        from assets import integration_machine_location
        from assets.models import AssetLocation

        reader = getattr(integration_machine_location, 'read_machine_placement', None)
        self.assertTrue(callable(reader), 'A versioned placement reader is required')
        assert callable(reader)
        empty = reader(self.actor, environment='s1-test', machine_pk=self.machine.pk)
        self.assertEqual(empty.state, 'unassigned')
        self.assertIsNone(empty.location)
        location = AssetLocation.objects.create(
            client=self.tenant,
            name='S1 placement',
            code='s1-placement',
            name_key='s1 placement',
            sibling_key='root',
            timezone='Europe/Berlin',
        )
        AssetMachine.objects.filter(pk=self.machine.pk).update(
            physical_location=location, placement_version=3
        )
        result = reader(self.actor, environment='s1-test', machine_pk=self.machine.pk)
        self.assertEqual(result.schema_version, 'aimms.machine.location.v2')
        self.assertEqual(result.state, 'available')
        self.assertEqual(result.location.pk, location.pk)
        self.assertEqual(result.placement_version, 3)
        assert_literal_bool(self, result.revision_complete, False)
        legacy = integration_machine_location.read_machine_location(
            self.actor, environment='s1-test', machine_pk=self.machine.pk
        )
        self.assertEqual(legacy.schema_version, 'aimms.machine.location.v1')
        self.assertFalse(hasattr(legacy, 'placement_version'))

    def machine_readers(self):
        """Cover every current machine projection, not just its shared helper."""
        from assets.integration_machine_location import read_machine_placement
        from assets.integration_sources import (
            read_machine,
            read_machine_barcode,
            read_machine_details,
            read_machine_profile,
            read_machine_registration,
        )

        return (
            read_machine,
            read_machine_details,
            read_machine_profile,
            read_machine_registration,
            read_machine_barcode,
            read_machine_placement,
        )

    def assert_machine_results(self, actor, *, available):
        """Require the expected disposition for each reader's real ORM path."""
        for reader in self.machine_readers():
            with self.subTest(reader=reader.__name__, available=available):
                result = reader(
                    actor, environment='s1-test', machine_pk=self.machine.pk
                )
                if available:
                    self.assertIsNotNone(result)
                else:
                    self.assertIsNone(result)

    def test_each_machine_reader_intersects_native_scope_with_explicit_grant(self):
        """Native-positive no/foreign grants and grant-positive empty scope deny."""
        from tasks.scope import MaintenanceScope, scoped_machines

        scope = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        }
        other = Client.objects.create(name='S1 unrelated tenant', code='s1-other')
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: scope):
            self.assert_machine_results(self.actor, available=True)
            self.grant.delete()
            ClientScopeGrant.objects.create(user=self.actor, client=other)
            self.assertTrue(
                scoped_machines(self.actor).filter(pk=self.machine.pk).exists()
            )
            self.assert_machine_results(self.actor, available=False)
            ClientScopeGrant.objects.create(user=self.actor, client=self.tenant)
            self.assert_machine_results(self.actor, available=True)
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: set()):
            self.assertTrue(
                ClientScopeGrant.objects.filter(
                    user=self.actor, client=self.tenant
                ).exists()
            )
            self.assertFalse(
                scoped_machines(self.actor).filter(pk=self.machine.pk).exists()
            )
            self.assert_machine_results(self.actor, available=False)
        self.assert_machine_results(self.actor, available=True)

    def test_each_machine_reader_requires_current_actor_role_client_and_owner(self):
        """Stale actor/cache and transferred-out records cannot reuse admission."""
        self.assert_machine_results(self.actor, available=True)
        for model, pk, field in (
            (get_user_model(), self.actor.pk, 'is_active'),
            (RuleSet, self.work.pk, 'can_view'),
            (Client, self.tenant.pk, 'active'),
        ):
            with self.subTest(model=model.__name__, field=field):
                model.objects.filter(pk=pk).update(**{field: False})
                self.assert_machine_results(self.actor, available=False)
                model.objects.filter(pk=pk).update(**{field: True})
                self.assert_machine_results(self.actor, available=True)
        other = Client.objects.create(name='S1 transferred owner', code='s1-transfer')
        AssetMachine.objects.filter(pk=self.machine.pk).update(client=other)
        self.assert_machine_results(self.actor, available=False)
        AssetMachine.objects.filter(pk=self.machine.pk).update(client=self.tenant)
        self.assert_machine_results(self.actor, available=True)
        self.assert_machine_results(None, available=False)
        get_user_model().objects.filter(pk=self.actor.pk).delete()
        self.assert_machine_results(self.actor, available=False)

    def test_each_machine_reader_keeps_superuser_native_scope_separate_from_grant(self):
        """A superuser can have native scope and still lack integration authority."""
        from tasks.scope import MaintenanceScope, scoped_machines

        actor = get_user_model().objects.create_superuser(username='s1-field-admin')
        scope = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda user: scope):
            self.assertTrue(scoped_machines(actor).filter(pk=self.machine.pk).exists())
            self.assert_machine_results(actor, available=False)
            ClientScopeGrant.objects.create(user=actor, client=self.tenant)
            self.assert_machine_results(actor, available=True)

    def test_client_structure_has_current_admin_grant_and_account_controls(self):
        """The structure counter follows client authority, not machine Work-role."""
        from assets.integration_sources import read_client_revision

        def read():
            return read_client_revision(
                self.actor, environment='s1-test', client_pk=self.tenant.pk
            )

        self.assertIsNotNone(read())
        RuleSet.objects.filter(pk=self.work.pk).update(can_view=False)
        self.assertIsNotNone(read())
        RuleSet.objects.filter(pk=self.admin.pk).update(can_view=False)
        self.assertIsNone(read())
        RuleSet.objects.filter(pk=self.admin.pk).update(can_view=True)
        self.assertIsNotNone(read())
        self.grant.delete()
        self.assertIsNone(read())
        ClientScopeGrant.objects.create(user=self.actor, client=self.tenant)
        self.assertIsNotNone(read())
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        self.assertIsNone(read())

    def test_all_profile_families_keep_raw_values_separate_from_native_normalization(
        self,
    ):
        """Schema-valid declarations neither resolve references nor authorize work."""
        from assets.integration_sources import read_machine_profile
        from assets.machine_profile import ALLOWED_KEYS, validate_machine_profile
        from repair.models import LockoutPoint

        raw = {
            'criticality': 'high',
            'maintenance_strategy': 'predictive',
            'components': [
                {'name': ' Pump ', 'ref': ' pump '},
                {'name': ' Shaft ', 'ref': ' shaft ', 'parent_ref': ' pump '},
            ],
            'energy_sources': [LockoutPoint.EnergySource.values[0]],
            'fault_codes': [' S1-fault '],
            'approved_spares': [' s1-unresolved-spare '],
        }
        self.assertEqual(set(raw), ALLOWED_KEYS)
        AssetMachine.objects.filter(pk=self.machine.pk).update(profile=raw)
        result = read_machine_profile(
            self.actor, environment='s1-test', machine_pk=self.machine.pk
        )
        assert result is not None and isinstance(result.profile, dict)
        self.assertEqual(result.status, 'valid')
        self.assertEqual(result.profile, raw)
        self.assertNotEqual(result.profile, validate_machine_profile(raw))
        self.assertNotIn('s1-unresolved-spare', repr(result))
        result.profile['fault_codes'].append('S1-mutated-result')
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.profile, raw)

    def test_invalid_profile_families_are_withheld_not_empty_valid_profiles(self):
        """Native validation failures keep an explicit invalid disposition."""
        from assets.integration_sources import read_machine_profile

        for profile in (
            {'criticality': 'unknown'},
            {'maintenance_strategy': 'unknown'},
            {'components': [{'name': 'bad', 'ref': 'a', 'parent_ref': 'missing'}]},
            {'energy_sources': ['s1-unrecognized-source']},
            {'fault_codes': [False]},
            {'approved_spares': ['']},
        ):
            with self.subTest(profile=profile):
                AssetMachine.objects.filter(pk=self.machine.pk).update(profile=profile)
                result = read_machine_profile(
                    self.actor, environment='s1-test', machine_pk=self.machine.pk
                )
                assert result is not None
                self.assertEqual(result.status, 'invalid_withheld')
                self.assertIsNone(result.profile)
        AssetMachine.objects.filter(pk=self.machine.pk).update(profile={})
        result = read_machine_profile(
            self.actor, environment='s1-test', machine_pk=self.machine.pk
        )
        assert result is not None
        self.assertEqual(result.status, 'valid')
        self.assertEqual(result.profile, {})

    def test_placement_final_fence_refuses_changed_version_and_revocation(self):
        """Mutations after all v1 endpoint work are still checked by v2."""
        from assets import integration_machine_location as placement

        original = placement.read_machine_location

        def mutate(*args, **kwargs):
            edge = original(*args, **kwargs)
            AssetMachine.objects.filter(pk=self.machine.pk).update(placement_version=8)
            return edge

        with patch.object(placement, 'read_machine_location', side_effect=mutate):
            self.assertIsNone(
                placement.read_machine_placement(
                    self.actor, environment='s1-test', machine_pk=self.machine.pk
                )
            )
        current = placement.read_machine_placement(
            self.actor, environment='s1-test', machine_pk=self.machine.pk
        )
        self.assertEqual(current.placement_version, 8)

        def revoke(*args, **kwargs):
            edge = original(*args, **kwargs)
            self.grant.delete()
            return edge

        with patch.object(placement, 'read_machine_location', side_effect=revoke):
            self.assertIsNone(
                placement.read_machine_placement(
                    self.actor, environment='s1-test', machine_pk=self.machine.pk
                )
            )

    def test_bulk_writes_do_not_advance_the_exposed_native_counters(self):
        """Alternate-writer evidence prohibits treating native counters as CDC."""
        from assets.integration_machine_location import read_machine_placement
        from assets.integration_sources import (
            read_client_revision,
            read_machine_details,
        )

        before = read_machine_placement(
            self.actor, environment='s1-test', machine_pk=self.machine.pk
        )
        structure = read_client_revision(
            self.actor, environment='s1-test', client_pk=self.tenant.pk
        )
        AssetMachine.objects.filter(pk=self.machine.pk).update(
            description='S1 bulk change'
        )
        after = read_machine_placement(
            self.actor, environment='s1-test', machine_pk=self.machine.pk
        )
        details = read_machine_details(
            self.actor, environment='s1-test', machine_pk=self.machine.pk
        )
        assert before is not None and after is not None and details is not None
        assert structure is not None
        self.assertEqual(details.description, 'S1 bulk change')
        self.assertEqual(before.placement_version, after.placement_version)
        updated_structure = read_client_revision(
            self.actor, environment='s1-test', client_pk=self.tenant.pk
        )
        assert updated_structure is not None
        self.assertEqual(
            structure.location_revision, updated_structure.location_revision
        )
        assert_literal_bool(self, after.revision_complete, False)
