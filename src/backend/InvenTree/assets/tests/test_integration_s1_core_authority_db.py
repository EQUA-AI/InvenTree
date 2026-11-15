"""Real synthetic ORM controls for S1's current-user authority foundation."""

from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone

from assets.integration_authority import explicitly_granted_client_ids
from assets.models import AssetLocation, AssetMachine, Client, ClientScopeGrant
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s1-fallback',
)
class CoreAuthorityTests(TestCase):
    """Grant queries must not inherit a stale account's active flag."""

    def setUp(self):
        """Create only synthetic users, role rows and source records."""
        self.actor = get_user_model().objects.create_user(username='s1-core-actor')
        group = Group.objects.create(name='S1 core readers')
        self.actor.groups.add(group)
        self.work, _ = RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.admin = RuleSet.objects.get(group=group, name='admin')
        RuleSet.objects.filter(pk=self.admin.pk).update(can_view=True)
        self.tenant = Client.objects.create(name='S1 synthetic tenant', code='s1-own')
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.tenant
        )
        self.machine = AssetMachine.objects.create(
            name='S1 synthetic machine', client=self.tenant
        )
        self.location = AssetLocation.objects.create(
            client=self.tenant,
            name='S1 synthetic root',
            code='s1-root',
            name_key='s1 synthetic root',
            sibling_key='root',
            timezone='Europe/Berlin',
        )

    def granted(self, actor):
        """Materialize only the helper's authorized client identities."""
        return {row['client_id'] for row in explicitly_granted_client_ids(actor)}

    def test_grant_helper_discards_account_deactivated_in_database(self):
        """A stale active user object cannot retain an inactive account's grants."""
        self.assertEqual(self.granted(self.actor), {self.tenant.pk})
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        self.assertTrue(self.actor.is_active)
        self.assertEqual(self.granted(self.actor), set())
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=True)
        self.assertEqual(self.granted(self.actor), {self.tenant.pk})

    def test_grant_helper_rejects_invalid_or_anonymous_actor_coordinates(self):
        """Invalid identities cannot be coerced into another account's grants."""
        self.assertEqual(self.granted(self.actor), {self.tenant.pk})
        for actor in (
            None,
            SimpleNamespace(pk=self.actor.pk, is_authenticated=False),
            SimpleNamespace(pk=True, is_authenticated=True),
            SimpleNamespace(pk=str(self.actor.pk), is_authenticated=True),
            SimpleNamespace(pk=0, is_authenticated=True),
        ):
            with self.subTest(actor=actor), self.assertNumQueries(0):
                self.assertEqual(self.granted(actor), set())

    def request(self, *, method='get', user=None):
        """Bind a synthetic user through DRF's forced-authentication transport."""
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory, force_authenticate

        raw = getattr(APIRequestFactory(), method)('/synthetic-s1-read/')
        force_authenticate(raw, user=user or self.actor)
        return Request(raw)

    def test_request_boundary_uses_fresh_actor_and_native_view_role(self):
        """A trusted request cannot carry an old native role/account decision."""
        from assets.integration_authority import integration_actor_from_request

        request = self.request()
        current = integration_actor_from_request(request, required_role='work_order')
        self.assertEqual(current.pk, self.actor.pk)
        self.assertIsNot(current, self.actor)
        RuleSet.objects.filter(pk=self.work.pk).update(can_view=False)
        self.assertIsNone(
            integration_actor_from_request(request, required_role='work_order')
        )
        RuleSet.objects.filter(pk=self.work.pk).update(can_view=True)
        self.assertIsNotNone(
            integration_actor_from_request(request, required_role='work_order')
        )
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        self.assertIsNone(
            integration_actor_from_request(request, required_role='work_order')
        )

    def test_request_boundary_rejects_unbound_or_non_read_context(self):
        """Actor labels and authenticated mutation requests are not read intake."""
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory

        from assets.integration_authority import integration_actor_from_request

        for request in (
            None,
            {'user': self.actor},
            SimpleNamespace(user=self.actor, method='GET'),
            Request(APIRequestFactory().get('/synthetic/'), authenticators=[]),
            self.request(method='post'),
        ):
            with self.subTest(request_type=type(request).__name__):
                self.assertIsNone(
                    integration_actor_from_request(request, required_role='work_order')
                )
        with self.assertRaises(ValueError):
            integration_actor_from_request(self.request(), required_role='part')

    def test_request_boundary_is_not_a_client_or_source_grant(self):
        """An admitted actor still needs the actual matching grant at the reader."""
        from tasks.scope import MaintenanceScope, scoped_machines

        from assets.integration_authority import integration_actor_from_request
        from assets.integration_sources import read_machine

        scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: scopes):
            self.grant.delete()
            current = integration_actor_from_request(
                self.request(), required_role='work_order'
            )
            self.assertIsNotNone(current)
            self.assertTrue(
                scoped_machines(current).filter(pk=self.machine.pk).exists()
            )
            self.assertIsNone(
                read_machine(current, environment='s1-test', machine_pk=self.machine.pk)
            )
            ClientScopeGrant.objects.create(user=self.actor, client=self.tenant)
            self.assertIsNotNone(
                read_machine(current, environment='s1-test', machine_pk=self.machine.pk)
            )

    def test_request_boundary_rechecks_real_oauth_row_scope_and_revocation(self):
        """Fake OAuth transport cannot bypass real token, role or grant controls."""
        from oauth2_provider.contrib.rest_framework.authentication import (
            OAuth2Authentication,
        )
        from oauth2_provider.models import get_access_token_model
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory

        from assets.integration_authority import integration_actor_from_request
        from users.oauth2_scopes import get_granular_scope

        actor = self.actor
        token = get_access_token_model().objects.create(
            user=actor,
            token='s1-synthetic-local-token',
            scope=get_granular_scope('view', 'work_order'),
            expires=timezone.now() + timedelta(hours=1),
        )

        class SyntheticOAuth(OAuth2Authentication):
            def authenticate(self, request):
                return actor, token

        request = Request(
            APIRequestFactory().get('/synthetic-oauth/'),
            authenticators=[SyntheticOAuth()],
        )
        self.assertIsNotNone(
            integration_actor_from_request(request, required_role='work_order')
        )
        token_type = get_access_token_model()
        token_type.objects.filter(pk=token.pk).update(scope='write')
        self.assertIsNone(
            integration_actor_from_request(request, required_role='work_order')
        )
        token_type.objects.filter(pk=token.pk).update(
            scope=get_granular_scope('view', 'work_order')
        )
        self.assertIsNotNone(
            integration_actor_from_request(request, required_role='work_order')
        )
        token_type.objects.filter(pk=token.pk).delete()
        self.assertIsNone(
            integration_actor_from_request(request, required_role='work_order')
        )
