"""Notification-system integration tests for the EQUA synthetic demo.

The synthetic demo works *with* the existing InvenTree notification system —
the notification system itself is never replaced, wrapped, disabled or
monkeypatched:

* Synthetic demo seed windows (``assets.demo_metrics.effects``) are classified
  as data import in ``InvenTree.ready.isImportingData()`` — the same supported
  bootstrap/import integration used by ``loaddata``/``flush``.
* The existing transport checks in
  ``common.notifications.trigger_notification`` and
  ``InvenTree.helpers_email.send_email`` then suppress notification/email
  effects during the seed window exactly as they do for any other data
  import. Plugins, dedup entries, recipient permission checks and user
  notification preferences all keep their stock behavior.

These tests exercise the REAL notification system end to end: real
``trigger_notification`` calls, the real UI notification plugin writing
``NotificationMessage`` rows, the real email notification plugin, and the real
email transport delivering into ``django.core.mail.outbox``. Nothing here
fakes or synthesizes delivery/dispatch.
"""

from django.core import mail

from assets.demo_metrics.effects import synthetic_effects
from common.models import NotificationEntry, NotificationMessage
from common.notifications import trigger_notification
from InvenTree import ready
from InvenTree.helpers_email import send_email
from InvenTree.unit_test import InvenTreeTestCase


class SyntheticDemoNotificationIsolationTests(InvenTreeTestCase):
    """Real notifications/emails: unchanged for users, suppressed for seeds."""

    def setUp(self):
        """Prime the real plugin registry and give the user a real mailbox."""
        super().setUp()

        # The notification delivery methods are plugins: ensure they are
        # collected against the current test database (same requirement as
        # InvenTree.test_tasks notification tests).
        self.ensurePluginsLoaded(force=True)

        self.user.email = 'demo.notify@inventree.org'
        self.user.is_superuser = True
        self.user.save()

        NotificationEntry.objects.all().delete()

    def notify(self, category: str):
        """Fire a REAL notification through the production dispatch path."""
        trigger_notification(
            self.user,
            category,
            targets=[self.user],
            context={
                'name': 'Demo Test Notification',
                'message': 'This is a real notification dispatched through the stock system.',
                'template': {
                    'html': 'email/test_email.html',
                    'subject': 'Demo Test Notification',
                },
            },
        )

    def send_real_email(self):
        """Send a REAL email through the production email transport."""
        return send_email(
            'Demo Test Email', 'This is a real email body.', [self.user.email]
        )

    def test_ordinary_notification_and_email_are_unchanged(self):
        """Outside any synthetic window the stock system behaves exactly as before."""
        self.assertFalse(ready.isImportingData())

        # Real notification -> real UI message + real email (user preference
        # default allows email), and the stock dedup entry is recorded.
        self.notify('test.demo.ordinary')
        self.assertEqual(NotificationMessage.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(NotificationEntry.objects.count(), 1)

        # The stock dedup check still suppresses an immediate repeat.
        self.notify('test.demo.ordinary')
        self.assertEqual(NotificationMessage.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

        # The real email transport still delivers.
        result = self.send_real_email()
        self.assertEqual(result, (True, None))
        self.assertEqual(len(mail.outbox), 2)

        # Recipient permission filtering is unchanged: a user without view
        # permission on the target receives nothing.
        from django.contrib.auth import get_user_model

        outsider = get_user_model().objects.create_user(
            username='demo_outsider', email='outsider@example.org', password='x'
        )
        self.assertFalse(outsider.is_superuser)
        self.notify('test.demo.permissions')
        self.assertFalse(NotificationMessage.objects.filter(user=outsider).exists())

    def test_synthetic_seed_window_suppresses_real_dispatch(self):
        """A seed window suppresses real notifications/emails via the stock checks."""
        with synthetic_effects():
            # Classified as data import: the existing transport checks skip
            # dispatch silently (no raise, no delivery, no synthetic substitute).
            self.assertTrue(ready.isImportingData())
            self.notify('test.demo.suppressed')
            result = self.send_real_email()
            self.assertFalse(result[0])

        # Nothing real happened: no UI messages, no email, and no dedup entry.
        self.assertEqual(NotificationMessage.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(NotificationEntry.objects.count(), 0)

        # And the classification is window-scoped: back to normal afterwards.
        self.assertFalse(ready.isImportingData())

    def test_nested_windows_stay_suppressed_until_all_exit(self):
        """Nested seed windows keep suppressing; the outermost exit restores."""
        with synthetic_effects():
            self.assertTrue(ready.isImportingData())
            with synthetic_effects():
                self.notify('test.demo.nested')
            # Inner window exited — the outer window still suppresses.
            self.assertTrue(ready.isImportingData())
            self.notify('test.demo.nested')

        self.assertEqual(NotificationMessage.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

        # Normal later call: the unchanged system delivers again.
        self.assertFalse(ready.isImportingData())
        self.notify('test.demo.nested')
        self.assertEqual(NotificationMessage.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_window_unwinds_on_exception_and_later_calls_work(self):
        """An exception inside a seed window unwinds it completely."""
        with self.assertRaises(RuntimeError):
            with synthetic_effects():
                self.notify('test.demo.unwind')
                raise RuntimeError('seed failure')

        self.assertFalse(ready.isImportingData())

        # Normal later calls work: real dispatch, no stale suppression.
        self.notify('test.demo.unwind')
        result = self.send_real_email()
        self.assertEqual(result, (True, None))
        self.assertEqual(NotificationMessage.objects.count(), 1)
        # One notification-delivery email + one direct email.
        self.assertEqual(len(mail.outbox), 2)

    def test_suppressed_dispatch_does_not_poison_stock_dedup(self):
        """A suppressed dispatch must not block the next real notification."""
        with synthetic_effects():
            self.notify('test.demo.dedup')

        # Same category afterwards must still deliver: the stock dedup entry
        # is written only when a notification is actually sent.
        self.notify('test.demo.dedup')
        self.assertEqual(NotificationMessage.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
