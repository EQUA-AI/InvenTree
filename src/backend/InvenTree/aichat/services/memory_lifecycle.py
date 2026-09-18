"""Immediate, retryable durable-memory forgetting; independent of TTL flags."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Max, Q
from django.utils import timezone

from ai.core.config import get_settings
from ai.core.memory.summary_body import normalize_text
from aichat.models import (
    ChatActionProposal,
    ChatThread,
    MemoryFact,
    MemoryFactClaim,
    MemoryFactEvent,
    MemoryFactTombstone,
    MemoryNoticeAcknowledgement,
    UserMemorySettings,
)
from InvenTree.restore_hold import restore_hold_enabled

LIVE_STATES = ('proposed', 'active', 'superseded', 'expired')
FORGET_REASONS = frozenset({'forget', 'opt_out', 'erasure', 'client_purge'})


def fingerprint(domain, value):
    """Deterministic, domain-separated keyed identity; never a per-row salt."""
    secret = get_settings().aimms_memory_fingerprint_key.get_secret_value()
    if len(secret) < 32:
        raise ValueError('Memory fingerprint configuration unavailable')
    payload = json.dumps(
        [domain, value],
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False,
    )
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def claim_fingerprint(text):
    """The same normalized claim hashes identically across supported origins."""
    return fingerprint('claim-v1', normalize_text(text))


def slot_fingerprint(fact):
    """Identity excludes mutable topics and wording but includes every scope leg."""
    return fingerprint(
        'slot-v1',
        [
            fact.owner_id,
            fact.client_code,
            fact.entity_kind,
            fact.entity_id,
            fact.memory_type,
            fact.slot_key,
            fact.visibility_scope,
            fact.classification,
        ],
    )


def _scrub_proposals(fact_id):
    """Execution receipts survive; the copied preview, intent and rationale do not."""
    rows = ChatActionProposal.objects.filter(
        Q(target_memory_fact_id=fact_id)
        | Q(intent__memory_fact_id=str(fact_id))
        | Q(intent__replacement_fact_id=str(fact_id))
    )
    rows.filter(state='proposed').update(
        state='rejected', failure_code='MEMORY_FORGOTTEN'
    )
    rows.update(intent={}, preview={}, preview_hash='', reason='')


def _forget_locked(fact, *, actor_id, reason):
    if fact.lifecycle_state in {'forgotten', 'withdrawn'}:
        return False
    if reason not in FORGET_REASONS:
        raise ValueError('Invalid memory forget reason')
    source = fingerprint('source-v1', [fact.source_thread_id, fact.source_message_id])
    tombstone, _created = MemoryFactTombstone.objects.update_or_create(
        owner_id=fact.owner_id,
        client_code=fact.client_code,
        slot_fingerprint=slot_fingerprint(fact),
        claim_fingerprint=fact.claim_fingerprint,
        fact_id=fact.pk,
        defaults={
            'owner_joined_at': fact.owner.date_joined,
            'source_fingerprint': source,
            'reason': reason,
            'deleted_at': timezone.now(),
            'source_sequence': fact.claims.aggregate(latest=Max('source_sequence'))[
                'latest'
            ]
            or 0,
        },
    )
    fact.text = ''
    fact.embedding = None
    fact.embedding_profile = ''
    fact.canonical_value = None
    fact.canonical_unit = ''
    fact.lifecycle_state = (
        'withdrawn' if fact.lifecycle_state == 'proposed' else 'forgotten'
    )
    fact.version += 1
    fact.source_thread = None
    fact.source_message = None
    fact.save(
        update_fields=[
            'text',
            'embedding',
            'embedding_profile',
            'canonical_value',
            'canonical_unit',
            'lifecycle_state',
            'version',
            'source_thread',
            'source_message',
            'updated_at',
        ]
    )
    MemoryFactClaim.objects.filter(fact=fact).delete()
    _scrub_proposals(fact.pk)
    MemoryFactEvent.objects.create(
        owner_id=fact.owner_id,
        actor_id=actor_id,
        fact_id=fact.pk,
        tombstone_id=tombstone.pk,
        action='forget',
        version=fact.version,
        claim_fingerprint=fact.claim_fingerprint,
    )
    return True


def _invalidate_summaries(owner_id):
    """Discard owner summaries and their consumed input windows conservatively.

    Summaries have no complete fact-id lineage yet. Clearing the owner's summaries
    prevents a surviving narrative from leaking a just-forgotten claim. Advancing
    to the current sequence also prevents automatic re-compaction of that history;
    original messages remain independently governed conversation data.
    """
    ChatThread.objects.filter(owner_id=owner_id).update(
        summary='', summary_through_sequence=F('next_sequence') - 1
    )
    MemoryFactTombstone.objects.filter(owner_id=owner_id, residual_pending=True).update(
        residual_pending=False
    )


@transaction.atomic
def forget_fact(owner, fact_id, *, expected_version):
    """Owner-only immediate forgetting with optimistic version protection."""
    if restore_hold_enabled():
        raise ValueError('Memory mutation unavailable')
    actor = (
        get_user_model()
        .objects.select_for_update()
        .filter(pk=owner.pk, is_active=True)
        .first()
    )
    if actor is None:
        raise ValueError('Memory mutation unavailable')
    fact = (
        MemoryFact.objects.select_for_update().filter(pk=fact_id, owner=actor).first()
    )
    if fact is None:
        raise ValueError('Memory not found')
    if fact.lifecycle_state in {'forgotten', 'withdrawn'}:
        return {'status': 'forgotten', 'fact_id': str(fact.pk), 'version': fact.version}
    if fact.version != expected_version:
        raise ValueError('Memory version changed')
    _forget_locked(fact, actor_id=actor.pk, reason='forget')
    _invalidate_summaries(actor.pk)
    return {'status': 'forgotten', 'fact_id': str(fact.pk), 'version': fact.version}


@transaction.atomic
def forget_owner_facts(
    owner_id,
    *,
    cutoff,
    reason='erasure',
    client_code=None,
    limit=200,
    replay_deleted_at=None,
):
    """Authorized operator/lifecycle seam; a fixed cutoff bounds every retry."""
    if reason not in FORGET_REASONS or not 1 <= limit <= 1000:
        raise ValueError('Invalid memory purge request')
    if replay_deleted_at is not None and (
        not restore_hold_enabled() or replay_deleted_at > timezone.now()
    ):
        raise ValueError('Invalid restore deletion clock')
    get_user_model().objects.select_for_update().get(pk=owner_id)
    rows = MemoryFact.objects.filter(
        owner_id=owner_id, created_at__lte=cutoff, lifecycle_state__in=LIVE_STATES
    )
    if client_code is not None:
        rows = rows.filter(client_code=client_code)
    selected = list(rows.select_for_update().order_by('pk')[:limit])
    for fact in selected:
        _forget_locked(fact, actor_id=None, reason=reason)
        if replay_deleted_at is not None:
            MemoryFactTombstone.objects.filter(
                owner_id=owner_id, fact_id=fact.pk
            ).update(deleted_at=replay_deleted_at)
    if selected:
        _invalidate_summaries(owner_id)
    remaining = rows.count()
    return {
        'status': 'purge_incomplete' if remaining else 'purged',
        'forgotten': len(selected),
        'remaining': remaining,
    }


def _parse_purge_reference(reference):
    try:
        user_id, micros, reason = reference.split(':')
        if int(user_id) < 1 or reason not in FORGET_REASONS:
            raise ValueError
        cutoff = datetime(1970, 1, 1, tzinfo=datetime_timezone.utc) + timedelta(
            microseconds=int(micros)
        )
        return int(user_id), cutoff, reason
    except (ValueError, TypeError, OverflowError, OSError):
        raise ValueError('Invalid memory purge reference') from None


def purge_reference(owner_id, cutoff, reason):
    """Only non-content identifiers and a frozen selection clock enter the outbox."""
    delta = cutoff.astimezone(datetime_timezone.utc) - datetime(
        1970, 1, 1, tzinfo=datetime_timezone.utc
    )
    micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    return f'{int(owner_id)}:{micros}:{reason}'


def retry_owner_purge(reference):
    """A bounded pass; the outbox residual probe controls completion/retry."""
    user_id, cutoff, reason = _parse_purge_reference(reference)
    forget_owner_facts(user_id, cutoff=cutoff, reason=reason)


def owner_purge_residual(reference):
    """Later facts cannot silently enter an earlier deletion selection."""
    user_id, cutoff, _reason = _parse_purge_reference(reference)
    return MemoryFact.objects.filter(
        owner_id=user_id, created_at__lte=cutoff, lifecycle_state__in=LIVE_STATES
    ).count()


def set_opt_out(owner, *, opted_out):
    """Commit the stop-learning state and durable cleanup obligation together."""
    if type(opted_out) is not bool or restore_hold_enabled():
        raise ValueError('Memory mutation unavailable')
    from aichat.services.retention import enqueue_outbox

    with transaction.atomic():
        user = (
            get_user_model()
            .objects.select_for_update()
            .filter(pk=owner.pk, is_active=True)
            .first()
        )
        if user is None:
            raise ValueError('Memory mutation unavailable')
        UserMemorySettings.objects.update_or_create(
            user=user, defaults={'opted_out': opted_out}
        )
        cutoff = timezone.now()
        if opted_out:
            MemoryNoticeAcknowledgement.objects.filter(user=user).delete()
            enqueue_outbox(
                'memory_owner_purge', purge_reference(user.pk, cutoff, 'opt_out')
            )
            MemoryFactEvent.objects.create(
                owner_id=user.pk, actor_id=user.pk, action='opt_out'
            )
    if not opted_out:
        return {'opted_out': False, 'status': 'notice_required'}
    try:
        result = forget_owner_facts(owner.pk, cutoff=cutoff, reason='opt_out')
    except Exception:
        result = {'status': 'purge_incomplete'}
    return {'opted_out': True, **result}


@transaction.atomic
def withdraw_proposals_for_thread(thread):
    """Learning-off withdraws pending suggestions, preserving confirmed facts."""
    for fact in MemoryFact.objects.select_for_update().filter(
        source_thread=thread, lifecycle_state='proposed'
    ):
        fact.text = ''
        fact.embedding = None
        fact.canonical_value = None
        fact.lifecycle_state = 'withdrawn'
        fact.version += 1
        fact.save(
            update_fields=[
                'text',
                'embedding',
                'canonical_value',
                'lifecycle_state',
                'version',
                'updated_at',
            ]
        )
        _scrub_proposals(fact.pk)
        MemoryFactEvent.objects.create(
            owner_id=fact.owner_id,
            fact_id=fact.pk,
            action='withdraw',
            version=fact.version,
        )
