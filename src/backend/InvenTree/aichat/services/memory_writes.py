"""The consent-bound proposed-fact writer; activation belongs to confirmation.

There are no provider calls here. Worker orchestration supplies a shield result
from its earlier bounded call and must retain its own execution lease. Every
mutable authorization is checked again inside the persistence transaction.
"""

import re

from django.db import connection, transaction
from django.db.models import Q

from tasks.scope import ScopeError, require_machine_scope, require_work_order_scope

from ai.core.trusted_context import resolve_actor_locale
from aichat.models import (
    ChatMessage,
    MemoryFact,
    MemoryFactClaim,
    MemoryFactEvent,
    MemoryFactTombstone,
)
from aichat.services.memory_eligibility import evaluate_memory_eligibility
from aichat.services.memory_lifecycle import (
    claim_fingerprint,
    fingerprint,
    slot_fingerprint,
)
from aichat.services.memory_policy import (
    MemoryPolicyError,
    render_verified,
    validate_candidate,
    validate_source_text,
    verify_native_field,
)

_EXPLICIT_REMEMBER = re.compile(
    r'\b(remember|recuerda|recordar|merke|erinnere|souviens|memorise)\b', re.IGNORECASE
)


def _client_for_entity(owner, candidate, eligible):
    from tasks.models import WorkOrder

    from assets.models import AssetMachine, Client

    kind, identity = candidate['entity_kind'], candidate['entity_id']
    if kind == 'user':
        if identity != str(owner.pk):
            raise MemoryPolicyError('source_scope')
        return ''
    try:
        if kind == 'client':
            client = Client.objects.filter(code=identity, active=True).first()
        elif kind == 'machine':
            record = AssetMachine.objects.select_related('client').get(pk=int(identity))
            require_machine_scope(owner, record)
            client = record.client
        else:
            record = WorkOrder.objects.select_related('machine__client').get(
                pk=int(identity)
            )
            require_work_order_scope(owner, record)
            client = record.machine.client if record.machine_id else None
        if client is None or not client.active or client.code not in eligible:
            raise MemoryPolicyError('source_scope')
        return client.code
    except (ValueError, ScopeError, AssetMachine.DoesNotExist, WorkOrder.DoesNotExist):
        raise MemoryPolicyError('source_scope') from None


def blocking_tombstones(fact):
    """Typed slots catch paraphrases; exact claims catch attempts to change slots."""
    return (
        MemoryFactTombstone.objects
        .filter(owner_id=fact.owner_id)
        .exclude(reason='supersede')
        .filter(
            Q(client_code=fact.client_code, slot_fingerprint=slot_fingerprint(fact))
            | Q(claim_fingerprint=fact.claim_fingerprint)
        )
    )


def validate_revival(fact, source, origin):
    """Only a newer explicit request may propose a new row after forgetting."""
    stones = list(blocking_tombstones(fact).order_by('-deleted_at', '-id'))
    if not stones:
        return None
    if (
        origin != 'user_explicit'
        or source.created_at <= stones[0].deleted_at
        or not _EXPLICIT_REMEMBER.search(source.content)
    ):
        raise MemoryPolicyError('tombstoned')
    return stones[0].pk


@transaction.atomic
def propose_fact(
    repository, *, thread_id, source_message_id, candidate, origin, shield_state
):
    """Persist one bounded suggestion with re-read source/consent, never activate."""
    if connection.vendor != 'postgresql':
        raise MemoryPolicyError('postgresql_required')
    if origin not in {'user_explicit', 'compaction', 'tool_read'}:
        # Mem0 is not admitted by the custom writer. It needs its own signed
        # admission gate before sharing this service's validated persistence.
        raise MemoryPolicyError('origin_unavailable')
    if shield_state not in {'clear', 'flagged', 'unavailable', 'pending'}:
        raise MemoryPolicyError('shield_state')
    thread = repository._lock_thread(thread_id)
    owner = thread.owner
    source = ChatMessage.objects.filter(
        pk=source_message_id, thread=thread, role='user'
    ).first()
    if source is None or source.sequence <= thread.memory_through_sequence:
        raise MemoryPolicyError('source_window')
    eligibility = evaluate_memory_eligibility(
        owner, thread, source_time=source.created_at
    )
    if not eligibility.allowed:
        raise MemoryPolicyError(eligibility.reason)
    validate_source_text(source.content)
    locale = resolve_actor_locale(owner.pk)
    data = validate_candidate(candidate, locale=locale)
    client = _client_for_entity(owner, data, eligibility.clients)
    preference = data['memory_type'] == 'user_preference'
    verification = 'inferred'
    native = None
    if not preference and data.get('source_model'):
        native = verify_native_field(
            owner,
            source_model=data['source_model'],
            source_id=data.get('source_id'),
            source_field=data.get('source_field'),
            eligible_clients=eligibility.clients,
        )
        if (
            native.entity_kind != data['entity_kind']
            or str(native.source_id) != data['entity_id']
            or native.client_code != client
        ):
            raise MemoryPolicyError('source_binding')
        data['text'] = render_verified(native, locale)
        data['slot_key'] = native.source_field
        verification = 'tool_verified'
    fact = MemoryFact(
        owner=owner,
        client_code=client,
        entity_kind=data['entity_kind'],
        entity_id=data['entity_id'],
        slot_key=data['slot_key'],
        memory_type=data['memory_type'],
        topics=data['topics'],
        text=data['text'],
        text_lang=locale,
        canonical_value=native.value if native else None,
        verification_class=verification,
        origin=origin,
        origin_modality='voice' if source.modality == 'voice' else 'chat',
        visibility_scope='user_private' if preference else 'client_shared',
        classification=data['classification'],
        source_thread=thread,
        source_message=source,
        notice_version=eligibility.notice_version,
        shield_state=shield_state,
        injection_flag=shield_state == 'flagged',
        claim_fingerprint=claim_fingerprint(data['text']),
    )
    fact.revives = validate_revival(fact, source, origin)
    existing = MemoryFact.objects.filter(
        owner=owner,
        source_thread=thread,
        source_message=source,
        client_code=client,
        entity_kind=fact.entity_kind,
        entity_id=fact.entity_id,
        memory_type=fact.memory_type,
        slot_key=fact.slot_key,
        claim_fingerprint=fact.claim_fingerprint,
        lifecycle_state__in=['proposed', 'active'],
    ).first()
    if existing is not None:
        return existing, False
    if (
        MemoryFact.objects.filter(owner=owner, lifecycle_state='proposed').count()
        >= 100
    ):
        raise MemoryPolicyError('pending_limit')
    fact.save()
    MemoryFactClaim.objects.create(
        fact=fact,
        source_thread=thread,
        source_message=source,
        source_class='tool_record' if native else 'user_utterance',
        content_trust='trusted_record' if native else 'untrusted_fenced',
        source_model=native.source_model if native else '',
        source_object_id=str(native.source_id) if native else '',
        source_field=native.source_field if native else '',
        source_sequence=source.sequence,
        source_fingerprint=fingerprint('source-v1', [thread.pk, source.pk]),
    )
    MemoryFactEvent.objects.create(
        owner_id=owner.pk,
        fact_id=fact.pk,
        action='propose',
        version=fact.version,
        claim_fingerprint=fact.claim_fingerprint,
    )
    return fact, True
