"""Wire-boundary adapter contracts without external connections."""

import base64
import socket
from email import policy
from email.parser import BytesParser
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

from ai.core.integrations.email.contracts import (
    AccountConfig,
    MailboxError,
    PreparedEmail,
)
from ai.core.integrations.email.google import GoogleProvider
from ai.core.integrations.email.graph import GraphProvider
from ai.core.integrations.email.http_mail import api_mime
from ai.core.integrations.email.network import endpoint
from ai.core.integrations.email.smtp_imap import SMTPIMAPProvider


class ProviderTests(TestCase):
    """Shared effect envelope and conservative transport outcomes."""

    def setUp(self):
        """Create one frozen MIME artifact with a protected hidden recipient."""
        self.raw = b'From: sender@example.test\r\nTo: visible@example.test\r\nMessage-ID: <test@example.test>\r\nSubject: Test\r\nContent-Type: text/plain\r\n\r\nHello\r\n'
        self.message = PreparedEmail(
            'account-1',
            'operation-1',
            'sender@example.test',
            ('visible@example.test', 'hidden@example.test'),
            '<test@example.test>',
            self.raw,
            'fingerprint',
        )

    def config(self, provider):
        """Each adapter receives explicit mailbox configuration."""
        return AccountConfig(
            'account-1',
            provider,
            'sender@example.test',
            {},
            {'access_token': 'recording-only'},
        )

    def test_api_projection_preserves_body_and_supplies_hidden_envelope(self):
        """MIME APIs receive Bcc while the frozen artifact remains unchanged."""
        raw = api_mime(self.message)
        self.assertTrue(raw.endswith(self.raw))
        self.assertEqual(
            BytesParser(policy=policy.default).parsebytes(raw)['Bcc'],
            'hidden@example.test',
        )
        self.assertNotIn(b'Bcc:', self.message.raw)

    def test_api_acceptance_without_ids_and_error_classification(self):
        """Graph 202 and Gmail 200 are acceptance, never claims of delivery."""
        for adapter, success in [
            (GraphProvider(self.config('graph')), 202),
            (GoogleProvider(self.config('google')), 200),
        ]:
            for status, outcome in [
                (success, 'succeeded'),
                (429, 'failed_before_effect'),
                (500, 'unknown'),
                (302, 'unknown'),
            ]:
                with (
                    self.subTest(provider=type(adapter).__name__, status=status),
                    patch.object(
                        adapter, 'request', return_value=(status, b'')
                    ) as request,
                ):
                    result = adapter.submit(self.message).validate(2)
                    self.assertEqual(result.outcome, outcome)
                    request.assert_called_once()
                    self.assertIn('sender%40example.test', request.call_args.args[1])

    def test_api_timeout_does_not_retry_or_resend_on_reconcile(self):
        """Reconciliation without authoritative acceptance remains unknown."""
        for adapter in [
            GraphProvider(self.config('graph')),
            GoogleProvider(self.config('google')),
        ]:
            with patch.object(
                adapter, 'request', side_effect=TimeoutError()
            ) as request:
                self.assertEqual(adapter.submit(self.message).outcome, 'unknown')
                self.assertEqual(adapter.reconcile(self.message).outcome, 'unknown')
                request.assert_called_once()

    def test_smtp_final_data_partial_and_loss(self):
        """RCPT 250 does not prove acceptance when the DATA response is lost."""
        provider = SMTPIMAPProvider(self.config('smtp_imap'))
        for response, outcome in [
            ((250, b'queued'), 'partial'),
            ((550, b'rejected'), 'failed_before_effect'),
            (TimeoutError(), 'unknown'),
        ]:
            client = Mock()
            client.mail.return_value = (250, b'ok')
            client.rcpt.side_effect = [(250, b'ok'), (550, b'no')]
            if isinstance(response, Exception):
                client.data.side_effect = response
            else:
                client.data.return_value = response
            with (
                self.subTest(outcome=outcome),
                patch.object(provider, '_smtp', return_value=client),
            ):
                observation = provider.submit(self.message).validate(2)
                self.assertEqual(observation.outcome, outcome)
                client.data.assert_called_once_with(self.raw)
                client.close.assert_called_once()

    def test_smtp_refusal_never_reaches_data(self):
        """All refused recipients are a proven no-effect failure."""
        provider = SMTPIMAPProvider(self.config('smtp_imap'))
        client = Mock()
        client.mail.return_value = (250, b'ok')
        client.rcpt.return_value = (550, b'no')
        with patch.object(provider, '_smtp', return_value=client):
            self.assertEqual(
                provider.submit(self.message).outcome, 'failed_before_effect'
            )
        client.data.assert_not_called()

    def test_smtp_close_failure_preserves_final_acceptance(self):
        """Connection cleanup is not evidence that a successful DATA was undone."""
        provider = SMTPIMAPProvider(self.config('smtp_imap'))
        client = Mock()
        client.mail.return_value = client.rcpt.return_value = (
            client.data.return_value
        ) = (250, b'ok')
        client.close.side_effect = OSError('fixture close failure')
        with patch.object(provider, '_smtp', return_value=client):
            result = provider.submit(self.message)
        self.assertEqual(result.outcome, 'succeeded')
        self.assertEqual(result.recipients, ('accepted', 'accepted'))
        client.data.assert_called_once()

    def test_sent_append_failure_preserves_acceptance(self):
        """A failed Sent copy cannot authorize another SMTP attempt."""
        provider = SMTPIMAPProvider(
            AccountConfig(
                'account-1', 'smtp_imap', 'sender@example.test', {'sent_copy': 'append'}
            )
        )
        client = Mock()
        client.mail.return_value = client.rcpt.return_value = (
            client.data.return_value
        ) = (250, b'ok')
        with (
            patch.object(provider, '_smtp', return_value=client),
            patch.object(provider, '_imap', side_effect=TimeoutError()),
        ):
            result = provider.submit(self.message)
        self.assertEqual(result.outcome, 'succeeded')
        self.assertTrue(result.sent_copy_failed)
        client.data.assert_called_once()

    def test_graph_empty_page_advances_only_its_folder(self):
        """An empty nextLink page is valid; another mailbox's cursor is not."""
        provider = GraphProvider(self.config('graph'))
        cursor = provider.root + '/mailFolders/inbox/messages/delta?$skiptoken=opaque'
        with patch.object(
            provider, 'get_json', return_value={'value': [], '@odata.nextLink': cursor}
        ):
            page = provider.sync(
                'Inbox', {'since': '2026-01-01T00:00:00+00:00'}
            ).validate('Inbox', 20000)
        self.assertFalse(page.complete)
        self.assertEqual(page.continuation, {'url': cursor})
        with self.assertRaisesRegex(MailboxError, 'invalid_provider_url'):
            provider.sync('Sent', page.continuation)

    def test_google_full_sync_checkpoints_starting_history(self):
        """Messages arriving during full sync are recovered from starting history."""
        provider = GoogleProvider(self.config('google'))
        with patch.object(
            provider,
            'get_json',
            side_effect=[
                {'historyId': '11'},
                {'messages': [{'id': 'CaseSensitive'}]},
                {
                    'raw': base64.urlsafe_b64encode(self.raw).decode(),
                    'labelIds': ['INBOX'],
                },
            ],
        ) as request:
            page = provider.sync('Inbox', {'since': '2026-01-01T00:00:00+00:00'})
        self.assertEqual(page.checkpoint, {'history': '11'})
        self.assertEqual(page.changes[0].identity, 'CaseSensitive')
        self.assertIn('after%3A', request.call_args_list[1].args[0])

    def test_api_does_not_send_credentials_to_redirect_target(self):
        """Provider links cannot redirect bearer tokens to arbitrary hosts."""
        provider = GraphProvider(self.config('graph'))
        with self.assertRaisesRegex(MailboxError, 'invalid_provider_url'):
            provider.request('GET', 'https://attacker.example/v1.0/users/a')

    def test_endpoint_blocks_private_and_metadata_addresses(self):
        """Reject mixed DNS answers before selecting an address to connect."""
        for ip in ['127.0.0.1', '169.254.169.254', '10.0.0.1', '::1']:
            with (
                self.subTest(ip=ip),
                patch.dict(
                    'os.environ', {'INVENTREE_AGENT_EMAIL_PRIVATE_NETWORKS': ''}
                ),
                patch(
                    'socket.getaddrinfo',
                    return_value=[
                        (socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 587))
                    ],
                ),
                self.assertRaisesRegex(MailboxError, 'endpoint_denied'),
            ):
                endpoint('mail.example.test', 587)

    def test_imap_refreshes_flags_and_records_expunged_uids(self):
        """Existing content needs only FLAGS; removed membership is explicit."""
        provider = SMTPIMAPProvider(self.config('smtp_imap'))
        client = MagicMock()
        client.__enter__.return_value = client
        client.select.return_value = ('OK', [b'1'])
        client.response.return_value = ('UIDVALIDITY', [b'7'])
        client.uid.side_effect = [('OK', [b'2']), ('OK', [b'1 (UID 2 FLAGS (\\Seen))'])]
        with patch.object(provider, '_imap', return_value=client):
            page = provider.sync(
                'Inbox',
                {
                    'validity': '7',
                    'known': [1, 2],
                    'since': '2026-01-01T00:00:00+00:00',
                },
            ).validate('Inbox', 20000)
        self.assertEqual(
            [change.kind for change in page.changes], ['flags', 'remove_location']
        )
        self.assertTrue(page.changes[0].is_read)
        client.select.assert_called_once_with('"Inbox"', readonly=True)
        self.assertEqual(client.uid.call_args.args[-1], '(UID FLAGS)')

    def test_imap_uidvalidity_change_stops_old_cursor(self):
        """UIDs from a reset mailbox cannot overwrite previous messages."""
        provider = SMTPIMAPProvider(self.config('smtp_imap'))
        client = MagicMock()
        client.__enter__.return_value = client
        client.select.return_value = ('OK', [b'1'])
        client.response.return_value = ('UIDVALIDITY', [b'8'])
        with (
            patch.object(provider, '_imap', return_value=client),
            self.assertRaisesRegex(MailboxError, 'uidvalidity_changed'),
        ):
            provider.sync('Inbox', {'validity': '7'})
        client.uid.assert_not_called()
