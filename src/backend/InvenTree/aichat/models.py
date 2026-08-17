"""Portable durable models for normalized AI conversations."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

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

    Mirrors the dropped ``ScopedConversationGrant`` semantics (B6): grant
    rows are audit records and are never hard-deleted — revocation stamps
    ``revoked_at``; expiry is optional. Only explicit single-thread READS
    honor a grant; every write path stays owner-only.
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
    DIRECT = 'direct', 'Direct text read'
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
