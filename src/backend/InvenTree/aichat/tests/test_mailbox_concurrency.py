"""Exercise competing dispatch workers against PostgreSQL row locks."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from ai.core.integrations.email.contracts import Capabilities, SendObservation
from aichat.models import ConnectedMailbox
from aichat.services.email.dispatch import dispatch_pending
from aichat.services.email.drafts import create_draft
from approvals import services
from approvals.models import ApprovalExecution
from approvals.review_evidence import required_sections
from approvals.review_sections import compute_review_hash


@skipUnless(
    connection.vendor == 'postgresql', 'Requires PostgreSQL row-lock concurrency'
)
@override_settings(
    AGENT_EMAIL_ENABLED=True, AGENT_EMAIL_MESSAGE_ID_DOMAIN='example.test'
)
class MailboxClaimConcurrencyTests(TransactionTestCase):
    """A duplicate worker cannot submit while the first transport is in flight."""

    def test_two_workers_consume_one_claim(self):
        """The second task exits before the first receives provider acceptance."""
        from django.contrib.contenttypes.models import ContentType

        ContentType.objects.clear_cache()
        user = get_user_model().objects.create_superuser(
            'claim-reviewer', 'admin@example.test', 'test'
        )
        account = ConnectedMailbox.objects.create(
            name='Concurrent recording',
            address='sender@example.test',
            provider='recording',
            owner=user,
            enabled=True,
            send_enabled=True,
            verified_send_at=timezone.now(),
            verified_receive_at=timezone.now(),
            recipient_allowlist=['@example.test'],
        )
        with patch.dict(
            'os.environ', {'AIMMS_EMAIL_RECIPIENT_ALLOWLIST': '@example.test'}
        ):
            approval = create_draft(
                user,
                account.pk,
                {
                    'to': 'recipient@example.test',
                    'subject': 'Recording',
                    'body': 'Hello',
                },
                'concurrent-request',
            )
            services.open_approval(approval.pk, actor=user)
            services.confirm_viewed(
                approval.pk,
                actor=user,
                data={
                    'revision': 0,
                    'review_hash': compute_review_hash(approval),
                    'sections': required_sections(approval),
                },
            )
            with patch('aichat.services.email.dispatch.publish'):
                services.approve(approval.pk, actor=user)
            operation = ApprovalExecution.objects.get(approval=approval)
            started, release = Event(), Event()
            provider = Mock(capabilities=Capabilities())

            def submit(message):
                started.set()
                if not release.wait(10):
                    raise TimeoutError('test worker release expired')
                return SendObservation('succeeded', ('accepted',), 'transport_response')

            provider.submit.side_effect = submit

            def worker():
                close_old_connections()
                try:
                    dispatch_pending(operation.pk)
                finally:
                    close_old_connections()

            with (
                patch(
                    'aichat.services.email.dispatch.provider_for', return_value=provider
                ),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                first = pool.submit(worker)
                try:
                    self.assertTrue(started.wait(10))
                    pool.submit(worker).result(timeout=10)
                finally:
                    release.set()
                first.result(timeout=10)
            provider.submit.assert_called_once()
            operation.refresh_from_db()
            self.assertEqual(operation.state, 'succeeded')
