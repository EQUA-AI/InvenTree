"""The authorized synchronous repository for durable chat history.

All public lookup operations apply the actor, server scope, and namespace
boundary before resolving a caller-supplied identifier. Async callers must use
``asgiref.sync.sync_to_async(..., thread_sensitive=True)`` around these methods.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from aichat.models import (
    ChatMessage,
    ChatThread,
    ChatTurn,
    MessageRole,
    ThreadNamespace,
    TurnModality,
    TurnState,
    generate_thread_id,
)


class ThreadRepositoryError(Exception):
    """Base class for safe repository errors."""


class AnonymousActorRejected(ThreadRepositoryError):  # noqa: N818
    """Raised when no authenticated boundary principal is supplied."""


class InvalidBoundary(ThreadRepositoryError, ValueError):  # noqa: N818
    """Raised when the trusted scope or namespace boundary is malformed."""


class ThreadNotFound(ThreadRepositoryError, LookupError):  # noqa: N818
    """Raised for every thread identifier outside the repository boundary."""


class ScopedThreadRejected(ThreadNotFound):
    """Raised when a scoped identifier reaches a legacy unscoped repository."""


class IdempotencyConflict(ThreadRepositoryError):  # noqa: N818
    """Raised when one key is reused with a different request fingerprint."""


class TurnStateConflict(ThreadRepositoryError):  # noqa: N818
    """Raised when a terminal turn is asked to transition or change result."""


@dataclass(frozen=True, slots=True)
class BeginTurnResult:
    """Result of beginning a new turn or replaying an existing turn."""

    turn: ChatTurn
    replayed: bool


def scope_fingerprint(scope_key: str) -> str:
    """Return the SHA-256 fingerprint of an exact server-derived scope key."""
    if not isinstance(scope_key, str) or not scope_key:
        raise InvalidBoundary('A non-empty server scope key is required')

    return hashlib.sha256(scope_key.encode('utf-8')).hexdigest()


def canonical_request_fingerprint(
    *,
    content: str,
    modality: str,
    trusted_context: Mapping[str, Any],
    modality_metadata: Mapping[str, Any] | None = None,
) -> str:
    """Hash the normalized inputs which define exact turn replay."""
    envelope = {
        'content': content,
        'modality': modality,
        'trusted_context': trusted_context,
        'modality_metadata': modality_metadata or {},
    }
    canonical = json.dumps(
        envelope, ensure_ascii=False, separators=(',', ':'), sort_keys=True
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


class ThreadRepository:
    """Boundary-bound repository and sole authorized chat persistence API."""

    def __init__(
        self, actor: Any, scope_key: str, namespace: str = ThreadNamespace.UNSCOPED
    ) -> None:
        """Bind all subsequent operations to an authenticated boundary."""
        actor_id: Any
        if isinstance(actor, bool) or actor is None:
            raise AnonymousActorRejected('An authenticated actor is required')
        if isinstance(actor, (int, str)):
            actor_id = actor
        else:
            if not getattr(actor, 'is_authenticated', False):
                raise AnonymousActorRejected('An authenticated actor is required')
            actor_id = getattr(actor, 'pk', None)
        if actor_id is None or actor_id == '':
            raise AnonymousActorRejected('An authenticated actor is required')
        if namespace not in ThreadNamespace.values:
            raise InvalidBoundary('Unknown chat thread namespace')

        self.actor = actor
        self.actor_id = actor_id
        self.scope_key = scope_key
        self.scope_hash = scope_fingerprint(scope_key)
        self.namespace = namespace

    def _threads(self) -> QuerySet[ChatThread]:
        """Return the non-negotiable authorization boundary queryset."""
        return ChatThread.objects.filter(
            owner_id=self.actor_id,
            scope_key=self.scope_key,
            scope_hash=self.scope_hash,
            namespace=self.namespace,
        )

    def _reject_wrong_namespace_id(self, thread_id: str) -> None:
        """Fail closed when identifier syntax crosses a namespace boundary."""
        is_scoped_id = thread_id.startswith('scoped_')
        if self.namespace == ThreadNamespace.UNSCOPED and is_scoped_id:
            raise ScopedThreadRejected('Scoped threads are unavailable here')
        if self.namespace == ThreadNamespace.SCOPED and not is_scoped_id:
            raise ThreadNotFound('Thread not found')

    def _get_thread(self, thread_id: str) -> ChatThread:
        """Resolve a thread only after applying the complete boundary."""
        self._reject_wrong_namespace_id(thread_id)
        try:
            return self._threads().get(pk=thread_id)
        except ChatThread.DoesNotExist as exc:
            raise ThreadNotFound('Thread not found') from exc

    def _lock_thread(self, thread_id: str) -> ChatThread:
        """Resolve and lock a parent thread under the complete boundary."""
        self._reject_wrong_namespace_id(thread_id)
        try:
            return self._threads().select_for_update().get(pk=thread_id)
        except ChatThread.DoesNotExist as exc:
            raise ThreadNotFound('Thread not found') from exc

    def get_or_create(
        self, thread_id: str | None = None, *, title: str = ''
    ) -> tuple[ChatThread, bool]:
        """Get a boundary-visible thread or create it without claiming collisions."""
        if thread_id is None:
            thread_id = generate_thread_id()
            if self.namespace == ThreadNamespace.SCOPED:
                thread_id = f'scoped_{thread_id}'
        if not isinstance(thread_id, str) or not thread_id or len(thread_id) > 80:
            raise InvalidBoundary('Thread identifier is invalid')
        if not isinstance(title, str) or len(title) > 255:
            raise InvalidBoundary('Thread title is invalid')
        self._reject_wrong_namespace_id(thread_id)

        existing = self._threads().filter(pk=thread_id).first()
        if existing is not None:
            return existing, False

        try:
            with transaction.atomic():
                thread = ChatThread.objects.create(
                    id=thread_id,
                    owner_id=self.actor_id,
                    scope_key=self.scope_key,
                    scope_hash=self.scope_hash,
                    namespace=self.namespace,
                    title=title,
                )
        except IntegrityError as exc:
            existing = self._threads().filter(pk=thread_id).first()
            if existing is not None:
                return existing, False
            raise ThreadNotFound('Thread not found') from exc

        return thread, True

    def list(self) -> list[ChatThread]:
        """Return materialized threads within the complete boundary."""
        return list(self._threads())

    def get(self, thread_id: str) -> ChatThread:
        """Return one thread within the complete boundary."""
        return self._get_thread(thread_id)

    def rename(self, thread_id: str, title: str) -> ChatThread:
        """Rename one boundary-visible thread."""
        if not isinstance(title, str) or len(title) > 255:
            raise InvalidBoundary('Thread title is invalid')
        with transaction.atomic():
            thread = self._lock_thread(thread_id)
            thread.title = title
            thread.save(update_fields=['title', 'updated_at'])
        return thread

    def delete(self, thread_id: str) -> None:
        """Delete one boundary-visible transcript and its turns."""
        with transaction.atomic():
            thread = self._lock_thread(thread_id)
            thread.delete()

    def _append_locked(
        self,
        thread: ChatThread,
        *,
        role: str,
        content: str,
        modality: str,
        metadata: Mapping[str, Any] | None,
        correlation_id: str,
    ) -> ChatMessage:
        """Allocate and append a message while the parent row is locked."""
        message = ChatMessage.objects.create(
            thread=thread,
            sequence=thread.next_sequence,
            role=role,
            content=content,
            modality=modality,
            metadata=dict(metadata or {}),
            correlation_id=correlation_id,
        )
        thread.next_sequence += 1
        thread.save(update_fields=['next_sequence', 'updated_at'])
        return message

    @staticmethod
    def _validate_message(
        *, role: str, content: str, modality: str, correlation_id: str
    ) -> None:
        """Validate portable scalar message values before persistence."""
        if role not in MessageRole.values:
            raise InvalidBoundary('Unknown chat message role')
        if modality not in TurnModality.values:
            raise InvalidBoundary('Unknown turn modality')
        if not isinstance(content, str):
            raise InvalidBoundary('Message content must be text')
        if not isinstance(correlation_id, str) or len(correlation_id) > 100:
            raise InvalidBoundary('Correlation identifier is invalid')

    def append(
        self,
        thread_id: str,
        *,
        role: str,
        content: str,
        modality: str = TurnModality.TEXT,
        metadata: Mapping[str, Any] | None = None,
        correlation_id: str = '',
    ) -> ChatMessage:
        """Append one exactly ordered message to a boundary-visible thread."""
        self._validate_message(
            role=role, content=content, modality=modality, correlation_id=correlation_id
        )
        with transaction.atomic():
            thread = self._lock_thread(thread_id)
            return self._append_locked(
                thread,
                role=role,
                content=content,
                modality=modality,
                metadata=metadata,
                correlation_id=correlation_id,
            )

    @staticmethod
    def _validate_turn_input(
        *,
        content: str,
        modality: str,
        idempotency_key: str,
        request_fingerprint: str,
        correlation_id: str,
    ) -> None:
        """Validate normalized turn scalars before opening a transaction."""
        ThreadRepository._validate_message(
            role=MessageRole.USER,
            content=content,
            modality=modality,
            correlation_id=correlation_id,
        )
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key
            or len(idempotency_key) > 255
        ):
            raise InvalidBoundary('Idempotency key is invalid')
        if (
            not isinstance(request_fingerprint, str)
            or len(request_fingerprint) != 64
            or any(char not in '0123456789abcdef' for char in request_fingerprint)
        ):
            raise InvalidBoundary('Request fingerprint must be a SHA-256 hex digest')

    def begin_turn(
        self,
        thread_id: str,
        *,
        content: str,
        modality: str,
        trusted_context: Mapping[str, Any],
        modality_metadata: Mapping[str, Any] | None,
        idempotency_key: str,
        request_fingerprint: str,
        correlation_id: str,
    ) -> BeginTurnResult:
        """Begin once, replay exactly, or reject a conflicting key reuse."""
        self._validate_turn_input(
            content=content,
            modality=modality,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            correlation_id=correlation_id,
        )
        if not isinstance(trusted_context, Mapping):
            raise InvalidBoundary('Trusted context must be an object')
        if modality_metadata is not None and not isinstance(modality_metadata, Mapping):
            raise InvalidBoundary('Modality metadata must be an object')

        with transaction.atomic():
            thread = self._lock_thread(thread_id)
            existing = (
                ChatTurn.objects
                .select_for_update()
                .filter(thread=thread, idempotency_key=idempotency_key)
                .first()
            )
            if existing is not None:
                if existing.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflict(
                        'Idempotency key was used for a different request'
                    )
                return BeginTurnResult(turn=existing, replayed=True)

            input_message = self._append_locked(
                thread,
                role=MessageRole.USER,
                content=content,
                modality=modality,
                metadata=modality_metadata,
                correlation_id=correlation_id,
            )
            turn = ChatTurn.objects.create(
                thread=thread,
                input_message=input_message,
                modality=modality,
                request_fingerprint=request_fingerprint,
                idempotency_key=idempotency_key,
                trusted_context=dict(trusted_context),
                modality_metadata=dict(modality_metadata or {}),
                correlation_id=correlation_id,
            )
            return BeginTurnResult(turn=turn, replayed=False)

    def _turn_thread_id(self, turn_id: str) -> str:
        """Resolve a turn's parent id only through the complete boundary."""
        thread_id = (
            ChatTurn.objects
            .filter(
                pk=turn_id,
                thread__owner_id=self.actor_id,
                thread__scope_key=self.scope_key,
                thread__scope_hash=self.scope_hash,
                thread__namespace=self.namespace,
            )
            .values_list('thread_id', flat=True)
            .first()
        )
        if thread_id is None:
            raise ThreadNotFound('Turn not found')
        return thread_id

    def terminal(
        self,
        turn_id: str,
        *,
        state: str,
        canonical_result: Mapping[str, Any],
        output_content: str | None = None,
        output_metadata: Mapping[str, Any] | None = None,
        workflow_id: str = '',
    ) -> ChatTurn:
        """Atomically persist the exact output and one terminal transition."""
        if state not in TurnState.terminal_values():
            raise TurnStateConflict('A terminal state is required')
        if not isinstance(canonical_result, Mapping):
            raise InvalidBoundary('Canonical result must be an object')
        if output_content is None:
            output_content = str(canonical_result.get('detailed_response', ''))
        self._validate_message(
            role=MessageRole.ASSISTANT,
            content=output_content,
            modality=TurnModality.TEXT,
            correlation_id='',
        )
        if not isinstance(workflow_id, str) or len(workflow_id) > 100:
            raise InvalidBoundary('Workflow identifier is invalid')

        with transaction.atomic():
            thread_id = self._turn_thread_id(turn_id)
            thread = self._lock_thread(thread_id)
            try:
                turn = ChatTurn.objects.select_for_update().get(
                    pk=turn_id, thread=thread
                )
            except ChatTurn.DoesNotExist as exc:
                raise ThreadNotFound('Turn not found') from exc

            result = dict(canonical_result)
            if turn.is_terminal:
                same_output = (
                    turn.output_message is not None
                    and turn.output_message.content == output_content
                )
                if (
                    turn.state == state
                    and turn.canonical_result == result
                    and same_output
                ):
                    return turn
                raise TurnStateConflict('Turn already has a different terminal result')

            metadata = dict(output_metadata or {})
            if workflow_id:
                metadata.setdefault('workflow_id', workflow_id)
            output_message = self._append_locked(
                thread,
                role=MessageRole.ASSISTANT,
                content=output_content,
                modality=TurnModality.TEXT,
                metadata=metadata,
                correlation_id=turn.correlation_id,
            )
            turn.output_message = output_message
            turn.canonical_result = result
            turn.state = state
            turn.completed_at = timezone.now()
            turn.save(
                update_fields=[
                    'output_message',
                    'canonical_result',
                    'state',
                    'completed_at',
                    'updated_at',
                ]
            )
            if workflow_id:
                thread.last_workflow = workflow_id
                thread.save(update_fields=['last_workflow', 'updated_at'])
            return turn

    def replay(
        self,
        thread_id: str,
        *,
        idempotency_key: str,
        request_fingerprint: str | None = None,
    ) -> ChatTurn:
        """Return a stored turn/result without disclosing another boundary."""
        thread = self._get_thread(thread_id)
        try:
            turn = ChatTurn.objects.select_related(
                'input_message', 'output_message'
            ).get(thread=thread, idempotency_key=idempotency_key)
        except ChatTurn.DoesNotExist as exc:
            raise ThreadNotFound('Turn not found') from exc
        if (
            request_fingerprint is not None
            and turn.request_fingerprint != request_fingerprint
        ):
            raise IdempotencyConflict(
                'Idempotency key was used for a different request'
            )
        return turn

    def messages(self, thread_id: str) -> list[ChatMessage]:
        """Return a materialized, ordered transcript inside the boundary."""
        thread = self._get_thread(thread_id)
        return list(ChatMessage.objects.filter(thread=thread))
