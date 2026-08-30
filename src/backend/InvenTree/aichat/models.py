"""Portable durable models for normalized AI conversations."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from pgvector.django import VectorField


def _stable_id(prefix: str) -> str:
    """Return a non-sequential public identifier."""
    return f'{prefix}_{uuid.uuid4().hex}'


def generate_thread_id() -> str:
    """Return an identifier for an unscoped thread."""
    return _stable_id('thread')


def generate_message_id() -> str:
    """Return an identifier for a message."""
    return _stable_id('message')


def generate_turn_id() -> str:
    """Return an identifier for a normalized turn."""
    return _stable_id('turn')


class ThreadNamespace(models.TextChoices):
    """Conversation namespaces with different authorization contracts.

    The SCOPED namespace was dropped with the scoped-chat rail (S14c); the
    ``scoped_`` id prefix stays permanently reserved so a stale identifier
    fails closed instead of resolving into the main namespace.
    """

    UNSCOPED = 'unscoped', 'Unscoped'


class MessageRole(models.TextChoices):
    """Roles represented in a conversation transcript."""

    USER = 'user', 'User'
    ASSISTANT = 'assistant', 'Assistant'
    SYSTEM = 'system', 'System'
    TOOL = 'tool', 'Tool'


class TurnModality(models.TextChoices):
    """Supported normalized turn input modalities."""

    TEXT = 'text', 'Text'
    VOICE = 'voice', 'Voice'


class TurnState(models.TextChoices):
    """Durable normalized-turn lifecycle states."""

    RUNNING = 'running', 'Running'
    COMPLETE = 'complete', 'Complete'
    INCOMPLETE = 'incomplete', 'Incomplete'
    CANCELED = 'canceled', 'Canceled'
    FAILED = 'failed', 'Failed'

    @classmethod
    def terminal_values(cls) -> tuple[str, ...]:
        """Return the terminal state values."""
        return (cls.COMPLETE, cls.INCOMPLETE, cls.CANCELED, cls.FAILED)


class ChatThread(models.Model):
    """An owner-, server-scope-, and namespace-bound conversation."""

    id = models.CharField(
        primary_key=True, max_length=80, default=generate_thread_id, editable=False
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='ai_chat_threads',
    )
    scope_key = models.CharField(max_length=255)
    scope_hash = models.CharField(max_length=64)
    namespace = models.CharField(
        max_length=16, choices=ThreadNamespace.choices, default=ThreadNamespace.UNSCOPED
    )
    title = models.CharField(max_length=255, blank=True, default='')
    # S38 compaction: first line of ``summary`` is a <=60-char label, then a
    # newline and the structured JSON body. Written only by the compaction
    # job; ``summary_through_sequence`` is its watermark — messages with
    # sequence <= watermark are represented by the summary.
    summary = models.TextField(blank=True, default='')
    summary_through_sequence = models.PositiveBigIntegerField(default=0)
    last_workflow = models.CharField(max_length=100, blank=True, default='')
    next_sequence = models.PositiveBigIntegerField(default=1)
    # S1 (analysis rail): the durable, non-authorizing active analysis scope.
    # ``analysis_scope`` only narrows which assets analysis answers may draw
    # on — the authorization boundary stays ``scope_key``/``scope_hash``
    # above plus the per-record scope resolvers, re-derived every turn. An
    # empty payload at version 0 reads as ``legacy_unconfirmed``; pre-typed
    # threads are never silently converted. Updates are owner-only and
    # optimistic (``analysis_scope_version`` check under row lock); shape
    # validation lives in ``ai.core.analysis.scope``, not the database.
    analysis_scope = models.JSONField(default=dict, blank=True)
    analysis_scope_version = models.PositiveBigIntegerField(default=0)
    analysis_scope_hash = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Database ordering, indexes, and scalar constraints."""

        ordering = ['-updated_at', '-created_at']
        indexes = [
            models.Index(
                fields=['owner', 'scope_hash', 'namespace', '-updated_at'],
                name='aichat_thread_boundary_idx',
            ),
            models.Index(
                fields=['owner', 'scope_key', 'namespace'],
                name='aichat_thread_scope_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(scope_key=''), name='aichat_thread_scope_not_empty'
            ),
            models.CheckConstraint(
                condition=~Q(scope_hash=''), name='aichat_thread_hash_not_empty'
            ),
            models.CheckConstraint(
                condition=Q(next_sequence__gte=1),
                name='aichat_thread_next_sequence_gte_1',
            ),
            models.CheckConstraint(
                condition=(
                    Q(namespace=ThreadNamespace.UNSCOPED) & ~Q(id__startswith='scoped_')
                ),
                name='aichat_thread_namespace_id',
            ),
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'{self.namespace} chat thread {self.pk}'


class ThreadGrantAccess(models.TextChoices):
    """Access levels a thread grant may confer (read-only for now)."""

    READ = 'read', 'Read'


class ChatThreadGrant(models.Model):
    """An explicit, logged, revocable read grant on another user's thread.

    Mirrors the dropped ``ScopedConversationGrant`` semantics (B6): while
    the thread lives, grant rows are audit records — revocation stamps
    ``revoked_at``, never deletes; expiry is optional. Only explicit
    single-thread READS honor a grant; every write path stays owner-only.

    When the thread itself is purged (user deletion or 400-day retention,
    S16), the audit obligation transfers to ``ChatThreadGrantTombstone``
    rows and the grant rows are then hard-deleted with the thread — the
    ``PROTECT`` below guarantees no path can delete a thread without going
    through that reconciliation.
    """

    thread = models.ForeignKey(
        ChatThread, on_delete=models.PROTECT, related_name='access_grants'
    )
    grantee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    access = models.CharField(
        max_length=16, choices=ThreadGrantAccess.choices, default=ThreadGrantAccess.READ
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Audit ordering and the lookup index for grantee reads."""

        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['grantee', 'revoked_at'], name='aichat_grant_grantee_idx'
            ),
            models.Index(fields=['thread', 'grantee'], name='aichat_grant_thread_idx'),
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'grant {self.pk} on {self.thread_id} ({self.access})'


class ChatMessage(models.Model):
    """An immutable, monotonically ordered transcript message."""

    id = models.CharField(
        primary_key=True, max_length=80, default=generate_message_id, editable=False
    )
    thread = models.ForeignKey(
        ChatThread, on_delete=models.CASCADE, related_name='messages'
    )
    sequence = models.PositiveBigIntegerField()
    role = models.CharField(max_length=16, choices=MessageRole.choices)
    content = models.TextField()
    modality = models.CharField(
        max_length=16, choices=TurnModality.choices, default=TurnModality.TEXT
    )
    metadata = models.JSONField(default=dict, blank=True)
    correlation_id = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Database ordering, indexes, and scalar constraints."""

        ordering = ['sequence']
        indexes = [
            models.Index(
                fields=['thread', 'sequence'], name='aichat_message_order_idx'
            ),
            # S36: the correlation spine's exit-gate join
            # (message -> proposal -> WorkOrderEvent) enters through this
            # column; unindexed it is a table scan.
            models.Index(fields=['correlation_id'], name='aichat_msg_correlation_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['thread', 'sequence'],
                name='aichat_message_thread_sequence_uniq',
            ),
            models.CheckConstraint(
                condition=Q(sequence__gte=1), name='aichat_message_sequence_gte_1'
            ),
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'{self.thread_id} message {self.sequence}'


class ChatTurn(models.Model):
    """An idempotent normalized request and its exact durable result."""

    id = models.CharField(
        primary_key=True, max_length=80, default=generate_turn_id, editable=False
    )
    thread = models.ForeignKey(
        ChatThread, on_delete=models.CASCADE, related_name='turns'
    )
    input_message = models.OneToOneField(
        ChatMessage, on_delete=models.CASCADE, related_name='input_for_turn'
    )
    output_message = models.OneToOneField(
        ChatMessage,
        on_delete=models.CASCADE,
        related_name='output_for_turn',
        null=True,
        blank=True,
    )
    modality = models.CharField(max_length=16, choices=TurnModality.choices)
    request_fingerprint = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=255)
    trusted_context = models.JSONField(default=dict, blank=True)
    modality_metadata = models.JSONField(default=dict, blank=True)
    canonical_result = models.JSONField(null=True, blank=True)
    state = models.CharField(
        max_length=16, choices=TurnState.choices, default=TurnState.RUNNING
    )
    correlation_id = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Database ordering, indexes, uniqueness, and lifecycle constraints."""

        ordering = ['created_at']
        indexes = [
            models.Index(
                fields=['thread', 'created_at'], name='aichat_turn_thread_time_idx'
            ),
            models.Index(
                fields=['thread', 'state', 'created_at'],
                name='aichat_turn_state_time_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['thread', 'idempotency_key'],
                name='aichat_turn_thread_idempotency_uniq',
            ),
            models.CheckConstraint(
                condition=~Q(idempotency_key=''),
                name='aichat_turn_idempotency_not_empty',
            ),
            models.CheckConstraint(
                condition=~Q(request_fingerprint=''),
                name='aichat_turn_fingerprint_not_empty',
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        state=TurnState.RUNNING,
                        canonical_result__isnull=True,
                        completed_at__isnull=True,
                        output_message__isnull=True,
                    )
                    | Q(
                        state__in=TurnState.terminal_values(),
                        canonical_result__isnull=False,
                        completed_at__isnull=False,
                        output_message__isnull=False,
                    )
                ),
                name='aichat_turn_terminal_result_state',
            ),
        ]

    @property
    def is_terminal(self) -> bool:
        """Whether the turn has reached a durable terminal state."""
        return self.state in TurnState.terminal_values()

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'{self.thread_id} turn {self.pk} ({self.state})'


def generate_evidence_set_id() -> str:
    """The opaque model-visible evidence-set handle; also the DB pk."""
    return _stable_id('set')


class ChatEvidenceSet(models.Model):
    """Durable evidence for one aggregate/population claim (S10, §7.6).

    Written ONLY inside ``ThreadRepository.terminal()``'s transaction, so a
    failed or canceled turn can never leave orphan evidence. The pk is the
    pre-minted opaque ``set_...`` handle the model/UI reference; the language
    model itself only ever saw the digest (operation, result, counts).

    ``authorization_scope_hash`` is server-only: it never appears in any
    model-visible or client-visible payload (the ``contracts.retrieval``
    split), and the read endpoint reauthorizes every member live instead of
    trusting it.
    """

    id = models.CharField(
        primary_key=True,
        max_length=80,
        default=generate_evidence_set_id,
        editable=False,
    )
    turn = models.ForeignKey(
        ChatTurn, on_delete=models.CASCADE, related_name='evidence_sets'
    )
    authorization_scope_hash = models.CharField(max_length=64, blank=True, default='')
    analysis_scope_hash = models.CharField(max_length=64, blank=True, default='')
    source_class = models.CharField(max_length=64)
    filters = models.JSONField(default=dict, blank=True)
    population_count = models.PositiveIntegerField()
    evaluated_count = models.PositiveIntegerField()
    displayed_count = models.PositiveIntegerField(default=0)
    complete_population = models.BooleanField()
    high_watermarks = models.JSONField(default=dict, blank=True)
    snapshot_hash = models.CharField(max_length=64, blank=True, default='')
    supports_expansion = models.BooleanField(default=False)
    member_count = models.PositiveIntegerField(default=0)
    member_cap = models.PositiveIntegerField(default=25000)
    calculation = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Indexes per Migration 3; the cap is a hard §7.6 envelope."""

        ordering = ['created_at']
        indexes = [
            models.Index(fields=['turn'], name='aichat_evset_turn_idx'),
            models.Index(fields=['source_class'], name='aichat_evset_class_idx'),
            models.Index(fields=['snapshot_hash'], name='aichat_evset_snap_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(member_count__lte=models.F('member_cap')),
                name='aichat_evset_members_within_cap',
            ),
            models.CheckConstraint(
                condition=Q(member_cap__lte=25000),
                name='aichat_evset_cap_within_envelope',
            ),
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'{self.pk} ({self.source_class}, {self.member_count} members)'


class ChatEvidenceSetMember(models.Model):
    """One evaluated operand of an expandable evidence set (§7.6).

    Stores references only — source class, object id, and a version/
    lifecycle marker. NO source text columns exist by design: the read
    endpoint resolves labels live from the record after reauthorizing the
    viewer, so revocation is indistinguishable from deletion and nothing
    here can leak.
    """

    set = models.ForeignKey(
        ChatEvidenceSet, on_delete=models.CASCADE, related_name='members'
    )
    ordinal = models.PositiveIntegerField()
    source_class = models.CharField(max_length=64)
    source_object_id = models.CharField(max_length=64)
    source_version = models.CharField(max_length=128, blank=True, default='')

    class Meta:
        """Uniqueness and the exact-expansion lookup index (Migration 3)."""

        ordering = ['ordinal']
        constraints = [
            models.UniqueConstraint(
                fields=['set', 'ordinal'], name='aichat_evidence_member_ordinal_uniq'
            )
        ]
        indexes = [
            models.Index(
                fields=['source_class', 'source_object_id'],
                name='aichat_evidence_member_src_idx',
            )
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'{self.set_id}#{self.ordinal} ({self.source_class})'


class ControlledDocumentState(models.TextChoices):
    """Lifecycle states for a governed document revision."""

    DRAFT = 'draft', 'Draft'
    INDEXING = 'indexing', 'Indexing'
    INDEXED = 'indexed', 'Indexed'
    FAILED = 'failed', 'Failed'
    SUPERSEDED = 'superseded', 'Superseded'
    ARCHIVED = 'archived', 'Archived'


class ControlledDocument(models.Model):
    """A scope-bound, immutable source revision for AI document retrieval."""

    selection_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    document_id = models.CharField(max_length=128)
    revision = models.CharField(max_length=64)
    title = models.CharField(max_length=255)
    document_class = models.CharField(max_length=128)
    scope_key = models.CharField(max_length=255)
    scope_hash = models.CharField(max_length=64)
    access_class = models.CharField(max_length=64)
    source_filename = models.CharField(max_length=255)
    source_location = models.CharField(max_length=1024)
    source_sha256 = models.CharField(max_length=64, blank=True, default='')
    revision_date = models.DateField(null=True, blank=True)
    facility = models.CharField(max_length=255, blank=True, default='')
    process_area = models.CharField(max_length=255, blank=True, default='')
    asset_id = models.CharField(max_length=64, blank=True, default='')
    child_asset_id = models.CharField(max_length=64, blank=True, default='')
    work_order_id = models.CharField(max_length=64, blank=True, default='')
    repair_packet_id = models.CharField(max_length=64, blank=True, default='')
    state = models.CharField(
        max_length=16,
        choices=ControlledDocumentState.choices,
        default=ControlledDocumentState.DRAFT,
        db_index=True,
    )
    is_current = models.BooleanField(default=False)
    search_index_name = models.CharField(max_length=128, blank=True, default='')
    indexed_at = models.DateTimeField(null=True, blank=True)
    indexing_error_code = models.CharField(max_length=64, blank=True, default='')
    # S17 A4: which embedding model produced this revision's chunk vectors and
    # at what dimensionality. Blank/0 marks revisions indexed before the stamp
    # existed; the governed re-embed command backfills them.
    embedding_model = models.CharField(max_length=128, blank=True, default='')
    embedding_dimensions = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='controlled_documents_created',
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='controlled_documents_approved',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Registry uniqueness, indexes, and lifecycle guards."""

        ordering = ['scope_key', 'document_id', '-created_at']
        indexes = [
            models.Index(
                fields=['scope_hash', 'document_id', 'state'],
                name='aichat_ctrl_doc_scope_idx',
            ),
            models.Index(
                fields=['asset_id', 'state'], name='aichat_ctrl_doc_asset_idx'
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['scope_key', 'document_id', 'revision'],
                name='aichat_ctrl_doc_revision_uniq',
            ),
            models.UniqueConstraint(
                fields=['scope_key', 'document_id'],
                condition=Q(is_current=True),
                name='aichat_ctrl_doc_current_uniq',
            ),
            models.CheckConstraint(
                condition=~Q(document_id=''), name='aichat_ctrl_doc_id_not_empty'
            ),
            models.CheckConstraint(
                condition=~Q(revision=''), name='aichat_ctrl_doc_revision_not_empty'
            ),
            models.CheckConstraint(
                condition=~Q(scope_key=''), name='aichat_ctrl_doc_scope_not_empty'
            ),
            models.CheckConstraint(
                condition=~Q(scope_hash=''), name='aichat_ctrl_doc_hash_not_empty'
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(is_current=True) | Q(state=ControlledDocumentState.INDEXED)
                ),
                name='aichat_ctrl_doc_current_indexed',
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(state=ControlledDocumentState.INDEXED)
                    | (~Q(source_sha256='') & ~Q(search_index_name=''))
                ),
                name='aichat_ctrl_doc_indexed_source',
            ),
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'{self.document_id} revision {self.revision}'


class ApplicabilityKind(models.TextChoices):
    """How one document revision claims to apply to equipment (S8b, A6)."""

    EXACT_MACHINE = 'exact_machine', 'Exact machine'
    INVERTER_MODEL = 'inverter_model', 'Inverter model'
    FIRMWARE_CONFIG = 'firmware_config', 'Firmware / configuration'
    FLEET_WIDE = 'fleet_wide', 'Fleet-wide'


class ApplicabilityState(models.TextChoices):
    """Lifecycle of one applicability claim. Append-only in spirit."""

    PROPOSED = 'proposed', 'Proposed'
    VERIFIED = 'verified', 'Verified'
    REVOKED = 'revoked', 'Revoked'
    SUPERSEDED = 'superseded', 'Superseded'


class ControlledDocumentApplicability(models.Model):
    """Verified applicability of one document revision to equipment (S8b).

    Deliberately in ``aichat`` with TEXT/INT-keyed machine targets rather
    than an FK into ``assets`` — the app has zero schema edges toward
    ``assets`` (AI-only settings run without it) and that stays true. The
    intra-app FK is PROTECT: an applicability claim pins its exact
    document row, and ``document_content_sha256`` copies the revision's
    content hash at proposal time so a re-ingest with different bytes
    silently invalidates every old verification (byte-anchored, never
    name-anchored).

    Nothing automated reaches ``verified``: a human with
    ``verify_document_applicability`` verifies, the proposer can never be
    that human (DB-enforced), and model/configuration kinds additionally
    require a distinct engineering countersign
    (``countersign_document_applicability``) before the state machine
    activates the row. Model inference and serial backfills only ever
    create ``proposed`` rows.

    Targets use non-null defaults (``0`` / ``''``) instead of NULLs so the
    live-row partial-unique works identically on every backend.
    """

    document = models.ForeignKey(
        ControlledDocument,
        on_delete=models.PROTECT,
        related_name='applicability_claims',
    )
    document_content_sha256 = models.CharField(max_length=64)
    kind = models.CharField(max_length=20, choices=ApplicabilityKind.choices)

    #: assets.AssetMachine pk (0 = not a machine target). Int-keyed on
    #: purpose: no cross-app FK, and pk resolution stays authorization-
    #: checked at read time via the maintenance scope helpers.
    target_machine_id = models.PositiveIntegerField(default=0)
    #: The machine serial as stamped on documents (verbatim operator text).
    target_serial = models.CharField(max_length=255, blank=True, default='')
    target_model = models.CharField(max_length=255, blank=True, default='')
    target_config = models.JSONField(default=dict, blank=True)

    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='applicability_proposals',
    )
    proposal_basis = models.TextField()
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='applicability_verifications',
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    countersigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='applicability_countersigns',
    )
    countersigned_at = models.DateTimeField(null=True, blank=True)

    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)

    state = models.CharField(
        max_length=16,
        choices=ApplicabilityState.choices,
        default=ApplicabilityState.PROPOSED,
        db_index=True,
    )
    revoke_reason = models.TextField(blank=True, default='')
    revoked_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='supersedes',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata: the verification workflow's DB-level teeth."""

        ordering = ['-created_at']
        permissions = [
            (
                'verify_document_applicability',
                'Can verify document applicability claims',
            ),
            (
                'countersign_document_applicability',
                'Can engineering-countersign model/configuration applicability',
            ),
        ]
        indexes = [
            models.Index(
                fields=['target_machine_id', 'state'], name='aichat_docappl_machine_idx'
            ),
            models.Index(
                fields=['target_serial', 'state'], name='aichat_docappl_serial_idx'
            ),
        ]
        constraints = [
            # One LIVE claim per (document, kind, target tuple).
            models.UniqueConstraint(
                fields=[
                    'document',
                    'kind',
                    'target_machine_id',
                    'target_serial',
                    'target_model',
                ],
                condition=Q(state='verified'),
                name='aichat_docappl_live_uniq',
            ),
            # Per-kind target coherence: a claim names exactly the target
            # shape its kind means, nothing more.
            models.CheckConstraint(
                condition=(
                    ~Q(kind='exact_machine')
                    | (Q(target_machine_id__gt=0) & Q(target_model=''))
                ),
                name='aichat_docappl_machine_target',
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(kind__in=['inverter_model', 'firmware_config'])
                    | (~Q(target_model='') & Q(target_machine_id=0))
                ),
                name='aichat_docappl_model_target',
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(kind='fleet_wide')
                    | (
                        Q(target_machine_id=0)
                        & Q(target_model='')
                        & Q(target_serial='')
                    )
                ),
                name='aichat_docappl_fleet_target',
            ),
            # Two-party: the proposer can never verify their own claim.
            models.CheckConstraint(
                condition=(
                    Q(verified_by__isnull=True) | ~Q(verified_by=F('proposed_by'))
                ),
                name='aichat_docappl_two_party',
            ),
            # verified state requires the verification record...
            models.CheckConstraint(
                condition=(
                    ~Q(state='verified')
                    | (Q(verified_by__isnull=False) & Q(verified_at__isnull=False))
                ),
                name='aichat_docappl_verified_rec',
            ),
            # ...and model/configuration kinds require the countersign.
            models.CheckConstraint(
                condition=(
                    ~(
                        Q(state='verified')
                        & Q(kind__in=['inverter_model', 'firmware_config'])
                    )
                    | Q(countersigned_by__isnull=False)
                ),
                name='aichat_docappl_countersign',
            ),
            models.CheckConstraint(
                condition=(~Q(state='revoked') | Q(revoked_at__isnull=False)),
                name='aichat_docappl_revoked_rec',
            ),
            models.CheckConstraint(
                condition=~Q(document_content_sha256=''),
                name='aichat_docappl_sha_not_empty',
            ),
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'{self.document_id}:{self.kind}:{self.state}'


class MessageFeedbackRating(models.TextChoices):
    """A reader's verdict on one assistant message."""

    UP = 'up', 'Helpful'
    DOWN = 'down', 'Not helpful'


class MessageFeedback(models.Model):
    """One user's durable rating of one assistant message.

    The drawer's thumbs previously died in React state, so the team had no
    ground-truth quality signal at all — and no before/after instrument for
    behaviour changes such as diagnosis turns becoming refusals. Because the
    message row carries thread, turn and metadata linkage, each rating joins
    to workflow, route and tool trace for free. One row per (message, user);
    re-rating updates in place, so the ledger records the latest verdict.
    """

    message = models.ForeignKey(
        ChatMessage, on_delete=models.CASCADE, related_name='feedback'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+'
    )
    rating = models.CharField(max_length=8, choices=MessageFeedbackRating.choices)
    reason = models.CharField(max_length=500, blank=True, default='')
    #: The id the client used when it rated a freshly streamed message whose
    #: durable pk it did not yet know — audit breadcrumb only, never identity.
    client_message_id = models.CharField(max_length=80, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """One live verdict per reader per message."""

        constraints = [
            models.UniqueConstraint(
                fields=['message', 'user'], name='aichat_feedback_one_per_user'
            )
        ]
        indexes = [
            models.Index(
                fields=['rating', 'created_at'], name='aichat_feedback_rating_idx'
            )
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'{self.rating} on {self.message_id}'


class RetrievalMiss(models.Model):
    """One controlled-corpus search outcome, query metadata only (S16 A7).

    ``search_manuals`` already computed everything here — hit count, top
    score, how the machine filter resolved — and discarded it, so "what
    questions can the manuals not answer" was unknowable. Every search
    writes one row (the rollup filters to misses); the ledger stores the
    question and its outcome, never any retrieved or generated answer text.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    #: The natural-language question, capped at the search contract's bound.
    query = models.CharField(max_length=500)
    hit_count = models.PositiveIntegerField(default=0)
    top_score = models.FloatField(null=True, blank=True)
    #: not_requested / not_applied / applied / ambiguous — mirrors the
    #: machine_filter outcome the search itself returns.
    machine_filter = models.CharField(max_length=16, blank=True, default='')
    document_class = models.CharField(max_length=128, blank=True, default='')
    scope_key = models.CharField(max_length=255, blank=True, default='')
    #: Which retrieval surface wrote the row: 'governed' (controlled manuals)
    #: or 'attachment' (R2 uploaded-document corpus). Keeps the two rollup
    #: vocabularies separable without prefix hacks in document_class.
    #: db_default keeps the column insertable by pre-R2 code — the postgres
    #: server is shared by aimms-experimental and aimms-dev, so the OTHER env
    #: keeps writing ledger rows during the deploy window (dark-safe rule).
    corpus = models.CharField(
        max_length=32, blank=True, default='governed', db_default='governed'
    )
    #: The attachment tool's part-narrowing outcome; same vocabulary as
    #: machine_filter. Empty for governed-corpus rows.
    part_filter = models.CharField(max_length=16, blank=True, default='', db_default='')
    #: S5 shadow evidence: the analysis-scope identity active for the search
    #: (empty when the turn had no typed scope). ``scope_hash`` is the thread
    #: scope's canonical hash — content-free, never query or answer text.
    #: db_default keeps every column insertable by pre-S5 code during the
    #: shared-postgres deploy window (same dark-safe rule as ``corpus``).
    scope_hash = models.CharField(max_length=64, blank=True, default='', db_default='')
    #: all_authorized_assets / explicit_assets / legacy_unconfirmed.
    scope_mode = models.CharField(max_length=32, blank=True, default='', db_default='')
    #: Whether enforce mode actually constrained this search.
    scope_enforced = models.BooleanField(default=False, db_default=False)
    #: How many returned/candidate rows fell outside the explicit scope
    #: (shadow: would have been excluded; enforce: were excluded).
    out_of_scope_hits = models.PositiveIntegerField(default=0, db_default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Rollups slice zero-hit rows by recency."""

        indexes = [
            models.Index(
                fields=['hit_count', 'created_at'], name='aichat_retrmiss_hit_idx'
            )
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'{self.hit_count} hits at {self.created_at:%Y-%m-%d}'


class ProposalAction(models.TextChoices):
    """The verified executable allow-list (WS7); nothing else may execute.

    Each action maps to exactly one canonical command dispatched at confirmation
    (see ``aichat.services.proposals``). Adding one requires that mapping and a
    security review.
    """

    WORK_ORDER_HOLD = 'work_order.hold', 'Hold work order'
    WORK_ORDER_RESUME = 'work_order.resume', 'Resume work order'
    # Scheduling actions (Phase 6c). All single-target and version-checked; each
    # dispatches a tasks.services.scheduling command at confirmation.
    WORK_ORDER_SCHEDULE = 'work_order.schedule', 'Schedule work order'
    WORK_ORDER_RESIZE = 'work_order.resize', 'Resize work order'
    WORK_ORDER_UPDATE = 'work_order.update', 'Update work order plan'
    WORK_ORDER_ASSIGN = 'work_order.assign', 'Assign work order'
    WORK_ORDER_DELETE = 'work_order.delete', 'Delete work order'
    WORK_ORDER_CANCEL = 'work_order.cancel', 'Cancel work order'
    WORK_ORDER_TRANSITION = 'work_order.transition', 'Transition work order lifecycle'
    WORK_ORDER_CREATE = 'work_order.create', 'Create work order'
    WORK_ORDER_CREATE_CHILD = 'work_order.create_child', 'Create child work order'
    # One compound action, not a work order followed by a packet: partial
    # approval of half a repair aggregate would leave an unowned work order.
    REPAIR_WORK_PACKAGE_CREATE = (
        'repair_work_package.create',
        'Create repair work package',
    )
    # Longest value (31 chars) — action_type max_length stays 32.
    WORK_ORDER_GENERATE_PROCUREMENT = (
        'work_order.generate_procurement',
        'Generate procurement child',
    )
    DEPENDENCY_CREATE = 'dependency.create', 'Create dependency'
    DEPENDENCY_DELETE = 'dependency.delete', 'Delete dependency'
    SCHEDULE_OPTIMIZE = 'schedule.optimize', 'Optimize schedule (bulk)'


class ProposalState(models.TextChoices):
    """Lifecycle of one governed action proposal."""

    PROPOSED = 'proposed', 'Proposed'
    EXECUTED = 'executed', 'Executed'
    REJECTED = 'rejected', 'Rejected'
    EXPIRED = 'expired', 'Expired'
    FAILED = 'failed', 'Failed'


TERMINAL_PROPOSAL_STATES = (
    ProposalState.EXECUTED,
    ProposalState.REJECTED,
    ProposalState.EXPIRED,
    ProposalState.FAILED,
)


class ChatActionProposal(models.Model):
    """A durable, expiring, owner-bound request for one allow-listed effect.

    Speech, transcripts, and model output never execute anything: a proposal
    is created from server-derived data, and only a separate authenticated
    visual confirmation may dispatch the canonical domain command. The
    receipt stores the command outcome exactly once (WS7-T2/T6).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='chat_action_proposals',
    )
    scope_key = models.CharField(max_length=255)
    scope_hash = models.CharField(max_length=64)
    thread_id = models.CharField(max_length=255, blank=True)
    source_turn_id = models.CharField(max_length=64, blank=True)
    action_type = models.CharField(max_length=32, choices=ProposalAction.choices)
    # Nullable so future non-single-target actions (dependency pairs, bulk plans)
    # can share the rail; every AI-2 action still binds exactly one target.
    target_work_order_id = models.PositiveIntegerField(null=True, blank=True)
    target_version = models.PositiveIntegerField(null=True, blank=True)
    # Server-validated action parameters (schedule window, assignee, plan fields).
    # Never trusted from the model: re-derived/re-checked at confirmation.
    intent = models.JSONField(default=dict, blank=True)
    preview = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    state = models.CharField(
        max_length=16, choices=ProposalState.choices, default=ProposalState.PROPOSED
    )
    policy_version = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=128)
    # S36 correlation spine: the originating turn's server-minted correlation
    # id, threaded into the tasks-service command at dispatch so one id joins
    # utterance -> proposal -> WorkOrderEvent. Blank on pre-S36 rows (those
    # keep the 3-hop idempotency_key join) and on proposals created outside a
    # turn. Deliberately NOT part of the idempotent-replay comparison.
    correlation_id = models.CharField(
        max_length=100, blank=True, default='', db_index=True
    )
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    receipt = models.JSONField(null=True, blank=True)
    failure_code = models.CharField(max_length=64, blank=True)
    # Exactly one execution authority per proposal. When this is set the global
    # approval queue owns execution and this row is only the chat-side preview:
    # confirming here points at the approval instead of dispatching, so the two
    # rails can never both run the same effect.
    approval = models.ForeignKey(
        'approvals.Approval',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='chat_proposals',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """One intent per key; executed proposals must carry a receipt."""

        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['owner', 'state', 'expires_at'],
                name='aichat_proposal_review_idx',
            )
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['owner', 'idempotency_key'], name='aichat_proposal_intent_key'
            ),
            models.CheckConstraint(
                condition=(
                    Q(state='executed', receipt__isnull=False) | ~Q(state='executed')
                ),
                name='aichat_proposal_executed_receipt',
            ),
            models.CheckConstraint(
                condition=~Q(scope_hash=''), name='aichat_proposal_scope_required'
            ),
        ]

    @property
    def is_terminal(self) -> bool:
        """Whether this proposal can never execute."""
        return self.state in TERMINAL_PROPOSAL_STATES

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'{self.action_type} wo={self.target_work_order_id} ({self.state})'


class AttachmentIngestState(models.TextChoices):
    """Lifecycle states for one auto-ingested attachment revision (R0)."""

    PENDING = 'pending', 'Pending'
    EXTRACTING = 'extracting', 'Extracting'
    EMBEDDING = 'embedding', 'Embedding'
    INDEXED = 'indexed', 'Indexed'
    FAILED = 'failed', 'Failed'
    SUPERSEDED = 'superseded', 'Superseded'
    DELETED = 'deleted', 'Deleted'
    #: Terminal router outcome (R1, decision #10): reachable content the v1
    #: pipeline deliberately does not ingest, with a value-free reason code.
    SKIPPED = 'skipped', 'Skipped'


class AttachmentIngestPipeline(models.TextChoices):
    """Which extraction/embedding path an attachment routed through."""

    DOC = 'doc', 'Document'
    IMAGE = 'image', 'Image'
    VIDEO = 'video', 'Video'


class AttachmentExtractor(models.TextChoices):
    """Which extraction leg produced the indexed text (R1, decision #12)."""

    DI_LAYOUT = 'di_layout', 'Document Intelligence layout'
    DI_READ = 'di_read', 'Document Intelligence read (OCR)'
    DIRECT = 'direct', 'Direct text read'
    FFMPEG = 'ffmpeg', 'FFmpeg segmentation'
    PYPDF_OVERRIDE = 'pypdf_override', 'pypdf (explicit override)'


class AttachmentIngest(models.Model):
    """System-of-record row for one (attachment, content-sha) ingestion.

    Deliberately separate from ``ControlledDocument``: auto-ingested uploads
    carry no curation provenance and must never satisfy a
    ``maintenance_authorized`` filter. ``promoted_controlled_document`` is the
    reserved linkage for the deferred upload→governed promotion flow.
    """

    #: Loose reference (no FK) mirroring Attachment.model_id semantics; the
    #: attachment row may outlive or predate this registry on either env.
    attachment_id = models.PositiveIntegerField(db_index=True)
    model_type = models.CharField(max_length=100)
    model_id = models.PositiveIntegerField()
    #: Client codes whose actors may retrieve this content (stamped at last
    #: projection; recomputed on MachinePart/client changes).
    client_codes = models.JSONField(default=list, blank=True)
    promoted_controlled_document = models.ForeignKey(
        ControlledDocument,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='promoted_from_ingests',
    )
    source_sha256 = models.CharField(max_length=64)
    pipeline = models.CharField(max_length=8, choices=AttachmentIngestPipeline.choices)
    state = models.CharField(
        max_length=16,
        choices=AttachmentIngestState.choices,
        default=AttachmentIngestState.PENDING,
        db_index=True,
    )
    #: Value-free failure code only — provider errors can carry credentials.
    error_code = models.CharField(max_length=64, blank=True, default='')
    chunk_count = models.PositiveIntegerField(default=0)
    segment_count = models.PositiveIntegerField(default=0)
    embedding_model = models.CharField(max_length=128, blank=True, default='')
    embedding_dimensions = models.PositiveIntegerField(default=0)
    #: Which corpus-affecting settings produced this row's vectors/captions.
    #: The homogeneity spine: a backfill re-forces exactly the stale profiles,
    #: because ``run_ingest`` short-circuits on an INDEXED row with the same
    #: sha and would otherwise leave R1-R4 content on the old profile forever.
    embedding_profile = models.CharField(
        max_length=32, blank=True, default='v1', db_default='v1'
    )
    search_index_name = models.CharField(max_length=128, blank=True, default='')
    #: Extraction provenance; silent quality divergence is worse than latency,
    #: so pypdf appears here only via the explicit backfill override.
    extractor = models.CharField(
        max_length=16, choices=AttachmentExtractor.choices, blank=True, default=''
    )
    attempts = models.PositiveIntegerField(default=0)
    #: Set only by the atomic claim (and renewed by the indexed short-circuit).
    #: Winner ordering keys on this, not ``created_at``, because a content
    #: revert re-claims an *old* row — registry-row age is not content recency.
    claimed_at = models.DateTimeField(null=True, blank=True)
    #: EXIF/container capture time. Derived at projection time and never
    #: stored before R5, so a rebuild that omitted it would NULL a live
    #: citation field (Search upload is a full replace, not a merge).
    media_recorded_at = models.DateTimeField(null=True, blank=True)
    #: The ``as_of`` stamped onto projected documents. ``updated_at`` is
    #: ``auto_now`` and is bumped by restamps, so a rebuild keyed on it would
    #: silently rewrite every citation's as_of to the rebuild date.
    indexed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Registry uniqueness and owner lookup indexes."""

        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['model_type', 'model_id'], name='aichat_att_ingest_owner_idx'
            )
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['attachment_id', 'source_sha256'],
                name='aichat_att_ingest_sha_uniq',
            ),
            models.CheckConstraint(
                condition=~Q(source_sha256=''), name='aichat_att_ingest_sha_not_empty'
            ),
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'attachment {self.attachment_id} {self.pipeline} ({self.state})'


class AttachmentChunk(models.Model):
    """One embedded text chunk projected into the attachment-docs index."""

    ingest = models.ForeignKey(
        AttachmentIngest, on_delete=models.CASCADE, related_name='chunks'
    )
    chunk_index = models.PositiveIntegerField()
    page_number = models.PositiveIntegerField(null=True, blank=True)
    section_path = models.CharField(max_length=512, blank=True, default='')
    #: Stored, not derived. ``section_path`` joins ALL non-empty heading
    #: levels while these are ``headings[:3]`` positionally, so splitting the
    #: path is wrong for h2-first documents (the common DI-layout output),
    #: level skips, and preamble text. Both are SearchableFields, so a
    #: split-derived rebuild would silently mutate BM25 scoring.
    heading_1 = models.CharField(max_length=256, blank=True, default='', db_default='')
    heading_2 = models.CharField(max_length=256, blank=True, default='', db_default='')
    heading_3 = models.CharField(max_length=256, blank=True, default='', db_default='')
    content = models.TextField()
    token_count = models.PositiveIntegerField(default=0)
    #: Cohere Embed v4 vector; populated by the embedding stage, so nullable.
    embedding = VectorField(dimensions=1536, null=True, blank=True)
    search_doc_id = models.CharField(max_length=256, blank=True, default='')

    class Meta:
        """Chunk identity within one ingest."""

        ordering = ['ingest', 'chunk_index']
        constraints = [
            models.UniqueConstraint(
                fields=['ingest', 'chunk_index'], name='aichat_att_chunk_idx_uniq'
            )
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'ingest {self.ingest_id} chunk {self.chunk_index}'


class MediaSegmentType(models.TextChoices):
    """What one media-space vector represents."""

    IMAGE = 'image', 'Image'
    VIDEO_SEGMENT = 'video_segment', 'Video segment'


class MediaSegment(models.Model):
    """One embedded image or video segment projected into the media index."""

    ingest = models.ForeignKey(
        AttachmentIngest, on_delete=models.CASCADE, related_name='segments'
    )
    media_type = models.CharField(max_length=16, choices=MediaSegmentType.choices)
    segment_index = models.PositiveIntegerField(default=0)
    timecode_start_s = models.FloatField(null=True, blank=True)
    timecode_end_s = models.FloatField(null=True, blank=True)
    caption = models.TextField(blank=True, default='')
    ocr_text = models.TextField(blank=True, default='')
    transcript = models.TextField(blank=True, default='')
    #: Media-relative path (attachment thumbnail or extracted keyframe) —
    #: never a raw filesystem path.
    thumbnail_path = models.CharField(max_length=512, blank=True, default='')
    #: Gemini Embedding 2 vector; populated by the embedding stage, so nullable.
    embedding = VectorField(dimensions=3072, null=True, blank=True)
    search_doc_id = models.CharField(max_length=256, blank=True, default='')

    class Meta:
        """Segment identity within one ingest."""

        ordering = ['ingest', 'segment_index']
        constraints = [
            models.UniqueConstraint(
                fields=['ingest', 'segment_index'], name='aichat_media_seg_idx_uniq'
            )
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return (
            f'ingest {self.ingest_id} segment {self.segment_index} ({self.media_type})'
        )


class AIQuotaProfile(models.TextChoices):
    """The finite quota profiles (S12, §8.9 — no blanket exemptions)."""

    STANDARD = 'standard', 'Standard'
    EVALUATION = 'evaluation', 'Evaluation'
    SERVICE = 'service', 'Service'


class AIQuotaPolicy(models.Model):
    """One versioned, immutable quota policy (S12 Migration 4).

    Every ENFORCEABLE policy version carries explicit numeric caps at all
    three levels — a missing level must fail validation, never inherit an
    unlimited default (§8.9). Rows are append-only: a change is a new
    version, and ``active`` retires old ones without rewriting history.
    """

    profile = models.CharField(max_length=16, choices=AIQuotaProfile.choices)
    version = models.PositiveIntegerField()
    #: Daily token caps (UTC day). Non-null by construction — the ORM level
    #: of the "missing level fails" rule; 0 is a deliberate hard-zero cap.
    user_daily_tokens = models.PositiveBigIntegerField()
    tenant_daily_tokens = models.PositiveBigIntegerField()
    deployment_daily_tokens = models.PositiveBigIntegerField()
    requests_per_minute = models.PositiveIntegerField()
    requests_per_hour = models.PositiveIntegerField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        """Policy identity plus the dedicated management permissions."""

        constraints = [
            models.UniqueConstraint(
                fields=['profile', 'version'], name='aichat_quota_policy_ver_uniq'
            )
        ]
        permissions = [
            ('assign_quota_policy', 'Can assign AI quota policies to users'),
            ('view_quota_reports', 'Can view AI quota operational reports'),
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'quota policy {self.profile} v{self.version}'


class AIQuotaAssignment(models.Model):
    """One expiring, auditable policy assignment (server-side only)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ai_quota_assignments',
    )
    policy = models.ForeignKey(
        AIQuotaPolicy, on_delete=models.PROTECT, related_name='assignments'
    )
    #: Expiring by construction — an assignment without an end date is not
    #: representable (the anti-"blanket exemption" rule).
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True, default='')
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Lookup path: the resolver reads (user, expires_at)."""

        indexes = [models.Index(fields=['user', 'expires_at'])]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'quota assignment user={self.user_id} policy={self.policy_id}'


class AIQuotaReservationState(models.TextChoices):
    """Reservation lifecycle for reconciliation."""

    RESERVED = 'reserved', 'Reserved'
    SETTLED = 'settled', 'Settled'
    EXPIRED = 'expired', 'Expired'


class AIQuotaReservation(models.Model):
    """Best-effort durable mirror of one turn's cache reservation (S12).

    The live counters stay in the shared cache; these rows exist for
    reconciliation and audit — writing one never blocks a turn, and a stale
    RESERVED row is expired by the scheduled reconciliation task.
    """

    idempotency_key = models.CharField(max_length=255, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    policy_version = models.PositiveIntegerField(default=0)
    #: ModelPurpose value or 'turn' for the whole-turn envelope.
    purpose = models.CharField(max_length=64, blank=True, default='')
    reserved_tokens = models.PositiveBigIntegerField(default=0)
    settled_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    state = models.CharField(
        max_length=16,
        choices=AIQuotaReservationState.choices,
        default=AIQuotaReservationState.RESERVED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        """Reconciliation scans (state, expires_at)."""

        indexes = [models.Index(fields=['state', 'expires_at'])]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'quota reservation {self.idempotency_key} ({self.state})'


class AIQuotaAuditAction(models.TextChoices):
    """What a quota audit row records."""

    ASSIGNED = 'assigned', 'Policy assigned'
    REVOKED = 'revoked', 'Assignment revoked'
    POLICY_CREATED = 'policy_created', 'Policy created'
    POLICY_DEACTIVATED = 'policy_deactivated', 'Policy deactivated'


class AIQuotaAuditEvent(models.Model):
    """Immutable audit row for every quota management action (S12)."""

    action = models.CharField(max_length=32, choices=AIQuotaAuditAction.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    policy = models.ForeignKey(
        AIQuotaPolicy,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    #: Short content-free detail (reason codes, expiry dates) — never text
    #: that could carry user content.
    detail = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Windowed reads (retention purge, reports) scan by time."""

        indexes = [
            models.Index(fields=['created_at'], name='aichat_quota_audit_time_idx')
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'quota audit {self.action} at {self.created_at}'


class AIPilotStopRole(models.TextChoices):
    """The five Q43 stop-authority roles.

    Each may STOP unilaterally; clearing requires a recorded approval
    from all five.
    """

    ENGINEERING = 'engineering', 'Engineering owner'
    PRODUCT = 'product', 'Product owner'
    MAINTENANCE_SAFETY = 'maintenance_safety', 'Maintenance/safety authority'
    DOCUMENT_CONTROL = 'document_control', 'Document-control/data owner'
    SECURITY_PRIVACY = 'security_privacy', 'Security/privacy owner'


class AIPilotStopReason(models.TextChoices):
    """The Q50 automatic-stop list, code for code — content-free by design."""

    POPULATION_DISCLOSURE = (
        'population_disclosure',
        'Cross-scope or incomplete population disclosure',
    )
    UNSAFE_PROCEDURAL_CONTENT = (
        'unsafe_procedural_content',
        'Unsafe uncited procedural content',
    )
    UNAUTHORIZED_EFFECT = 'unauthorized_effect', 'Unauthorized effect or rail bypass'
    FABRICATED_LIVE_STATE = 'fabricated_live_state', 'Fabricated current-state claim'
    STALE_DOMAIN_CONTAMINATION = (
        'stale_domain_contamination',
        'Stale-domain contamination',
    )
    EVAL_FIXTURE_LEAK = (
        'eval_fixture_leak',
        'Eval fixture returned to a non-eval principal',
    )
    ENFORCE_FAIL_OPEN = 'enforce_fail_open', 'Enforce-mode limiter store failed open'
    MODEL_PIN_MISMATCH = (
        'model_pin_mismatch',
        'Model identity mismatch in a frozen window',
    )
    MANUAL = 'manual', 'Owner judgment'


class AIPilotStopLatch(models.Model):
    """One durable pilot-stop episode (S15, §15.4/§16).

    Append-only: engaging creates the single active row (the partial
    unique constraint makes double-engage structurally impossible);
    clearing sets ``cleared_at`` when approvals exist for ALL FIVE roles —
    rows are never deleted. The AI plane's fail-closed admission gate
    reads this state through a cached loader.
    """

    reason_code = models.CharField(max_length=40, choices=AIPilotStopReason.choices)
    source = models.CharField(max_length=16, default='manual')  # manual | automatic
    #: Codes/identifiers only — never prose, prompts, or source text.
    detail = models.CharField(max_length=255, blank=True, default='')
    engaged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    engaged_role = models.CharField(
        max_length=32, blank=True, default='', choices=AIPilotStopRole.choices
    )
    active = models.BooleanField(default=True)
    engaged_at = models.DateTimeField(auto_now_add=True)
    cleared_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """One active episode; cleared rows keep their timestamp."""

        ordering = ['-engaged_at']
        constraints = [
            models.UniqueConstraint(
                fields=['active'],
                condition=Q(active=True),
                name='aichat_pilot_latch_one_active',
            ),
            models.CheckConstraint(
                condition=(
                    (Q(active=True) & Q(cleared_at__isnull=True))
                    | (Q(active=False) & Q(cleared_at__isnull=False))
                ),
                name='aichat_pilot_latch_cleared_state',
            ),
        ]
        permissions = [
            ('manage_pilot_stop', 'Can set or clear the AI pilot stop latch')
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        state = 'ACTIVE' if self.active else 'cleared'
        return f'pilot latch {self.reason_code} ({state})'


class AIPilotStopApproval(models.Model):
    """One immutable recorded restart approval — one per role per episode."""

    latch = models.ForeignKey(
        AIPilotStopLatch, on_delete=models.CASCADE, related_name='approvals'
    )
    role = models.CharField(max_length=32, choices=AIPilotStopRole.choices)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    #: A content-free reference (dossier/document id) backing the approval.
    reference = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Each role approves an episode at most once."""

        constraints = [
            models.UniqueConstraint(
                fields=['latch', 'role'], name='aichat_pilot_approval_role_uniq'
            )
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'pilot restart approval {self.role}'


class AIRequestRejection(models.Model):
    """Content-free ledger of typed pre-turn rejections (S15, §8.10).

    429/503 rejections happen before any ``ChatTurn`` exists, so without
    this row the operations report's error denominator undercounts.
    Written best-effort by the rejection paths — a write failure must
    never block or alter the rejection response.
    """

    code = models.CharField(max_length=40)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Report queries scan by code and window."""

        indexes = [
            models.Index(
                fields=['code', 'created_at'], name='aichat_rejection_code_idx'
            )
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'request rejection {self.code}'


class ChatThreadTombstone(models.Model):
    """Non-content receipt of a purged thread (S16, Q48).

    Written by every thread purge — immediate user deletion and scheduled
    400-day expiry alike — so grant/audit integrity survives content
    removal. Deliberately carries NO title, summary, or scope key: counts
    and hashes only. Tombstones themselves purge 400 days after
    ``deleted_at``.
    """

    #: The purged thread's original primary key.
    thread_id = models.CharField(max_length=80, unique=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    namespace = models.CharField(max_length=16)
    scope_hash = models.CharField(max_length=64)
    thread_created_at = models.DateTimeField()
    deleted_at = models.DateTimeField(auto_now_add=True)
    #: Why the thread was purged: ``user_delete`` | ``retention_expiry``.
    reason = models.CharField(max_length=32)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    message_count = models.PositiveIntegerField(default=0)
    turn_count = models.PositiveIntegerField(default=0)
    had_grants = models.BooleanField(default=False)

    class Meta:
        """The tombstone purge scans by deletion time."""

        indexes = [
            models.Index(fields=['deleted_at'], name='aichat_tombstone_time_idx')
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'thread tombstone {self.thread_id} ({self.reason})'


class ChatThreadGrantTombstone(models.Model):
    """Audit record of one grant that existed when its thread was purged.

    ``ChatThreadGrant`` rows cannot outlive their thread (FK integrity), so
    the "grant audit rows survive" promise transfers here at purge time:
    who held access, who granted it, and when it was revoked — queryable
    per grantee, with zero content. Cascades with its thread tombstone.
    """

    tombstone = models.ForeignKey(
        ChatThreadTombstone, on_delete=models.CASCADE, related_name='grants'
    )
    grantee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    access = models.CharField(max_length=16)
    granted_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'grant tombstone on {self.tombstone.thread_id}'


class AIUsageMonthlyAggregate(models.Model):
    """Sanitized monthly usage aggregate — the 13-month store (S16, Q48).

    Written by the 90-day detail scrub BEFORE the per-turn
    ``metadata['usage']`` blobs are removed, then retained thirteen months.
    ``user_id`` is a raw integer, not a foreign key: the aggregate is
    content-free and must survive account deletion, and a nullable FK
    would break upsert uniqueness under Postgres NULL-distinctness.
    """

    #: First day of the UTC month this row aggregates.
    month = models.DateField()
    #: ``turn_usage`` (ChatMessage usage metadata) | ``quota_reservation``.
    source = models.CharField(max_length=32)
    user_id = models.IntegerField(null=True, blank=True)
    #: Per-source breakdown key: usage-event source name or reservation
    #: purpose; empty for the per-user total row.
    dimension = models.CharField(max_length=64, blank=True, default='')
    turn_count = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveBigIntegerField(default=0)
    output_tokens = models.PositiveBigIntegerField(default=0)
    cached_input_tokens = models.PositiveBigIntegerField(default=0)
    total_tokens = models.PositiveBigIntegerField(default=0)
    reserved_tokens = models.PositiveBigIntegerField(default=0)
    settled_tokens = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """One row per (month, source, user, dimension); purge scans by month."""

        constraints = [
            models.UniqueConstraint(
                fields=['month', 'source', 'user_id', 'dimension'],
                name='aichat_usage_agg_uniq',
            )
        ]
        indexes = [models.Index(fields=['month'], name='aichat_usage_agg_month_idx')]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'usage aggregate {self.source} {self.month}'


class AIRetentionOutbox(models.Model):
    """Retryable record of one external deletion the purge owes (S16).

    Filesystem and search-index removals cannot ride a database
    transaction, so each is recorded here and driven to completion with
    backoff; a missing target is success (idempotent). ``failed_permanent``
    rows are the failure metric the operations report surfaces.
    """

    #: ``upload_dir`` today; ``search_index`` reserved for thread-linked
    #: index artifacts added later.
    kind = models.CharField(max_length=32)
    #: The deletion target: the thread id for ``upload_dir``.
    reference = models.CharField(max_length=255)
    state = models.CharField(max_length=16, default='pending')
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField()
    last_error_code = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Idempotent enqueue; the processor claims due pending rows."""

        constraints = [
            models.UniqueConstraint(
                fields=['kind', 'reference'],
                condition=Q(state='pending'),
                name='aichat_outbox_pending_uniq',
            )
        ]
        indexes = [
            models.Index(
                fields=['state', 'next_attempt_at'], name='aichat_outbox_claim_idx'
            )
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'retention outbox {self.kind}:{self.reference} ({self.state})'
