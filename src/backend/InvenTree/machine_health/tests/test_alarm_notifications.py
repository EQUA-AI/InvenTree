"""Machine alarms reach the people who could act on them, and only them."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from common.models import InvenTreeUserSetting, NotificationEntry, NotificationMessage
from InvenTree.unit_test import PluginRegistryMixin
from machine_health.services.alarm_notifications import CATEGORY, recipients_for
from machine_health.services.anomalies import evaluate_thresholds

from .fixtures import HealthEnvMixin


#: The client each test user is scoped to; set by the test, read by the resolver
#: for whichever instance of the user the service happens to hold.
SCOPES = {}


def scope_by_username(actor):
    """Scope on the client the test assigned this user, if any."""
    client_id = SCOPES.get(actor.username)
    if client_id is None:
        return []
    return [{'customer_id': None, 'site_key': None, 'client_id': client_id}]


class AlarmNotificationTest(HealthEnvMixin, PluginRegistryMixin, TestCase):
    """Who is told, when, and how often."""

    def setUp(self):
        """One bounded signal and three users who differ in exactly one gate."""
        super().setUp()
        # Startup collection can precede the test database, and automatic
        # registry reloads are disabled in tests. Load the real delivery plugins
        # against this test's database rather than relying on an earlier suite.
        self.ensurePluginsLoaded(force=True)
        self.build_health_env()
        # The shared environment grants every actor the machine's client, which
        # is the one thing this suite must be able to withhold. Its resolver is
        # therefore replaced here, after the environment has installed its own.
        scoped = override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=scope_by_username)
        scoped.enable()
        self.addCleanup(scoped.disable)
        self.now = timezone.now()
        User = get_user_model()
        # Holds the role, and scope on this machine's client.
        self.operator = User.objects.create_superuser(
            'operator', 'operator@example.com', 'pw'
        )
        # Holds the role, scoped to a different plant.
        self.elsewhere = User.objects.create_superuser(
            'other-plant', 'elsewhere@example.com', 'pw'
        )
        # Scoped here, but no work-order role.
        self.visitor = User.objects.create_user('visitor', 'visitor@example.com', 'pw')
        SCOPES.clear()
        SCOPES.update({
            'operator': self.client_tenant.pk,
            'other-plant': self.client_tenant.pk + 1000,
            'visitor': self.client_tenant.pk,
        })

    def messages(self):
        """The bell notifications this test has produced, by recipient."""
        return sorted(
            NotificationMessage.objects.filter(category=CATEGORY).values_list(
                'user__username', flat=True
            )
        )

    def test_the_audience_is_the_health_tabs_own_two_gates(self):
        """Role and scope, the same two the page checks, and nothing else."""
        self.assertEqual(
            [user.username for user in recipients_for(self.machine)], ['operator']
        )

    def test_a_warning_and_critical_escalation_are_each_sent_once(self):
        """Warning delivery cannot suppress its critical escalation."""
        self.set_signal(7.0, observed_at=self.now)
        [warning] = evaluate_thresholds(self.machine)
        self.assertEqual(self.messages(), ['operator'])
        [message] = NotificationMessage.objects.filter(category=CATEGORY)
        self.assertIn('Warning alarm', message.name)
        self.assertIn(f'?anomaly={warning.pk}', message.link)
        evaluate_thresholds(self.machine)
        self.assertEqual(self.messages(), ['operator'])

        self.set_signal(10.0, observed_at=self.now)
        evaluate_thresholds(self.machine)
        self.assertEqual(self.messages(), ['operator', 'operator'])
        message = NotificationMessage.objects.filter(category=CATEGORY).latest('pk')
        self.assertIn('Critical alarm', message.name)
        self.assertIn(self.machine.name, message.name)
        self.assertIn('outside configured limits', message.message)
        self.assertIn(f'/machines/machine/{self.machine.pk}/health', message.link)

        # Still critical on the next evaluation: the same condition, no repeat.
        self.set_signal(10.5, observed_at=self.now)
        evaluate_thresholds(self.machine)
        self.assertEqual(self.messages(), ['operator', 'operator'])

    def test_standing_alarms_without_delivery_are_reconciled_on_ingest(self):
        """A condition predating this change needs no new state transition."""
        for value in (7.0, 10.0):
            with self.subTest(value=value):
                self.set_signal(value, observed_at=self.now)
                with patch('machine_health.services.anomalies.notify_alarm'):
                    [alarm] = evaluate_thresholds(self.machine)
                before = len(self.messages())
                evaluate_thresholds(self.machine)
                self.assertEqual(len(self.messages()), before + 1)
                self.assertIn(
                    f'?anomaly={alarm.pk}',
                    NotificationMessage.objects.filter(category=CATEGORY).latest('pk').link,
                )

    def test_a_delivered_condition_does_not_repeat_after_the_quiet_period(self):
        """Once per condition remains true after the per-signal quiet period."""
        self.set_signal(7.0, observed_at=self.now)
        evaluate_thresholds(self.machine)
        NotificationEntry.objects.update(updated=self.now - timedelta(days=2))
        evaluate_thresholds(self.machine)
        self.assertEqual(self.messages(), ['operator'])

    def test_legacy_critical_delivery_is_not_announced_again(self):
        """Previously delivered critical alarms retain their delivery record."""
        self.set_signal(10.0, observed_at=self.now)
        with patch('machine_health.services.anomalies.notify_alarm'):
            [alarm] = evaluate_thresholds(self.machine)
        NotificationEntry.notify(CATEGORY, alarm.pk)
        evaluate_thresholds(self.machine)
        self.assertEqual(self.messages(), [])

    def test_failed_escalation_delivery_is_retried(self):
        """An old warning delivery is not evidence of a critical delivery."""
        self.set_signal(7.0, observed_at=self.now)
        evaluate_thresholds(self.machine)
        self.set_signal(10.0, observed_at=self.now)
        with patch('common.notifications.trigger_notification'):
            evaluate_thresholds(self.machine)
        self.assertEqual(self.messages(), ['operator'])
        evaluate_thresholds(self.machine)
        self.assertEqual(self.messages(), ['operator', 'operator'])

    def test_a_critical_that_opens_as_one_is_sent_too(self):
        """Opening straight at critical is the common case, not the escalation."""
        self.set_signal(10.0, observed_at=self.now)
        evaluate_thresholds(self.machine)
        self.assertEqual(self.messages(), ['operator'])

    def test_a_signal_that_cannot_make_up_its_mind_is_announced_once_an_hour(self):
        """Each re-raise is a new condition; the signal is the same signal."""
        self.set_signal(10.0, observed_at=self.now)
        evaluate_thresholds(self.machine)
        self.set_signal(3.0, observed_at=self.now + timedelta(minutes=6))
        evaluate_thresholds(self.machine)
        self.set_signal(10.0, observed_at=self.now + timedelta(minutes=7))
        [again] = evaluate_thresholds(self.machine)

        # A second condition stands, and the page shows it; the bell rang once.
        self.assertEqual(again.status, 'open')
        self.assertEqual(self.messages(), ['operator'])

    def test_a_user_can_switch_them_off(self):
        """The page still shows everything; the bell stays quiet."""
        InvenTreeUserSetting.set_setting(
            'NOTIFY_MACHINE_ALARMS', False, self.operator, user=self.operator
        )
        self.assertEqual(recipients_for(self.machine), [])
        self.set_signal(10.0, observed_at=self.now)
        evaluate_thresholds(self.machine)
        self.assertEqual(self.messages(), [])

    def test_a_dismissed_condition_confirmed_critical_tells_nobody(self):
        """Dismissing is saying: do not tell me about this one."""
        from machine_health.services.anomalies import dismiss_anomaly

        self.set_signal(7.0, observed_at=self.now)
        [warning] = evaluate_thresholds(self.machine)
        dismiss_anomaly(warning.pk, actor=self.operator, note='Limit under review')
        NotificationMessage.objects.all().delete()

        self.set_signal(10.0, observed_at=self.now + timedelta(minutes=1))
        evaluate_thresholds(self.machine)
        self.assertEqual(self.messages(), [])

    def test_the_email_carries_the_machine_and_the_evidence(self):
        """Rendered and sent through the ordinary mail backend."""
        from allauth.account.models import EmailAddress

        EmailAddress.objects.create(
            user=self.operator, email='operator@example.com', primary=True, verified=True
        )
        mail.outbox.clear()
        self.set_signal(10.0, observed_at=self.now)
        evaluate_thresholds(self.machine)

        [message] = mail.outbox
        self.assertEqual(message.to, ['operator@example.com'])
        self.assertIn('Critical alarm', message.subject)
        self.assertIn(self.machine.name, message.subject)
        [(html, _)] = message.alternatives
        self.assertIn('read 10.0 mm/s', html)
        self.assertIn(f'/machines/machine/{self.machine.pk}/health', html)
