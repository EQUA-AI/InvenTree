"""S17: the rollback floor is mechanically monotonic (§14).

Once ``arm_rollback_floor`` writes the one-way marker, attempting to run
below the floor — scope enforcement off, or fixture isolation pointed at
a non-granted resolver — fails the Django system check loudly at EVERY
tier including 0. The floor's other leg (the unsafe-shortcut guard) is
code-bound: the island CI pin proves no registry entry can dark it.
"""

from io import StringIO

from django.core.checks.registry import registry
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from aimms_capability import ROLLBACK_FLOOR, ROLLBACK_FLOOR_SETTING


def _aimms_errors():
    return [
        check
        for check in registry.run_checks(tags=['aimms'])
        if check.id in ('aichat.E020', 'aichat.E021')
    ]


def _arm_floor():
    from common.models import InvenTreeSetting

    InvenTreeSetting.set_setting(ROLLBACK_FLOOR_SETTING, 'true', None)


class RollbackFloorCheckTests(TestCase):
    """The armed floor binds the Django plane's flags."""

    def test_floor_names_are_frozen(self):
        """The floor membership itself is review-gated."""
        self.assertEqual(
            ROLLBACK_FLOOR, ('scope_enforce', 'shortcut_guard', 'fixture_isolation')
        )

    def test_unarmed_tier_zero_checks_nothing(self):
        """Today's dark deployment stays inert."""
        self.assertEqual(_aimms_errors(), [])

    def test_armed_floor_fails_loudly_below_the_floor_even_at_tier_zero(self):
        """Arming binds the floor legs regardless of tier."""
        _arm_floor()
        errors = _aimms_errors()
        named = {error.msg.split(':', 1)[0] for error in errors}
        # scope enforcement is dark and the resolver is not the granted
        # one on today's defaults — both floor legs are named.
        self.assertIn('scope_enforce', named)
        self.assertIn('fixture_isolation', named)
        self.assertTrue(all(error.id == 'aichat.E021' for error in errors))

    @override_settings(
        FEATURE_AI_THREAD_SCOPE_ENFORCE=True,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    )
    def test_armed_floor_passes_when_the_floor_holds(self):
        """A floor-satisfying deployment checks clean."""
        _arm_floor()
        self.assertEqual(_aimms_errors(), [])

    @override_settings(
        FEATURE_AI_THREAD_SCOPE_ENFORCE=True,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    )
    def test_disabling_scope_enforcement_after_arming_fails_loudly(self):
        """The monotonic-safety gate item, verbatim."""
        # The S17 gate item verbatim: attempting to disable scope
        # enforcement after enablement fails, not silently degrades.
        _arm_floor()
        self.assertEqual(_aimms_errors(), [])
        with override_settings(FEATURE_AI_THREAD_SCOPE_ENFORCE=False):
            named = {error.msg.split(':', 1)[0] for error in _aimms_errors()}
            self.assertEqual(named, {'scope_enforce'})

    @override_settings(AIMMS_CAPABILITY_TIER='not-a-number')
    def test_non_integer_tier_is_a_typed_error(self):
        """A malformed tier is one E020, not a crash."""
        errors = _aimms_errors()
        self.assertEqual([error.id for error in errors], ['aichat.E020'])


class ArmRollbackFloorCommandTests(TestCase):
    """Arming is explicit, one-way, and idempotent."""

    def test_requires_confirmation(self):
        """Arming without --yes refuses."""
        with self.assertRaises(CommandError):
            call_command('arm_rollback_floor', stdout=StringIO())

    def test_arms_once_and_stays_armed(self):
        """Arming is one-way and idempotent; no disarm exists."""
        from common.models import InvenTreeSetting

        out = StringIO()
        call_command('arm_rollback_floor', '--yes', stdout=out)
        self.assertIn('ARMED', out.getvalue())
        self.assertEqual(
            str(InvenTreeSetting.get_setting(ROLLBACK_FLOOR_SETTING, '')).lower(),
            'true',
        )
        # A second invocation is a no-op report, never an error — and there
        # is no disarm command at all.
        again = StringIO()
        call_command('arm_rollback_floor', '--yes', stdout=again)
        self.assertIn('already armed', again.getvalue())
