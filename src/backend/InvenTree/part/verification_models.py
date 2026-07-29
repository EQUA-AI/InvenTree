"""Persistence for the Right-Part Finder part verification aggregate.

Models here own verification state only. Catalog, BOM, asset, work, stock,
approval, and order records remain the authorities for their own facts; this
aggregate stores immutable snapshots and fingerprints of those facts.

These models are registered with the ``part`` app by an explicit import at the
bottom of ``part/models.py`` so discovery via
``apps.get_model('part', 'PartVerificationSession')`` is deterministic.

State transitions, cross-row invariants, and locking are owned by
``part.verification.services``; model ``clean()`` is not concurrency
authority (spec section 5.3).
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from part.verification.schema import (
    DecisionKind,
    EventType,
    EvidenceDecision,
    PartVerificationPurpose,
    PartVerificationState,
    PolicyStatus,
    RequirementResolution,
    RequirementValueKind,
)

# Stored hash strings are 'sha256:<64 hex>' = 71 characters
HASH_LENGTH = 71


class PartVerificationPolicyVersion(models.Model):
    """One immutable version of a part verification policy document.

    The canonical ``definition`` becomes immutable once the version leaves
    DRAFT. Revocation preserves history; it never rewrites the definition.
    """

    class Meta:
        """Model metadata and manage permission."""

        unique_together = [('key', 'version')]
        permissions = [
            ('manage_partverificationpolicy', 'Can manage part verification policy')
        ]

    key = models.CharField(max_length=64, help_text=_('Policy key'))

    version = models.PositiveIntegerField(default=1, help_text=_('Policy version'))

    status = models.CharField(
        max_length=16,
        choices=PolicyStatus.choices,
        default=PolicyStatus.DRAFT,
        db_index=True,
    )

    schema_version = models.PositiveIntegerField(default=1)

    definition = models.JSONField(
        default=dict, help_text=_('Canonical policy definition document')
    )

    definition_hash = models.CharField(max_length=HASH_LENGTH, blank=True)

    effective_from = models.DateTimeField(null=True, blank=True)

    effective_until = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )

    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """Return the policy identity string."""
        return f'{self.key} v{self.version} ({self.status})'

    def save(self, *args, **kwargs):
        """Prevent definition rewrites once the version has left DRAFT."""
        if self.pk is not None:
            original = (
                PartVerificationPolicyVersion.objects
                .filter(pk=self.pk)
                .values('status', 'definition')
                .first()
            )
            if (
                original
                and original['status'] != PolicyStatus.DRAFT
                and original['definition'] != self.definition
            ):
                raise ValidationError(
                    'The definition of an activated policy version is immutable'
                )
        super().save(*args, **kwargs)


class PartVerificationSession(models.Model):
    """One scoped, versioned verification session for one precise purpose."""

    REFERENCE_PREFIX = 'PVS-'

    class Meta:
        """Model metadata, indexes, and service-level permissions."""

        indexes = [
            models.Index(fields=['state', 'purpose']),
            models.Index(fields=['scope_customer', 'state']),
            models.Index(fields=['expires_at']),
        ]
        permissions = [
            ('review_partverification', 'Can review part verification candidates'),
            ('confirm_partverification', 'Can confirm part verification decisions'),
            ('invalidate_partverification', 'Can invalidate part verifications'),
            ('use_partverification', 'Can bind part verifications to effects'),
        ]

    reference = models.CharField(max_length=32, blank=True, unique=True, db_index=True)

    purpose = models.CharField(
        max_length=32, choices=PartVerificationPurpose.choices, db_index=True
    )

    state = models.CharField(
        max_length=24,
        choices=PartVerificationState.choices,
        default=PartVerificationState.COLLECTING,
        db_index=True,
    )

    revision = models.PositiveIntegerField(default=1)

    scope_customer = models.ForeignKey(
        'company.Company',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='part_verification_sessions',
    )

    scope_client = models.ForeignKey(
        'assets.Client',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='part_verification_sessions',
    )

    scope_site_key = models.CharField(max_length=100, blank=True)

    scope_fingerprint = models.CharField(max_length=HASH_LENGTH)

    requested_part = models.ForeignKey(
        'part.Part',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='verification_sessions',
    )

    machine = models.ForeignKey(
        'assets.AssetMachine',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='part_verification_sessions',
    )

    machine_part = models.ForeignKey(
        'assets.MachinePart',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='part_verification_sessions',
    )

    bom_item = models.ForeignKey(
        'part.BomItem',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='verification_sessions',
    )

    work_order = models.ForeignKey(
        'tasks.WorkOrder',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='part_verification_sessions',
    )

    job_kit_line = models.ForeignKey(
        'tasks.JobKitLine',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='part_verification_sessions',
    )

    policy = models.ForeignKey(
        PartVerificationPolicyVersion, on_delete=models.PROTECT, related_name='sessions'
    )

    requirements_hash = models.CharField(max_length=HASH_LENGTH, blank=True)

    source_fingerprint = models.CharField(max_length=HASH_LENGTH, blank=True)

    evaluation_hash = models.CharField(max_length=HASH_LENGTH, blank=True)

    current_decision = models.ForeignKey(
        'part.PartVerificationDecision',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )

    stale_reason = models.CharField(max_length=64, blank=True)

    expires_at = models.DateTimeField(null=True, blank=True)

    universe_complete = models.BooleanField(default=False)

    considered_count = models.PositiveIntegerField(default=0)

    eligible_count = models.PositiveIntegerField(default=0)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='part_verification_sessions',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        """Return the session reference."""
        return self.reference or f'{self.REFERENCE_PREFIX}?{self.pk}'

    def save(self, *args, **kwargs):
        """Assign a stable unique reference on first insert."""
        creating = self.pk is None
        super().save(*args, **kwargs)
        if creating and not self.reference:
            self.reference = f'{self.REFERENCE_PREFIX}{self.pk:06d}'
            super().save(update_fields=['reference'])


class PartVerificationRequirement(models.Model):
    """One current typed application requirement for a session."""

    class Meta:
        """Model metadata."""

        unique_together = [('session', 'key')]
        ordering = ['key']

    session = models.ForeignKey(
        PartVerificationSession, on_delete=models.CASCADE, related_name='requirements'
    )

    key = models.CharField(max_length=100)

    category = models.CharField(max_length=50, blank=True)

    value_kind = models.CharField(max_length=16, choices=RequirementValueKind.choices)

    operator = models.CharField(max_length=32)

    value = models.JSONField(null=True, blank=True)

    raw_value = models.JSONField(null=True, blank=True)

    unit = models.CharField(max_length=25, blank=True)

    tolerance = models.JSONField(default=dict, blank=True)

    hard_constraint = models.BooleanField(default=True)

    resolution = models.CharField(
        max_length=16,
        choices=RequirementResolution.choices,
        default=RequirementResolution.MISSING,
    )

    blocker_code = models.CharField(max_length=64, blank=True)

    authority = models.CharField(max_length=64, blank=True)

    provenance = models.JSONField(default=list, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        """Return the requirement key with its resolution."""
        return f'{self.key} [{self.resolution}]'


class PartVerificationEvidence(models.Model):
    """One captured evidence item and its acceptance state.

    Rows are superseded logically, never rewritten destructively.
    """

    class Meta:
        """Model metadata."""

        ordering = ['pk']
        indexes = [models.Index(fields=['session', 'requirement_key'])]

    session = models.ForeignKey(
        PartVerificationSession, on_delete=models.CASCADE, related_name='evidence_items'
    )

    requirement_key = models.CharField(max_length=100, blank=True)

    source_kind = models.CharField(max_length=32)

    source_model = models.CharField(max_length=100, blank=True)

    source_object_id = models.CharField(max_length=36, blank=True)

    source_field = models.CharField(max_length=100, blank=True)

    source_version = models.CharField(max_length=100, blank=True)

    source_fingerprint = models.CharField(max_length=HASH_LENGTH, blank=True)

    digest = models.CharField(max_length=HASH_LENGTH, blank=True)

    raw_value = models.JSONField(null=True, blank=True)

    canonical_value = models.JSONField(null=True, blank=True)

    unit = models.CharField(max_length=25, blank=True)

    authority = models.CharField(max_length=64, blank=True)

    origin = models.CharField(max_length=32, default='system')

    decision = models.CharField(
        max_length=16,
        choices=EvidenceDecision.choices,
        default=EvidenceDecision.PROPOSED,
        db_index=True,
    )

    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )

    decided_at = models.DateTimeField(null=True, blank=True)

    expires_at = models.DateTimeField(null=True, blank=True)

    superseded_by = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """Return a short evidence identity string."""
        return f'{self.source_kind}:{self.requirement_key or "-"} [{self.decision}]'


class PartCandidateEvaluation(models.Model):
    """Point-in-time eligibility record for one candidate part.

    Immutable once written; a new session revision creates new rows.
    """

    class Meta:
        """Model metadata and the never-rank-ineligible constraint."""

        unique_together = [('session', 'session_revision', 'candidate')]
        ordering = ['session', 'session_revision', 'rank', 'candidate']
        constraints = [
            models.CheckConstraint(
                condition=Q(eligible=True) | Q(rank__isnull=True),
                name='rpf_ineligible_candidate_has_no_rank',
            )
        ]

    session = models.ForeignKey(
        PartVerificationSession,
        on_delete=models.CASCADE,
        related_name='candidate_evaluations',
    )

    session_revision = models.PositiveIntegerField()

    candidate = models.ForeignKey(
        'part.Part', on_delete=models.PROTECT, related_name='verification_evaluations'
    )

    retrieval_tiers = models.JSONField(default=list, blank=True)

    candidate_snapshot = models.JSONField(default=dict)

    candidate_fingerprint = models.CharField(max_length=HASH_LENGTH)

    eligible = models.BooleanField(default=False, db_index=True)

    hard_conflicts = models.JSONField(default=list, blank=True)

    matched_attributes = models.JSONField(default=list, blank=True)

    missing_attributes = models.JSONField(default=list, blank=True)

    rank_factors = models.JSONField(default=list, blank=True)

    rank_value = models.IntegerField(null=True, blank=True)

    rank = models.PositiveIntegerField(null=True, blank=True)

    availability_snapshot = models.JSONField(default=dict, blank=True)

    requirements_hash = models.CharField(max_length=HASH_LENGTH)

    policy = models.ForeignKey(
        PartVerificationPolicyVersion, on_delete=models.PROTECT, related_name='+'
    )

    evaluation_hash = models.CharField(max_length=HASH_LENGTH)

    evaluated_at = models.DateTimeField()

    rejected = models.BooleanField(default=False)

    rejected_reason = models.TextField(blank=True)

    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )

    def __str__(self):
        """Return the evaluation identity string."""
        state = 'eligible' if self.eligible else 'excluded'
        return f'{self.session_id} r{self.session_revision} part {self.candidate_id} ({state})'


class PartVerificationDecision(models.Model):
    """Immutable human confirmation or no-safe-match snapshot."""

    class Meta:
        """Model metadata and decision-kind invariants."""

        ordering = ['-decided_at']
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        kind=DecisionKind.CONFIRMED,
                        selected_part__isnull=False,
                        selected_evaluation__isnull=False,
                    )
                    | Q(
                        kind=DecisionKind.NO_SAFE_MATCH,
                        selected_part__isnull=True,
                        selected_evaluation__isnull=True,
                    )
                ),
                name='rpf_decision_kind_selection_invariant',
            )
        ]

    session = models.ForeignKey(
        PartVerificationSession, on_delete=models.PROTECT, related_name='decisions'
    )

    session_revision = models.PositiveIntegerField()

    kind = models.CharField(max_length=20, choices=DecisionKind.choices)

    selected_evaluation = models.ForeignKey(
        PartCandidateEvaluation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='decisions',
    )

    selected_part = models.ForeignKey(
        'part.Part',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='verification_decisions',
    )

    decision_snapshot = models.JSONField(default=dict)

    decision_hash = models.CharField(max_length=HASH_LENGTH, unique=True)

    requirements_hash = models.CharField(max_length=HASH_LENGTH, blank=True)

    source_fingerprint = models.CharField(max_length=HASH_LENGTH, blank=True)

    evaluation_hash = models.CharField(max_length=HASH_LENGTH, blank=True)

    policy_hash = models.CharField(max_length=HASH_LENGTH, blank=True)

    scope_fingerprint = models.CharField(max_length=HASH_LENGTH, blank=True)

    policy = models.ForeignKey(
        PartVerificationPolicyVersion, on_delete=models.PROTECT, related_name='+'
    )

    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )

    reason = models.TextField()

    decided_at = models.DateTimeField()

    valid_until = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        """Return the decision identity string."""
        return f'{self.kind} for session {self.session_id} r{self.session_revision}'


class PartVerificationUse(models.Model):
    """Immutable proof that a consumer accepted a current decision."""

    class Meta:
        """Model metadata and replay-safe effect uniqueness."""

        unique_together = [('consumer_kind', 'consumer_action', 'idempotency_key')]
        ordering = ['pk']

    decision = models.ForeignKey(
        PartVerificationDecision, on_delete=models.PROTECT, related_name='uses'
    )

    consumer_kind = models.CharField(max_length=32)

    consumer_model = models.CharField(max_length=100, blank=True)

    consumer_object_id = models.CharField(max_length=36, blank=True)

    consumer_action = models.CharField(max_length=64)

    scope_fingerprint = models.CharField(max_length=HASH_LENGTH, blank=True)

    final_observation_hash = models.CharField(max_length=HASH_LENGTH, blank=True)

    command_hash = models.CharField(max_length=HASH_LENGTH, blank=True)

    idempotency_key = models.CharField(max_length=64)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """Return the use identity string."""
        return f'{self.consumer_kind}:{self.consumer_action} of decision {self.decision_id}'


class PartVerificationEvent(models.Model):
    """Append-only typed verification event."""

    class Meta:
        """Model metadata."""

        ordering = ['pk']
        indexes = [models.Index(fields=['session', 'event_type'])]

    session = models.ForeignKey(
        PartVerificationSession, on_delete=models.CASCADE, related_name='events'
    )

    event_type = models.CharField(max_length=32, choices=EventType.choices)

    state = models.CharField(max_length=24, blank=True)

    reason = models.CharField(max_length=64, blank=True)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )

    correlation_id = models.CharField(max_length=36, blank=True)

    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """Return the event identity string."""
        return f'{self.event_type} (session {self.session_id})'


class PartVerificationCommand(models.Model):
    """Replay-safe command ledger row for one mutating verification command."""

    class Meta:
        """Model metadata and idempotency uniqueness."""

        unique_together = [('command', 'idempotency_key')]
        ordering = ['pk']

    STATUS_PENDING = 'pending'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'

    command = models.CharField(max_length=64)

    session = models.ForeignKey(
        PartVerificationSession,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='commands',
    )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )

    scope_fingerprint = models.CharField(max_length=HASH_LENGTH, blank=True)

    idempotency_key = models.CharField(max_length=64)

    request_hash = models.CharField(max_length=HASH_LENGTH)

    status = models.CharField(max_length=16, default=STATUS_PENDING)

    result = models.JSONField(null=True, blank=True)

    error = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        """Return the command identity string."""
        return f'{self.command} [{self.status}]'
