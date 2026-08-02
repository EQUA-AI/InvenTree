"""Durable message-feedback recording for the chat drawer's thumbs.

One narrow write path: the owner of a thread rates one assistant message.
Identity resolution is deliberately server-side — freshly streamed messages
carry client-generated ids that never exist in the database, so an unknown id
falls back to the thread's latest assistant message and the client's id is
kept only as an audit breadcrumb. The race this accepts (rating lands while a
newer answer completes concurrently in the same thread) is narrow and honest;
the alternative — trusting a client-supplied id as identity — is not.
"""

from __future__ import annotations

import hashlib

from django.db import transaction

from aichat.models import (
    ChatMessage,
    ChatThread,
    MessageFeedback,
    MessageFeedbackRating,
    ThreadNamespace,
)


class FeedbackError(Exception):
    """A stable-coded refusal; the code is the API error contract."""

    def __init__(self, code: str, message: str):
        """Store the stable code alongside the human summary."""
        super().__init__(message)
        self.code = code


VALID_RATINGS = frozenset(MessageFeedbackRating.values)


def _owned_thread(owner, thread_id: str) -> ChatThread:
    """The caller's own unscoped drawer thread, or the uniform refusal."""
    thread = ChatThread.objects.filter(
        pk=thread_id, owner=owner, namespace=ThreadNamespace.UNSCOPED
    ).first()
    if thread is None:
        raise FeedbackError('FEEDBACK_THREAD_UNAVAILABLE', 'no such thread')
    return thread


def record_feedback(
    *,
    owner,
    thread_id: str,
    message_id: str,
    rating: str,
    reason: str = '',
    content_sha256: str = '',
) -> MessageFeedback:
    """Upsert the owner's rating of one assistant message in their thread.

    Fail-closed boundaries: the thread must belong to the caller (a foreign or
    missing thread is the same uniform error) and sit in the unscoped drawer
    namespace, only assistant messages are ratable, and the rating vocabulary
    is closed.

    Identity resolution, strongest first: (1) a durable message pk; (2) the
    SHA-256 of the rated content — freshly streamed messages carry client
    ids that never exist server-side, but the *content* the user rated is
    exact, so hashing it attributes the verdict to the right row even for
    older answers in the session; (3) the thread's newest assistant message,
    as the last resort with the client id kept as an audit breadcrumb.
    """
    if rating not in VALID_RATINGS:
        raise FeedbackError('FEEDBACK_INVALID_RATING', 'rating must be up or down')
    reason = (reason or '').strip()[:500]

    thread = _owned_thread(owner, thread_id)

    message = ChatMessage.objects.filter(
        pk=message_id, thread=thread, role='assistant'
    ).first()
    client_message_id = ''
    if message is None and content_sha256:
        client_message_id = str(message_id)[:80]
        wanted = content_sha256.strip().lower()
        recent = ChatMessage.objects.filter(thread=thread, role='assistant').order_by(
            '-sequence'
        )[:50]
        for candidate in recent:
            digest = hashlib.sha256(candidate.content.encode('utf-8')).hexdigest()
            if digest == wanted:
                message = candidate
                break
    if message is None:
        # Last resort: bind the thread's newest assistant message.
        client_message_id = str(message_id)[:80]
        message = (
            ChatMessage.objects
            .filter(thread=thread, role='assistant')
            .order_by('-sequence')
            .first()
        )
    if message is None:
        raise FeedbackError('FEEDBACK_MESSAGE_UNAVAILABLE', 'no assistant message')

    with transaction.atomic():
        row, _created = MessageFeedback.objects.update_or_create(
            message=message,
            user=owner,
            defaults={
                'rating': rating,
                'reason': reason,
                'client_message_id': client_message_id,
            },
        )
    return row


def clear_feedback(
    *, owner, thread_id: str, message_id: str, content_sha256: str = ''
) -> bool:
    """Retract the owner's verdict for one message.

    The ledger records the LATEST verdict, and 'no verdict' is a legitimate
    latest state. Returns whether a row existed; missing rows are not an
    error — retraction is idempotent.
    """
    thread = _owned_thread(owner, thread_id)
    rows = MessageFeedback.objects.filter(user=owner, message__thread=thread)
    match = rows.filter(message__pk=message_id)
    if not match.exists() and content_sha256:
        wanted = content_sha256.strip().lower()
        for row in rows.select_related('message'):
            digest = hashlib.sha256(row.message.content.encode('utf-8')).hexdigest()
            if digest == wanted:
                match = rows.filter(pk=row.pk)
                break
    deleted, _ = match.delete()
    return bool(deleted)
