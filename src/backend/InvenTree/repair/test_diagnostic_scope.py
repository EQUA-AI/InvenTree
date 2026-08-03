"""The production diagnostic capability resolver and its seam wiring.

Until this suite existed nothing asserted that a *production* resolver was
importable, grantable, and fail-closed — which is how a deployment shipped
with the diagnosis flag on and no resolver at all, running the reasoning
model with zero tools. These tests pin both halves: the resolver's own
grants, and the ``AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER`` seam resolving it
end to end through ``repair.services``.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from . import services
from .diagnostic_scope import (
    BASE_DIAGNOSTIC_CAPABILITIES,
    single_site_diagnostic_capability_resolver,
)

RESOLVER_PATH = 'repair.diagnostic_scope.single_site_diagnostic_capability_resolver'


def _user(*, superuser: bool, active: bool = True):
    """Create a throwaway actor; superusers hold every role check."""
    suffix = uuid.uuid4().hex[:8]
    factory = (
        get_user_model().objects.create_superuser
        if superuser
        else get_user_model().objects.create_user
    )
    user = factory(
        username=f'diag-scope-{suffix}', email=f'{suffix}@example.com', password='pw'
    )
    if not active:
        user.is_active = False
        user.save(update_fields=['is_active'])
    return user


class SingleSiteDiagnosticCapabilityResolverTest(TestCase):
    """Grants of the resolver itself."""

    def test_authorized_operator_receives_the_base_read_set(self):
        """A role-holding active user gets every base read grant, nothing more."""
        grants = single_site_diagnostic_capability_resolver(_user(superuser=True))
        self.assertEqual(grants, BASE_DIAGNOSTIC_CAPABILITIES)
        self.assertNotIn('diagnostics.health.read', grants)
        self.assertNotIn('diagnostics.safety_p0.read', grants)

    @override_settings(AIMMS_MACHINE_AI_READ_ENABLED=True)
    def test_health_read_is_its_own_grant_behind_the_machine_ai_flag(self):
        """Live-telemetry reads are granted separately, per the capability doc."""
        grants = single_site_diagnostic_capability_resolver(_user(superuser=True))
        self.assertEqual(
            grants, BASE_DIAGNOSTIC_CAPABILITIES | {'diagnostics.health.read'}
        )
        self.assertNotIn('diagnostics.safety_p0.read', grants)

    def test_safety_p0_is_never_granted_by_this_resolver(self):
        """Opening the live-safety surface is a deliberate deployment decision."""
        with override_settings(AIMMS_MACHINE_AI_READ_ENABLED=True):
            grants = single_site_diagnostic_capability_resolver(_user(superuser=True))
        self.assertNotIn('diagnostics.safety_p0.read', grants)

    def test_missing_role_inactive_and_absent_actors_fail_closed(self):
        """No role, an inactive account, or no actor at all grants nothing."""
        self.assertEqual(
            single_site_diagnostic_capability_resolver(_user(superuser=False)),
            frozenset(),
        )
        self.assertEqual(
            single_site_diagnostic_capability_resolver(
                _user(superuser=True, active=False)
            ),
            frozenset(),
        )
        self.assertEqual(single_site_diagnostic_capability_resolver(None), frozenset())


class ResolverSeamTest(TestCase):
    """The dotted-path seam in repair.services resolves this module."""

    @override_settings(AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER=RESOLVER_PATH)
    def test_seam_imports_and_intersects_with_the_allowlist(self):
        """The production dotted path resolves, and grants stay allowlisted."""
        grants = services.diagnostic_capabilities_for_actor(_user(superuser=True))
        self.assertEqual(grants, BASE_DIAGNOSTIC_CAPABILITIES)
        self.assertLessEqual(grants, services._DIAGNOSTIC_CAPABILITIES)

    @override_settings(AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER=RESOLVER_PATH)
    def test_seam_fails_closed_for_unauthorized_actors(self):
        """An actor without the role resolves to zero capabilities."""
        self.assertEqual(
            services.diagnostic_capabilities_for_actor(_user(superuser=False)),
            frozenset(),
        )

    def test_unset_seam_still_fails_closed(self):
        """With no resolver configured anywhere, no capabilities exist.

        This is the exact production misconfiguration that ran the reasoning
        model tool-less; the turn service now refuses that turn, and this
        pins the upstream half: unset means empty, never a default grant.
        """
        with self.settings(AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER=None):
            grants = services.diagnostic_capabilities_for_actor(_user(superuser=True))
        self.assertEqual(grants, frozenset())


class DiagnosticRecordRootListingTest(TestCase):
    """The record-root lister must speak the same scope dialect as the ACL.

    Machines are identified by their owning *client* (the customer column was
    dropped when machines became client-scoped), and the per-entity ACL
    (``_diagnostic_scoped_entity``) already matches on ``client_id``. The
    lister silently kept filtering on ``scope.customer_id`` — always ``None``
    from the production resolver — so every actor got zero record roots, the
    diagnostic context factory returned ``None``, and the reasoning rail
    refused every turn. Found live on 2026-08-03 (battery test R1); nothing
    exercised the real lister until this suite.
    """

    @classmethod
    def setUpTestData(cls):
        """One client with an active machine, plus decoys."""
        from assets.models import AssetMachine, Client

        cls.client_row = Client.objects.create(name='Site A', code='site-a')
        cls.other_client = Client.objects.create(name='Site B', code='site-b')
        cls.machine = AssetMachine.objects.create(
            name='Influent Pump 1', client=cls.client_row
        )
        AssetMachine.objects.create(
            name='Inactive Pump', client=cls.client_row, active=False
        )
        AssetMachine.objects.create(name='Foreign Pump', client=cls.other_client)

    def _actor(self, scopes):
        actor = _user(superuser=False)
        actor.maintenance_scopes = scopes
        return actor

    def test_client_scoped_actor_lists_only_their_active_machines(self):
        """The production scope shape yields the client's active machines.

        Each root carries a non-empty optimistic-read revision.
        """
        from tasks.scope import MaintenanceScope

        actor = self._actor({
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.client_row.pk
            )
        })
        roots = services.list_diagnostic_record_roots(actor)
        machine_roots = [r for r in roots if r['entity_type'] == 'machine']
        self.assertEqual([r['entity_id'] for r in machine_roots], [self.machine.pk])
        self.assertTrue(machine_roots[0]['expected_revision'])
        self.assertEqual(machine_roots[0]['authorization_class'], 'maintenance_scope')
        self.assertEqual(machine_roots[0]['display_name'], 'Influent Pump 1')

    def test_customer_only_scope_lists_nothing(self):
        """A customer-only scope must not resolve any machine root.

        Machines carry no customer identity — that claim belongs to work
        orders.
        """
        from tasks.scope import MaintenanceScope

        actor = self._actor({
            MaintenanceScope(customer_id=999, site_key=None, client_id=None)
        })
        self.assertEqual(services.list_diagnostic_record_roots(actor), [])

    @override_settings(
        AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
        AIMMS_SINGLE_SITE_CLIENT_CODE='site-a',
    )
    def test_single_site_resolver_feeds_the_lister_end_to_end(self):
        """The exact production wiring: resolver -> client scope -> roots.

        This is the path that was dark in production: the resolver granted a
        client-scoped boundary and the lister looked for a customer one.
        """
        actor = _user(superuser=True)
        roots = services.list_diagnostic_record_roots(actor)
        self.assertIn(self.machine.pk, [r['entity_id'] for r in roots])
