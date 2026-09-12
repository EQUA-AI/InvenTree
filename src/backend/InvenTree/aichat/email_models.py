"""Account-scoped correspondence, kept separate from notification mail logs."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class ConnectedMailbox(models.Model):
    """An explicitly authorized mailbox with independently paused effects."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    provider = models.CharField(
        max_length=20,
        choices=[(p, p) for p in ('smtp_imap', 'graph', 'google', 'recording')],
    )
    address = models.EmailField()
    aliases = models.JSONField(default=list)
    options = models.JSONField(default=dict)
    encrypted_credentials = models.TextField(blank=True, editable=False)
    binding_version = models.PositiveIntegerField(default=1)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    enabled = models.BooleanField(default=False)
    send_enabled = models.BooleanField(default=False)
    receive_enabled = models.BooleanField(default=False)
    verified_send_at = models.DateTimeField(null=True, blank=True)
    verified_receive_at = models.DateTimeField(null=True, blank=True)
    recipient_allowlist = models.JSONField(default=list, null=True)
    signature = models.TextField(blank=True)
    retention_days = models.PositiveIntegerField(default=30)
    backfill_days = models.PositiveIntegerField(default=30)
    recovery_days = models.PositiveIntegerField(default=90)
    health = models.CharField(max_length=40, default='setup_required')
    oauth_refresh_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class MailboxGrant(models.Model):
    """Independent read, draft, send and account-administration authority."""

    account = models.ForeignKey(
        ConnectedMailbox, on_delete=models.CASCADE, related_name='grants'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE
    )
    group = models.ForeignKey(
        'auth.Group', null=True, blank=True, on_delete=models.CASCADE
    )
    can_read = models.BooleanField(default=False)
    can_draft = models.BooleanField(default=False)
    can_send = models.BooleanField(default=False)
    can_admin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """One grantee type and no duplicate grants within an account."""

        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(user__isnull=False, group__isnull=True)
                    | Q(user__isnull=True, group__isnull=False)
                ),
                name='mailbox_grant_one_subject',
            ),
            models.UniqueConstraint(
                fields=['account', 'user'], name='mailbox_grant_user'
            ),
            models.UniqueConstraint(
                fields=['account', 'group'], name='mailbox_grant_group'
            ),
        ]


class MailboxOAuthAttempt(models.Model):
    """Short-lived, one-use state bound to the initiating administrator."""

    state_hash = models.CharField(max_length=64, primary_key=True)
    account = models.ForeignKey(ConnectedMailbox, on_delete=models.CASCADE)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    binding_version = models.PositiveIntegerField()
    encrypted_verifier = models.TextField(editable=False)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)


class MailConversation(models.Model):
    """A local conversation; RFC headers never grant business-record access."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(ConnectedMailbox, on_delete=models.PROTECT)
    subject = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MailMessage(models.Model):
    """Private message content and stable local identity independent of folders."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(ConnectedMailbox, on_delete=models.PROTECT)
    conversation = models.ForeignKey(MailConversation, on_delete=models.PROTECT)
    identity_hash = models.CharField(max_length=64)
    provider_identity = models.TextField()
    rfc_message_id = models.CharField(max_length=998, blank=True)
    in_reply_to = models.TextField(blank=True)
    references = models.JSONField(default=list)
    sender = models.JSONField(default=list)
    to = models.JSONField(default=list)
    cc = models.JSONField(default=list)
    reply_to = models.JSONField(default=list)
    subject = models.TextField(blank=True)
    body = models.TextField(blank=True)
    raw = models.BinaryField(default=bytes, editable=False)
    content_hash = models.CharField(max_length=64)
    provider_date = models.DateTimeField(null=True)
    received_at = models.DateTimeField(auto_now_add=True)
    direction = models.CharField(max_length=10, default='inbound')
    processed = models.BooleanField(default=False)
    deleted = models.BooleanField(default=False)
    expired = models.BooleanField(default=False)

    class Meta:
        """Provider IDs are case-sensitive through their digest on every DB."""

        constraints = [
            models.UniqueConstraint(
                fields=['account', 'identity_hash'],
                name='mail_message_account_identity',
            )
        ]


class MailLocation(models.Model):
    """Provider folder membership, including independently retained tombstones."""

    account = models.ForeignKey(ConnectedMailbox, on_delete=models.PROTECT)
    message = models.ForeignKey(
        MailMessage, on_delete=models.CASCADE, related_name='locations'
    )
    collection = models.CharField(max_length=255)
    location_hash = models.CharField(max_length=64)
    provider_location = models.TextField()
    is_read = models.BooleanField(default=False)
    removed = models.BooleanField(default=False)
    generation = models.PositiveIntegerField(default=0)

    class Meta:
        """Location keys cannot collide across accounts or monitored folders."""

        constraints = [
            models.UniqueConstraint(
                fields=['account', 'collection', 'location_hash'],
                name='mail_location_account_key',
            )
        ]


class MailAttachment(models.Model):
    """Bounded private artifact; content is inaccessible until scanning passes."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(ConnectedMailbox, on_delete=models.PROTECT)
    message = models.ForeignKey(
        MailMessage, null=True, on_delete=models.CASCADE, related_name='attachments'
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT
    )
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=150)
    digest = models.CharField(max_length=64)
    size = models.PositiveIntegerField()
    content = models.BinaryField(editable=False)
    scan_state = models.CharField(max_length=15, default='quarantined')
    created_at = models.DateTimeField(auto_now_add=True)


class MailDraft(models.Model):
    """The only mailbox binding of an email approval, assigned server-side."""

    approval = models.OneToOneField(
        'approvals.Approval',
        primary_key=True,
        on_delete=models.PROTECT,
        related_name='email_draft',
    )
    account = models.ForeignKey(ConnectedMailbox, on_delete=models.PROTECT)
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    binding_version = models.PositiveIntegerField()
    fingerprint = models.CharField(max_length=64)
    envelope = models.JSONField(default=list)
    sender = models.EmailField()
    rfc_message_id = models.CharField(max_length=255)
    raw = models.BinaryField(editable=False)
    attachments = models.ManyToManyField(
        MailAttachment, blank=True, related_name='drafts'
    )
    created_at = models.DateTimeField(auto_now_add=True)


class MailReceipt(models.Model):
    """Immutable observation backing an execution result, including uncertainty."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    execution = models.ForeignKey(
        'approvals.ApprovalExecution',
        on_delete=models.PROTECT,
        related_name='mail_receipts',
    )
    account = models.ForeignKey(ConnectedMailbox, on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=64)
    outcome = models.CharField(max_length=25)
    evidence = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class MailSyncState(models.Model):
    """A fenced collection cursor with explicit history coverage limitations."""

    account = models.ForeignKey(
        ConnectedMailbox, on_delete=models.CASCADE, related_name='sync_states'
    )
    collection = models.CharField(max_length=255)
    scope_version = models.PositiveIntegerField(default=1)
    generation = models.PositiveIntegerField(default=0)
    continuation = models.JSONField(null=True)
    checkpoint = models.JSONField(null=True)
    lease_until = models.DateTimeField(null=True)
    last_success = models.DateTimeField(null=True)
    coverage_start = models.DateTimeField(null=True)
    status = models.CharField(max_length=25, default='catching_up')
    has_gap = models.BooleanField(default=False)
    error = models.CharField(max_length=60, blank=True)

    class Meta:
        """Each monitored collection owns its own progress and lease."""

        constraints = [
            models.UniqueConstraint(
                fields=['account', 'collection'], name='mail_sync_account_collection'
            )
        ]
