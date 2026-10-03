"""A critical alarm reaches the people who could act on it, and only them."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from common.models import NotificationMessage
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
