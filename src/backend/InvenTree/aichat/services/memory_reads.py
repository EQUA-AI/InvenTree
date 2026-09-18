"""Owner-only, bounded memory inspection/export; no model or vector calls."""

import uuid

from django.contrib.auth import get_user_model
from django.core import signing
from django.db.models import Q
from django.utils import timezone

from tasks.scope import ScopeError, client_codes_for_actor

from aichat.memory_choices import DurableMemoryType, MemoryTopic
from aichat.models import MemoryFact, UserMemorySettings
from aichat.services.memory_policy import MemoryPolicyError
from aichat.services.memory_writes import _client_for_entity
from InvenTree.restore_hold import restore_hold_enabled

PAGE_SIZE = 50
CURSOR_SALT = 'aichat.memory.inspection.v1'
CONVERSATION_SALT = 'aichat.memory.conversations.v1'


def excluded_conversations(owner, *, cursor=''):
    """Page owned exclusion metadata without transcript, title or client labels."""
    from aichat.models import ChatThread
    from aichat.services.memory_controls import thread_memory_status

    if restore_hold_enabled():
        raise ValueError('Memory unavailable')
    owner = get_user_model().objects.filter(pk=owner.pk, is_active=True).first()
    if owner is None:
        raise ValueError('Memory unavailable')
    after = ''
    if cursor:
        try:
            payload = signing.loads(cursor, salt=CONVERSATION_SALT, max_age=900)
            if payload['owner'] != owner.pk or not isinstance(payload['after'], str):
                raise ValueError
            after = payload['after']
        except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
            raise ValueError('Invalid cursor') from exc
    rows = list(
        ChatThread.objects.filter(owner=owner, pk__gt=after).order_by('pk')[
            : PAGE_SIZE + 1
        ]
    )
    page = rows[:PAGE_SIZE]
    results = []
    for thread in page:
        status = thread_memory_status(owner, thread)
        if status['extraction_status'] != 'eligible':
            results.append({**status, 'created_at': thread.created_at.isoformat()})
    following = (
        signing.dumps({'owner': owner.pk, 'after': page[-1].pk}, salt=CONVERSATION_SALT)
        if len(rows) > PAGE_SIZE
        else None
    )
    return {'results': results, 'next_cursor': following}


def can_read(owner, fact, *, clients=None):
    """Stored client labels cannot authorize reassigned or deleted entities."""
    if fact.owner_id != owner.pk or not owner.is_active:
        return False
    if not fact.client_code:
        return fact.entity_kind == 'user' and fact.entity_id == str(owner.pk)
    try:
        clients = client_codes_for_actor(owner) if clients is None else clients
        return (
            _client_for_entity(
                owner,
                {'entity_kind': fact.entity_kind, 'entity_id': fact.entity_id},
                clients,
            )
            == fact.client_code
        )
    except (ScopeError, MemoryPolicyError):
        return False


def fact_payload(fact):
    """Plain data for a freshly authorized fact; no source body or vector."""
    return {
        'id': str(fact.pk),
        'version': fact.version,
        'text': fact.text,
        'text_lang': fact.text_lang,
        'canonical_value': fact.canonical_value,
        'canonical_unit': fact.canonical_unit,
        'memory_type': fact.memory_type,
        'topics': fact.topics,
        'state': fact.lifecycle_state,
        'verification': fact.verification_class,
        'shield_state': fact.shield_state,
        'origin': fact.origin,
        'source_available': bool(fact.source_thread_id and fact.source_message_id),
        'valid_until': fact.valid_until.isoformat() if fact.valid_until else None,
        'last_verified_at': fact.last_verified_at.isoformat()
        if fact.last_verified_at
        else None,
    }


def list_facts(owner, *, state='active', memory_type='', topic='', cursor=''):
    """A signed cursor binds owner and filters; each page reauthorizes access.

    The cursor advances across scanned rows, including inaccessible moved
    entities. No total count or identifiers of those rows are disclosed.
    """
    if (
        state not in {'active', 'proposed'}
        or (memory_type and memory_type not in DurableMemoryType.values)
        or (topic and topic not in MemoryTopic.values)
    ):
        raise ValueError('Invalid memory selection')
    if restore_hold_enabled():
        raise ValueError('Memory unavailable during restore')
    owner = get_user_model().objects.filter(pk=owner.pk, is_active=True).first()
    if owner is None:
        raise ValueError('Memory owner unavailable')
    binding = [owner.pk, state, memory_type, topic]
    after = None
    if cursor:
        try:
            decoded = signing.loads(cursor, salt=CURSOR_SALT, max_age=900)
            if decoded['selection'] != binding:
                raise ValueError
            after = uuid.UUID(decoded['after'])
        except (signing.BadSignature, ValueError, KeyError, TypeError):
            raise ValueError('Invalid memory cursor') from None
    if UserMemorySettings.objects.filter(user=owner, opted_out=True).exists():
        return {'results': [], 'next_cursor': None}
    try:
        clients = client_codes_for_actor(owner)
    except ScopeError:
        clients = frozenset()
    now = timezone.now()
    rows = MemoryFact.objects.filter(
        Q(client_code__in=clients)
        | Q(client_code='', entity_kind='user', entity_id=str(owner.pk)),
        Q(valid_until__isnull=True) | Q(valid_until__gt=now),
        owner=owner,
        lifecycle_state=state,
        valid_from__lte=now,
    ).defer('embedding')
    if memory_type:
        rows = rows.filter(memory_type=memory_type)
    # Use the same bounded Python membership filter for PostgreSQL and fixture
    # SQLite arrays; the cursor advances even when a page has no matching topics.
    if after:
        rows = rows.filter(pk__gt=after)
    scanned = list(rows.order_by('pk')[: PAGE_SIZE + 1])
    page = scanned[:PAGE_SIZE]
    results = [
        fact_payload(fact)
        for fact in page
        if (not topic or topic in fact.topics)
        and can_read(owner, fact, clients=clients)
    ]
    next_cursor = (
        signing.dumps(
            {'selection': binding, 'after': str(page[-1].pk)}, salt=CURSOR_SALT
        )
        if len(scanned) > PAGE_SIZE
        else None
    )
    return {'results': results, 'next_cursor': next_cursor}
