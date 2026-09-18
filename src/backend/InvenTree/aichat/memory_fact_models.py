"""Durable facts and content-free lifecycle records.

SQLite keeps a dark fixture schema for normal deletion collectors. Semantic
execution and array/vector qualification require PostgreSQL.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from pgvector.django import VectorField

from aichat.memory_fields import (
    MemoryGinIndex,
    MemoryHnswIndex,
    MemoryTopicsConstraint,
    MemoryTopicsField,
)

from .memory_choices import (
    DurableMemoryType,
    ExtractionState,
    MemoryClassification,
    MemoryEventAction,
    MemoryLifecycle,
    MemoryOrigin,
    MemoryShieldState,
    MemorySourceClass,
    MemoryTopic,
    MemoryTrust,
    MemoryVerification,
    MemoryVisibility,
)


class MemoryFact(models.Model):
    """One owner/client/typed-slot claim; authority is independent of its origin."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='memory_facts'
    )
    client_code = models.CharField(max_length=64, blank=True, default='')
    entity_kind = models.CharField(max_length=16)
    entity_id = models.CharField(max_length=100)
    slot_key = models.CharField(max_length=128)
    memory_type = models.CharField(max_length=32, choices=DurableMemoryType.choices)
    topics = MemoryTopicsField(
        models.CharField(max_length=32, choices=MemoryTopic.choices),
        default=list,
        db_default=[],
        blank=True,
    )
    text = models.TextField(default='', blank=True)
    text_lang = models.CharField(max_length=8)
    canonical_value = models.JSONField(null=True, blank=True)
    canonical_unit = models.CharField(max_length=32, default='', blank=True)
    embedding = VectorField(dimensions=1536, null=True, blank=True)
    embedding_profile = models.CharField(max_length=128, default='', blank=True)
    verification_class = models.CharField(
        max_length=24,
        choices=MemoryVerification.choices,
        default=MemoryVerification.INFERRED,
    )
    lifecycle_state = models.CharField(
        max_length=16, choices=MemoryLifecycle.choices, default=MemoryLifecycle.PROPOSED
    )
    origin = models.CharField(max_length=16, choices=MemoryOrigin.choices)
    origin_modality = models.CharField(
        max_length=16,
        choices=[('chat', 'Chat'), ('voice', 'Voice'), ('settings', 'Settings')],
    )
    visibility_scope = models.CharField(max_length=16, choices=MemoryVisibility.choices)
    classification = models.CharField(
        max_length=16, choices=MemoryClassification.choices
    )
    prohibited = models.BooleanField(default=False)
    source_thread = models.ForeignKey(
        'aichat.ChatThread',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='memory_facts',
    )
    source_message = models.ForeignKey(
        'aichat.ChatMessage',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    source_tombstone = models.ForeignKey(
        'aichat.ChatThreadTombstone',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    confirming_proposal = models.ForeignKey(
        'aichat.ChatActionProposal',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    notice_version = models.CharField(max_length=64)
    injection_flag = models.BooleanField(default=False)
    shield_state = models.CharField(
        max_length=16,
        choices=MemoryShieldState.choices,
        default=MemoryShieldState.PENDING,
    )
    claim_fingerprint = models.CharField(max_length=64)
    # Content-free pointer: old tombstones never become live facts again.
    revives = models.UUIDField(null=True, blank=True)
    supersedes = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='successors',
    )
    version = models.PositiveIntegerField(default=1)
    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """SQL enforces scope/authority and the closed vocabulary on direct writes."""

        permissions = [('write_memory', 'Can propose and confirm own memories')]
        indexes = [
            models.Index(
                fields=['owner', 'client_code', 'lifecycle_state', 'memory_type'],
                name='memory_fact_recall_idx',
            ),
            MemoryGinIndex(fields=['topics'], name='memory_fact_topics_gin'),
            MemoryHnswIndex(
                fields=['embedding'],
                name='memory_fact_vector_hnsw',
                m=16,
                ef_construction=64,
                opclasses=['vector_cosine_ops'],
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(memory_type__in=DurableMemoryType.values),
                name='memory_fact_type',
            ),
            MemoryTopicsConstraint(
                condition=Q(topics__contained_by=MemoryTopic.values)
                & Q(topics__len__lte=3),
                name='memory_fact_topics',
            ),
            models.CheckConstraint(
                condition=Q(verification_class__in=MemoryVerification.values),
                name='memory_fact_verification',
            ),
            models.CheckConstraint(
                condition=Q(lifecycle_state__in=MemoryLifecycle.values),
                name='memory_fact_lifecycle',
            ),
            models.CheckConstraint(
                condition=Q(origin__in=MemoryOrigin.values), name='memory_fact_origin'
            ),
            models.CheckConstraint(
                condition=Q(origin_modality__in=['chat', 'voice', 'settings']),
                name='memory_fact_modality',
            ),
            models.CheckConstraint(
                condition=Q(visibility_scope__in=MemoryVisibility.values),
                name='memory_fact_visibility',
            ),
            models.CheckConstraint(
                condition=Q(classification__in=MemoryClassification.values),
                name='memory_fact_classification',
            ),
            models.CheckConstraint(
                condition=Q(shield_state__in=MemoryShieldState.values),
                name='memory_fact_shield',
            ),
            models.CheckConstraint(
                condition=Q(entity_kind__in=['user', 'client', 'machine', 'work_order'])
                & ~Q(entity_id='')
                & ~Q(slot_key=''),
                name='memory_fact_entity_slot',
            ),
            models.CheckConstraint(
                condition=~Q(notice_version='')
                & ~Q(claim_fingerprint='')
                & Q(version__gte=1),
                name='memory_fact_identity',
            ),
            models.CheckConstraint(
                condition=Q(text_lang__in=['en', 'es', 'de', 'fr']),
                name='memory_fact_locale',
            ),
            models.CheckConstraint(
                condition=Q(prohibited=False), name='memory_fact_not_prohibited'
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        memory_type='user_preference',
                        visibility_scope='user_private',
                        classification='preference',
                        client_code='',
                        entity_kind='user',
                    )
                    | (
                        ~Q(memory_type='user_preference')
                        & Q(
                            visibility_scope='client_shared',
                            classification__in=['operational', 'personal'],
                        )
                        & ~Q(client_code='')
                        & ~Q(entity_kind='user')
                    )
                ),
                name='memory_fact_scope_type',
            ),
            models.CheckConstraint(
                condition=~Q(classification='personal') | Q(memory_type='contact_role'),
                name='memory_fact_personal_role',
            ),
            models.CheckConstraint(
                condition=~Q(lifecycle_state='active')
                | (
                    Q(
                        last_verified_at__isnull=False,
                        shield_state__in=['clear', 'flagged'],
                    )
                    & (
                        (
                            Q(
                                classification='preference',
                                verification_class='user_confirmed',
                                confirming_proposal__isnull=False,
                            )
                        )
                        | (
                            ~Q(classification='preference')
                            & Q(verification_class='tool_verified')
                        )
                    )
                ),
                name='memory_fact_active_authority',
            ),
            models.CheckConstraint(
                condition=~Q(lifecycle_state__in=['forgotten', 'withdrawn'])
                | Q(text='', embedding__isnull=True, canonical_value__isnull=True),
                name='memory_fact_terminal_scrub',
            ),
            models.CheckConstraint(
                condition=Q(valid_until__isnull=True)
                | Q(valid_until__gt=F('valid_from')),
                name='memory_fact_validity',
            ),
            models.UniqueConstraint(
                fields=[
                    'owner',
                    'client_code',
                    'entity_kind',
                    'entity_id',
                    'memory_type',
                    'slot_key',
                ],
                condition=Q(lifecycle_state='active'),
                name='memory_fact_active_slot',
            ),
        ]


class MemoryFactTombstone(models.Model):
    """No text, embeddings or recoverable claims; retained on the deletion clock."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_id = models.PositiveBigIntegerField(db_index=True)
    owner_joined_at = models.DateTimeField(null=True, blank=True)
    client_code = models.CharField(max_length=64, blank=True, default='')
    slot_fingerprint = models.CharField(max_length=64)
    claim_fingerprint = models.CharField(max_length=64)
    source_fingerprint = models.CharField(max_length=64)
    source_sequence = models.PositiveBigIntegerField(default=0)
    fact_id = models.UUIDField(null=True, blank=True)
    deleted_at = models.DateTimeField(default=timezone.now)
    reason = models.CharField(
        max_length=16,
        choices=[
            ('forget', 'Forget'),
            ('supersede', 'Supersede'),
            ('opt_out', 'Opt out'),
            ('erasure', 'Erasure'),
            ('client_purge', 'Client purge'),
        ],
    )
    residual_pending = models.BooleanField(default=True)

    class Meta:
        """Fingerprint identity is deterministic across retries."""

        constraints = [
            models.UniqueConstraint(
                fields=[
                    'owner_id',
                    'client_code',
                    'slot_fingerprint',
                    'claim_fingerprint',
                    'fact_id',
                ],
                name='memory_tombstone_fact_claim',
            ),
            models.CheckConstraint(
                condition=~Q(slot_fingerprint='')
                & ~Q(claim_fingerprint='')
                & ~Q(source_fingerprint=''),
                name='memory_tombstone_fingerprints',
            ),
            models.CheckConstraint(
                condition=Q(
                    reason__in=[
                        'forget',
                        'supersede',
                        'opt_out',
                        'erasure',
                        'client_purge',
                    ]
                ),
                name='memory_tombstone_reason',
            ),
        ]


class MemoryFactClaim(models.Model):
    """A severable source pointer, never authority by virtue of a tool envelope."""

    fact = models.ForeignKey(
        MemoryFact, on_delete=models.CASCADE, related_name='claims'
    )
    source_thread = models.ForeignKey(
        'aichat.ChatThread',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    source_message = models.ForeignKey(
        'aichat.ChatMessage',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    source_class = models.CharField(max_length=32, choices=MemorySourceClass.choices)
    content_trust = models.CharField(max_length=24, choices=MemoryTrust.choices)
    source_model = models.CharField(max_length=64, blank=True, default='')
    source_object_id = models.CharField(max_length=100, blank=True, default='')
    source_field = models.CharField(max_length=64, blank=True, default='')
    attachment_id = models.PositiveIntegerField(null=True, blank=True)
    source_sequence = models.PositiveBigIntegerField(default=0)
    source_fingerprint = models.CharField(max_length=64)
    severed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Source channels cannot be arbitrary tags or grant authority."""

        constraints = [
            models.CheckConstraint(
                condition=Q(source_class__in=MemorySourceClass.values),
                name='memory_claim_source_class',
            ),
            models.CheckConstraint(
                condition=Q(content_trust__in=MemoryTrust.values),
                name='memory_claim_trust',
            ),
            models.CheckConstraint(
                condition=~Q(source_fingerprint=''), name='memory_claim_fingerprint'
            ),
        ]


class MemoryFactEvent(models.Model):
    """Only IDs, enum codes and timestamps; no free-form event payload."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_id = models.PositiveBigIntegerField(db_index=True)
    actor_id = models.PositiveBigIntegerField(null=True, blank=True)
    fact_id = models.UUIDField(null=True, blank=True)
    tombstone_id = models.UUIDField(null=True, blank=True)
    proposal_id = models.UUIDField(null=True, blank=True)
    action = models.CharField(max_length=16, choices=MemoryEventAction.choices)
    version = models.PositiveIntegerField(default=0)
    claim_fingerprint = models.CharField(
        max_length=64, blank=True, default='', db_default=''
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Append-only audit shape enforced independently from application code."""

        constraints = [
            models.CheckConstraint(
                condition=Q(action__in=MemoryEventAction.values),
                name='memory_event_action',
            )
        ]


class MemoryExtractionClaim(models.Model):
    """Idempotent sequence window, lease and retry state; no transcript copy."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(
        'aichat.ChatThread',
        on_delete=models.CASCADE,
        related_name='memory_extraction_claims',
    )
    through_sequence = models.PositiveBigIntegerField()
    state = models.CharField(
        max_length=16, choices=ExtractionState.choices, default=ExtractionState.PENDING
    )
    attempts = models.PositiveIntegerField(default=0)
    claimed_at = models.DateTimeField(null=True, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Only one extraction obligation per thread/through-sequence."""

        constraints = [
            models.UniqueConstraint(
                fields=['thread', 'through_sequence'], name='memory_extract_sequence'
            ),
            models.CheckConstraint(
                condition=Q(state__in=ExtractionState.values),
                name='memory_extract_state',
            ),
        ]
        indexes = [
            models.Index(
                fields=['state', 'next_attempt_at'], name='memory_extract_due_idx'
            )
        ]


class MemoryExtractionRun(models.Model):
    """Counts and deployment metadata; no model input or extracted output."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    claim = models.ForeignKey(
        MemoryExtractionClaim,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='runs',
    )
    thread = models.ForeignKey(
        'aichat.ChatThread',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    owner_id = models.PositiveBigIntegerField(db_index=True)
    outcome = models.CharField(max_length=16, choices=ExtractionState.choices)
    flag_state = models.PositiveSmallIntegerField(default=0)
    model = models.CharField(max_length=128, blank=True, default='')
    deployment = models.CharField(max_length=128, blank=True, default='')
    corpus_id = models.CharField(max_length=64, blank=True, default='')
    n_input_messages = models.PositiveIntegerField(default=0)
    n_proposals = models.PositiveIntegerField(default=0)
    n_rejected = models.PositiveIntegerField(default=0)
    n_rejected_vocabulary = models.PositiveIntegerField(default=0)
    n_topics_dropped = models.PositiveIntegerField(default=0)
    n_shield_flagged = models.PositiveIntegerField(default=0)
    n_shield_unavailable = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Runs share the ninety-day detail-retention family."""

        constraints = [
            models.CheckConstraint(
                condition=Q(outcome__in=ExtractionState.values),
                name='memory_run_outcome',
            )
        ]


class MemoryFactJob(models.Model):
    """Version-bound provider preparation; only identifiers and counters persist."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fact = models.ForeignKey(MemoryFact, on_delete=models.CASCADE, related_name='jobs')
    fact_version = models.PositiveIntegerField()
    kind = models.CharField(
        max_length=16, choices=[('shield', 'Shield'), ('embedding', 'Embedding')]
    )
    state = models.CharField(
        max_length=16, choices=ExtractionState.choices, default='pending'
    )
    attempts = models.PositiveIntegerField(default=0)
    lease_token = models.UUIDField(null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """One preparation obligation per fact version and provider purpose."""

        constraints = [
            models.UniqueConstraint(
                fields=['fact', 'fact_version', 'kind'], name='memory_fact_job_version'
            ),
            models.CheckConstraint(
                condition=Q(kind__in=['shield', 'embedding']),
                name='memory_fact_job_kind',
            ),
            models.CheckConstraint(
                condition=Q(state__in=ExtractionState.values),
                name='memory_fact_job_state',
            ),
        ]
        indexes = [
            models.Index(
                fields=['state', 'next_attempt_at'], name='memory_fact_job_due_idx'
            )
        ]
