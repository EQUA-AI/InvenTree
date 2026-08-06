"""Closeout Automation models (Feature #15).

The structured ``WorkOrderCloseout`` remains the authoritative record; the
models here own the layers in front of it (narrative capture, schema-versioned
extraction proposals, per-field human decisions, parts-usage reconciliation,
readings) and behind it (durable post-commit effect intents, governed learning
drafts, append-only amendments). Nothing in this module moves stock, changes a
lifecycle, or alters safety state.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

CLOSEOUT_EXTRACTION_SCHEMA_VERSION = 1


class CloseoutSourceType(models.TextChoices):
    """Provenance of a closeout narrative."""

    TYPED = 'typed', _('Typed')
    VOICE = 'voice', _('Reviewed Voice Transcript')


class CloseoutCaptureStatus(models.TextChoices):
    """Lifecycle of a narrative capture; truth only via completion."""

    OPEN = 'open', _('Open')
    EXTRACTING = 'extracting', _('Extracting')
    PROPOSED = 'proposed', _('Proposed')
    REVIEWED = 'reviewed', _('Reviewed')
    CONSUMED = 'consumed', _('Consumed by Completion')
    ABANDONED = 'abandoned', _('Abandoned')


# Capture states that still occupy the single in-flight slot per work order.
ACTIVE_CAPTURE_STATUSES = (
    CloseoutCaptureStatus.OPEN,
    CloseoutCaptureStatus.EXTRACTING,
    CloseoutCaptureStatus.PROPOSED,
    CloseoutCaptureStatus.REVIEWED,
)


class CloseoutCapture(models.Model):
    """Source envelope for one closeout narrative; never truth by itself."""

    work_order = models.ForeignKey(
        'tasks.WorkOrder', on_delete=models.PROTECT, related_name='closeout_captures'
    )
    status = models.CharField(
        max_length=16,
        choices=CloseoutCaptureStatus.choices,
        default=CloseoutCaptureStatus.OPEN,
        db_index=True,
    )
    source_type = models.CharField(
        max_length=16,
        choices=CloseoutSourceType.choices,
        default=CloseoutSourceType.TYPED,
    )
    transcript_reference = models.CharField(max_length=128, null=True, blank=True)
    current_revision = models.ForeignKey(
        'tasks.CloseoutCaptureRevision',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    completed_closeout = models.OneToOneField(
        'tasks.WorkOrderCloseout',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='source_capture',
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Constraints, indexes, and the additive closeout permission set."""

        permissions = [
            ('capture_closeout', 'Can capture closeout narratives'),
            ('review_closeout', 'Can review closeout proposals'),
            ('reconcile_closeout_parts', 'Can reconcile closeout part usage'),
            ('verify_closeout', 'Can verify completed closeouts'),
            ('amend_closeout', 'Can amend completed closeouts'),
            ('view_closeout_audit', 'Can view closeout audit surfaces'),
        ]
        indexes = [
            models.Index(
                fields=['work_order', 'status'], name='tasks_closeout_cap_wo_status'
            )
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['work_order', 'transcript_reference'],
                name='tasks_closeout_voice_ref_uniq',
            ),
            models.CheckConstraint(
                condition=~Q(source_type='voice')
                | (Q(transcript_reference__isnull=False) & ~Q(transcript_reference='')),
                name='tasks_closeout_voice_ref_req',
            ),
        ]

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'CloseoutCapture#{self.pk} (wo {self.work_order_id}, {self.status})'


class CloseoutCaptureRevision(models.Model):
    """One immutable narrative snapshot; edits append, never mutate."""

    capture = models.ForeignKey(
        CloseoutCapture, on_delete=models.CASCADE, related_name='revisions'
    )
    revision = models.PositiveIntegerField()
    narrative = models.TextField()
    source_content_hash = models.CharField(max_length=64)
    work_order_version = models.PositiveIntegerField()
    supersedes = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='superseded_by',
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """One monotonic revision number per capture."""

        constraints = [
            models.UniqueConstraint(
                fields=['capture', 'revision'], name='tasks_closeout_cap_rev_uniq'
            )
        ]

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'CloseoutCaptureRevision#{self.pk} (r{self.revision})'


class CloseoutProposalStatus(models.TextChoices):
    """Review lifecycle of one extraction proposal."""

    PROPOSED = 'proposed', _('Proposed')
    REVIEWED = 'reviewed', _('Reviewed')
    SUPERSEDED = 'superseded', _('Superseded')


class CloseoutProposal(models.Model):
    """Schema-versioned extraction output; untrusted until humanly decided."""

    capture_revision = models.ForeignKey(
        CloseoutCaptureRevision, on_delete=models.PROTECT, related_name='proposals'
    )
    schema_version = models.PositiveSmallIntegerField()
    extractor = models.CharField(max_length=64)
    model_provenance = models.JSONField(default=dict, blank=True)
    fields = models.JSONField()
    part_candidates = models.JSONField(default=list, blank=True)
    reading_candidates = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    content_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16,
        choices=CloseoutProposalStatus.choices,
        default=CloseoutProposalStatus.PROPOSED,
        db_index=True,
    )
    supersedes = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """At most one live (non-superseded) proposal per capture revision."""

        constraints = [
            models.UniqueConstraint(
                fields=['capture_revision'],
                condition=~Q(status='superseded'),
                name='tasks_closeout_prop_live_uniq',
            )
        ]

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'CloseoutProposal#{self.pk} ({self.status})'


class CloseoutFieldDecision(models.Model):
    """The explicit human promotion decision for one proposal field."""

    proposal = models.ForeignKey(
        CloseoutProposal, on_delete=models.CASCADE, related_name='decisions'
    )
    field_path = models.CharField(max_length=128)
    origin = models.CharField(max_length=16)  # extracted | manual
    decision = models.CharField(max_length=16)  # accepted | edited | rejected
    # Null means the human recorded 'no value' (a rejection, or clearing a
    # field): distinct from {} which is an empty edited value.
    final_value = models.JSONField(null=True, blank=True, default=dict)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    decided_at = models.DateTimeField()

    class Meta:
        """One decision row per proposal field."""

        constraints = [
            models.UniqueConstraint(
                fields=['proposal', 'field_path'], name='tasks_closeout_decision_uniq'
            )
        ]

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'CloseoutFieldDecision#{self.pk} ({self.field_path}: {self.decision})'


class PartUsageDisposition(models.TextChoices):
    """Explicit resolutions for usage that differs from custody truth."""

    CONSUMED = 'consumed', _('Consumed')
    RETURNED = 'returned', _('Returned Unused')
    SCRAPPED = 'scrapped', _('Scrapped')
    SPARE_INSTALLED = 'spare_installed', _('Installed as Spare')
    SERIALIZED_MANUAL = 'serialized_manual', _('Serialized - Manual Handling')
    CORRECTION = 'correction', _('Quantity Correction')
    DISMISSED = 'dismissed', _('Candidate Dismissed')


class CloseoutPartUsageState(models.TextChoices):
    """Reconciliation state of one usage row."""

    PENDING = 'pending', _('Pending')
    RECONCILED = 'reconciled', _('Reconciled')
    BLOCKED = 'blocked', _('Blocked')


class CloseoutPartUsage(models.Model):
    """Reconciliation view of actual part usage; custody stays authoritative."""

    work_order = models.ForeignKey(
        'tasks.WorkOrder', on_delete=models.CASCADE, related_name='closeout_part_usage'
    )
    allocation = models.ForeignKey(
        'tasks.JobKitAllocation', null=True, blank=True, on_delete=models.PROTECT
    )
    part = models.ForeignKey(
        'part.Part', null=True, blank=True, on_delete=models.PROTECT
    )
    stock_item = models.ForeignKey(
        'stock.StockItem', null=True, blank=True, on_delete=models.PROTECT
    )
    planned_quantity = models.DecimalField(
        max_digits=15, decimal_places=5, null=True, blank=True
    )
    issued_quantity = models.DecimalField(
        max_digits=15, decimal_places=5, null=True, blank=True
    )
    used_quantity = models.DecimalField(
        max_digits=15, decimal_places=5, null=True, blank=True
    )
    disposition = models.CharField(
        max_length=24, choices=PartUsageDisposition.choices, blank=True
    )
    variance_reason = models.TextField(blank=True)
    stock_tracking_id = models.IntegerField(null=True, blank=True)
    source = models.CharField(max_length=16, default='kit')  # kit | walkup | narrative
    candidate_text = models.CharField(max_length=255, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT
    )
    state = models.CharField(
        max_length=16,
        choices=CloseoutPartUsageState.choices,
        default=CloseoutPartUsageState.PENDING,
        db_index=True,
    )
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """One reconciliation row per kit allocation."""

        constraints = [
            models.UniqueConstraint(
                fields=['work_order', 'allocation'],
                condition=Q(allocation__isnull=False),
                name='tasks_closeout_usage_alloc_uniq',
            )
        ]
        indexes = [
            models.Index(
                fields=['work_order', 'state'], name='tasks_closeout_usage_wo_state'
            )
        ]

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'CloseoutPartUsage#{self.pk} ({self.source}, {self.state})'


class CloseoutReadingState(models.TextChoices):
    """Verification lifecycle of one closeout reading."""

    PENDING = 'pending', _('Pending')
    VERIFIED = 'verified', _('Verified')
    FAILED = 'failed', _('Failed')
    DISPOSITIONED = 'dispositioned', _('Dispositioned')


class CloseoutReading(models.Model):
    """A closeout-level measurement with raw text and normalized value."""

    work_order = models.ForeignKey(
        'tasks.WorkOrder', on_delete=models.CASCADE, related_name='closeout_readings'
    )
    step_execution = models.ForeignKey(
        'tasks.WorkOrderStepExecution', null=True, blank=True, on_delete=models.PROTECT
    )
    label = models.CharField(max_length=128)
    phase = models.CharField(max_length=8, default='after')  # before | after
    raw_text = models.CharField(max_length=64)
    source_spans = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    value = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    unit = models.CharField(max_length=32, blank=True)
    expected_min = models.DecimalField(
        max_digits=20, decimal_places=6, null=True, blank=True
    )
    expected_max = models.DecimalField(
        max_digits=20, decimal_places=6, null=True, blank=True
    )
    required = models.BooleanField(default=False)
    normalization_rule_version = models.CharField(max_length=32)
    verification_state = models.CharField(
        max_length=16,
        choices=CloseoutReadingState.choices,
        default=CloseoutReadingState.PENDING,
        db_index=True,
    )
    disposition_reason = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    recorded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'CloseoutReading#{self.pk} ({self.label}: {self.verification_state})'


class CloseoutReadingEvidence(models.Model):
    """Relational, ownership-validated evidence link for one reading."""

    reading = models.ForeignKey(
        CloseoutReading, on_delete=models.CASCADE, related_name='evidence_links'
    )
    attachment = models.ForeignKey('common.Attachment', on_delete=models.PROTECT)
    linked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    linked_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'CloseoutReadingEvidence#{self.pk} (reading {self.reading_id})'


class CloseoutEffectStatus(models.TextChoices):
    """Fan-out intent states; unknown-after-dispatch is never blind-replayed."""

    PENDING = 'pending', _('Pending')
    LEASED = 'leased', _('Leased')
    DISPATCHING = 'dispatching', _('Dispatching')
    RETRYABLE = 'retryable', _('Retryable')
    OUTCOME_UNKNOWN = 'outcome_unknown', _('Outcome Unknown')
    SUCCEEDED = 'succeeded', _('Succeeded')
    FAILED = 'failed', _('Failed')
    ABANDONED = 'abandoned', _('Abandoned')


class CloseoutEffect(models.Model):
    """Durable post-commit fan-out intent owned by one completed closeout."""

    closeout = models.ForeignKey(
        'tasks.WorkOrderCloseout', on_delete=models.PROTECT, related_name='effects'
    )
    effect_type = models.CharField(max_length=40, db_index=True)
    effect_key = models.CharField(max_length=128, unique=True)
    payload_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=24,
        choices=CloseoutEffectStatus.choices,
        default=CloseoutEffectStatus.PENDING,
        db_index=True,
    )
    attempts = models.PositiveIntegerField(default=0)
    lease_owner = models.CharField(max_length=128, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    reconciliation_due_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    result_reference = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Deterministic keys collapse duplicate intents."""

        indexes = [
            models.Index(
                fields=['closeout', 'effect_type'], name='tasks_closeout_eff_type'
            )
        ]

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'CloseoutEffect#{self.pk} ({self.effect_type}: {self.status})'


class CloseoutLearningDraft(models.Model):
    """Governed, draft-only learning candidate; never auto-published."""

    closeout = models.ForeignKey(
        'tasks.WorkOrderCloseout',
        on_delete=models.PROTECT,
        related_name='learning_drafts',
    )
    draft_type = models.CharField(max_length=40, default='problem_solution')
    payload = models.JSONField(default=dict, blank=True)
    provenance = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, default='draft', db_index=True)
    # draft | approved | rejected
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Effect replay resolves to one draft per closeout and type."""

        constraints = [
            models.UniqueConstraint(
                fields=['closeout', 'draft_type'], name='tasks_closeout_draft_uniq'
            )
        ]

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'CloseoutLearningDraft#{self.pk} ({self.draft_type}: {self.status})'


class CloseoutAmendmentStatus(models.TextChoices):
    """Governed correction lifecycle; originals stay byte-stable."""

    PROPOSED = 'proposed', _('Proposed')
    APPROVED = 'approved', _('Approved')
    APPLIED = 'applied', _('Applied')
    REJECTED = 'rejected', _('Rejected')


class CloseoutAmendment(models.Model):
    """Append-only correction of a completed closeout."""

    closeout = models.ForeignKey(
        'tasks.WorkOrderCloseout', on_delete=models.PROTECT, related_name='amendments'
    )
    changes = models.JSONField()  # {field: {'from': ..., 'to': ...}}
    base_content_hash = models.CharField(max_length=64)
    reason = models.TextField()
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    approval = models.ForeignKey(
        'approvals.Approval', null=True, blank=True, on_delete=models.PROTECT
    )
    status = models.CharField(
        max_length=16,
        choices=CloseoutAmendmentStatus.choices,
        default=CloseoutAmendmentStatus.PROPOSED,
        db_index=True,
    )
    effective_snapshot = models.JSONField(null=True, blank=True)
    effective_snapshot_hash = models.CharField(max_length=64, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return (
            f'CloseoutAmendment#{self.pk} (closeout {self.closeout_id}, {self.status})'
        )


def new_effect_key(closeout_id: int, effect_type: str, version: int = 1) -> str:
    """Build the deterministic idempotency key for one fan-out intent."""
    return f'closeout:{closeout_id}:{effect_type}:v{version}'


def new_correlation_id() -> uuid.UUID:
    """Mint a correlation id for closeout command flows."""
    return uuid.uuid4()
