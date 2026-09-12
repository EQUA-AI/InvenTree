"""Private, bounded MIME ingestion with atomic cursor progress and quarantine."""

import re
import socket
import struct
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ai.core.integrations.email.contracts import MailboxError
from aichat.models import (
    ConnectedMailbox,
    MailAttachment,
    MailConversation,
    MailLocation,
    MailMessage,
    MailSyncState,
)

from .access import require_account, require_enabled
from .accounts import provider_for
from .drafts import digest

MAX_BYTES = 20 * 1024 * 1024


class _PlainHTML(HTMLParser):
    """Extract inert text without following links or loading remote resources."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        elif tag in ('br', 'p', 'div', 'li'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def scan_content(content):
    """Fail closed using ClamAV INSTREAM; never execute or unpack attachments."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(settings.AGENT_EMAIL_CLAMAV_SOCKET)
            client.sendall(b'zINSTREAM\x00')
            for offset in range(0, len(content), 65536):
                chunk = content[offset : offset + 65536]
                client.sendall(struct.pack('!I', len(chunk)) + chunk)
            client.sendall(struct.pack('!I', 0))
            result = client.recv(4096)
        if result.strip(b'\x00\r\n') == b'stream: OK':
            return 'clean'
        return 'infected' if b' FOUND' in result else 'quarantined'
    except (OSError, AttributeError):
        return 'quarantined'


def artifact_fields(filename, content):
    """Never use sender-provided names as filesystem paths or active MIME types."""
    filename = re.sub(r'[\x00-\x1f\x7f/\\]', '_', str(filename or 'attachment'))[:255]
    if len(content) > MAX_BYTES:
        raise MailboxError('attachment_too_large')
    return {
        'filename': filename,
        'content_type': 'application/octet-stream',
        'digest': digest(content),
        'size': len(content),
        'content': content,
        'scan_state': scan_content(content),
    }


def upload(actor, account_id, filename, content):
    """Store user uploads privately and scan them before drafting."""
    require_enabled()
    account = require_account(actor, account_id, 'draft')
    return MailAttachment.objects.create(
        account=account, uploaded_by=actor, **artifact_fields(filename, content)
    )


def parse(raw):
    """Bound MIME size and part count; expose plain text only."""
    if not raw or len(raw) > MAX_BYTES:
        raise MailboxError('invalid_message_size')
    message = BytesParser(policy=policy.default).parsebytes(raw)
    parts, attachments, text, html = 0, [], [], []
    for part in message.walk():
        parts += 1
        if parts > 100:
            raise MailboxError('mime_part_limit')
        if part.is_multipart():
            continue
        content = part.get_payload(decode=True) or b''
        if (
            part.get_content_disposition() == 'attachment'
            or part.get_filename()
            or part.get_content_type() not in ('text/plain', 'text/html')
        ):
            if len(attachments) >= 20:
                raise MailboxError('attachment_limit')
            attachments.append(artifact_fields(part.get_filename(), content))
        elif part.get_content_type() == 'text/plain':
            try:
                text.append(
                    content.decode(
                        part.get_content_charset() or 'utf-8', errors='replace'
                    )
                )
            except LookupError:
                text.append(content.decode('utf-8', errors='replace'))
        elif part.get_content_type() == 'text/html':
            parser = _PlainHTML()
            parser.feed(content[: 1024 * 1024].decode('utf-8', errors='replace'))
            html.append(''.join(parser.parts))
    # HTML-only mail is reduced to inert text; active markup is never rendered.
    fields = {
        key: [address for _, address in getaddresses(message.get_all(header, []))][:100]
        for key, header in [
            ('sender', 'From'),
            ('to', 'To'),
            ('cc', 'Cc'),
            ('reply_to', 'Reply-To'),
        ]
    }
    fields.update(
        subject=str(message.get('Subject', ''))[:998],
        body='\n'.join(text or html)[: 1024 * 1024],
        rfc_message_id=str(message.get('Message-ID', ''))[:998],
        in_reply_to=str(message.get('In-Reply-To', ''))[:998],
        references=re.findall(
            r'<[^<>\s]+@[^<>\s]+>', str(message.get('References', ''))
        )[:100],
    )
    try:
        date = parsedate_to_datetime(str(message.get('Date', '')))
        fields['provider_date'] = date if timezone.is_aware(date) else None
    except (TypeError, ValueError, OverflowError):
        fields['provider_date'] = None
    return fields, attachments


def _ingest(account, state, change, parsed):
    identity = digest(change.identity)
    message = MailMessage.objects.filter(
        account=account, identity_hash=identity
    ).first()
    location_key = digest(change.location)
    if change.kind == 'flags':
        MailLocation.objects.filter(
            account=account, collection=state.collection, location_hash=location_key
        ).update(is_read=change.is_read, removed=False)
        return
    if change.kind != 'upsert':
        if message:
            MailLocation.objects.filter(
                account=account,
                message=message,
                collection=state.collection,
                location_hash=location_key,
            ).update(removed=True)
            if change.kind == 'delete_message':
                message.deleted = True
                message.save(update_fields=['deleted'])
        return
    fields, attachments = parsed
    if message is None:
        parent_ids = [fields['in_reply_to'], *fields['references']]
        conversations = list(
            MailMessage.objects
            .filter(account=account, rfc_message_id__in=parent_ids)
            .exclude(rfc_message_id='')
            .values_list('conversation_id', flat=True)
            .distinct()[:2]
        )
        conversation_id = (
            conversations[0]
            if len(conversations) == 1
            else MailConversation.objects.create(
                account=account, subject=fields['subject']
            ).pk
        )
        message = MailMessage.objects.create(
            account=account,
            conversation_id=conversation_id,
            identity_hash=identity,
            provider_identity=change.identity,
            raw=change.raw,
            content_hash=digest(change.raw),
            direction='outbound'
            if state.collection == account.options.get('sent', 'Sent')
            else 'inbound',
            **fields,
        )
        for artifact in attachments:
            MailAttachment.objects.create(account=account, message=message, **artifact)
    elif message.content_hash != digest(change.raw) and not message.expired:
        # MIME rewrites remain explicit; frozen drafts are never changed by sync.
        for key, value in fields.items():
            setattr(message, key, value)
        message.raw, message.content_hash, message.deleted = (
            change.raw,
            digest(change.raw),
            False,
        )
        message.save()
        # Keep existing attachment references usable; add new content only once.
        for artifact in attachments:
            if not message.attachments.filter(
                digest=artifact['digest'], filename=artifact['filename']
            ).exists():
                MailAttachment.objects.create(
                    account=account, message=message, **artifact
                )
    MailLocation.objects.update_or_create(
        account=account,
        collection=state.collection,
        location_hash=location_key,
        defaults={
            'message': message,
            'provider_location': change.location,
            'is_read': change.is_read,
            'removed': False,
            'generation': state.generation,
        },
    )


def sync_account(account_id, collection):
    """Fetch one page outside transactions and commit content with its cursor."""
    require_enabled()
    with transaction.atomic():
        account = ConnectedMailbox.objects.select_for_update().get(pk=account_id)
        if not account.enabled or not account.receive_enabled:
            return
        if collection not in {
            account.options.get('inbox', 'Inbox'),
            account.options.get('sent', 'Sent'),
        }:
            raise MailboxError('invalid_collection')
        state, _ = MailSyncState.objects.get_or_create(
            account=account, collection=collection
        )
        now = timezone.now()
        if state.lease_until and state.lease_until > now:
            return
        state.generation += 1
        state.lease_until = now + timedelta(minutes=5)
        state.save(update_fields=['generation', 'lease_until'])
        generation, version = state.generation, account.binding_version
        cursor = (
            state.continuation
            or state.checkpoint
            or {'since': (now - timedelta(days=account.backfill_days)).isoformat()}
        )
    try:
        provider = provider_for(account)
        page = provider.sync(collection, cursor).validate(collection, MAX_BYTES)
        parsed = [
            parse(change.raw) if change.kind == 'upsert' else None
            for change in page.changes
        ]
    except Exception as exc:
        code = exc.code if isinstance(exc, MailboxError) else 'sync_failed'
        updates = {'lease_until': None, 'status': 'error', 'error': code}
        if code in ('cursor_expired', 'uidvalidity_changed'):
            updates.update(
                checkpoint=None,
                continuation=None,
                has_gap=True,
                status='resync_required',
            )
        MailSyncState.objects.filter(
            pk=state.pk, generation=generation, account__binding_version=version
        ).update(**updates)
        return
    with transaction.atomic():
        current_account = ConnectedMailbox.objects.select_for_update().get(
            pk=account_id
        )
        current = MailSyncState.objects.select_for_update().get(pk=state.pk)
        if (
            current.generation != generation
            or current_account.binding_version != version
            or not current_account.enabled
            or not current_account.receive_enabled
        ):
            return
        for change, content in zip(page.changes, parsed, strict=True):
            _ingest(current_account, current, change, content)
        current.continuation = page.continuation
        if page.complete:
            current.checkpoint = page.checkpoint
            current.last_success = timezone.now()
            current_account.verified_receive_at = current.last_success
            current_account.health = (
                'ready'
                if current_account.verified_send_at
                else 'send_verification_required'
            )
            current_account.save(update_fields=['verified_receive_at', 'health'])
        current.has_gap = current.has_gap or page.gap
        current.status = 'current' if page.complete else 'catching_up'
        current.error, current.lease_until = '', None
        if current.coverage_start is None:
            current.coverage_start = now - timedelta(days=account.backfill_days)
        current.save()


def message_data(message):
    """Minimal untrusted correspondence for UI and tools, with no raw MIME."""
    return {
        **{
            key: getattr(message, key)
            for key in (
                'subject',
                'body',
                'sender',
                'to',
                'cc',
                'reply_to',
                'direction',
                'processed',
                'expired',
                'received_at',
            )
        },
        'id': str(message.pk),
        'account_id': str(message.account_id),
        'conversation_id': str(message.conversation_id),
        'content_trust': 'untrusted_email',
        'attachments': list(
            message.attachments.values('id', 'filename', 'size', 'scan_state')
        )
        if not message.expired
        else [],
    }


def expire_content():
    """Remove expired content while retaining minimal deduplication identity."""
    for account in ConnectedMailbox.objects.all().iterator():
        cutoff = timezone.now() - timedelta(days=account.retention_days)
        for message in MailMessage.objects.filter(
            account=account, received_at__lt=cutoff, expired=False
        )[:100]:
            # A pending review owns its frozen bytes until its separate audit lifecycle ends.
            with transaction.atomic():
                message.attachments.filter(drafts__isnull=True).update(
                    content=b'', filename='expired', scan_state='expired'
                )
                MailMessage.objects.filter(pk=message.pk).update(
                    raw=b'',
                    body='',
                    subject='',
                    sender=[],
                    to=[],
                    cc=[],
                    reply_to=[],
                    expired=True,
                )
                # Conversation subjects are copies of message content, not identity.
                MailConversation.objects.filter(pk=message.conversation_id).update(
                    subject=''
                )
        MailAttachment.objects.filter(
            account=account,
            message__isnull=True,
            drafts__isnull=True,
            created_at__lt=cutoff,
        ).update(content=b'', filename='expired', scan_state='expired')


def rescan_quarantine():
    """A recovered scanner can release quarantined artifacts without re-uploading."""
    for artifact in MailAttachment.objects.filter(scan_state='quarantined').order_by(
        'created_at'
    )[:20]:
        content = bytes(artifact.content)
        state = (
            scan_content(content) if digest(content) == artifact.digest else 'infected'
        )
        MailAttachment.objects.filter(
            pk=artifact.pk, digest=artifact.digest, scan_state='quarantined'
        ).update(scan_state=state)
