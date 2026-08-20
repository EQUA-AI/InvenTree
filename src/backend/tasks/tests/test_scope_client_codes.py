"""``client_codes_for_actor``: the code-valued, fail-closed scope projection.

The attachment-RAG retrieval filter (R2) is authored in client *codes*, so
this helper must obey exactly the rules ``machine_scope_filter`` documents:
site-keyed grants contribute nothing, customer-only grants contribute
nothing, and an empty outcome raises rather than returning an empty set —
a missing filter clause would read as "everyone's documents".
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from assets.models import Client
from tasks.scope import MaintenanceScope, ScopeError, client_codes_for_actor

_GRANTS: dict[str, set[MaintenanceScope]] = {}


def _grant_resolver(actor):
    """Resolver seam: return whatever the test granted this username."""
    return _GRANTS.get(actor.get_username(), set())


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=f'{__name__}._grant_resolver')
class ClientCodesForActorTest(TestCase):
    """Codes come only from active, client-valued, site-less grants."""

    def setUp(self):
        _GRANTS.clear()
        self.user = get_user_model().objects.create_user(
            username='scoped-tech', password='x'
        )
        self.acme = Client.objects.create(code='acme', name='Acme', active=True)
        self.zeta = Client.objects.create(code='zeta', name='Zeta', active=True)
        self.dormant = Client.objects.create(
            code='dormant', name='Dormant', active=False
        )

    def _grant(self, *scopes):
        _GRANTS[self.user.get_username()] = set(scopes)

    def test_resolved_client_grants_become_codes(self):
        self._grant(
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.acme.pk),
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.zeta.pk),
        )
        self.assertEqual(client_codes_for_actor(self.user), frozenset({'acme', 'zeta'}))

    def test_site_keyed_grants_contribute_nothing(self):
        """Same skip rule as machine_scope_filter: a site-qualified grant
        authorizes no machine row, so it must not widen a search filter."""
        self._grant(
            MaintenanceScope(
                customer_id=None, site_key='plant-2', client_id=self.acme.pk
            ),
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.zeta.pk),
        )
        self.assertEqual(client_codes_for_actor(self.user), frozenset({'zeta'}))

    def test_only_site_keyed_grants_refuse(self):
        self._grant(
            MaintenanceScope(
                customer_id=None, site_key='plant-2', client_id=self.acme.pk
            )
        )
        with self.assertRaises(ScopeError):
            client_codes_for_actor(self.user)

    def test_customer_only_grants_refuse(self):
        """Codes ride client grants; a customer grant names no tenant code."""
        self._grant(MaintenanceScope(customer_id=77, site_key=None))
        with self.assertRaises(ScopeError):
            client_codes_for_actor(self.user)

    def test_inactive_clients_are_dropped_and_empty_refuses(self):
        self._grant(
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.dormant.pk
            )
        )
        with self.assertRaises(ScopeError):
            client_codes_for_actor(self.user)

    def test_inactive_client_does_not_poison_an_active_grant(self):
        self._grant(
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.acme.pk),
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.dormant.pk
            ),
        )
        self.assertEqual(client_codes_for_actor(self.user), frozenset({'acme'}))

    def test_deleted_client_row_refuses(self):
        missing_pk = self.acme.pk
        self._grant(
            MaintenanceScope(customer_id=None, site_key=None, client_id=missing_pk)
        )
        self.acme.delete()
        with self.assertRaises(ScopeError):
            client_codes_for_actor(self.user)

    def test_unresolved_scope_propagates(self):
        # No grant at all: scope_for_actor itself refuses.
        with self.assertRaises(ScopeError):
            client_codes_for_actor(self.user)

    def test_unauthenticated_actor_refuses(self):
        with self.assertRaises(ScopeError):
            client_codes_for_actor(None)
