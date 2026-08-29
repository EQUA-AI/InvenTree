"""S6 (WP-A5): the grant-aware maintenance-scope resolver.

The isolation contract: explicit ``ClientScopeGrant`` rows win; a user with
none resolves through ``single_site_scope_resolver`` byte-identically — so
flipping ``AIMMS_MAINTENANCE_SCOPE_RESOLVER`` to the new resolver changes
NOTHING for ordinary operators (including the part-verification reuse of
the same setting), while the evaluation user's grants make ``eval-fixtures``
reachable for them alone.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from assets.models import Client, ClientScopeGrant
from tasks.scope import (
    MaintenanceScope,
    granted_client_scope_resolver,
    scope_for_actor,
    single_site_scope_resolver,
)


class GrantedResolverTests(TestCase):
    """Grants win; zero grants falls back; inactive rows never resolve."""

    @classmethod
    def setUpTestData(cls):
        """One internal tenant, the eval client, two users."""
        user_model = get_user_model()
        cls.operator = user_model.objects.create_user(
            username='ordinary-op', is_superuser=True
        )
        cls.evaluator = user_model.objects.create_user(
            username='solar-evaluation', is_superuser=True
        )
        # The backfill migration may already have created it.
        cls.internal, _ = Client.objects.get_or_create(
            code='internal', defaults={'name': 'Internal'}
        )
        cls.eval_client = Client.objects.create(
            name='RAG Evaluation Fixtures', code='eval-fixtures'
        )
        cls.inactive = Client.objects.create(
            name='Retired', code='retired-client', active=False
        )

    def test_zero_grant_user_matches_single_site_exactly(self):
        """The fallback is byte-identical — ordinary operators are untouched."""
        self.assertEqual(
            granted_client_scope_resolver(self.operator),
            single_site_scope_resolver(self.operator),
        )
        self.assertEqual(
            granted_client_scope_resolver(self.operator),
            {MaintenanceScope(customer_id=None, site_key=None, client_id=self.internal.pk)},
        )

    def test_granted_user_resolves_exactly_the_granted_clients(self):
        """Grant rows replace the fallback wholesale."""
        ClientScopeGrant.objects.create(user=self.evaluator, client=self.internal)
        ClientScopeGrant.objects.create(user=self.evaluator, client=self.eval_client)
        self.assertEqual(
            granted_client_scope_resolver(self.evaluator),
            {
                MaintenanceScope(
                    customer_id=None, site_key=None, client_id=self.internal.pk
                ),
                MaintenanceScope(
                    customer_id=None, site_key=None, client_id=self.eval_client.pk
                ),
            },
        )

    def test_eval_fixtures_is_unreachable_without_a_grant(self):
        """The isolation invariant: no grant row, no eval-fixtures scope."""
        ClientScopeGrant.objects.create(user=self.evaluator, client=self.eval_client)
        operator_scopes = granted_client_scope_resolver(self.operator)
        self.assertNotIn(
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.eval_client.pk
            ),
            operator_scopes,
        )

    def test_inactive_clients_and_inactive_users_resolve_nothing(self):
        """Inactive clients contribute nothing; inactive users get nothing."""
        ClientScopeGrant.objects.create(user=self.evaluator, client=self.inactive)
        # An inactive granted client contributes nothing; with no other
        # grants the user falls back to single-site.
        self.assertEqual(
            granted_client_scope_resolver(self.evaluator),
            single_site_scope_resolver(self.evaluator),
        )
        self.evaluator.is_active = False
        self.assertEqual(granted_client_scope_resolver(self.evaluator), set())

    @override_settings(
        AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
    )
    def test_scope_for_actor_honors_the_configured_resolver(self):
        """The deployment flip: scope_for_actor routes through the grants."""
        ClientScopeGrant.objects.create(user=self.evaluator, client=self.eval_client)
        self.assertEqual(
            scope_for_actor(self.evaluator),
            frozenset({
                MaintenanceScope(
                    customer_id=None, site_key=None, client_id=self.eval_client.pk
                )
            }),
        )
        # Part-verification regression proxy: the ordinary user's resolution
        # through the SAME setting is unchanged by the flip.
        self.assertEqual(
            scope_for_actor(self.operator),
            frozenset(single_site_scope_resolver(self.operator)),
        )
