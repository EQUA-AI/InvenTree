"""S15 (WP-B1): the durable pilot-stop latch — models, service, commands.

Any one owner stops; all five recorded approvals clear; one active
episode ever; everything works with FEATURE_AI_PILOT_STOP_LATCH dark
(operator drills before enablement).
"""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from aichat.services import pilot_latch


def _user(name, *, with_perm=False):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission

    user = get_user_model().objects.create_user(username=name, password='unused')
    if with_perm:
        user.user_permissions.add(
            Permission.objects.get(codename='manage_pilot_stop')
        )
    return user


ALL_ROLES = (
    'engineering',
    'product',
    'maintenance_safety',
    'document_control',
    'security_privacy',
)


class LatchServiceTests(TestCase):
    """Engage / approve / clear semantics."""

    def test_unlatched_state_is_clear(self):
        """The default state gates nothing and lists nothing."""
        state = pilot_latch.current_state()
        self.assertFalse(state['latched'])
        self.assertEqual(state['missing_roles'], [])

    def test_engage_is_idempotent_and_single_active(self):
        """A second stop keeps the one active episode."""
        from aichat.models import AIPilotStopLatch

        first = pilot_latch.engage_latch(reason_code='manual')
        second = pilot_latch.engage_latch(reason_code='model_pin_mismatch')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(AIPilotStopLatch.objects.filter(active=True).count(), 1)
        state = pilot_latch.current_state()
        self.assertTrue(state['latched'])
        self.assertEqual(state['reason_code'], 'manual')
        self.assertEqual(len(state['missing_roles']), 5)

    def test_engage_writes_the_shared_cache_directly(self):
        """Automatic stops must not wait out the reader TTL."""
        from django.core.cache import cache

        cache.delete(pilot_latch.LATCH_CACHE_KEY)
        pilot_latch.engage_latch(reason_code='enforce_fail_open', source='automatic')
        self.assertEqual(cache.get(pilot_latch.LATCH_CACHE_KEY), 'enforce_fail_open')

    def test_four_approvals_stay_latched_the_fifth_clears(self):
        """Q43: restart requires ALL FIVE recorded approvals."""
        from aichat.models import AIPilotStopLatch

        pilot_latch.engage_latch(reason_code='manual')
        approver = _user('approver')
        for role in ALL_ROLES[:4]:
            state = pilot_latch.record_resume_approval(role=role, approved_by=approver)
            self.assertTrue(state['latched'])
        self.assertEqual(state['missing_roles'], ['security_privacy'])

        state = pilot_latch.record_resume_approval(
            role='security_privacy', approved_by=approver, reference='dossier-7'
        )
        self.assertFalse(state['latched'])
        episode = AIPilotStopLatch.objects.get()
        self.assertFalse(episode.active)
        self.assertIsNotNone(episode.cleared_at)
        self.assertEqual(episode.approvals.count(), 5)

    def test_repeat_approval_for_one_role_is_idempotent(self):
        """One role cannot count twice."""
        pilot_latch.engage_latch(reason_code='manual')
        approver = _user('repeat-approver')
        pilot_latch.record_resume_approval(role='product', approved_by=approver)
        state = pilot_latch.record_resume_approval(role='product', approved_by=approver)
        self.assertEqual(state['approvals'], ['product'])
        self.assertEqual(len(state['missing_roles']), 4)

    def test_approval_without_a_latch_is_a_typed_error(self):
        """Approving nothing is a loud mistake, not a no-op."""
        with self.assertRaises(ValueError):
            pilot_latch.record_resume_approval(role='product', approved_by=None)

    def test_cleared_state_reads_clear_and_a_new_stop_starts_fresh(self):
        """Episodes never resurrect; a new stop is a new row."""
        from aichat.models import AIPilotStopLatch

        pilot_latch.engage_latch(reason_code='manual')
        approver = _user('cycle-approver')
        for role in ALL_ROLES:
            pilot_latch.record_resume_approval(role=role, approved_by=approver)
        self.assertFalse(pilot_latch.current_state()['latched'])
        pilot_latch.engage_latch(reason_code='stale_domain_contamination')
        self.assertEqual(AIPilotStopLatch.objects.count(), 2)
        self.assertEqual(
            pilot_latch.current_state()['reason_code'], 'stale_domain_contamination'
        )


class LatchAlertingTests(TestCase):
    """Owner notification targeting, honest fallbacks."""

    @override_settings(
        AIMMS_PILOT_STOP_OWNERS=['engineering:eng-owner', 'product:prod-owner']
    )
    def test_named_owners_are_targeted(self):
        """The Part-4 csv resolves to real users."""
        eng = _user('eng-owner')
        _user('prod-owner')
        with mock.patch('common.notifications.trigger_notification') as trigger:
            pilot_latch.engage_latch(reason_code='manual')
        targets = trigger.call_args.kwargs['targets']
        self.assertIn(eng, targets)
        self.assertEqual(len(targets), 2)
        self.assertEqual(
            trigger.call_args.kwargs['category'], pilot_latch.NOTIFICATION_CATEGORY
        )

    def test_permission_holders_are_the_fallback(self):
        """Without the csv, aichat.manage_pilot_stop holders are alerted."""
        holder = _user('perm-holder', with_perm=True)
        with mock.patch('common.notifications.trigger_notification') as trigger:
            pilot_latch.engage_latch(reason_code='manual')
        self.assertEqual(trigger.call_args.kwargs['targets'], [holder])

    def test_notification_failure_never_masks_the_stop(self):
        """Alerting is best-effort; the latch holds regardless."""
        with mock.patch(
            'common.notifications.trigger_notification', side_effect=RuntimeError('smtp down')
        ):
            pilot_latch.engage_latch(reason_code='manual')
        self.assertTrue(pilot_latch.current_state()['latched'])


class LatchCommandTests(TestCase):
    """The operator drill: pilot_stop / pilot_resume, flag dark."""

    def _stop(self, *args):
        out = StringIO()
        call_command('pilot_stop', *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_stop_status_and_full_resume_cycle(self):
        """The drill end to end, exactly as the runbook describes."""
        owner = _user('drill-owner', with_perm=True)
        output = self._stop('--reason-code=manual', f'--by={owner.username}')
        self.assertIn('ENGAGED', output)
        status = self._stop('--status')
        self.assertIn('latched=True', status)

        for index, role in enumerate(ALL_ROLES):
            out = StringIO()
            call_command(
                'pilot_resume', f'--role={role}', f'--by={owner.username}', stdout=out
            )
            if index < 4:
                self.assertIn('Still missing', out.getvalue())
        self.assertIn('CLEARED', out.getvalue())
        self.assertIn('latched=False', self._stop('--status'))

    def test_stop_requires_reason_and_user(self):
        """No anonymous, reasonless stops."""
        with self.assertRaises(CommandError):
            self._stop('--by=nobody-set')
        with self.assertRaises(CommandError):
            self._stop('--reason-code=manual', '--by=missing-user')

    def test_resume_without_a_latch_is_a_command_error(self):
        """The typed service error surfaces as a CommandError."""
        owner = _user('early-approver')
        with self.assertRaises(CommandError):
            call_command(
                'pilot_resume', '--role=product', f'--by={owner.username}', stdout=StringIO()
            )
