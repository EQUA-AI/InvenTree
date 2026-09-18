"""Default-off extraction claims, bounded worker attempts and durable recovery."""

import logging
import time
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ai.core.config import get_settings
from ai.core.trusted_context import resolve_actor_locale
from aichat.models import (
    ChatMessage,
    ChatThread,
    MemoryExtractionClaim,
    MemoryExtractionRun,
)
from aichat.services import memory_budget, memory_worker
from aichat.services.memory_eligibility import (
    evaluate_memory_eligibility,
    voice_source_has_memory_consent,
)
from aichat.services.memory_policy import (
    MemoryPolicyError,
    validate_candidate,
    validate_source_text,
)
from aichat.services.memory_writes import _EXPLICIT_REMEMBER, propose_fact
from aichat.services.threads import ThreadRepository
from InvenTree.restore_hold import restore_hold_enabled

TASK = 'aichat.services.memory_extraction.run'
MAX_ATTEMPTS = 3
RETRY_SECONDS = 60
logger = logging.getLogger('inventree')


def enabled():
    """Execution-time settings and restore hold are always authoritative."""
    settings = get_settings()
    return (
        settings.feature_semantic_memory_extract_shadow
        and settings.aimms_memory_worker_enabled
        and not restore_hold_enabled()
    )


def enqueue_for_thread(thread):
    """Record an obligation during turn commit; never run providers on the request."""
    try:
        if not enabled():
            return
        with transaction.atomic():
            if not evaluate_memory_eligibility(thread.owner, thread).allowed:
                return
            if thread.next_sequence - 1 <= thread.memory_through_sequence:
                return
            claim, _ = MemoryExtractionClaim.objects.get_or_create(
                thread=thread, through_sequence=thread.next_sequence - 1
            )
            transaction.on_commit(lambda: publish(claim.pk))
    except Exception:
        logger.warning('Memory extraction admission unavailable')


def publish(claim_id):
    """Publication failure leaves a durable obligation for the recovery sweep."""
    try:
        if not enabled():
            return {'status': 'deferred'}
        claim = MemoryExtractionClaim.objects.filter(
            pk=claim_id,
            state__in=['pending', 'deferred'],
            next_attempt_at__lte=timezone.now(),
        ).first()
        if claim is None:
            return {'status': 'skipped'}
        config = memory_worker.cluster_config()
        status = memory_worker.worker_status()
        if status['backpressured'] or not status['heartbeat_fresh']:
            return {'status': 'deferred'}
        task_id = memory_worker.async_task(
            TASK,
            str(claim.pk),
            group=memory_worker.CLUSTER,
            cluster=memory_worker.CLUSTER,
            broker=memory_worker._broker(),
            timeout=config['timeout'],
            sync=False,
        )
        if not task_id:
            return {'status': 'deferred'}
        # A short publication lease prevents a once-per-minute sweep flooding
        # duplicate tasks; execution owns the separate UUID processing lease.
        MemoryExtractionClaim.objects.filter(
            pk=claim.pk, state__in=['pending', 'deferred']
        ).update(
            state='pending',
            next_attempt_at=timezone.now() + timedelta(seconds=RETRY_SECONDS),
        )
        return {'status': 'queued'}
    except Exception:
        logger.warning('Memory extraction publication deferred')
        return {'status': 'deferred'}


def _repository(claim):
    thread = ChatThread.objects.filter(pk=claim.thread_id).first()
    if thread is None:
        raise MemoryPolicyError('thread_unavailable')
    return ThreadRepository(
        actor=thread.owner_id, scope_key=thread.scope_key, namespace=thread.namespace
    )


@transaction.atomic
def _acquire(claim_id):
    claim = MemoryExtractionClaim.objects.filter(pk=claim_id).first()
    if claim is None:
        return None
    repository = _repository(claim)
    thread = repository._lock_thread(claim.thread_id)
    claim = MemoryExtractionClaim.objects.select_for_update().get(pk=claim.pk)
    expired = timezone.now() - timedelta(
        seconds=memory_worker.cluster_config()['timeout'] + 180
    )
    if claim.state not in {'pending', 'deferred', 'claimed'} or (
        claim.state == 'claimed' and claim.claimed_at and claim.claimed_at > expired
    ):
        return None
    if claim.state == 'deferred' and claim.next_attempt_at > timezone.now():
        return None
    if (
        MemoryExtractionClaim.objects
        .filter(thread=thread, state='claimed', claimed_at__gt=expired)
        .exclude(pk=claim.pk)
        .exists()
    ):
        return None
    claim.state, claim.lease_token, claim.claimed_at = (
        'claimed',
        uuid.uuid4(),
        timezone.now(),
    )
    claim.attempts += 1
    claim.save(
        update_fields=['state', 'lease_token', 'claimed_at', 'attempts', 'updated_at']
    )
    if not evaluate_memory_eligibility(thread.owner, thread).allowed:
        _finish(
            repository,
            claim.pk,
            claim.lease_token,
            claim.through_sequence,
            'skipped',
            {},
        )
        return None
    rows = list(
        ChatMessage.objects.filter(
            thread=thread,
            role='user',
            sequence__gt=thread.memory_through_sequence,
            sequence__lte=claim.through_sequence,
        ).order_by('sequence')[:6]
    )
    selected, consumed, chars, rejected = [], thread.memory_through_sequence, 0, 0
    for row in rows[:5]:
        if selected and chars + len(row.content) > 10000:
            break
        consumed = row.sequence
        try:
            if not voice_source_has_memory_consent(row):
                raise MemoryPolicyError('voice_notice_required')
            validate_source_text(row.content)
            if not evaluate_memory_eligibility(
                thread.owner, thread, source_time=row.created_at
            ).allowed:
                raise MemoryPolicyError('source_ineligible')
        except MemoryPolicyError:
            rejected += 1
            continue
        selected.append(row)
        chars += len(row.content)
    if len(rows) <= 5 and (not rows or consumed == rows[-1].sequence):
        consumed = claim.through_sequence
    return repository, claim, selected, consumed, rejected


def _lease_live(claim_id, token):
    if not enabled():
        return False
    claim = (
        MemoryExtractionClaim.objects
        .filter(pk=claim_id, state='claimed', lease_token=token)
        .select_related('thread__owner')
        .first()
    )
    return (
        claim is not None
        and evaluate_memory_eligibility(claim.thread.owner, claim.thread).allowed
    )


@transaction.atomic
def _finish(repository, claim_id, token, through, outcome, counters):
    if restore_hold_enabled():
        return False
    snapshot = MemoryExtractionClaim.objects.filter(pk=claim_id).first()
    if snapshot is None:
        return False
    thread = repository._lock_thread(snapshot.thread_id)
    claim = (
        MemoryExtractionClaim.objects
        .select_for_update()
        .filter(pk=claim_id, state='claimed', lease_token=token)
        .first()
    )
    if claim is None:
        return False
    thread.memory_through_sequence = max(thread.memory_through_sequence, through)
    thread.save(update_fields=['memory_through_sequence'])
    claim.state = 'pending' if through < claim.through_sequence else outcome
    claim.lease_token, claim.claimed_at = None, None
    claim.attempts = 0
    claim.next_attempt_at = timezone.now()
    claim.save(
        update_fields=[
            'state',
            'lease_token',
            'claimed_at',
            'attempts',
            'next_attempt_at',
            'updated_at',
        ]
    )
    MemoryExtractionRun.objects.create(
        claim=claim,
        thread=thread,
        owner_id=thread.owner_id,
        outcome=outcome,
        flag_state=1 if enabled() else 0,
        deployment=get_settings().memory_extraction_deployment[:128],
        **counters,
    )
    if claim.state == 'pending':
        transaction.on_commit(lambda: publish(claim.pk))
    return True


def _retry(repository, claim, through, counters, *, budget=False):
    if claim.attempts >= MAX_ATTEMPTS and not budget:
        # A failed bounded window becomes a visible diagnostic gap. It is not
        # silently retried forever through later cumulative sequence claims.
        return _finish(
            repository, claim.pk, claim.lease_token, through, 'failed', counters
        )
    delay = 3600 if budget else RETRY_SECONDS * max(1, claim.attempts)
    with transaction.atomic():
        repository._lock_thread(claim.thread_id)
        updated = MemoryExtractionClaim.objects.filter(
            pk=claim.pk, state='claimed', lease_token=claim.lease_token
        ).update(
            state='deferred',
            lease_token=None,
            claimed_at=None,
            next_attempt_at=timezone.now() + timedelta(seconds=delay),
            attempts=max(0, claim.attempts - 1) if budget else claim.attempts,
        )
        if updated:
            MemoryExtractionRun.objects.create(
                claim_id=claim.pk,
                thread_id=claim.thread_id,
                owner_id=repository.actor_id,
                outcome='deferred',
                flag_state=1 if enabled() else 0,
                deployment=get_settings().memory_extraction_deployment[:128],
                **counters,
            )
    return False


def run(claim_id):
    """Run only on the dedicated worker, with no DB locks held across providers."""
    memory_worker.heartbeat()
    if not enabled():
        return {'status': 'deferred'}
    from ai.core.integrations.memory_extractor import (
        extract,
        request_payload,
        reservation_bound,
    )
    from ai.core.integrations.memory_providers import shield_documents

    acquired = _acquire(claim_id)
    if acquired is None:
        return {'status': 'skipped'}
    repository, claim, sources, through, rejected = acquired
    counters = {'n_input_messages': len(sources), 'n_rejected': rejected}
    started = time.monotonic()
    if claim.attempts > MAX_ATTEMPTS:
        _finish(repository, claim.pk, claim.lease_token, through, 'failed', counters)
        return {'status': 'failed'}
    if not sources:
        _finish(repository, claim.pk, claim.lease_token, through, 'complete', counters)
        return {'status': 'complete'}
    try:
        settings = get_settings()
        locale = resolve_actor_locale(repository.actor_id)
        documents = [
            {'source_message_id': row.pk, 'content': row.content} for row in sources
        ]
        payload = request_payload(
            documents, owner_id=repository.actor_id, locale=locale
        )
        reservation = memory_budget.reserve(
            tokens=reservation_bound(payload),
            deployment=settings.memory_extraction_deployment,
            thread_id=claim.thread_id,
            settings=settings,
        )
        if reservation is None:
            _retry(repository, claim, through, counters, budget=True)
            return {'status': 'budget_deferred'}
        if not _lease_live(claim.pk, claim.lease_token):
            from ai.core.integrations.memory_extractor import ExtractionResult

            memory_budget.settle(reservation, ExtractionResult())
            return {'status': 'stale'}
        source_shields = shield_documents(
            [row.content for row in sources], settings=settings
        )
        if not _lease_live(claim.pk, claim.lease_token):
            from ai.core.integrations.memory_extractor import ExtractionResult

            memory_budget.settle(reservation, ExtractionResult())
            return {'status': 'stale'}
        result = extract(
            documents, owner_id=repository.actor_id, locale=locale, settings=settings
        )
        memory_budget.settle(reservation, result)
        counters.update(
            input_tokens=result.input_tokens, output_tokens=result.output_tokens
        )
        if result.error_code:
            _retry(repository, claim, through, counters)
            return {'status': 'deferred'}
        source_map = {row.pk: row for row in sources}
        source_states = dict(zip(source_map, source_shields.states, strict=True))
        valid = []
        for raw in result.candidates:
            source_id = raw['source_message_id']
            candidate = {
                key: value for key, value in raw.items() if key != 'source_message_id'
            }
            try:
                candidate = validate_candidate(candidate, locale=locale)
            except MemoryPolicyError:
                counters['n_rejected'] += 1
                continue
            valid.append((source_id, candidate))
        if not _lease_live(claim.pk, claim.lease_token):
            return {'status': 'stale'}
        annotations = (
            shield_documents(
                [candidate['text'] for _, candidate in valid], settings=settings
            ).states
            if valid
            else ()
        )
        counters['n_proposals'] = 0
        with transaction.atomic():
            repository._lock_thread(claim.thread_id)
            locked_claim = (
                MemoryExtractionClaim.objects
                .select_for_update()
                .filter(pk=claim.pk, state='claimed', lease_token=claim.lease_token)
                .first()
            )
            if locked_claim is None or not _lease_live(claim.pk, claim.lease_token):
                return {'status': 'stale'}
            for (source_id, candidate), state in zip(valid, annotations, strict=True):
                source_state = source_states[source_id]
                state = (
                    'unavailable'
                    if 'unavailable' in (source_state, state)
                    else 'flagged'
                    if 'flagged' in (source_state, state)
                    else 'clear'
                )
                field = (
                    'n_shield_unavailable'
                    if state == 'unavailable'
                    else 'n_shield_flagged'
                    if state == 'flagged'
                    else None
                )
                if field:
                    counters[field] = counters.get(field, 0) + 1
                source = source_map[source_id]
                origin = (
                    'user_explicit'
                    if _EXPLICIT_REMEMBER.search(source.content)
                    else 'compaction'
                )
                try:
                    _, created = propose_fact(
                        repository,
                        thread_id=claim.thread_id,
                        source_message_id=source_id,
                        candidate=candidate,
                        origin=origin,
                        shield_state=state,
                    )
                    counters['n_proposals'] += int(created)
                except MemoryPolicyError:
                    counters['n_rejected'] += 1
            counters['latency_ms'] = max(0, int((time.monotonic() - started) * 1000))
            _finish(
                repository, claim.pk, claim.lease_token, through, 'complete', counters
            )
        return {'status': 'complete', 'proposals': counters['n_proposals']}
    except Exception:
        logger.warning('Memory extraction attempt deferred')
        _retry(repository, claim, through, counters)
        return {'status': 'deferred'}


def sweep():
    """Recover due work and expired leases; metadata only, at most fifty claims."""
    memory_worker.heartbeat()
    from aichat.services.memory_fact_jobs import sweep as sweep_fact_jobs

    fact_jobs = sweep_fact_jobs()
    if not enabled():
        return {'status': 'disabled', 'queued': 0, 'fact_jobs': fact_jobs}
    now = timezone.now()
    expiry = now - timedelta(seconds=memory_worker.cluster_config()['timeout'] + 180)
    MemoryExtractionClaim.objects.filter(
        state='claimed', claimed_at__lte=expiry
    ).update(state='deferred', lease_token=None, claimed_at=None, next_attempt_at=now)
    ids = list(
        MemoryExtractionClaim.objects
        .filter(Q(state='pending') | Q(state='deferred'), next_attempt_at__lte=now)
        .order_by('next_attempt_at', 'created_at')
        .values_list('pk', flat=True)[:50]
    )
    queued = sum(publish(identity)['status'] == 'queued' for identity in ids)
    return {'status': 'processed', 'queued': queued, 'fact_jobs': fact_jobs}
