"""Owner-bound governance operations for scoped conversations (SC-ADR-006/007).

Every lookup applies owner and scope before resolving a caller-supplied
identifier, so cross-owner access is indistinguishable from a missing
conversation. The durable transcript lives in the scoped ``ChatThread``
namespace and is only ever addressed through the same boundary-bound
repository the rest of the store uses.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from aichat.models import ConversationStatus, ScopedConversation, ThreadNamespace
from aichat.services.context import ChatContext
from aichat.services.threads import ThreadRepository


class ConversationError(Exception):
    """Base class carrying a stable conversation error code."""

    code = 'CONVERSATION_INVALID'


class ConversationNotFound(ConversationError):  # noqa: N818
    """Unknown conversation, or one outside the caller's boundary."""

    code = 'CONVERSATION_NOT_FOUND'


class ConversationReadOnly(ConversationError):  # noqa: N818
    """The conversation no longer accepts mutation."""

    code = 'CONVERSATION_READ_ONLY'


def _repository(owner, scope_key: str) -> ThreadRepository:
    """Return the scoped-namespace transcript repository for this owner."""
    return ThreadRepository(owner, scope_key, namespace=ThreadNamespace.SCOPED)


def create_conversation(
    *, owner, context: ChatContext, title: str = ''
) -> ScopedConversation:
    """Create one scoped conversation and its durable scoped transcript."""
    if not isinstance(title, str) or len(title) > 255:
        raise ConversationError('title is invalid')
    label = title or context.display_label[:255]
    with transaction.atomic():
        thread, _ = _repository(owner, context.scope_key).get_or_create(title=label)
        return ScopedConversation.objects.create(
            owner=owner,
            context_type=context.context_type,
            object_id=context.object_id,
            scope_key=context.scope_key,
            scope_hash=context.scope_hash,
            title=label,
            ai_thread_id=thread.pk,
            last_context_revision=context.source_revision,
        )


def _owned(owner, scope_hash: str):
    """Return the non-negotiable conversation boundary queryset."""
    return ScopedConversation.objects.filter(owner=owner, scope_hash=scope_hash)


def list_conversations(
    *,
    owner,
    scope_hash: str,
    context_type: str | None = None,
    object_id: str | None = None,
) -> list[ScopedConversation]:
    """List the owner's conversations, optionally filtered to one record."""
    rows = _owned(owner, scope_hash).exclude(status=ConversationStatus.DELETED)
    if context_type is not None:
        rows = rows.filter(context_type=context_type)
    if object_id is not None:
        rows = rows.filter(object_id=str(object_id))
    return list(rows)


def get_conversation(*, owner, scope_hash: str, conversation_id) -> ScopedConversation:
    """Owner-safe lookup; existence is never disclosed across owners."""
    try:
        return _owned(owner, scope_hash).get(pk=conversation_id)
    except Exception as exc:  # DoesNotExist / ValidationError / ValueError
        raise ConversationNotFound('no such conversation') from exc


def rename_conversation(
    *, owner, scope_hash: str, conversation_id, title: str
) -> ScopedConversation:
    """Rename one active conversation."""
    if not isinstance(title, str) or not title or len(title) > 255:
        raise ConversationError('title is invalid')
    with transaction.atomic():
        conversation = (
            _owned(owner, scope_hash)
            .select_for_update()
            .filter(pk=conversation_id)
            .first()
        )
        if conversation is None or conversation.status == ConversationStatus.DELETED:
            raise ConversationNotFound('no such conversation')
        if conversation.status != ConversationStatus.ACTIVE:
            raise ConversationReadOnly('conversation is read only')
        conversation.title = title
        conversation.save(update_fields=['title', 'updated_at'])
        _repository(owner, conversation.scope_key).rename(
            conversation.ai_thread_id, title
        )
        return conversation


def close_conversation(
    *, owner, scope_hash: str, conversation_id
) -> ScopedConversation:
    """Idempotently close one conversation (it becomes read only)."""
    with transaction.atomic():
        conversation = (
            _owned(owner, scope_hash)
            .select_for_update()
            .filter(pk=conversation_id)
            .first()
        )
        if conversation is None or conversation.status == ConversationStatus.DELETED:
            raise ConversationNotFound('no such conversation')
        if conversation.status != ConversationStatus.CLOSED:
            conversation.status = ConversationStatus.CLOSED
            conversation.save(update_fields=['status', 'updated_at'])
        return conversation


def delete_conversation(*, owner, scope_hash: str, conversation_id) -> None:
    """Tombstone the governance row and delete the scoped transcript.

    The governance, citation, grant, and tool-invocation rows are retained
    as audit metadata; only the transcript content is removed.
    """
    with transaction.atomic():
        conversation = (
            _owned(owner, scope_hash)
            .select_for_update()
            .filter(pk=conversation_id)
            .first()
        )
        if conversation is None or conversation.status == ConversationStatus.DELETED:
            raise ConversationNotFound('no such conversation')
        _repository(owner, conversation.scope_key).delete(conversation.ai_thread_id)
        conversation.status = ConversationStatus.DELETED
        conversation.deleted_at = timezone.now()
        conversation.save(update_fields=['status', 'deleted_at', 'updated_at'])
