"""A complete approved correspondence journey over loopback TLS mail servers."""

import ipaddress
import re
import socketserver
import ssl
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.utils import timezone

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from rest_framework.test import APIClient

from aichat.models import ConnectedMailbox, MailMessage
from aichat.services.email.credentials import encrypt_credentials
from aichat.services.email.dispatch import dispatch_pending
from aichat.services.email.drafts import create_draft
from aichat.services.email.receive import sync_account
from approvals import services
from approvals.models import ApprovalExecution
from approvals.review_evidence import required_sections
from approvals.review_sections import compute_review_hash


class _MailServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return self.context.wrap_socket(connection, server_side=True), address


class _SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b'220 localhost ESMTP fixture\r\n')
        recipients = []
        while line := self.rfile.readline():
            command = line.split()[0].upper()
            if command == b'EHLO':
                self.wfile.write(b'250-localhost\r\n250 AUTH PLAIN\r\n')
            elif command == b'AUTH':
                self.wfile.write(b'235 Authenticated\r\n')
            elif command == b'MAIL':
                recipients = []
                self.wfile.write(b'250 Sender accepted\r\n')
            elif command == b'RCPT':
                recipients.append(re.search(rb'<(.*?)>', line)[1].decode())
                self.wfile.write(b'250 Recipient accepted\r\n')
            elif command == b'DATA':
                self.wfile.write(b'354 Send message\r\n')
                lines = []
                while (line := self.rfile.readline()) not in (b'.\r\n', b''):
                    lines.append(line[1:] if line.startswith(b'..') else line)
                self.server.submissions.append((recipients, b''.join(lines)))
                self.wfile.write(b'250 Accepted\r\n')
            elif command == b'QUIT':
                self.wfile.write(b'221 Closing\r\n')
                return
            else:
                self.wfile.write(b'500 Unsupported command\r\n')


class _IMAPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b'* OK IMAP fixture ready\r\n')
        folder = 'Inbox'
        while line := self.rfile.readline():
            tag, command, *args = line.rstrip().split()
            self.server.commands.append(command)
            if command == b'CAPABILITY':
                self.wfile.write(b'* CAPABILITY IMAP4rev1\r\n')
            elif command == b'LOGIN':
                pass
            elif command == b'EXAMINE':
                folder = args[0].strip(b'"').decode()
                self.wfile.write(b'* OK [UIDVALIDITY 71] Valid identifiers\r\n')
                self.wfile.write(
                    f'* {len(self.server.folders[folder])} EXISTS\r\n'.encode()
                )
            elif command == b'APPEND':
                target = args[0].strip(b'"').decode()
                length = int(re.search(rb'\{(\d+)\}', line)[1])
                self.wfile.write(b'+ Ready for literal\r\n')
                raw = self.rfile.read(length)
                self.rfile.readline()
                self.server.folders[target].append(raw)
            elif command == b'UID' and args[0] == b'SEARCH':
                uids = ' '.join(
                    str(i + 1) for i in range(len(self.server.folders[folder]))
                )
                self.wfile.write(f'* SEARCH {uids}\r\n'.encode())
            elif command == b'UID' and args[0] == b'FETCH':
                uid = int(args[1])
                raw = self.server.folders[folder][uid - 1]
                metadata = f'* {uid} FETCH (UID {uid} FLAGS ()'
                if b'BODY.PEEK' in line:
                    self.wfile.write(
                        f'{metadata} RFC822.SIZE {len(raw)} BODY[] {{{len(raw)}}}\r\n'.encode()
                        + raw
                        + b')\r\n'
                    )
                else:
                    self.wfile.write(f'{metadata})\r\n'.encode())
            elif command == b'LOGOUT':
                self.wfile.write(b'* BYE Closing\r\n' + tag + b' OK Logout\r\n')
                return
            else:
                self.wfile.write(tag + b' BAD Unsupported command\r\n')
                continue
            self.wfile.write(tag + b' OK Completed\r\n')


@override_settings(
    AGENT_EMAIL_ENABLED=True, AGENT_EMAIL_MESSAGE_ID_DOMAIN='mail.example.test'
)
class LocalMailJourneyTests(TestCase):
    """Use real adapters, TLS, MIME and the ledger; no remote mail is contacted."""

    def setUp(self):
        """Generate a temporary trusted certificate and isolated local listeners."""
        ContentType.objects.clear_cache()
        self.user = get_user_model().objects.create_superuser('local-mail-admin')
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]
                ),
                False,
            )
            .sign(key, hashes.SHA256())
        )
        cert_path, key_path = Path(temporary) / 'cert.pem', Path(temporary) / 'key.pem'
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert_path, key_path)
        client_context = ssl.create_default_context(cafile=str(cert_path))
        self.enterContext(
            patch(
                'ai.core.integrations.email.smtp_imap.ssl.create_default_context',
                return_value=client_context,
            )
        )
        self.enterContext(
            patch.dict(
                'os.environ',
                {
                    'INVENTREE_AGENT_EMAIL_PRIVATE_NETWORKS': '127.0.0.0/8,::1/128',
                    'AIMMS_EMAIL_RECIPIENT_ALLOWLIST': '@example.test',
                },
            )
        )
        self.enterContext(
            override_settings(
                AGENT_EMAIL_CREDENTIAL_KEYS=[Fernet.generate_key().decode()]
            )
        )
        self.enterContext(patch('aichat.services.email.dispatch.publish'))
        self.enterContext(
            patch('aichat.services.email.receive.scan_content', return_value='clean')
        )
        self.smtp, self.imap = self.server(_SMTPHandler), self.server(_IMAPHandler)
        self.smtp.context = self.imap.context = server_context
        self.smtp.submissions = []
        self.imap.commands = []
        self.imap.folders = {'Inbox': [], 'Sent': []}
        self.account = ConnectedMailbox.objects.create(
            owner=self.user,
            name='Loopback only',
            provider='smtp_imap',
            address='sender@example.test',
            enabled=True,
            send_enabled=True,
            receive_enabled=True,
            verified_send_at=timezone.now(),
            verified_receive_at=timezone.now(),
            recipient_allowlist=['@example.test'],
            options={
                'smtp_host': '127.0.0.1',
                'smtp_port': self.smtp.server_address[1],
                'smtp_tls': 'implicit',
                'imap_host': '127.0.0.1',
                'imap_port': self.imap.server_address[1],
                'sent_copy': 'append',
            },
            encrypted_credentials=encrypt_credentials(
                dict.fromkeys(
                    [
                        'smtp_username',
                        'smtp_password',
                        'imap_username',
                        'imap_password',
                    ],
                    'local-fixture',
                )
            ),
        )

    def server(self, handler):
        """Start a loopback listener and guarantee shutdown even on failure."""
        server = _MailServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        return server

    def send(self, data, request_id):
        """Approve exact content through the production service before dispatch."""
        approval = create_draft(self.user, self.account.pk, data, request_id)
        services.open_approval(approval.pk, actor=self.user)
        services.confirm_viewed(
            approval.pk,
            actor=self.user,
            data={
                'revision': 0,
                'review_hash': compute_review_hash(approval),
                'sections': required_sections(approval),
            },
        )
        services.approve(approval.pk, actor=self.user)
        operation = ApprovalExecution.objects.get(approval=approval)
        dispatch_pending(operation.pk)
        dispatch_pending(operation.pk)
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'succeeded', operation.result)
        self.assertFalse(operation.result['payload']['sent_copy_failed'])
        return approval

    def test_review_send_receive_attachment_and_reviewed_reply(self):
        """Real protocol reads preserve flags and reply identity, with no replay."""
        approval = self.send(
            {
                'to': ['recipient@example.test'],
                'bcc': ['hidden@example.test'],
                'subject': 'Local journey',
                'body': 'First message\n.A dot-stuffed line',
            },
            'first',
        )
        recipients, raw = self.smtp.submissions[0]
        self.assertEqual(recipients, ['recipient@example.test', 'hidden@example.test'])
        self.assertEqual(raw, bytes(approval.email_draft.raw))
        self.assertIsNone(BytesParser(policy=policy.default).parsebytes(raw)['Bcc'])
        sync_account(self.account.pk, 'Sent')
        original = MailMessage.objects.get(account=self.account, direction='outbound')
        reply = EmailMessage(policy=policy.SMTP)
        reply['From'], reply['To'] = 'recipient@example.test', self.account.address
        reply['Subject'], reply['Message-ID'] = (
            'Re: Local journey',
            '<reply@example.test>',
        )
        reply['In-Reply-To'] = original.rfc_message_id
        reply.set_content('Reply received over IMAP')
        reply.add_attachment(
            b'local attachment', maintype='text', subtype='plain', filename='reply.txt'
        )
        self.imap.folders['Inbox'].append(reply.as_bytes())
        sync_account(self.account.pk, 'Inbox')
        sync_account(self.account.pk, 'Inbox')
        incoming = MailMessage.objects.get(account=self.account, direction='inbound')
        self.assertEqual(incoming.conversation_id, original.conversation_id)
        self.assertFalse(incoming.locations.get().is_read)
        artifact = incoming.attachments.get()
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.get(
            f'/api/aichat/email/accounts/{self.account.pk}/attachments/{artifact.pk}/'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'local attachment')
        reviewed_reply = self.send(
            {
                'to': ['recipient@example.test'],
                'subject': 'Re: Local journey',
                'body': 'Reviewed response',
                'reply_message_id': str(incoming.pk),
                'attachment_ids': [str(artifact.pk)],
            },
            'reply',
        )
        parsed = BytesParser(policy=policy.default).parsebytes(
            bytes(reviewed_reply.email_draft.raw)
        )
        self.assertEqual(parsed['In-Reply-To'], '<reply@example.test>')
        self.assertEqual(
            next(parsed.iter_attachments()).get_payload(decode=True),
            b'local attachment',
        )
        self.assertEqual(len(self.smtp.submissions), 2)
        self.assertNotIn(b'SELECT', self.imap.commands)
        self.assertNotIn(b'STORE', self.imap.commands)
