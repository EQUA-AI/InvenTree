"""Synthetic client-source authorization and projection controls."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from assets.models import Client, ClientScopeGrant
from users.models import RuleSet


class ClientSourceDatabaseTests(TestCase):
    """Native admin visibility intersects the current explicit client grant."""

    def setUp(self):
        """Create an active admin-role actor and a synthetic granted client."""
        self.actor = get_user_model().objects.create_user(username='client-source-test')
        group = Group.objects.create(name='Client source readers')
        self.actor.groups.add(group)
        self.rule = RuleSet.objects.get(group=group, name='admin')
        RuleSet.objects.filter(pk=self.rule.pk).update(can_view=True)
        self.tenant = Client.objects.create(
            name='Synthetic tenant', code='synthetic-client'
        )
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.tenant
        )

    def test_admin_with_matching_grant_reads_basic_client(self):
        """Expose only the current client's identity and native basic fields."""
        from assets.integration_sources import read_client

        result = read_client(self.actor, environment='test', client_pk=self.tenant.pk)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.schema_version, 'aimms.client.basic.v1')
        self.assertEqual(result.identity.environment, 'test')
        self.assertEqual(result.identity.pk, self.tenant.pk)
        self.assertEqual(result.name, 'Synthetic tenant')
        self.assertEqual(result.code, 'synthetic-client')
        self.assertTrue(result.active)

    def test_client_details_preserve_native_fields_and_source_timestamps(self):
        """Expose original client values without related counts or settings."""
        from assets.integration_sources import read_client_details

        result = read_client_details(
            self.actor, environment='test', client_pk=self.tenant.pk
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.schema_version, 'aimms.client.details.v2')
        self.assertEqual(result.identity.pk, self.tenant.pk)
        self.assertEqual(result.name, self.tenant.name)
        self.assertEqual(result.code, self.tenant.code)
        self.assertEqual(result.active, self.tenant.active)
        self.assertEqual(result.created_at, self.tenant.created_at)
        self.assertEqual(result.updated_at, self.tenant.updated_at)
        for omitted in ('location_revision', 'machine_count', 'memory_enabled'):
            self.assertFalse(hasattr(result, omitted))

    def test_client_details_match_native_direct_fields(self):
        """The new direct fields agree with the native synthetic client detail."""
        from rest_framework.fields import DateTimeField
        from rest_framework.test import APIRequestFactory, force_authenticate

        from assets.api import ClientDetail
        from assets.integration_sources import read_client_details

        result = read_client_details(
            self.actor, environment='test', client_pk=self.tenant.pk
        )
        assert result is not None
        request = APIRequestFactory().get('/synthetic-client-detail/')
        force_authenticate(request, user=self.actor)
        response = ClientDetail.as_view()(request, pk=self.tenant.pk)
        self.assertEqual(response.status_code, 200)
        for name in ('name', 'code', 'active'):
            self.assertEqual(getattr(result, name), response.data[name])
        for name in ('created_at', 'updated_at'):
            # Source timestamps remain lossless even when native API display
            # settings intentionally omit seconds, precision or the UTC suffix.
            self.assertEqual(getattr(result, name), getattr(self.tenant, name))
            self.assertEqual(
                DateTimeField().to_representation(getattr(result, name)),
                response.data[name],
            )

    def test_client_details_require_fresh_role_grant_and_active_client(self):
        """Deny each missing authority with a positive control between cases."""
        from assets.integration_sources import read_client_details

        def read():
            return read_client_details(
                self.actor, environment='test', client_pk=self.tenant.pk
            )

        self.assertIsNotNone(read())
        RuleSet.objects.filter(pk=self.rule.pk).update(can_view=False)
        self.assertIsNone(read())
        RuleSet.objects.filter(pk=self.rule.pk).update(can_view=True)
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

    def test_client_details_withhold_grant_revoked_between_checks(self):
        """A grant lost during selection cannot release the source timestamps."""
        from assets.integration_authority import explicitly_granted_client_ids
        from assets.integration_sources import read_client_details

        self.assertIsNotNone(
            read_client_details(
                self.actor, environment='test', client_pk=self.tenant.pk
            )
        )
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
                read_client_details(
                    self.actor, environment='test', client_pk=self.tenant.pk
                )
            )
        self.assertEqual(calls, 2)

    def test_grant_and_admin_role_are_independently_required(self):
        """Remove and restore each authority with the other still present."""
        from assets.integration_sources import read_client

        def read():
            return read_client(self.actor, environment='test', client_pk=self.tenant.pk)

        self.assertIsNotNone(read())
        RuleSet.objects.filter(pk=self.rule.pk).update(can_view=False)
        self.assertTrue(ClientScopeGrant.objects.filter(pk=self.grant.pk).exists())
        self.assertIsNone(read())
        RuleSet.objects.filter(pk=self.rule.pk).update(can_view=True)
        self.assertIsNotNone(read())
        self.grant.delete()
        self.assertIsNone(read())
        ClientScopeGrant.objects.create(user=self.actor, client=self.tenant)
        self.assertIsNotNone(read())

    def test_inactive_user_and_client_are_independently_withheld(self):
        """A stale user or inactive client never inherits earlier authority."""
        from assets.integration_sources import read_client

        def read():
            return read_client(self.actor, environment='test', client_pk=self.tenant.pk)

        self.assertIsNotNone(read())
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        self.assertIsNone(read())
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=True)
        self.assertIsNotNone(read())
        Client.objects.filter(pk=self.tenant.pk).update(active=False)
        self.assertIsNone(read())
        Client.objects.filter(pk=self.tenant.pk).update(active=True)
        self.assertIsNotNone(read())

    def test_foreign_missing_and_anonymous_are_withheld(self):
        """A different client's grant and missing identity reveal no row."""
        from assets.integration_sources import read_client

        foreign = Client.objects.create(name='Other synthetic', code='other-synthetic')
        self.assertIsNotNone(
            read_client(self.actor, environment='test', client_pk=self.tenant.pk)
        )
        self.assertIsNone(
            read_client(self.actor, environment='test', client_pk=foreign.pk)
        )
        self.assertIsNone(read_client(self.actor, environment='test', client_pk=999999))
        self.assertIsNone(
            read_client(None, environment='test', client_pk=self.tenant.pk)
        )

    def test_superuser_does_not_bypass_explicit_client_grant(self):
        """Native superuser visibility is still narrowed by integration policy."""
        from assets.integration_sources import read_client

        admin = get_user_model().objects.create_user(
            username='synthetic-superuser-client', is_superuser=True
        )
        self.assertIsNone(
            read_client(admin, environment='test', client_pk=self.tenant.pk)
        )
        ClientScopeGrant.objects.create(user=admin, client=self.tenant)
        self.assertIsNotNone(
            read_client(admin, environment='test', client_pk=self.tenant.pk)
        )

    def test_grant_revoked_between_checks_withholds_result(self):
        """A first successful read cannot carry a revoked grant through recheck."""
        from assets.integration_authority import explicitly_granted_client_ids
        from assets.integration_sources import read_client

        self.assertIsNotNone(
            read_client(self.actor, environment='test', client_pk=self.tenant.pk)
        )
        calls = 0

        def revoke_before_second_check(user):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.grant.delete()
            return explicitly_granted_client_ids(user)

        with patch(
            'assets.integration_authority.explicitly_granted_client_ids',
            side_effect=revoke_before_second_check,
        ):
            self.assertIsNone(
                read_client(self.actor, environment='test', client_pk=self.tenant.pk)
            )
        self.assertEqual(calls, 2)

    def test_admin_role_revoked_between_checks_withholds_result(self):
        """Current role is rechecked separately from the still-present grant."""
        from ai.core.integrations.retrieval_authority import fresh_role
        from assets.integration_sources import read_client

        self.assertIsNotNone(
            read_client(self.actor, environment='test', client_pk=self.tenant.pk)
        )
        calls = 0

        def revoke_before_second_check(user, role):
            nonlocal calls
            calls += 1
            if calls == 2:
                RuleSet.objects.filter(pk=self.rule.pk).update(can_view=False)
            return fresh_role(user, role)

        with patch(
            'ai.core.integrations.retrieval_authority.fresh_role',
            side_effect=revoke_before_second_check,
        ):
            self.assertIsNone(
                read_client(self.actor, environment='test', client_pk=self.tenant.pk)
            )
        self.assertEqual(calls, 2)
        self.assertTrue(ClientScopeGrant.objects.filter(pk=self.grant.pk).exists())

    def test_native_client_detail_allows_admin_fixture(self):
        """The separate native client view admits the synthetic admin actor."""
        from rest_framework.test import APIRequestFactory, force_authenticate

        from assets.api import ClientDetail

        request = APIRequestFactory().get('/synthetic-client-detail/')
        force_authenticate(request, user=self.actor)
        response = ClientDetail.as_view()(request, pk=self.tenant.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['pk'], self.tenant.pk)
