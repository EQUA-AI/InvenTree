"""Portable durable models for normalized AI conversations."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


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
    """Conversation namespaces with different authorization contracts."""

    UNSCOPED = 'unscoped', 'Unscoped'
    SCOPED = 'scoped', 'Scoped'


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
    summary = models.TextField(blank=True, default='')
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
                    Q(namespace=ThreadNamespace.SCOPED, id__startswith='scoped_')
                    | (
                        Q(namespace=ThreadNamespace.UNSCOPED)
                        & ~Q(id__startswith='scoped_')
                    )
                ),
                name='aichat_thread_namespace_id',
            ),
        ]

    def __str__(self) -> str:
        """Return a safe diagnostic representation."""
        return f'{self.namespace} chat thread {self.pk}'


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
            models.Index(fields=['thread', 'sequence'], name='aichat_message_order_idx')
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


class ProposalAction(models.TextChoices):
    """The verified executable allow-list (WS7); nothing else may execute."""

    WORK_ORDER_HOLD = 'work_order.hold', 'Hold work order'
    WORK_ORDER_RESUME = 'work_order.resume', 'Resume work order'


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
    target_work_order_id = models.PositiveIntegerField()
    target_version = models.PositiveIntegerField()
    preview = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    state = models.CharField(
        max_length=16, choices=ProposalState.choices, default=ProposalState.PROPOSED
    )
    policy_version = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    receipt = models.JSONField(null=True, blank=True)
    failure_code = models.CharField(max_length=64, blank=True)
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
