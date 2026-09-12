"""Single-claim delivery and observation-only recovery of durable operations."""

from dataclasses import asdict

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ai.core.integrations.email.contracts import MailboxError, SendObservation
from aichat.models import ConnectedMailbox, MailDraft, MailReceipt
from approvals.execution import record_result
from approvals.executors import EffectResult
from approvals.models import Approval, ApprovalExecution
from approvals.review_sections import compute_review_hash

from .access import current_user, require_account, require_enabled
from .accounts import provider_for
from .drafts import check_policy, digest, prepared, validate_binding


def publish(operation_id):
    """Queue a durable intent; periodic publication recovers broker failures."""
    from django_q.tasks import async_task

    try:
        async_task('aichat.services.email.dispatch.dispatch_pending', operation_id)
    except Exception:
        # No provider was called here. The committed pending row is the outbox.
        return False
    return True


def authorize(approval, draft, execution):
    """Recheck current human authority, policy, content and mailbox binding."""
    require_enabled()
    if getattr(settings, 'AGENT_EMAIL_SEND_PAUSED', False):
        raise MailboxError('sending_paused')
    ConnectedMailbox.objects.select_for_update().get(pk=draft.account_id)
    account = require_account(execution.actor, draft.account_id, 'send')
    require_account(draft.requester, draft.account_id, 'draft')
    if not current_user(execution.actor).has_perm('approvals.review'):
        raise MailboxError('permission_denied')
    from approvals.review_evidence import require_acknowledgment

    acknowledgment = (
        approval.review_acknowledgments
        .filter(actor=execution.actor, invalidated_at__isnull=True)
        .order_by('-created_at')
        .first()
    )
    if acknowledgment is None:
        raise MailboxError('review_required')
    require_acknowledgment(
        approval, actor=execution.actor, channel=acknowledgment.channel
    )
    if not account.enabled:
        raise MailboxError('account_disabled')
    verification = approval.payload.get('_mailbox', {}).get('verification') is True
    if verification:
        require_account(execution.actor, account.pk, 'admin')
        if not account.recipient_allowlist:
            raise MailboxError('verification_requires_allowlist')
    elif (
        not account.send_enabled
        or not account.verified_send_at
        or not account.verified_receive_at
    ):
        raise MailboxError('verification_required')
    draft.account = account
    validate_binding(approval, draft)
    if (
        execution.review_hash != compute_review_hash(approval)
        or execution.revision != approval.current_revision_number
    ):
        raise MailboxError('review_changed')
    check_policy(account, draft.envelope)
    expected_artifacts = {
        item['id']: item['sha256'] for item in approval.payload.get('attachments', [])
    }
    if set(expected_artifacts) != {
        str(pk) for pk in draft.attachments.values_list('pk', flat=True)
    }:
        raise MailboxError('attachment_changed')
    for artifact in draft.attachments.all():
        if (
            artifact.scan_state != 'clean'
            or digest(bytes(artifact.content)) != artifact.digest
            or artifact.account_id != draft.account_id
            or artifact.digest != expected_artifacts.get(str(artifact.pk))
            or (
                artifact.message_id
                and (artifact.message.expired or artifact.message.deleted)
            )
        ):
            raise MailboxError('attachment_changed')
    if approval.payload.get('reply_message_id'):
        from aichat.models import MailMessage

        if not MailMessage.objects.filter(
            pk=approval.payload['reply_message_id'],
            account=account,
            expired=False,
            deleted=False,
        ).exists():
            raise MailboxError('reply_unavailable')
    return account


@transaction.atomic
def persist(execution, draft, observation):
    """Commit the transport observation and approval result together."""
    approval = Approval.objects.select_for_update().get(pk=execution.approval_id)
    current = ApprovalExecution.objects.select_for_update().get(pk=execution.pk)
    if current.state in ('succeeded', 'failed_before_effect'):
        return approval
    observation.validate(len(draft.envelope))
    if current.state == 'partial' and observation.outcome != 'succeeded':
        return approval
    receipt = MailReceipt.objects.create(
        execution=current,
        account=draft.account,
        fingerprint=draft.fingerprint,
        outcome=observation.outcome,
        evidence=asdict(observation),
    )
    result = EffectResult(
        observation.outcome == 'succeeded',
        f'email-receipt:{receipt.pk}',
        {'receipt_id': str(receipt.pk), **asdict(observation)},
        None
        if observation.outcome == 'succeeded'
        else 'Submission incomplete or unverified; do not resend.',
        outcome=observation.outcome,
    )
    approval = record_result(approval.pk, current.pk, result)
    if (
        observation.outcome == 'succeeded'
        and approval.payload['_mailbox']['verification']
    ):
        ConnectedMailbox.objects.filter(
            pk=draft.account_id, binding_version=draft.binding_version, enabled=True
        ).update(verified_send_at=timezone.now())
    return approval


def dispatch_pending(operation_id):
    """Only the winner of a pending-to-submitting claim can call submit."""
    if getattr(settings, 'AGENT_EMAIL_SEND_PAUSED', False):
        return
    existing = ApprovalExecution.objects.filter(
        pk=operation_id, approval__email_draft__isnull=False
    ).first()
    if existing is None:
        return
    with transaction.atomic():
        approval = Approval.objects.select_for_update().get(pk=existing.approval_id)
        execution = ApprovalExecution.objects.select_for_update().get(pk=operation_id)
        if execution.state != 'pending_dispatch':
            return
        draft = MailDraft.objects.select_related('account', 'requester').get(
            approval=approval
        )
        try:
            account = authorize(approval, draft, execution)
            provider = provider_for(account)
            message = prepared(draft, execution)
            if (
                len(message.raw) > provider.capabilities.max_message_bytes
                or len(message.recipients) > provider.capabilities.max_recipients
            ):
                raise MailboxError('provider_limit_exceeded')
        except Exception:
            return persist(
                execution,
                draft,
                SendObservation(
                    'failed_before_effect',
                    ('rejected',) * len(draft.envelope),
                    'pre_dispatch',
                ),
            )
        # The conditional update also prevents two claims on databases without row locks.
        if not ApprovalExecution.objects.filter(
            pk=execution.pk, state='pending_dispatch'
        ).update(state='submitting', updated_at=timezone.now()):
            return
    # Network I/O is outside the transaction. A crash here is never auto-retried.
    try:
        observation = provider.submit(message).validate(len(message.recipients))
    except Exception:
        observation = SendObservation.unknown(len(message.recipients))
    return persist(execution, draft, observation)


def reconcile(approval, execution):
    """Recovery may inspect the provider, but never invoke submit."""
    if execution.state == 'pending_dispatch':
        publish(execution.pk)
        return approval
    if execution.state in ('succeeded', 'failed_before_effect'):
        return approval
    draft = MailDraft.objects.select_related('account').get(approval=approval)
    if draft.binding_version != draft.account.binding_version:
        return approval
    try:
        require_enabled()
        provider = provider_for(draft.account)
        observation = provider.reconcile(prepared(draft, execution)).validate(
            len(draft.envelope)
        )
    except Exception:
        observation = SendObservation.unknown(len(draft.envelope))
    return persist(execution, draft, observation)
