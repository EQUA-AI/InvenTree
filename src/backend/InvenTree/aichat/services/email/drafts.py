"""Freeze the complete reviewed message before granting dispatch authority."""

import hashlib
import json
import re
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ai.core.integrations.email.contracts import MailboxError, PreparedEmail
from ai.core.integrations.email.policy import check_recipients, normalize_recipients
from aichat.models import ConnectedMailbox, MailAttachment, MailDraft, MailMessage
from approvals.models import Approval, compute_idempotency_key
from approvals.sanitizers import sanitize_email_html
from approvals.serializers import ApprovalCreateSerializer

from .access import current_user, require_account, require_enabled


def digest(value):
    """Hash canonical JSON or exact frozen bytes."""
    if not isinstance(value, bytes):
        value = json.dumps(
            value, sort_keys=True, separators=(',', ':'), ensure_ascii=False
        ).encode()
    return hashlib.sha256(value).hexdigest()


def check_policy(account, recipients):
    """Apply deployment and account restrictions to the entire envelope."""
    policy = account.recipient_allowlist
    if not recipients or not check_recipients(recipients).allowed:
        raise MailboxError('blocked_by_policy')
    if (
        policy is not None
        and not check_recipients(
            recipients, environ={'AIMMS_EMAIL_RECIPIENT_ALLOWLIST': ','.join(policy)}
        ).allowed
    ):
        raise MailboxError('blocked_by_policy')


def _header(value, limit=998):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(c in value for c in ('\r', '\n', '\x00'))
    ):
        raise MailboxError('invalid_header')
    return value


def prepare(actor, account, data, operation_id):
    """Resolve all references within this mailbox and build immutable plain MIME."""
    from .accounts import capabilities

    limits = capabilities(account)
    if not isinstance(data, dict) or set(data) - {
        'sender',
        'to',
        'cc',
        'bcc',
        'reply_to',
        'subject',
        'body',
        'attachment_ids',
        'reply_message_id',
        'verification',
    }:
        raise MailboxError('invalid_draft')
    try:
        headers = {
            key: list(dict.fromkeys(normalize_recipients(data.get(key))))
            for key in ('to', 'cc', 'bcc', 'reply_to')
        }
        senders = normalize_recipients(data.get('sender', account.address))
    except ValueError:
        raise MailboxError('invalid_header') from None
    if len(senders) != 1 or senders[0] not in [account.address, *account.aliases]:
        raise MailboxError('unauthorized_sender')
    recipients = list(dict.fromkeys(headers['to'] + headers['cc'] + headers['bcc']))
    if len(recipients) > 100:
        raise MailboxError('too_many_recipients')
    check_policy(account, recipients)
    subject = _header(data.get('subject', ''))
    body = data.get('body', '')
    if not isinstance(body, str) or len(body.encode()) > 1024 * 1024 or '\x00' in body:
        raise MailboxError('invalid_body')
    body = sanitize_email_html(
        body + ('\n\n' + account.signature if account.signature else '')
    )
    verification = data.get('verification', False)
    if not isinstance(verification, bool):
        raise MailboxError('invalid_draft')
    if verification:
        require_account(actor, account.pk, 'admin')
        if not account.recipient_allowlist:
            raise MailboxError('verification_requires_allowlist')
    domain = getattr(settings, 'AGENT_EMAIL_MESSAGE_ID_DOMAIN', '')
    if not re.fullmatch(r'[a-zA-Z0-9.-]+\.[a-zA-Z]{2,63}', domain):
        raise MailboxError('message_id_domain_required')
    reference = f'<{operation_id}@{domain}>'
    message = EmailMessage(policy=SMTP)
    message['From'] = senders[0]
    for key, label in [('to', 'To'), ('cc', 'Cc'), ('reply_to', 'Reply-To')]:
        if headers[key]:
            message[label] = ', '.join(headers[key])
    message['Subject'] = subject
    message['Message-ID'] = reference
    message['Date'] = format_datetime(timezone.now())
    parent_id = data.get('reply_message_id')
    if parent_id:
        parent = MailMessage.objects.filter(
            pk=parent_id, account=account, expired=False, deleted=False
        ).first()
        if parent is None or not re.fullmatch(
            r'<[^<>\s]+@[^<>\s]+>', parent.rfc_message_id
        ):
            raise MailboxError('reply_unavailable')
        message['In-Reply-To'] = _header(parent.rfc_message_id)
        references = list(
            dict.fromkeys([*parent.references[-19:], parent.rfc_message_id])
        )
        message['References'] = _header(' '.join(references))
    message.set_content(body)
    ids = data.get('attachment_ids', [])
    if not isinstance(ids, list) or len(ids) > 20 or len(set(ids)) != len(ids):
        raise MailboxError('invalid_attachments')
    attachments = list(
        MailAttachment.objects.filter(
            pk__in=ids, account=account, scan_state='clean'
        ).order_by('pk')
    )
    if len(attachments) != len(ids):
        raise MailboxError('attachment_unavailable')
    for artifact in attachments:
        raw = bytes(artifact.content)
        if digest(raw) != artifact.digest or len(raw) != artifact.size:
            raise MailboxError('attachment_changed')
        # An upload remains private to its creator until it is attached to a message or review.
        if (
            artifact.message_id is None
            and artifact.uploaded_by_id != actor.pk
            and not artifact.drafts.exists()
        ):
            raise MailboxError('attachment_unavailable')
        if artifact.message_id and (
            artifact.message.expired or artifact.message.deleted
        ):
            raise MailboxError('attachment_unavailable')
        message.add_attachment(
            raw,
            maintype='application',
            subtype='octet-stream',
            filename=_header(artifact.filename, 255),
        )
    raw = message.as_bytes()
    if len(raw) > limits['max_message_bytes']:
        raise MailboxError('message_too_large')
    payload = {
        **headers,
        'sender': senders[0],
        'subject': subject,
        'body': body,
        'reply_message_id': str(parent_id) if parent_id else None,
        'attachments': [
            {
                'id': str(a.pk),
                'filename': a.filename,
                'size': a.size,
                'sha256': a.digest,
            }
            for a in attachments
        ],
        '_mailbox': {
            'account_id': str(account.pk),
            'name': account.name,
            'binding_version': account.binding_version,
            'raw_sha256': digest(raw),
            'input_hash': digest(data),
            'signature': account.signature,
            'verification': verification,
        },
    }
    fingerprint = digest(payload)
    payload['_mailbox']['fingerprint'] = fingerprint
    return payload, raw, recipients, reference, attachments


@transaction.atomic
def create_draft(actor, account_id, data, request_id):
    """Idempotently create an approval; this operation cannot send mail."""
    require_enabled()
    user = current_user(actor)
    require_account(user, account_id, 'draft')
    account = ConnectedMailbox.objects.select_for_update().get(pk=account_id)
    if not account.enabled:
        raise MailboxError('account_disabled')
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        raise MailboxError('request_id_required')
    run_id = f'mailbox:{account.pk}:{user.pk}'
    operation_id = compute_idempotency_key(run_id, request_id)
    existing = Approval.objects.filter(idempotency_key=operation_id).first()
    if existing:
        if not MailDraft.objects.filter(
            approval=existing, account=account, requester=user
        ).exists() or existing.payload['_mailbox']['input_hash'] != digest(data):
            raise MailboxError('idempotency_conflict')
        return existing
    payload, raw, envelope, reference, artifacts = prepare(
        user, account, data, operation_id
    )
    serializer = ApprovalCreateSerializer(
        data={
            'tool_call_id': request_id,
            'agent_run_id': run_id,
            'agent_checkpoint_id': 'mailbox',
            'action_type': 'email',
            'summary': f'Email from {account.address}: {payload["subject"]}',
            'payload': payload,
        },
        context={'mailbox_service': True},
    )
    serializer.is_valid(raise_exception=True)
    approval = serializer.save()
    draft = MailDraft.objects.create(
        approval=approval,
        account=account,
        requester=user,
        binding_version=account.binding_version,
        fingerprint=payload['_mailbox']['fingerprint'],
        envelope=envelope,
        sender=payload['sender'],
        rfc_message_id=reference,
        raw=raw,
    )
    draft.attachments.set(artifacts)
    return approval


def prepared(draft, execution):
    """Rehydrate the frozen envelope, never reconstruct it from model text."""
    return PreparedEmail(
        str(draft.account_id),
        str(execution.pk),
        draft.sender,
        tuple(draft.envelope),
        draft.rfc_message_id,
        bytes(draft.raw),
        draft.fingerprint,
    )


def validate_binding(approval, draft):
    """Detect changes to either the reviewed content or private frozen bytes."""
    payload = json.loads(json.dumps(approval.payload))
    metadata = payload.get('_mailbox', {})
    fingerprint = metadata.pop('fingerprint', None)
    if (
        fingerprint != draft.fingerprint
        or digest(payload) != draft.fingerprint
        or digest(bytes(draft.raw)) != metadata.get('raw_sha256')
    ):
        raise MailboxError('draft_changed')
    if (
        str(draft.account_id) != metadata.get('account_id')
        or draft.binding_version != draft.account.binding_version
    ):
        raise MailboxError('binding_changed')
    envelope = list(dict.fromkeys(payload['to'] + payload['cc'] + payload['bcc']))
    if draft.envelope != envelope or draft.sender != payload['sender']:
        raise MailboxError('draft_changed')


def revise_draft(approval, actor, payload):
    """Rebuild private bytes in the approval's revision transaction."""
    draft = approval.email_draft
    account = require_account(actor, draft.account_id, 'draft')
    data = {
        key: payload.get(key)
        for key in (
            'sender',
            'to',
            'cc',
            'bcc',
            'reply_to',
            'subject',
            'body',
            'reply_message_id',
        )
    }
    data['attachment_ids'] = [item['id'] for item in payload.get('attachments', [])]
    data['verification'] = approval.payload['_mailbox']['verification']
    old_signature = approval.payload['_mailbox'].get('signature')
    if old_signature and data['body'].endswith('\n\n' + old_signature):
        data['body'] = data['body'][: -(len(old_signature) + 2)]
    updated, raw, envelope, reference, artifacts = prepare(
        actor, account, data, approval.idempotency_key
    )
    draft.raw, draft.envelope, draft.sender = raw, envelope, updated['sender']
    draft.fingerprint, draft.binding_version = (
        updated['_mailbox']['fingerprint'],
        account.binding_version,
    )
    draft.rfc_message_id = reference
    draft.save()
    draft.attachments.set(artifacts)
    return updated
