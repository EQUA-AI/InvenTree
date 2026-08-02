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
