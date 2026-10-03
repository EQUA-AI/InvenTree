"""A critical alarm reaches the people who could act on it, and only them."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from common.models import InvenTreeUserSetting, NotificationMessage
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


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=scope_by_username)
class AlarmNotificationTest(HealthEnvMixin, TestCase):
    """Who is told, when, and how often."""

    def setUp(self):
        """One bounded signal and three users who differ in exactly one gate."""
        super().setUp()
        self.build_health_env()
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

    def test_a_critical_is_sent_once_and_a_warning_is_not(self):
        """Told when it opens as a critical; not again while it stands."""
        self.set_signal(7.0, observed_at=self.now)
        evaluate_thresholds(self.machine)
        self.assertEqual(self.messages(), [])

        self.set_signal(10.0, observed_at=self.now)
        evaluate_thresholds(self.machine)
        self.assertEqual(self.messages(), ['operator'])
        [message] = NotificationMessage.objects.filter(category=CATEGORY)
        self.assertIn(self.machine.name, message.name)
        self.assertIn('outside configured limits', message.message)
        self.assertIn(f'/machines/machine/{self.machine.pk}/health', message.link)

        # Still critical on the next evaluation: the same condition, no repeat.
        self.set_signal(10.5, observed_at=self.now)
        evaluate_thresholds(self.machine)
        self.assertEqual(self.messages(), ['operator'])

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
