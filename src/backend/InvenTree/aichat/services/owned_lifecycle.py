"""Owner/scope-bound bulk deletion and streaming transcript export.

These operations cover the repository's owned conversations, not account or
client erasure. A signed position never replaces the repository boundary.
"""

from __future__ import annotations

from django.core import signing
from django.db.models import Max
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from aichat.models import ChatMessage, ChatThreadTombstone
from aichat.services import retention
from aichat.services.threads import InvalidBoundary

TOKEN_SALT = 'aichat.owned-delete.request.v1'
CURSOR_SALT = 'aichat.owned-delete.cursor.v1'
TOKEN_MAX_AGE = 86400


def _boundary(repository):
    return [str(repository.actor_id), repository.scope_hash, repository.namespace]


def _decode(token, *, salt, repository):
    try:
        value = signing.loads(token, salt=salt, max_age=TOKEN_MAX_AGE)
        if not isinstance(value, dict) or value['boundary'] != _boundary(repository):
            raise ValueError
        cutoff = parse_datetime(value['cutoff'])
        if cutoff is None or timezone.is_naive(cutoff) or cutoff > timezone.now():
            raise ValueError
        return value, cutoff
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise InvalidBoundary('Invalid deletion continuation') from None


def prepare_owned_deletion(repository):
    """Freeze an owner/scope cutoff without deleting or creating any records.

    The browser obtains this token before its first mutation so a lost response
    can be retried without expanding the operation to later conversations.
    """
    request = {'boundary': _boundary(repository), 'cutoff': timezone.now().isoformat()}
    return {
        'scope': 'owned_threads',
        'cutoff': request['cutoff'],
        'request_token': signing.dumps(request, salt=TOKEN_SALT),
    }


def delete_owned_batch(repository, *, limit=20, request_token=None, cursor=None):
    """Delete at most 100 conversations and verify their registered derivatives.

    Reuse request_token to restart a failed scan with the original cutoff; use
    next_cursor to continue it. Each page also visits historical tombstones so
    retries re-probe completed/missing obligations. The cumulative failure count
    prevents a later clean page from hiding an earlier failure. Tokens expire
    after 24 hours. This is a bounded scan, not a persistent erasure job.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise InvalidBoundary('Deletion limit must be between 1 and 100')
    if cursor and not request_token:
        raise InvalidBoundary('Deletion continuation requires its request token')
    if request_token:
        request, cutoff = _decode(request_token, salt=TOKEN_SALT, repository=repository)
    else:
        request_token = prepare_owned_deletion(repository)['request_token']
        request, cutoff = _decode(request_token, salt=TOKEN_SALT, repository=repository)
    after = ''
    incomplete = processed = 0
    if cursor:
        position, _ = _decode(cursor, salt=CURSOR_SALT, repository=repository)
        try:
            if position['cutoff'] != request['cutoff']:
                raise ValueError
            after = position['after']
            incomplete, processed = position['incomplete'], position['processed']
            if (
                not isinstance(after, str)
                or len(after) > 80
                or type(incomplete) is not int
                or type(processed) is not int
                or not 0 <= incomplete <= processed
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise InvalidBoundary('Invalid deletion continuation') from None

    # Include tombstones even when their outbox says done. Order the union in
    # the database so cursor comparisons use the same collation as selection.
    live = repository._threads().filter(created_at__lte=cutoff, pk__gt=after)
    deleted = ChatThreadTombstone.objects.filter(
        owner_id=repository.actor_id,
        scope_hash=repository.scope_hash,
        namespace=repository.namespace,
        thread_created_at__lte=cutoff,
        thread_id__gt=after,
    )
    ids = list(
        live
        .order_by()
        .values_list('id', flat=True)
        .union(deleted.order_by().values_list('thread_id', flat=True))
        .order_by('id')[: limit + 1]
    )
    results = []
    for thread_id in ids[:limit]:
        try:
            receipt = repository.delete(thread_id)
        except Exception:
            # Core failures keep their live root eligible on a restarted scan.
            # Do not expose exception messages, SQL, content or filesystem paths.
            receipt = {'thread_id': thread_id, 'status': 'purge_incomplete'}
        results.append(receipt)
        processed += 1
        incomplete += receipt['status'] != 'deleted'
    next_cursor = None
    if len(ids) > limit:
        next_cursor = signing.dumps(
            {
                **request,
                'after': ids[limit - 1],
                'incomplete': incomplete,
                'processed': processed,
            },
            salt=CURSOR_SALT,
        )
    # Even an empty boundary cannot qualify an unregistered derivative family.
    coverage_complete = not retention.THREAD_DERIVATIVES.uncovered_models()
    remaining_threads = repository._threads().filter(created_at__lte=cutoff).count()
    return {
        'status': 'deleted'
        if next_cursor is None
        and incomplete == 0
        and remaining_threads == 0
        and coverage_complete
        else 'purge_incomplete',
        'scope': 'owned_threads',
        'cutoff': request['cutoff'],
        'request_token': request_token,
        'next_cursor': next_cursor,
        'processed': processed,
        'incomplete': incomplete,
        'remaining_threads': remaining_threads,
        'results': results,
    }


def transcript_records(repository, *, chunk_size=200):
    """Yield explicit transcript projections with bounded ORM pages.

    Export is a live read, not a transactionally consistent database snapshot.
    A start-time cutoff excludes subsequent threads/messages. Every message
    page reapplies ownership; shared threads and arbitrary metadata are omitted.
    The final record distinguishes a finished artifact from a truncated file.
    """
    if type(chunk_size) is not int or not 1 <= chunk_size <= 1000:
        raise InvalidBoundary('Export chunk size must be between 1 and 1000')
    cutoff = timezone.now()
    yield {
        'type': 'manifest',
        'schema_version': 1,
        'scope': 'owned_thread_transcripts',
        'owner_id': str(repository.actor_id),
        'scope_hash': repository.scope_hash,
        'namespace': repository.namespace,
        'started_at': cutoff.isoformat(),
        'consistency': 'live_read_with_creation_cutoff',
        'includes': ['thread_titles', 'stored_summaries', 'messages'],
        'excludes': [
            'shared_threads',
            'voice_capture_revisions',
            'voice_utterances',
            'attachments',
            'evidence',
            'proposals',
            'operational_records',
            'legacy_conversations',
            'semantic_facts',
            'provider_state',
        ],
    }
    after = ''
    thread_count = message_count = 0
    while True:
        rows = list(
            repository
            ._threads()
            .filter(created_at__lte=cutoff, pk__gt=after)
            .order_by('pk')[:chunk_size]
        )
        if not rows:
            break
        for thread in rows:
            watermark = (
                ChatMessage.objects.filter(
                    thread_id=thread.pk, created_at__lte=cutoff
                ).aggregate(sequence=Max('sequence'))['sequence']
                or 0
            )
            yield {
                'type': 'thread',
                'thread_id': thread.pk,
                'title': thread.title,
                'summary': thread.summary,
                'summary_through_sequence': thread.summary_through_sequence,
                'created_at': thread.created_at.isoformat(),
                'updated_at': thread.updated_at.isoformat(),
                'message_watermark': watermark,
            }
            thread_count += 1
            sequence = 0
            while sequence < watermark:
                messages = list(
                    ChatMessage.objects.filter(
                        thread__in=repository._threads().filter(pk=thread.pk),
                        sequence__gt=sequence,
                        sequence__lte=watermark,
                        created_at__lte=cutoff,
                    ).order_by('sequence')[:chunk_size]
                )
                if not messages:
                    break
                for message in messages:
                    yield {
                        'type': 'message',
                        'thread_id': thread.pk,
                        'message_id': message.pk,
                        'sequence': message.sequence,
                        'role': message.role,
                        'content': message.content,
                        'modality': message.modality,
                        'created_at': message.created_at.isoformat(),
                    }
                    message_count += 1
                sequence = messages[-1].sequence
        after = rows[-1].pk
    yield {
        'type': 'complete',
        'threads': thread_count,
        'messages': message_count,
        'finished_at': timezone.now().isoformat(),
    }
