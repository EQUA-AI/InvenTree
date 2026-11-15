"""Synthetic integration controls for direct location properties."""

from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from assets.models import AssetLocation, Client, ClientScopeGrant
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='unavailable-in-test',
)
class LocationDetailsSourceTests(TestCase):
    """Direct fields remain distinct from separately authorized edges."""

    def setUp(self):
        """Create a scoped actor and a synthetic archived location."""
        self.actor = get_user_model().objects.create_user(
            username='location-detail-test'
        )
        group = Group.objects.create(name='Location detail readers')
        self.actor.groups.add(group)
        self.rule, _ = RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.tenant = Client.objects.create(
            name='Synthetic location tenant', code='loc-tenant'
        )
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.tenant
        )
        self.location = AssetLocation.objects.create(
            client=self.tenant,
            name='Synthetic workshop',
            code='workshop',
            kind=AssetLocation.Kind.BUILDING,
            timezone='',
            name_key='workshop',
            sibling_key='root',
            archived=True,
        )

    def test_authorized_direct_properties_keep_native_values(self):
        """Expose direct native properties without resolving any related edge."""
        from assets.integration_sources import read_location_details

        result = read_location_details(
            self.actor, environment='test', location_pk=self.location.pk
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.identity.pk, self.location.pk)
        self.assertEqual(result.schema_version, 'aimms.location.details.v1')
        self.assertEqual(result.name, 'Synthetic workshop')
        self.assertEqual(result.code, 'workshop')
        self.assertEqual(result.kind, AssetLocation.Kind.BUILDING)
        self.assertEqual(result.timezone, '')
        self.assertTrue(result.archived)
        self.assertEqual(result.version, self.location.version)
        for omitted in ('parent', 'path', 'counts', 'client', 'effective_timezone'):
            self.assertFalse(hasattr(result, omitted))

    def test_extended_direct_properties_preserve_native_text_and_timestamps(self):
        """The new version exposes native direct values without inferred edges."""
        from assets.integration_sources import read_location_properties

        AssetLocation.objects.filter(pk=self.location.pk).update(
            description='Synthetic multiline\ndescription'
        )
        self.location.refresh_from_db()
        result = read_location_properties(
            self.actor, environment='test', location_pk=self.location.pk
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.schema_version, 'aimms.location.details.v2')
        self.assertEqual(result.description, self.location.description)
        self.assertEqual(result.created_at, self.location.created_at)
        self.assertEqual(result.updated_at, self.location.updated_at)
        self.assertEqual(result.timezone, '')
        for name in ('name', 'code', 'kind', 'archived', 'version'):
            self.assertEqual(getattr(result, name), getattr(self.location, name))
        for omitted in ('parent', 'path', 'counts', 'client', 'effective_timezone'):
            self.assertFalse(hasattr(result, omitted))

    def test_extended_properties_preserve_blank_and_native_detail_values(self):
        """Blank text and source timestamps match the native detail response."""
        from rest_framework.test import APIRequestFactory, force_authenticate

        from assets.integration_sources import read_location_properties
        from assets.location_api import LocationDetail

        result = read_location_properties(
            self.actor, environment='test', location_pk=self.location.pk
        )
        assert result is not None
        self.assertEqual(result.description, '')
        request = APIRequestFactory().get('/synthetic-location-detail/')
        force_authenticate(request, user=self.actor)
        response = LocationDetail.as_view()(request, pk=self.location.pk)
        self.assertEqual(response.status_code, 200)
        for name in (
            'name',
            'code',
            'kind',
            'description',
            'timezone',
            'archived',
            'version',
            'created_at',
            'updated_at',
        ):
            with self.subTest(field=name):
                self.assertEqual(getattr(result, name), response.data[name])

    def test_extended_properties_require_fresh_role_grant_and_active_actor(self):
        """A positive read is denied when any selected authority is removed."""
        from assets.integration_sources import read_location_properties

        def read():
            return read_location_properties(
                self.actor, environment='test', location_pk=self.location.pk
            )

        self.assertIsNotNone(read())
        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])
        self.assertIsNone(read())
        self.rule.can_view = True
        self.rule.save(update_fields=['can_view'])
        self.assertIsNotNone(read())
        self.grant.delete()
        self.assertIsNone(read())
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.tenant
        )
        self.assertIsNotNone(read())
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        self.assertIsNone(read())
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=True)
        self.assertIsNotNone(read())
        Client.objects.filter(pk=self.tenant.pk).update(active=False)
        self.assertIsNone(read())

    def test_extended_properties_withhold_revocation_between_source_checks(self):
        """Losing the explicit grant during selection prevents free-text release."""
        from assets.integration_sources import read_location_properties
        from assets.locations import locations_for

        self.assertIsNotNone(
            read_location_properties(
                self.actor, environment='test', location_pk=self.location.pk
            )
        )
        calls = 0

        def revoke(actor):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.grant.delete()
            return locations_for(actor)

        with patch('assets.locations.locations_for', side_effect=revoke):
            self.assertIsNone(
                read_location_properties(
                    self.actor, environment='test', location_pk=self.location.pk
                )
            )
        self.assertEqual(calls, 2)

    def test_extended_properties_require_both_native_scope_and_explicit_grant(self):
        """Native scope and the target client's grant are independently required."""
        from assets.integration_sources import read_location_properties

        def read():
            return read_location_properties(
                self.actor, environment='test', location_pk=self.location.pk
            )

        self.assertIsNotNone(read())
        with patch(
            'assets.locations.locations_for', return_value=AssetLocation.objects.none()
        ):
            self.assertTrue(ClientScopeGrant.objects.filter(pk=self.grant.pk).exists())
            self.assertIsNone(read())
        self.assertIsNotNone(read())
        self.grant.delete()
        other = Client.objects.create(name='Other v2 tenant', code='other-v2-tenant')
        ClientScopeGrant.objects.create(user=self.actor, client=other)
        with patch(
            'assets.locations.locations_for', return_value=AssetLocation.objects.all()
        ):
            self.assertIsNone(read())

    def test_role_and_grant_are_independently_enforced(self):
        """Remove and restore role and grant independently from an allowed read."""
        from assets.integration_sources import read_location_details

        def read():
            return read_location_details(
                self.actor, environment='test', location_pk=self.location.pk
            )

        self.assertIsNotNone(read())
        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])
        self.assertTrue(ClientScopeGrant.objects.filter(pk=self.grant.pk).exists())
        self.assertIsNone(read())
        self.rule.can_view = True
        self.rule.save(update_fields=['can_view'])
        self.assertIsNotNone(read())
        self.grant.delete()
        self.assertIsNone(read())
        ClientScopeGrant.objects.create(user=self.actor, client=self.tenant)
        self.assertIsNotNone(read())

    def test_native_scope_and_explicit_grant_are_independently_required(self):
        """Native scope and the separate integration grant cannot replace each other."""
        from assets.integration_sources import read_location, read_location_details
        from assets.locations import locations_for

        readers = (read_location, read_location_details)

        def read(reader):
            return reader(self.actor, environment='test', location_pk=self.location.pk)

        self.assertTrue(locations_for(self.actor).filter(pk=self.location.pk).exists())
        self.assertTrue(all(read(reader) is not None for reader in readers))
        with patch(
            'assets.integration_authority.explicitly_granted_client_ids',
            return_value=ClientScopeGrant.objects.none().values('client_id'),
        ):
            self.assertTrue(
                locations_for(self.actor).filter(pk=self.location.pk).exists()
            )
            self.assertTrue(all(read(reader) is None for reader in readers))
        self.assertTrue(all(read(reader) is not None for reader in readers))
        with patch(
            'assets.locations.locations_for', return_value=AssetLocation.objects.none()
        ):
            self.assertTrue(ClientScopeGrant.objects.filter(pk=self.grant.pk).exists())
            self.assertTrue(all(read(reader) is None for reader in readers))
        self.assertTrue(all(read(reader) is not None for reader in readers))
        self.grant.delete()
        with patch(
            'assets.locations.locations_for', return_value=AssetLocation.objects.all()
        ):
            self.assertTrue(all(read(reader) is None for reader in readers))

    def test_superuser_and_anonymous_still_require_the_integration_boundary(self):
        """Superuser role cannot replace an explicit grant; anonymous is denied."""
        from assets.integration_sources import read_location, read_location_details

        admin = get_user_model().objects.create_user(
            username='synthetic-location-admin', is_superuser=True
        )
        with patch(
            'assets.locations.locations_for', return_value=AssetLocation.objects.all()
        ):
            for reader in (read_location, read_location_details):
                with self.subTest(reader=reader.__name__):
                    self.assertIsNone(
                        reader(admin, environment='test', location_pk=self.location.pk)
                    )
                    self.assertIsNone(
                        reader(None, environment='test', location_pk=self.location.pk)
                    )
                    ClientScopeGrant.objects.get_or_create(
                        user=admin, client=self.tenant
                    )
                    self.assertIsNotNone(
                        reader(admin, environment='test', location_pk=self.location.pk)
                    )
                    ClientScopeGrant.objects.filter(
                        user=admin, client=self.tenant
                    ).delete()

    def test_native_scope_error_is_withheld_without_disclosing_details(self):
        """Both readers deny a native scope resolver failure."""
        from tasks.scope import ScopeError

        from assets.integration_sources import read_location, read_location_details

        for reader in (read_location, read_location_details):
            with self.subTest(reader=reader.__name__):
                self.assertIsNotNone(
                    reader(self.actor, environment='test', location_pk=self.location.pk)
                )
                with patch(
                    'assets.locations.scope_for_actor',
                    side_effect=ScopeError('synthetic'),
                ):
                    self.assertIsNone(
                        reader(
                            self.actor, environment='test', location_pk=self.location.pk
                        )
                    )

    def test_native_location_detail_allows_the_same_actor(self):
        """The native detail exposes the same direct fields to this actor."""
        from rest_framework.test import APIRequestFactory, force_authenticate

        from assets.location_api import LocationDetail

        request = APIRequestFactory().get('/synthetic-location-detail/')
        force_authenticate(request, user=self.actor)
        response = LocationDetail.as_view()(request, pk=self.location.pk)
        self.assertEqual(response.status_code, 200)
        for name in ('code', 'kind', 'timezone', 'archived', 'version'):
            self.assertEqual(response.data[name], getattr(self.location, name))

    def test_native_and_integration_readers_deny_missing_work_role(self):
        """The synthetic native endpoint and both source readers require the role."""
        from rest_framework.test import APIRequestFactory, force_authenticate

        from assets.integration_sources import read_location, read_location_details
        from assets.location_api import LocationDetail

        self.assertIsNotNone(
            read_location_details(
                self.actor, environment='test', location_pk=self.location.pk
            )
        )
        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])
        for reader in (read_location, read_location_details):
            self.assertIsNone(
                reader(self.actor, environment='test', location_pk=self.location.pk)
            )
        request = APIRequestFactory().get('/synthetic-location-detail/')
        force_authenticate(request, user=self.actor)
        response = LocationDetail.as_view()(request, pk=self.location.pk)
        self.assertEqual(response.status_code, 403)

    def test_wrong_client_grant_does_not_authorize_broad_native_scope(self):
        """A B grant cannot release A when native scope independently includes A."""
        from assets.integration_sources import read_location, read_location_details

        other = Client.objects.create(
            name='Other grant tenant', code='other-grant-tenant'
        )
        self.grant.delete()
        ClientScopeGrant.objects.create(user=self.actor, client=other)
        with patch(
            'assets.locations.locations_for', return_value=AssetLocation.objects.all()
        ):
            for reader in (read_location, read_location_details):
                with self.subTest(reader=reader.__name__):
                    self.assertIsNone(
                        reader(
                            self.actor, environment='test', location_pk=self.location.pk
                        )
                    )

    def test_invalid_actor_and_missing_location_are_withheld(self):
        """Both readers reject malformed or unauthenticated actor coordinates."""
        from assets.integration_sources import read_location, read_location_details

        actors = (
            None,
            SimpleNamespace(pk=self.actor.pk),
            SimpleNamespace(is_authenticated=False, pk=self.actor.pk),
            SimpleNamespace(is_authenticated=True, pk='1'),
            SimpleNamespace(is_authenticated=True, pk=True),
            SimpleNamespace(is_authenticated=True, pk=0),
            SimpleNamespace(is_authenticated=True, pk=-1),
        )
        for reader in (read_location, read_location_details):
            for actor in actors:
                with self.subTest(reader=reader.__name__, actor=actor):
                    self.assertIsNone(
                        reader(actor, environment='test', location_pk=self.location.pk)
                    )
            self.assertIsNone(
                reader(self.actor, environment='test', location_pk=999999)
            )

    def test_stored_timezone_is_not_inherited_from_parent(self):
        """A blank child timezone is distinct from its native effective timezone."""
        from assets.integration_sources import read_location_details
        from assets.locations import graph_for, location_data

        parent = AssetLocation.objects.create(
            client=self.tenant,
            name='Synthetic parent',
            code='parent',
            timezone='Europe/Berlin',
            name_key='parent',
            sibling_key='root',
        )
        AssetLocation.objects.filter(pk=self.location.pk).update(parent=parent)
        self.location.refresh_from_db()
        details = read_location_details(
            self.actor, environment='test', location_pk=self.location.pk
        )
        assert details is not None
        self.assertEqual(details.timezone, '')
        native = location_data(self.location, graph_for(self.actor))
        self.assertEqual(native['timezone'], '')
        self.assertEqual(native['effective_timezone'], 'Europe/Berlin')

        AssetLocation.objects.filter(pk=self.location.pk).update(
            timezone='America/Chicago'
        )
        self.location.refresh_from_db()
        details = read_location_details(
            self.actor, environment='test', location_pk=self.location.pk
        )
        assert details is not None
        self.assertEqual(details.timezone, 'America/Chicago')
        self.assertEqual(
            location_data(self.location, graph_for(self.actor))['effective_timezone'],
            'America/Chicago',
        )

    def test_record_shapes_are_explicit_for_both_versions(self):
        """No implicit parent, client, description, path or count is released."""
        from assets.integration_contracts import (
            LocationDetailsSourceRecord,
            LocationSourceRecord,
        )

        self.assertEqual(
            {f.name for f in fields(LocationSourceRecord)},
            {'identity', 'name', 'archived', 'schema_version'},
        )
        self.assertEqual(
            {f.name for f in fields(LocationDetailsSourceRecord)},
            {
                'identity',
                'name',
                'code',
                'kind',
                'timezone',
                'archived',
                'version',
                'schema_version',
            },
        )

    def test_native_edits_advance_the_returned_location_version(self):
        """Native commands increment version for each selected field edit."""
        from assets.integration_sources import read_location_details
        from assets.locations import save_location

        self.rule.can_change = True
        self.rule.save(update_fields=['can_change'])
        AssetLocation.objects.filter(pk=self.location.pk).update(
            timezone='Europe/London'
        )
        self.location.refresh_from_db()
        for name, value in (
            ('name', 'Synthetic workshop updated'),
            ('code', 'workshop-updated'),
            ('kind', AssetLocation.Kind.ROOM),
            ('timezone', 'Europe/Berlin'),
            ('archived', False),
        ):
            with self.subTest(field=name):
                previous = self.location.version
                save_location(
                    self.actor,
                    {
                        'reason': 'synthetic test',
                        'expected_version': previous,
                        name: value,
                    },
                    pk=self.location.pk,
                )
                self.location.refresh_from_db()
                details = read_location_details(
                    self.actor, environment='test', location_pk=self.location.pk
                )
                assert details is not None
                self.assertEqual(details.version, previous + 1)
                self.assertEqual(getattr(details, name), value)

    def test_direct_bulk_update_can_change_properties_without_version_increment(self):
        """The native version is not a complete revision or change-feed proof."""
        from assets.integration_sources import read_location_details

        before = self.location.version
        AssetLocation.objects.filter(pk=self.location.pk).update(
            name='Synthetic bulk name'
        )
        details = read_location_details(
            self.actor, environment='test', location_pk=self.location.pk
        )
        assert details is not None
        self.assertEqual(details.name, 'Synthetic bulk name')
        self.assertEqual(details.version, before)

    def test_inactive_user_client_and_foreign_location_are_withheld(self):
        """Foreign locations and inactive actors or clients are withheld."""
        from assets.integration_sources import read_location_details

        def read(location):
            return read_location_details(
                self.actor, environment='test', location_pk=location.pk
            )

        foreign_client = Client.objects.create(name='Other synthetic', code='loc-other')
        foreign = AssetLocation.objects.create(
            client=foreign_client,
            name='Other',
            code='other',
            name_key='other',
            sibling_key='root',
        )
        self.assertIsNotNone(read(self.location))
        self.assertIsNone(read(foreign))
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        self.assertIsNone(read(self.location))
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=True)
        self.assertIsNotNone(read(self.location))
        Client.objects.filter(pk=self.tenant.pk).update(active=False)
        self.assertIsNone(read(self.location))
        Client.objects.filter(pk=self.tenant.pk).update(active=True)
        self.assertIsNotNone(read(self.location))

    def test_revocation_between_checks_withholds_both_location_readers(self):
        """Both projections share the same current-grant recheck."""
        from assets.integration_sources import read_location, read_location_details
        from assets.locations import locations_for

        for reader in (read_location, read_location_details):
            with self.subTest(reader=reader.__name__):
                grant, _ = ClientScopeGrant.objects.get_or_create(
                    user=self.actor, client=self.tenant
                )
                self.assertIsNotNone(
                    reader(self.actor, environment='test', location_pk=self.location.pk)
                )
                calls = 0

                def revoke(actor, target_grant=grant):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        target_grant.delete()
                    return locations_for(actor)

                with patch('assets.locations.locations_for', side_effect=revoke):
                    self.assertIsNone(
                        reader(
                            self.actor, environment='test', location_pk=self.location.pk
                        )
                    )
                self.assertEqual(calls, 2)
