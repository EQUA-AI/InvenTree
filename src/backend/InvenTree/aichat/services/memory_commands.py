"""Canonical memory actions dispatched only by the governed proposal rail."""

import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ai.core.config import get_settings
from aichat.models import (
    MemoryFact,
    MemoryFactEvent,
    MemoryFactTombstone,
    UserMemorySettings,
)
from aichat.services import proposals
from aichat.services.memory_eligibility import evaluate_memory_eligibility
from aichat.services.memory_lifecycle import (
    LIVE_STATES,
    _forget_locked,
    _invalidate_summaries,
    _scrub_proposals,
    fingerprint,
    forget_owner_facts,
    purge_reference,
    slot_fingerprint,
)
from aichat.services.memory_policy import (
    MemoryPolicyError,
    render_verified,
    verify_native_field,
)
from aichat.services.memory_writes import validate_revival
from aichat.services.threads import scope_fingerprint
from InvenTree.restore_hold import restore_hold_enabled

ACTIONS = frozenset({
    'memory.remember',
    'memory.update',
    'memory.forget',
    'memory.forget_all',
})
DAILY_DECISION_LIMIT = 20


def prepare_action(owner, *, action_type, idempotency_key, intent):
    """Shared browser/model preparation; only the decision rail executes effects."""
    from aichat.models import ChatActionProposal

    owner = require_permission(owner)
    if (
        not isinstance(action_type, str)
        or action_type not in ACTIONS
        or not isinstance(intent, dict)
        or not isinstance(idempotency_key, str)
        or not 1 <= len(idempotency_key) <= 128
    ):
        raise proposals.ProposalError('Invalid memory action')
    scope_key, scope_hash = owner_scope(owner)
    if action_type == 'memory.forget_all':
        if intent:
            raise proposals.ProposalError('Invalid memory action')
        existing = ChatActionProposal.objects.filter(
            owner=owner,
            idempotency_key=idempotency_key,
            action_type=action_type,
            scope_hash=scope_hash,
        ).first()
        intent = existing.intent if existing else {'before': timezone.now().isoformat()}
    return proposals.create_proposal(
        owner=owner,
        scope_key=scope_key,
        scope_hash=scope_hash,
        action_type=action_type,
        work_order_id=None,
        reason='',
        idempotency_key=idempotency_key,
        policy_version='memory-actions-v1',
        intent=intent,
    )


def owner_scope(owner):
    """The rail's memory scope is server-derived and cannot broaden client access."""
    key = f'user:{owner.pk}'
    return key, scope_fingerprint(key)


def lock_scope_owner(owner, scope_hash):
    """Owner-before-proposal locking agrees with erasure and copied-content scrub."""
    if scope_hash == owner_scope(owner)[1]:
        user = (
            get_user_model()
            .objects.select_for_update()
            .filter(pk=owner.pk, is_active=True)
            .first()
        )
        if user is None:
            raise proposals.ProposalNotFound('Memory action unavailable')
        return user
    return owner


def require_permission(owner):
    """Re-read active actor and permission, avoiding a request's permission cache."""
    user = get_user_model().objects.filter(pk=owner.pk, is_active=True).first()
    if user is None or not user.has_perm('aichat.write_memory'):
        raise proposals.CapabilityDenied('Memory actions require ai_memory.write')
    if restore_hold_enabled():
        raise proposals.ProposalError('Memory actions are held during restore')
    return user


def _fact(owner, identity):
    try:
        identity = uuid.UUID(str(identity))
    except (ValueError, TypeError, AttributeError):
        raise proposals.ProposalNotFound('Memory not found') from None
    row = MemoryFact.objects.filter(pk=identity, owner=owner).first()
    if row is None:
        raise proposals.ProposalNotFound('Memory not found')
    return row


def _decision_budget(owner):
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if (
        MemoryFactEvent.objects.filter(
            owner_id=owner.pk, created_at__gte=start, action__in=['confirm', 'reject']
        ).count()
        >= DAILY_DECISION_LIMIT
    ):
        raise proposals.ProposalStateConflict('Daily memory review limit reached')


def _can_read(owner, fact):
    from aichat.services.memory_reads import can_read

    return can_read(owner, fact)


def authorize_preview(owner, proposal):
    """Reauthorize every text-bearing fact before returning a stored preview."""
    if proposal.action_type in {'memory.forget', 'memory.forget_all'}:
        return
    identities = {
        proposal.target_memory_fact_id,
        proposal.intent.get('replacement_fact_id'),
    }
    for identity in identities - {None, ''}:
        fact = _fact(owner, identity)
        if not _can_read(owner, fact):
            raise proposals.ProposalNotFound('Memory action unavailable')


def _rememberable(owner, fact):
    if fact.lifecycle_state != 'proposed' or not _can_read(owner, fact):
        raise proposals.ProposalNotFound('Memory suggestion unavailable')
    if fact.shield_state not in {'clear', 'flagged'}:
        raise proposals.ProposalStateConflict('Memory screening is not complete')
    if fact.source_thread_id is None or fact.source_message_id is None:
        raise proposals.ProposalStateConflict('Memory source is unavailable')
    source = fact.source_message
    if source.thread_id != fact.source_thread_id or source.role != 'user':
        raise proposals.ProposalStateConflict('Memory source changed')
    eligibility = evaluate_memory_eligibility(
        owner, fact.source_thread, source_time=source.created_at
    )
    if not eligibility.allowed or (
        fact.client_code and fact.client_code not in eligibility.clients
    ):
        raise proposals.ProposalStateConflict('Memory consent or scope changed')
    try:
        if validate_revival(fact, source, fact.origin) != fact.revives:
            raise MemoryPolicyError('revival_changed')
        if fact.memory_type == 'user_preference':
            if fact.verification_class != 'inferred':
                raise MemoryPolicyError('preference_authority')
        else:
            claim = fact.claims.filter(content_trust='trusted_record').first()
            if fact.verification_class != 'tool_verified' or claim is None:
                raise MemoryPolicyError('operational_authority')
            value = verify_native_field(
                owner,
                source_model=claim.source_model,
                source_id=int(claim.source_object_id),
                source_field=claim.source_field,
                eligible_clients=eligibility.clients,
            )
            if (
                value.client_code != fact.client_code
                or value.entity_kind != fact.entity_kind
                or str(value.source_id) != fact.entity_id
                or value.value != fact.canonical_value
                or render_verified(value, fact.text_lang) != fact.text
            ):
                raise MemoryPolicyError('source_changed')
    except (MemoryPolicyError, ValueError):
        raise proposals.ProposalRevalidationFailed(
            'Memory authority or source changed'
        ) from None


def _active_slot(fact):
    return MemoryFact.objects.filter(
        owner_id=fact.owner_id,
        client_code=fact.client_code,
        entity_kind=fact.entity_kind,
        entity_id=fact.entity_id,
        memory_type=fact.memory_type,
        slot_key=fact.slot_key,
        lifecycle_state='active',
    ).first()


def _cutoff(value):
    result = parse_datetime(value) if isinstance(value, str) else None
    if result is None or timezone.is_naive(result) or result > timezone.now():
        raise proposals.ProposalError('Invalid memory deletion selection')
    return result


def prepare(owner, action, intent):
    """Reconstruct the exact preview from current authorized state; no providers."""
    owner = require_permission(owner)
    if action not in ACTIONS or not isinstance(intent, dict):
        raise proposals.ProposalError('Invalid memory action')
    if action == 'memory.forget_all':
        if set(intent) != {'before'}:
            raise proposals.ProposalError('Invalid memory deletion selection')
        cutoff = _cutoff(intent['before'])
        count = MemoryFact.objects.filter(
            owner=owner, created_at__lte=cutoff, lifecycle_state__in=LIVE_STATES
        ).count()
        return None, {
            'action': action,
            'before': cutoff.isoformat(),
            'count': count,
            'confirm_phrase': 'forget all my memories',
            'warning': 'This clears your conversation summaries. Original messages remain.',
        }
    allowed = {'memory_fact_id', 'expected_version'}
    if action == 'memory.update':
        allowed |= {'topics', 'replacement_fact_id'}
    if set(intent) - allowed or not {'memory_fact_id', 'expected_version'} <= set(
        intent
    ):
        raise proposals.ProposalError('Invalid memory action parameters')
    fact = _fact(owner, intent['memory_fact_id'])
    if (
        type(intent['expected_version']) is not int
        or fact.version != intent['expected_version']
    ):
        raise proposals.ProposalPreviewChanged(
            'Memory changed; request a fresh preview'
        )
    preview = {
        'action': action,
        'memory_fact_id': str(fact.pk),
        'version': fact.version,
    }
    if action == 'memory.forget':
        # Forget remains possible after source/client access is revoked. The
        # confirmation preview does not re-disclose inaccessible fact text.
        preview['warning'] = (
            'This clears your conversation summaries. Original messages remain.'
        )
        return fact.version, preview
    if (
        not get_settings().feature_semantic_memory_extract_shadow
        or UserMemorySettings.objects.filter(user=owner, opted_out=True).exists()
    ):
        raise proposals.ProposalStateConflict('Memory learning is disabled')
    if not _can_read(owner, fact):
        raise proposals.ProposalNotFound('Memory not found')
    candidate = fact
    if action == 'memory.update':
        if fact.lifecycle_state != 'active' or ('topics' in intent) == (
            'replacement_fact_id' in intent
        ):
            raise proposals.ProposalError('Choose one memory correction')
        if 'topics' in intent:
            from aichat.memory_choices import MemoryTopic

            topics = intent['topics']
            if (
                not isinstance(topics, list)
                or len(topics) > 3
                or any(
                    not isinstance(tag, str) or tag not in MemoryTopic.values
                    for tag in topics
                )
                or len(set(topics)) != len(topics)
            ):
                raise proposals.ProposalError('Invalid memory topics')
            preview.update(text=fact.text, current_topics=fact.topics, topics=topics)
            return fact.version, preview
        candidate = _fact(owner, intent['replacement_fact_id'])
        preview.update(
            replacement_fact_id=str(candidate.pk), replacement_version=candidate.version
        )
    _rememberable(owner, candidate)
    active = _active_slot(candidate)
    if action == 'memory.update' and active is not None and active.pk != fact.pk:
        raise proposals.ProposalStateConflict(
            'The replacement slot already has a different memory'
        )
    preview.update(
        text=candidate.text,
        text_lang=candidate.text_lang,
        memory_type=candidate.memory_type,
        topics=candidate.topics,
        shield_state=candidate.shield_state,
        untrusted=candidate.verification_class == 'inferred',
        source_thread_id=candidate.source_thread_id,
        source_message_id=candidate.source_message_id,
        revives=str(candidate.revives) if candidate.revives else None,
        supersedes=str(active.pk) if active else None,
        supersedes_version=active.version if active else None,
    )
    return fact.version, preview


def _supersede(fact, owner):
    stone, _ = MemoryFactTombstone.objects.get_or_create(
        owner_id=owner.pk,
        client_code=fact.client_code,
        slot_fingerprint=slot_fingerprint(fact),
        claim_fingerprint=fact.claim_fingerprint,
        fact_id=fact.pk,
        defaults={
            'owner_joined_at': fact.owner.date_joined,
            'source_fingerprint': fingerprint(
                'source-v1', [fact.source_thread_id, fact.source_message_id]
            ),
            'source_sequence': fact.claims.aggregate(latest=Max('source_sequence'))[
                'latest'
            ]
            or 0,
            'reason': 'supersede',
            'residual_pending': False,
        },
    )
    fact.lifecycle_state = 'superseded'
    fact.embedding = None
    fact.version += 1
    fact.save(update_fields=['lifecycle_state', 'embedding', 'version', 'updated_at'])
    MemoryFactEvent.objects.create(
        owner_id=owner.pk,
        actor_id=owner.pk,
        fact_id=fact.pk,
        tombstone_id=stone.pk,
        action='supersede',
        version=fact.version,
        claim_fingerprint=fact.claim_fingerprint,
    )


@transaction.atomic
def execute(proposal, owner):
    """Apply one revalidated action under owner/proposal locks and save its receipt."""
    owner = lock_scope_owner(owner, proposal.scope_hash)
    if (proposal.scope_key, proposal.scope_hash) != owner_scope(owner):
        raise proposals.ProposalError('Invalid memory scope')
    identities = [proposal.target_memory_fact_id]
    replacement = proposal.intent.get('replacement_fact_id')
    if replacement:
        identities.append(replacement)
    # Hold fact rows before source revalidation: source deletion uses SET_NULL
    # on these same rows and cannot invalidate proof between this read and write.
    list(
        MemoryFact.objects
        .select_for_update()
        .filter(owner=owner, pk__in=[identity for identity in identities if identity])
        .order_by('pk')
    )
    version, preview = prepare(owner, proposal.action_type, proposal.intent)
    if (
        version != proposal.target_version
        or proposals.compute_preview_hash(preview) != proposal.preview_hash
    ):
        raise proposals.ProposalPreviewChanged('Memory changed; review it again')
    action = proposal.action_type
    if action == 'memory.forget_all':
        from aichat.services.retention import enqueue_outbox

        cutoff = _cutoff(proposal.intent['before'])
        reference = purge_reference(owner.pk, cutoff, 'forget')
        enqueue_outbox('memory_owner_purge', reference)
        result = forget_owner_facts(owner.pk, cutoff=cutoff, reason='forget')
        return {'action': action, 'cleanup_reference': reference, **result}
    fact = MemoryFact.objects.select_for_update().get(
        pk=proposal.target_memory_fact_id, owner=owner
    )
    if action == 'memory.forget':
        _forget_locked(fact, actor_id=owner.pk, reason='forget')
        _invalidate_summaries(owner.pk)
        return {
            'action': action,
            'fact_id': str(fact.pk),
            'version': fact.version,
            'status': 'forgotten',
        }
    _decision_budget(owner)
    if action == 'memory.update' and 'topics' in proposal.intent:
        fact.topics = proposal.intent['topics']
        fact.version += 1
        fact.save(update_fields=['topics', 'version', 'updated_at'])
        event_action = 'retag'
    else:
        candidate = (
            fact
            if action == 'memory.remember'
            else MemoryFact.objects.select_for_update().get(
                pk=proposal.intent['replacement_fact_id'], owner=owner
            )
        )
        prior = _active_slot(candidate) if action == 'memory.remember' else fact
        if prior is not None:
            _supersede(prior, owner)
            candidate.supersedes = prior
            _invalidate_summaries(owner.pk)
        candidate.verification_class = (
            'user_confirmed'
            if candidate.memory_type == 'user_preference'
            else 'tool_verified'
        )
        candidate.lifecycle_state = 'active'
        candidate.confirming_proposal = proposal
        candidate.last_verified_at = timezone.now()
        candidate.version += 1
        candidate.save()
        fact = candidate
        event_action = 'confirm'
    MemoryFactEvent.objects.create(
        owner_id=owner.pk,
        actor_id=owner.pk,
        fact_id=fact.pk,
        proposal_id=proposal.pk,
        action='confirm',
        version=fact.version,
        claim_fingerprint=fact.claim_fingerprint,
    )
    if event_action == 'retag':
        MemoryFactEvent.objects.create(
            owner_id=owner.pk,
            actor_id=owner.pk,
            fact_id=fact.pk,
            proposal_id=proposal.pk,
            action='retag',
            version=fact.version,
            claim_fingerprint=fact.claim_fingerprint,
        )
    return {
        'action': action,
        'fact_id': str(fact.pk),
        'version': fact.version,
        'status': 'active',
    }


def reject(proposal, owner):
    """Count one suggested-fact decision; rejection cannot affect active facts."""
    require_permission(owner)
    if proposal.action_type not in {'memory.remember', 'memory.update'}:
        return
    _decision_budget(owner)
    fact_id = (
        proposal.intent.get('replacement_fact_id') or proposal.target_memory_fact_id
    )
    fact = _fact(owner, fact_id)
    if fact.lifecycle_state == 'proposed':
        fact.text, fact.canonical_value, fact.embedding = '', None, None
        fact.canonical_unit, fact.embedding_profile = '', ''
        fact.lifecycle_state = 'withdrawn'
        fact.version += 1
        fact.save()
        fact.claims.all().delete()
        _scrub_proposals(fact.pk)
    MemoryFactEvent.objects.create(
        owner_id=owner.pk,
        actor_id=owner.pk,
        fact_id=fact.pk,
        proposal_id=proposal.pk,
        action='reject',
        version=fact.version,
        claim_fingerprint=fact.claim_fingerprint,
    )
