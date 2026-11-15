"""Database-backed authorization controls for the native machine source."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from assets.integration_sources import read_machine
from assets.models import AssetLocation, AssetMachine, Client, ClientScopeGrant
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='unavailable-in-test',
)
class NativeMachineSourceDatabaseTests(TestCase):
    """Real native actor, role, scope and synthetic machines in a test database."""

    def setUp(self):
        """Create an actor with one permitted client and one unrelated client."""
        self.actor = get_user_model().objects.create_user(username='contract-reader')
        group = Group.objects.create(name='Contract test readers')
        self.actor.groups.add(group)
        self.rule, _ = RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        own = Client.objects.create(name='Synthetic own', code='contract-own')
        foreign = Client.objects.create(
            name='Synthetic foreign', code='contract-foreign'
        )
        self.grant = ClientScopeGrant.objects.create(user=self.actor, client=own)
        self.machine = AssetMachine.objects.create(
            name='Synthetic own machine', client=own
        )
        self.foreign = AssetMachine.objects.create(
            name='Synthetic foreign machine', client=foreign
        )

    def test_authorized_machine_is_read_but_foreign_and_missing_are_not(self):
        """Role plus matching current client scope is required before projection."""
        result = read_machine(
            self.actor, environment='test', machine_pk=self.machine.pk
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.identity.pk, self.machine.pk)
        self.assertEqual(result.name, self.machine.name)
        self.assertIsNone(
            read_machine(self.actor, environment='test', machine_pk=self.foreign.pk)
        )
        self.assertIsNone(
            read_machine(self.actor, environment='test', machine_pk=999999)
        )

    def test_valid_machine_profile_retains_raw_native_json(self):
        """Return authorized validated profile data without normalizing its text."""
        from assets.integration_sources import read_machine_profile

        profile = {
            'criticality': 'high',
            'components': [{'name': ' Synthetic bearing ', 'ref': ' B1 '}],
        }
        AssetMachine.objects.filter(pk=self.machine.pk).update(profile=profile)
        result = read_machine_profile(
            self.actor, environment='test', machine_pk=self.machine.pk
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.profile, profile)
        self.assertEqual(result.status, 'valid')
        self.assertNotIn('Synthetic bearing', repr(result))
        self.assertIsNone(
            read_machine_profile(
                self.actor, environment='test', machine_pk=self.foreign.pk
            )
        )

    def test_machine_profile_invalid_and_empty_are_distinct(self):
        """A malformed profile is withheld, not silently converted to empty."""
        from assets.integration_sources import read_machine_profile

        empty = read_machine_profile(
            self.actor, environment='test', machine_pk=self.machine.pk
        )
        assert empty is not None
        self.assertEqual(empty.status, 'valid')
        self.assertEqual(empty.profile, {})
        AssetMachine.objects.filter(pk=self.machine.pk).update(
            profile={'unknown-key': 'synthetic-private-value'}
        )
        invalid = read_machine_profile(
            self.actor, environment='test', machine_pk=self.machine.pk
        )
        assert invalid is not None
        self.assertEqual(invalid.status, 'invalid_withheld')
        self.assertIsNone(invalid.profile)
        self.assertNotIn('synthetic-private-value', repr(invalid))

    @override_settings(AIMMS_PLANT_TIMEZONE='')
    def test_machine_profile_matches_native_detail_and_is_detached(self):
        """The native detail and new profile agree without aliasing source JSON."""
        from rest_framework.test import APIRequestFactory, force_authenticate

        from assets.api import AssetMachineDetail
        from assets.integration_sources import read_machine_profile

        profile = {'components': [{'name': ' Synthetic ', 'ref': ' ref '}]}
        AssetMachine.objects.filter(pk=self.machine.pk).update(profile=profile)
        result = read_machine_profile(
            self.actor, environment='test', machine_pk=self.machine.pk
        )
        request = APIRequestFactory().get('/synthetic-machine-detail/')
        force_authenticate(request, user=self.actor)
        response = AssetMachineDetail.as_view()(request, pk=self.machine.pk)
        self.assertEqual(response.status_code, 200)
        assert result is not None
        assert isinstance(result.profile, dict)
        self.assertEqual(result.profile, response.data['profile'])
        result.profile['components'][0]['name'] = 'local mutation'
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.profile, profile)

    def test_machine_profile_requires_scope_grant_role_and_current_actor(self):
        """Each authorization boundary remains necessary for profile data."""
        from assets.integration_sources import read_machine_profile

        def read():
            return read_machine_profile(
                self.actor, environment='test', machine_pk=self.machine.pk
            )

        self.assertIsNotNone(read())
        with patch(
            'tasks.scope.scoped_machines', return_value=AssetMachine.objects.none()
        ):
            self.assertIsNone(read())
        with patch(
            'assets.integration_authority.explicitly_granted_client_ids',
            return_value=set(),
        ):
            self.assertIsNone(read())
        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])
        self.assertIsNone(read())
        self.rule.can_view = True
        self.rule.save(update_fields=['can_view'])
        self.assertIsNotNone(read())
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        self.assertIsNone(read())

    def test_profile_rechecks_revocation_after_validation(self):
        """A revocation during profile work prevents the final projection."""
        from assets.integration_sources import read_machine_profile
        from assets.machine_profile import validate_machine_profile

        self.assertIsNotNone(
            read_machine_profile(
                self.actor, environment='test', machine_pk=self.machine.pk
            )
        )

        def revoke(value):
            result = validate_machine_profile(value)
            self.grant.delete()
            return result

        with patch(
            'assets.machine_profile.validate_machine_profile', side_effect=revoke
        ):
            self.assertIsNone(
                read_machine_profile(
                    self.actor, environment='test', machine_pk=self.machine.pk
                )
            )

    def test_profile_change_during_validation_withholds_old_value(self):
        """Do not return profile data from before a selected concurrent edit."""
        from assets.integration_sources import read_machine_profile
        from assets.machine_profile import validate_machine_profile

        self.assertIsNotNone(
            read_machine_profile(
                self.actor, environment='test', machine_pk=self.machine.pk
            )
        )

        def change(value):
            result = validate_machine_profile(value)
            AssetMachine.objects.filter(pk=self.machine.pk).update(
                profile={'criticality': 'high'}
            )
            return result

        with patch(
            'assets.machine_profile.validate_machine_profile', side_effect=change
        ):
            self.assertIsNone(
                read_machine_profile(
                    self.actor, environment='test', machine_pk=self.machine.pk
                )
            )
        current = read_machine_profile(
            self.actor, environment='test', machine_pk=self.machine.pk
        )
        assert current is not None
        self.assertEqual(current.profile, {'criticality': 'high'})

    def test_invalid_profile_status_requires_authority_after_validation(self):
        """Even the withheld-invalid marker is not returned after revocation."""
        from django.core.exceptions import ValidationError

        from assets.integration_sources import read_machine_profile

        def revoke_and_fail(value):
            self.grant.delete()
            raise ValidationError('synthetic invalid profile')

        with patch(
            'assets.machine_profile.validate_machine_profile',
            side_effect=revoke_and_fail,
        ):
            self.assertIsNone(
                read_machine_profile(
                    self.actor, environment='test', machine_pk=self.machine.pk
                )
            )

    def test_machine_details_preserve_native_direct_values(self):
        """Direct fields preserve blanks and original timestamps without edges."""
        from assets.integration_sources import read_machine_details

        AssetMachine.objects.filter(pk=self.machine.pk).update(
            description='Synthetic machine description',
            location='Synthetic bay',
            manufacturer='Synthetic maker',
            model='',
            serial='Synthetic serial',
        )
        self.machine.refresh_from_db()
        result = read_machine_details(
            self.actor, environment='test', machine_pk=self.machine.pk
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.schema_version, 'aimms.machine.details.v2')
        for name in (
            'name',
            'description',
            'active',
            'asset_type',
            'location',
            'manufacturer',
            'model',
            'serial',
            'created_at',
            'updated_at',
        ):
            with self.subTest(field=name):
                self.assertEqual(getattr(result, name), getattr(self.machine, name))
        for omitted in ('client', 'parent', 'physical_location', 'profile', 'counts'):
            self.assertFalse(hasattr(result, omitted))
        self.assertNotIn('Synthetic machine description', repr(result))
        self.assertNotIn('Synthetic serial', repr(result))

    @override_settings(AIMMS_PLANT_TIMEZONE='')
    def test_machine_details_match_selected_native_detail_fields(self):
        """The selected direct values match the native synthetic detail view."""
        from rest_framework.fields import DateTimeField
        from rest_framework.test import APIRequestFactory, force_authenticate

        from assets.api import AssetMachineDetail
        from assets.integration_sources import read_machine_details

        result = read_machine_details(
            self.actor, environment='test', machine_pk=self.machine.pk
        )
        assert result is not None
        request = APIRequestFactory().get('/synthetic-machine-detail/')
        force_authenticate(request, user=self.actor)
        response = AssetMachineDetail.as_view()(request, pk=self.machine.pk)
        self.assertEqual(response.status_code, 200)
        for name in (
            'name',
            'description',
            'active',
            'asset_type',
            'location',
            'manufacturer',
            'model',
            'serial',
        ):
            with self.subTest(field=name):
                self.assertEqual(getattr(result, name), response.data[name])
        for name in ('created_at', 'updated_at'):
            self.assertEqual(getattr(result, name), getattr(self.machine, name))
            self.assertEqual(
                DateTimeField().to_representation(getattr(result, name)),
                response.data[name],
            )

    def test_machine_details_require_current_role_grant_scope_and_actor(self):
        """Selected direct fields require both native scope and explicit grant."""
        from rest_framework.test import APIRequestFactory, force_authenticate

        from assets.api import AssetMachineDetail
        from assets.integration_sources import read_machine_details

        def read():
            return read_machine_details(
                self.actor, environment='test', machine_pk=self.machine.pk
            )

        self.assertIsNotNone(read())
        self.assertIsNone(
            read_machine_details(
                self.actor, environment='test', machine_pk=self.foreign.pk
            )
        )
        with patch(
            'tasks.scope.scoped_machines', return_value=AssetMachine.objects.none()
        ):
            self.assertIsNone(read())
        self.assertIsNotNone(read())
        with patch(
            'assets.integration_authority.explicitly_granted_client_ids',
            return_value=set(),
        ):
            self.assertIsNone(read())
        self.assertIsNotNone(read())
        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])
        self.assertIsNone(read())
        request = APIRequestFactory().get('/synthetic-machine-detail/')
        force_authenticate(request, user=self.actor)
        self.assertEqual(
            AssetMachineDetail.as_view()(request, pk=self.machine.pk).status_code, 403
        )
        self.rule.can_view = True
        self.rule.save(update_fields=['can_view'])
        self.assertIsNotNone(read())
        self.grant.delete()
        self.assertIsNone(read())
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.machine.client
        )
        self.assertIsNotNone(read())
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        self.assertIsNone(read())
        self.assertIsNone(
            read_machine_details(None, environment='test', machine_pk=self.machine.pk)
        )

    def test_superuser_machine_details_still_require_an_explicit_grant(self):
        """Superuser status does not bypass integration-specific client grants."""
        from assets.integration_sources import read_machine_details

        admin = get_user_model().objects.create_superuser(
            username='synthetic-integration-admin'
        )
        self.assertIsNone(
            read_machine_details(admin, environment='test', machine_pk=self.machine.pk)
        )
        ClientScopeGrant.objects.create(user=admin, client=self.machine.client)
        self.assertIsNotNone(
            read_machine_details(admin, environment='test', machine_pk=self.machine.pk)
        )

    def test_machine_details_withhold_interleaved_grant_revocation(self):
        """A revoked grant between source reads prevents direct-value release."""
        from assets.integration_authority import explicitly_granted_client_ids
        from assets.integration_sources import read_machine_details

        calls = 0

        def revoke(user):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.grant.delete()
            return explicitly_granted_client_ids(user)

        with patch(
            'assets.integration_authority.explicitly_granted_client_ids',
            side_effect=revoke,
        ):
            self.assertIsNone(
                read_machine_details(
                    self.actor, environment='test', machine_pk=self.machine.pk
                )
            )
        self.assertEqual(calls, 2)

    def test_revoked_grant_is_not_authorized(self):
        """A successful read does not survive client grant revocation."""
        self.assertIsNotNone(
            read_machine(self.actor, environment='test', machine_pk=self.machine.pk)
        )
        self.grant.delete()
        self.assertIsNone(
            read_machine(self.actor, environment='test', machine_pk=self.machine.pk)
        )

    def test_disabled_user_is_not_authorized(self):
        """User deactivation is enforced while role and client grant remain."""
        self.assertIsNotNone(
            read_machine(self.actor, environment='test', machine_pk=self.machine.pk)
        )
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        self.assertIsNone(
            read_machine(self.actor, environment='test', machine_pk=self.machine.pk)
        )
        self.actor.is_active = True
        self.actor.save(update_fields=['is_active'])
        self.assertIsNotNone(
            read_machine(self.actor, environment='test', machine_pk=self.machine.pk)
        )

    def test_revoked_work_role_is_not_authorized(self):
        """A new read checks current role state instead of an earlier result."""
        self.assertIsNotNone(
            read_machine(self.actor, environment='test', machine_pk=self.machine.pk)
        )
        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])
        self.assertIsNone(
            read_machine(self.actor, environment='test', machine_pk=self.machine.pk)
        )

    def test_location_requires_matching_current_scope_and_explicit_grant(self):
        """A separate location read cannot borrow authority from a machine."""
        from assets.integration_sources import read_location

        reader = read_location
        own = AssetLocation.objects.create(
            client=self.machine.client,
            name='Synthetic authorized location',
            code='own-location',
            name_key='own',
            sibling_key='root',
            archived=True,
        )
        foreign = AssetLocation.objects.create(
            client=self.foreign.client,
            name='Synthetic foreign location',
            code='foreign-location',
            name_key='foreign',
            sibling_key='root',
        )
        result = reader(self.actor, environment='test', location_pk=own.pk)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.identity.pk, own.pk)
        self.assertEqual(result.name, own.name)
        self.assertTrue(result.archived)
        self.assertEqual(result.schema_version, 'aimms.location.basic.v1')
        self.assertIsNone(
            reader(self.actor, environment='test', location_pk=foreign.pk)
        )
        with patch(
            'assets.locations.locations_for', return_value=AssetLocation.objects.none()
        ):
            self.assertIsNone(
                reader(self.actor, environment='test', location_pk=own.pk)
            )
        self.assertIsNotNone(reader(self.actor, environment='test', location_pk=own.pk))
        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])
        self.assertIsNone(reader(self.actor, environment='test', location_pk=own.pk))
        self.rule.can_view = True
        self.rule.save(update_fields=['can_view'])
        self.assertIsNotNone(reader(self.actor, environment='test', location_pk=own.pk))
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        self.assertIsNone(reader(self.actor, environment='test', location_pk=own.pk))
        self.actor.is_active = True
        self.actor.save(update_fields=['is_active'])
        self.assertIsNotNone(reader(self.actor, environment='test', location_pk=own.pk))
        self.grant.delete()
        self.assertIsNone(reader(self.actor, environment='test', location_pk=own.pk))

    def test_location_withholds_if_grant_changes_between_scope_reads(self):
        """A selected sequential revocation is checked before projection."""
        from assets.integration_sources import read_location
        from assets.locations import locations_for

        own = AssetLocation.objects.create(
            client=self.machine.client,
            name='Synthetic changing location',
            code='changing-location',
            name_key='changing',
            sibling_key='root',
        )
        calls = []

        def revoke(actor):
            calls.append(actor.pk)
            if len(calls) == 2:
                self.grant.delete()
            return locations_for(actor)

        with patch('assets.locations.locations_for', side_effect=revoke):
            result = read_location(self.actor, environment='test', location_pk=own.pk)
        self.assertEqual(calls, [self.actor.pk, self.actor.pk])
        self.assertIsNone(result)
