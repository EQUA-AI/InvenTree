"""Mailbox pause controls and cursor lifetime across ORM queue publication."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from django_q.brokers.orm import ORM
from rest_framework.test import APIRequestFactory, force_authenticate

from aichat.email_api import MailboxSync
from aichat.models import ConnectedMailbox
from aichat.services.email.receive import sync_account
from aichat.tasks import synchronize_mailboxes
from aimms_testing import requires_postgres


@override_settings(AGENT_EMAIL_ENABLED=True, AGENT_EMAIL_SYNC_PAUSED=True)
class MailboxSyncPauseTests(SimpleTestCase):
    """Paused scheduled and already-queued attempts require no database access."""

    def test_scheduled_attempt_and_retry_are_noops(self):
        """A paused sweep succeeds without publishing more work."""
        with patch('django_q.tasks.async_task') as enqueue:
            synchronize_mailboxes()
            synchronize_mailboxes()
        enqueue.assert_not_called()

    def test_already_queued_sync_is_a_noop(self):
        """Queued account attempts cannot reach providers or create new failures."""
        with patch('aichat.services.email.receive.provider_for') as provider:
            sync_account('not-an-account', 'Inbox')
        provider.assert_not_called()


@override_settings(AGENT_EMAIL_ENABLED=True, AGENT_EMAIL_SYNC_PAUSED=False)
class MailboxSyncSchedulingTests(TransactionTestCase):
    """Use autocommit so broker connection cleanup has its real worker behavior."""

    def setUp(self):
        """Create mailbox pages and one account excluded from polling."""
        self.user = get_user_model().objects.create_superuser(
            'sync-control', 'sync@example.test', 'test'
        )
        self.accounts = [
            ConnectedMailbox.objects.create(
                name=f'Mailbox {index}',
                provider='recording',
                address=f'mailbox{index}@example.test',
                owner=self.user,
                enabled=True,
                receive_enabled=True,
            )
            for index in range(3)
        ]
        ConnectedMailbox.objects.create(
            name='Paused account',
            provider='recording',
            address='paused@example.test',
            owner=self.user,
            enabled=True,
            receive_enabled=False,
        )

    @requires_postgres
    def test_enqueue_can_close_connections_between_account_batches(self):
        """Actual ORM enqueue cleanup must not invalidate a streaming cursor."""
        broker = ORM(list_key='mailbox-sync-regression')

        def enqueue(*args):
            broker.enqueue('test-only-queued-payload')

        with (
            patch.dict(connection.settings_dict, {'CONN_MAX_AGE': 0}),
            patch('aichat.tasks.MAILBOX_SYNC_BATCH_SIZE', 2),
            patch('django_q.tasks.async_task', side_effect=enqueue) as publish,
        ):
            synchronize_mailboxes()
        self.assertEqual(
            {call.args for call in publish.call_args_list},
            {
                ('aichat.services.email.receive.sync_account', str(account.pk), folder)
                for account in self.accounts
                for folder in ('Inbox', 'Sent')
            },
        )
        self.assertEqual(publish.call_count, 6)

    @override_settings(AGENT_EMAIL_SYNC_PAUSED=True)
    def test_manual_sync_reports_pause_without_enqueueing(self):
        """A manual caller must not be told paused work has been scheduled."""
        request = APIRequestFactory().post('/mailbox/sync/', {})
        force_authenticate(request, self.user)
        with patch('django_q.tasks.async_task') as enqueue:
            response = MailboxSync.as_view()(request, account_id=self.accounts[0].pk)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'error': 'sync_paused'})
        enqueue.assert_not_called()

    def test_unpausing_resumes_polling(self):
        """Changing the flag preserves the existing schedule's ability to run."""
        with patch('django_q.tasks.async_task') as enqueue:
            with override_settings(AGENT_EMAIL_SYNC_PAUSED=True):
                synchronize_mailboxes()
            enqueue.assert_not_called()
            synchronize_mailboxes()
        self.assertEqual(enqueue.call_count, 6)
