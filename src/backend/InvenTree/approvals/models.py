"""Data models for the AI Agent Approval Queue.

Implements the four core tables specified in the approval queue spec:
- Approval: core approval record with FSM status
- ApprovalEvent: append-only audit log
- ApprovalRevision: structured revision history for modify loop
- ExecutedEffect: idempotency ledger for executed side effects
"""

import hashlib
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

import structlog

logger = structlog.get_logger('approvals')


# ---------------------------------------------------------------------------
# Enums / Choices
# ---------------------------------------------------------------------------


class ApprovalStatus(models.TextChoices):
    """Approval status finite state machine states."""

    PENDING = 'pending', _('Pending')
    IN_REVIEW = 'in_review', _('In Review')
    CHANGES_REQUESTED = 'changes_requested', _('Changes Requested')
    APPROVED = 'approved', _('Approved')
    EXECUTING = 'executing', _('Executing')
    SUCCEEDED = 'succeeded', _('Succeeded')
    DENIED = 'denied', _('Denied')
    FAILED = 'failed', _('Failed')
    EXPIRED = 'expired', _('Expired')
    CANCELED = 'canceled', _('Canceled')


TERMINAL_STATUSES = frozenset({
    ApprovalStatus.SUCCEEDED,
    ApprovalStatus.DENIED,
    ApprovalStatus.FAILED,
    ApprovalStatus.EXPIRED,
    ApprovalStatus.CANCELED,
})


class ActionType(models.TextChoices):
    """Supported approval action types."""

    EMAIL = 'email', _('Send Email')
    PURCHASE_ORDER = 'purchase_order', _('Create Purchase Order')
    SALES_ORDER = 'sales_order', _('Create Sales Order')
    STOCK_UPDATE = 'stock_update', _('Update Stock')
    WORKFLOW = 'workflow', _('Run Workflow')
    NOTIFICATION = 'notification', _('Send Notification')
    SAFETY_GATE = 'safety_gate', _('Safety Gate')
    PROCEDURE_PUBLISH = 'procedure_publish', _('Publish Procedure')
    JOB_KIT_SUBSTITUTION = ('job_kit_substitution', _('Approve Job Kit Substitution'))


class EventType(models.TextChoices):
    """Approval event types for the audit log."""

    CREATED = 'created', _('Created')
    OPENED = 'opened', _('Opened')
    VIEWED_CONFIRMED = 'viewed_confirmed', _('Viewed Confirmed')
    CHANGES_REQUESTED = 'changes_requested', _('Changes Requested')
    REVISED = 'revised', _('Revised')
    SUPERSEDED = 'superseded', _('Superseded')
    APPROVED = 'approved', _('Approved')
    DENIED = 'denied', _('Denied')
    EXECUTING = 'executing', _('Executing')
    SUCCEEDED = 'succeeded', _('Succeeded')
    FAILED = 'failed', _('Failed')
    EXPIRED = 'expired', _('Expired')
    CANCELED = 'canceled', _('Canceled')
    CANCEL_REVERTED = 'cancel_reverted', _('Cancel Reverted')
    REVALIDATION_FAILED = 'revalidation_failed', _('Revalidation Failed')
    RESUME_FAILED = 'resume_failed', _('Resume Failed')
    EXECUTION_CALLBACK = 'execution_callback', _('Execution Callback')
    LOCK_ACQUIRED = 'lock_acquired', _('Lock Acquired')
    LOCK_RELEASED = 'lock_released', _('Lock Released')


# ---------------------------------------------------------------------------
# State Machine Transitions
# ---------------------------------------------------------------------------

# Maps (from_status) -> set of allowed (to_status) values
VALID_TRANSITIONS: dict[str, set[str]] = {
    ApprovalStatus.PENDING: {
        ApprovalStatus.IN_REVIEW,
        ApprovalStatus.CANCELED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.FAILED,  # Agent orphaned / cleanup path
    },
    ApprovalStatus.IN_REVIEW: {
        ApprovalStatus.CHANGES_REQUESTED,
        ApprovalStatus.APPROVED,
        ApprovalStatus.DENIED,
        ApprovalStatus.CANCELED,
        ApprovalStatus.EXPIRED,
    },
    ApprovalStatus.CHANGES_REQUESTED: {
        ApprovalStatus.IN_REVIEW,
        ApprovalStatus.DENIED,
        ApprovalStatus.CANCELED,
        ApprovalStatus.EXPIRED,
    },
    ApprovalStatus.APPROVED: {ApprovalStatus.EXECUTING, ApprovalStatus.FAILED},
    ApprovalStatus.EXECUTING: {ApprovalStatus.SUCCEEDED, ApprovalStatus.FAILED},
}


def is_valid_transition(from_status: str, to_status: str) -> bool:
    """Check whether a status transition is allowed by the FSM."""
    allowed = VALID_TRANSITIONS.get(from_status, set())
    return to_status in allowed


def compute_idempotency_key(agent_run_id: str, tool_call_id: str) -> str:
    """Compute deterministic idempotency key: SHA-256(agent_run_id + ':' + tool_call_id)."""
    raw = f'{agent_run_id}:{tool_call_id}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def _get_setting(name: str, default):
    """Get a setting from Django settings with a default fallback."""
    return getattr(settings, name, default)


def get_default_expiry_days() -> int:
    """Get default expiry days."""
    return _get_setting('APPROVAL_DEFAULT_EXPIRY_DAYS', 7)


def get_lock_ttl_seconds() -> int:
    """Get lock ttl seconds."""
    return _get_setting('APPROVAL_MODIFY_LOCK_TTL_SECONDS', 600)


def get_baseline_stale_threshold_hours() -> int:
    """Get baseline stale threshold hours."""
    return _get_setting('APPROVAL_BASELINE_STALE_THRESHOLD_HOURS', 24)


def get_retention_days() -> int:
    """Get retention days."""
    return _get_setting('APPROVAL_RETENTION_DAYS', 90)


def is_approval_queue_enabled() -> bool:
    """Is approval queue enabled."""
    return _get_setting('APPROVAL_QUEUE_ENABLED', False)


def is_modify_in_chat_enabled() -> bool:
    """Is modify in chat enabled."""
    return _get_setting('APPROVAL_MODIFY_IN_CHAT_ENABLED', False)


def is_revalidation_enabled() -> bool:
    """Is revalidation enabled."""
    return _get_setting('APPROVAL_REVALIDATION_ENABLED', True)


def is_expiry_job_enabled() -> bool:
    """Is expiry job enabled."""
    return _get_setting('APPROVAL_EXPIRY_JOB_ENABLED', True)


def is_retention_purge_enabled() -> bool:
    """Is retention purge enabled."""
    return _get_setting('APPROVAL_RETENTION_PURGE_ENABLED', False)


def get_resume_stuck_threshold_seconds() -> int:
    """Get resume stuck threshold seconds."""
    return _get_setting('APPROVAL_RESUME_STUCK_THRESHOLD_SECONDS', 300)


def get_execution_stuck_threshold_seconds() -> int:
    """Get execution stuck threshold seconds."""
    return _get_setting('APPROVAL_EXECUTION_STUCK_THRESHOLD_SECONDS', 1800)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Approval(models.Model):
    """Core approval record — one per approval-required tool invocation.

    Implements the approvals table from the spec (Section 16.1).
    """

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_('ID')
    )

    # --- Status FSM ---
    status = models.CharField(
        max_length=30,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
        db_index=True,
        verbose_name=_('Status'),
    )

    risk_tier = models.SmallIntegerField(
        default=2,
        verbose_name=_('Risk Tier'),
        help_text=_('0-3, higher = more dangerous'),
    )

    action_type = models.CharField(
        max_length=30,
        choices=ActionType.choices,
        db_index=True,
        verbose_name=_('Action Type'),
    )

    summary = models.TextField(
        verbose_name=_('Summary'),
        help_text=_('Human-readable one-line summary of the proposed action'),
    )

    # --- Payload ---
    payload = models.JSONField(
        verbose_name=_('Payload'), help_text=_('Current draft arguments (JSONB)')
    )

    payload_schema_version = models.IntegerField(
        default=1, verbose_name=_('Payload Schema Version')
    )

    # --- Correlation ---
    source_chat_id = models.CharField(
        max_length=255, blank=True, default='', verbose_name=_('Source Chat ID')
    )

    agent_run_id = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name=_('Agent Run ID'),
        help_text=_('Durable workflow instance correlation'),
    )

    agent_checkpoint_id = models.CharField(
        max_length=255, verbose_name=_('Agent Checkpoint ID')
    )

    tool_call_id = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name=_('Tool Call ID'),
        help_text=_('Identifies approval request in the agent run'),
    )

    assigned_to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_approvals',
        verbose_name=_('Assigned To'),
    )

    # --- Timestamps ---
    created_at = models.DateTimeField(
        auto_now_add=True, db_index=True, verbose_name=_('Created At')
    )

    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    expires_at = models.DateTimeField(
        null=True, blank=True, db_index=True, verbose_name=_('Expires At')
    )

    # --- Safety / context ---
    baseline_context = models.JSONField(
        default=dict,
        verbose_name=_('Baseline Context'),
        help_text=_('Snapshot for drift detection'),
    )

    preconditions = models.JSONField(
        default=dict,
        verbose_name=_('Preconditions'),
        help_text=_('Machine-check rules'),
    )

    card_context = models.JSONField(
        default=dict,
        verbose_name=_('Card Context'),
        help_text=_('Self-contained bundle for Modify-in-chat'),
    )

    # --- Idempotency ---
    idempotency_key = models.CharField(
        max_length=64,
        unique=True,
        verbose_name=_('Idempotency Key'),
        help_text=_('SHA-256(agent_run_id:tool_call_id)'),
    )

    # --- Viewed-confirmed gate ---
    viewed_confirmed_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_('Viewed Confirmed At')
    )

    viewed_confirmed_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='viewed_confirmed_approvals',
        verbose_name=_('Viewed Confirmed By'),
    )

    # --- Revision tracking (denormalized) ---
    current_revision_number = models.IntegerField(
        default=0,
        verbose_name=_('Current Revision Number'),
        help_text=_('Denormalized for optimistic concurrency on /revise'),
    )

    # --- Modify-in-chat lock ---
    modification_lock_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='locked_approvals',
        verbose_name=_('Modification Lock User'),
    )

    modification_lock_acquired_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_('Lock Acquired At')
    )

    modification_lock_expires_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_('Lock Expires At')
    )

    # --- Denormalized terminal fields ---
    deny_reason = models.TextField(
        blank=True, default='', verbose_name=_('Deny Reason')
    )

    canceled_reason = models.TextField(
        blank=True, default='', verbose_name=_('Canceled Reason')
    )

    execution_result = models.JSONField(
        null=True, blank=True, verbose_name=_('Execution Result')
    )

    execution_error = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_('Execution Error'),
        help_text=_('Redacted error details'),
    )

    resolved_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_('Resolved At')
    )

    resolved_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='resolved_approvals',
        verbose_name=_('Resolved By'),
    )

    class Meta:
        """Model metadata."""

        ordering = ['-created_at']
        verbose_name = _('Approval')
        verbose_name_plural = _('Approvals')
        permissions = [('review', 'Can review and act on approvals')]
        indexes = [
            models.Index(
                fields=['status', 'created_at'], name='idx_approvals_status_created'
            ),
            models.Index(
                fields=['action_type', 'created_at'],
                name='idx_approvals_action_created',
            ),
            models.Index(
                fields=['assigned_to_user', 'status'],
                name='idx_approvals_assigned_status',
            ),
            models.Index(
                fields=['expires_at'],
                name='idx_approvals_expires_at',
                condition=models.Q(expires_at__isnull=False),
            ),
        ]

    def __str__(self):
        """Readable identity for admin and logs."""
        return f'Approval {self.id} [{self.status}] - {self.summary[:60]}'

    # ---- Properties ----

    @property
    def is_terminal(self) -> bool:
        """Whether this approval is in a terminal status."""
        return self.status in TERMINAL_STATUSES

    @property
    def is_lock_active(self) -> bool:
        """Whether the modification lock is currently active (not expired)."""
        if not self.modification_lock_user_id or not self.modification_lock_expires_at:
            return False
        return timezone.now() < self.modification_lock_expires_at

    @property
    def lock_holder_id(self):
        """The user ID of the lock holder, or None if no active lock."""
        if self.is_lock_active:
            return self.modification_lock_user_id
        return None

    # ---- FSM helpers ----

    def can_transition_to(self, new_status: str) -> bool:
        """Check if transitioning to new_status is valid."""
        return is_valid_transition(self.status, new_status)

    def transition_to(
        self,
        new_status: str,
        actor_user=None,
        event_payload=None,
        extra_update_fields=None,
    ):
        """Perform a state transition with event logging.

        Args:
            new_status: Target status.
            actor_user: User performing the action.
            event_payload: Optional payload for the audit event.
            extra_update_fields: Additional model fields to save atomically
                with the transition (e.g. ['deny_reason', 'execution_error']).

        Raises ValueError if the transition is invalid or called outside a
        transaction.
        """
        from django.db import connection

        if not connection.in_atomic_block:
            raise RuntimeError(
                'transition_to() must be called within a transaction.atomic() block'
            )

        if not self.can_transition_to(new_status):
            raise ValueError(f'Invalid transition: {self.status} → {new_status}')

        old_status = self.status
        self.status = new_status
        self.updated_at = timezone.now()

        # Set resolved fields for terminal transitions
        if new_status in TERMINAL_STATUSES:
            self.resolved_at = timezone.now()
            if actor_user:
                self.resolved_by_user = actor_user

        fields_to_save = ['status', 'updated_at', 'resolved_at', 'resolved_by_user']
        if extra_update_fields:
            fields_to_save.extend(extra_update_fields)

        self.save(update_fields=fields_to_save)

        # Create audit event
        event_type_map = {
            ApprovalStatus.IN_REVIEW: EventType.OPENED,
            ApprovalStatus.CHANGES_REQUESTED: EventType.CHANGES_REQUESTED,
            ApprovalStatus.APPROVED: EventType.APPROVED,
            ApprovalStatus.DENIED: EventType.DENIED,
            ApprovalStatus.EXECUTING: EventType.EXECUTING,
            ApprovalStatus.SUCCEEDED: EventType.SUCCEEDED,
            ApprovalStatus.FAILED: EventType.FAILED,
            ApprovalStatus.EXPIRED: EventType.EXPIRED,
            ApprovalStatus.CANCELED: EventType.CANCELED,
        }

        event_type = event_type_map.get(new_status, EventType.CREATED)

        ApprovalEvent.objects.create(
            approval=self,
            event_type=event_type,
            actor_user=actor_user,
            event_payload=event_payload
            or {'from_status': old_status, 'to_status': new_status},
        )

        logger.info(
            'approval_transition',
            approval_id=str(self.id),
            action_type=self.action_type,
            risk_tier=self.risk_tier,
            from_status=old_status,
            to_status=new_status,
            actor_user_id=getattr(actor_user, 'pk', None),
        )

        return self

    # ---- Lock helpers ----

    def acquire_lock(self, user) -> dict:
        """Acquire the modification lock for a user.

        Returns lock metadata dict.
        Raises ValueError if locked by another user.
        """
        now = timezone.now()

        # If locked by same user, extend the lease
        if self.is_lock_active and self.modification_lock_user_id == user.pk:
            ttl = get_lock_ttl_seconds()
            self.modification_lock_expires_at = now + timezone.timedelta(seconds=ttl)
            self.save(update_fields=['modification_lock_expires_at', 'updated_at'])
            return self._lock_metadata()

        # If locked by another user and not expired
        if self.is_lock_active:
            raise ValueError(
                f'Approval is locked by user {self.modification_lock_user_id} '
                f'until {self.modification_lock_expires_at}'
            )

        # Acquire new lock
        ttl = get_lock_ttl_seconds()
        self.modification_lock_user = user
        self.modification_lock_acquired_at = now
        self.modification_lock_expires_at = now + timezone.timedelta(seconds=ttl)
        self.save(
            update_fields=[
                'modification_lock_user',
                'modification_lock_acquired_at',
                'modification_lock_expires_at',
                'updated_at',
            ]
        )

        # Emit lock_acquired event
        ApprovalEvent.objects.create(
            approval=self,
            event_type=EventType.LOCK_ACQUIRED,
            actor_user=user,
            event_payload={
                'holder_user_id': user.pk,
                'expires_at': self.modification_lock_expires_at.isoformat(),
            },
        )

        return self._lock_metadata()

    def release_lock(self, user, force=False):
        """Release the modification lock.

        Only the holder or an admin (force=True) can release.
        Idempotent — safe to call when no lock is held.
        """
        if not self.modification_lock_user_id:
            return  # Already released

        if not force and self.modification_lock_user_id != user.pk:
            raise ValueError('Only the lock holder or an admin can release the lock')

        self.modification_lock_user = None
        self.modification_lock_acquired_at = None
        self.modification_lock_expires_at = None
        self.save(
            update_fields=[
                'modification_lock_user',
                'modification_lock_acquired_at',
                'modification_lock_expires_at',
                'updated_at',
            ]
        )

        ApprovalEvent.objects.create(
            approval=self,
            event_type=EventType.LOCK_RELEASED,
            actor_user=user,
            event_payload={'released_by': user.pk},
        )

    def _lock_metadata(self) -> dict:
        """Return lock metadata for API responses."""
        return {
            'holder_user_id': self.modification_lock_user_id,
            'acquired_at': (
                self.modification_lock_acquired_at.isoformat()
                if self.modification_lock_acquired_at
                else None
            ),
            'expires_at': (
                self.modification_lock_expires_at.isoformat()
                if self.modification_lock_expires_at
                else None
            ),
        }

    def check_lock_allows_action(self, user, action: str = 'approve'):
        """Check that modification lock does not block this user.

        Raises ValueError if the lock blocks the action.
        """
        if not self.is_lock_active:
            return  # No active lock

        if self.modification_lock_user_id == user.pk:
            return  # Lock holder is allowed

        raise ValueError(
            f'Approval is being modified by user {self.modification_lock_user_id}. '
            f'{action.capitalize()} is blocked until the modification lock is released.'
        )


class ApprovalEvent(models.Model):
    """Append-only audit log for approval state changes.

    Implements the approval_events table from the spec (Section 16.2).
    """

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_('ID')
    )

    approval = models.ForeignKey(
        Approval,
        on_delete=models.CASCADE,
        related_name='events',
        verbose_name=_('Approval'),
    )

    event_type = models.CharField(
        max_length=30,
        choices=EventType.choices,
        db_index=True,
        verbose_name=_('Event Type'),
    )

    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_('Actor User'),
    )

    timestamp = models.DateTimeField(
        auto_now_add=True, db_index=True, verbose_name=_('Timestamp')
    )

    event_payload = models.JSONField(
        default=dict,
        verbose_name=_('Event Payload'),
        help_text=_('Diffs, reasons, errors, metadata'),
    )

    class Meta:
        """Model metadata."""

        ordering = ['timestamp']
        verbose_name = _('Approval Event')
        verbose_name_plural = _('Approval Events')
        indexes = [
            models.Index(
                fields=['approval', 'timestamp'], name='idx_events_approval_timestamp'
            )
        ]

    def __str__(self):
        """Readable identity for admin and logs."""
        return f'{self.event_type} on {self.approval_id} at {self.timestamp}'


class ApprovalRevision(models.Model):
    """Structured revision history for the modify loop.

    Implements the approval_revisions table from the spec (Section 16.3).
    Revision 0 is system-generated at creation; user revisions start at 1.
    """

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_('ID')
    )

    approval = models.ForeignKey(
        Approval,
        on_delete=models.CASCADE,
        related_name='revisions',
        verbose_name=_('Approval'),
    )

    revision_number = models.IntegerField(
        verbose_name=_('Revision Number'),
        help_text=_('0 = initial snapshot at creation; 1+ = user revisions'),
    )

    payload_snapshot = models.JSONField(verbose_name=_('Payload Snapshot'))

    diff_summary = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_('Diff Summary'),
        help_text=_('NULL for revision 0 (initial snapshot at creation)'),
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    created_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_('Created By'),
        help_text=_('NULL for revision 0 (system-generated)'),
    )

    class Meta:
        """Model metadata."""

        ordering = ['revision_number']
        verbose_name = _('Approval Revision')
        verbose_name_plural = _('Approval Revisions')
        unique_together = [('approval', 'revision_number')]

    def __str__(self):
        """Readable identity for admin and logs."""
        return f'Rev {self.revision_number} of {self.approval_id}'


class ExecutedEffect(models.Model):
    """Idempotency ledger for executed side effects.

    Implements the executed_effects table from the spec (Section 16.4).
    """

    idempotency_key = models.CharField(
        max_length=64, primary_key=True, verbose_name=_('Idempotency Key')
    )

    approval = models.ForeignKey(
        Approval,
        on_delete=models.CASCADE,
        related_name='executed_effects',
        verbose_name=_('Approval'),
    )

    effect_type = models.CharField(max_length=100, verbose_name=_('Effect Type'))

    effect_ref = models.CharField(
        max_length=255,
        verbose_name=_('Effect Reference'),
        help_text=_('Reference to created object/message'),
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        """Model metadata."""

        verbose_name = _('Executed Effect')
        verbose_name_plural = _('Executed Effects')

    def __str__(self):
        """Readable identity for admin and logs."""
        return f'{self.effect_type}: {self.effect_ref} ({self.idempotency_key[:12]}...)'
