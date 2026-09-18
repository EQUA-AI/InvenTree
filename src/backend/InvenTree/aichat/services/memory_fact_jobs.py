"""Version-bound fact preparation on the dedicated memory worker only.

Jobs contain identifiers, never copied text. The periodic sweep discovers missing
obligations, so request transactions need not publish provider work. A terminal
failure remains attached to its fact version and is not silently retried forever.
"""

import logging
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from ai.core.config import get_settings
from aichat.models import MemoryFact, MemoryFactJob
from aichat.services import memory_budget, memory_worker
from aichat.services.memory_eligibility import (
    evaluate_memory_eligibility,
    evaluate_owner_memory,
    voice_source_has_memory_consent,
)
from aichat.services.memory_policy import MemoryPolicyError, validate_source_text
from aichat.services.memory_writes import _client_for_entity
from InvenTree.restore_hold import restore_hold_enabled

TASK = 'aichat.services.memory_fact_jobs.run'
MAX_ATTEMPTS = 3
logger = logging.getLogger('inventree')


def enabled():
    """Keep schema, scans and providers dark until explicitly enabled."""
    settings = get_settings()
    return (
        settings.aimms_memory_worker_enabled
        and (
            settings.feature_semantic_memory_extract_shadow
            or settings.feature_semantic_memory_recall
        )
        and not restore_hold_enabled()
    )


def eligible(fact, job):
    """Recheck current entity ownership as well as stored client and consent."""
    if not enabled() or fact.version != job.fact_version:
        return False
    from aichat.services.memory_retention import deletion_holds_fact

    if deletion_holds_fact(fact):
        return False
    now = timezone.now()
    if fact.valid_from > now or (fact.valid_until and fact.valid_until <= now):
        return False
    if job.kind == 'shield':
        if (
            fact.lifecycle_state != 'proposed'
            or fact.shield_state not in {'pending', 'unavailable'}
            or not fact.source_thread_id
            or not fact.source_message_id
        ):
            return False
        source = fact.source_message
        if not voice_source_has_memory_consent(source):
            return False
        decision = evaluate_memory_eligibility(
            fact.owner, fact.source_thread, source_time=source.created_at
        )
    elif job.kind == 'embedding':
        if fact.lifecycle_state != 'active' or fact.embedding is not None:
            return False
        decision = evaluate_owner_memory(fact.owner)
    else:
        return False
    if not decision.allowed:
        return False
    try:
        validate_source_text(fact.text)
        if job.kind == 'shield':
            validate_source_text(fact.source_message.content)
        client = _client_for_entity(
            fact.owner,
            {'entity_kind': fact.entity_kind, 'entity_id': fact.entity_id},
            decision.clients,
        )
        return client == fact.client_code
    except MemoryPolicyError:
        return False


def _locked(job_id):
    """Global deletion ordering: owner, fact, then job; no provider under locks."""
    snapshot = (
        MemoryFactJob.objects
        .filter(pk=job_id)
        .values('fact_id', 'fact__owner_id')
        .first()
    )
    if snapshot is None:
        return None, None
    owner = (
        get_user_model()
        .objects.select_for_update()
        .filter(pk=snapshot['fact__owner_id'])
        .first()
    )
    if owner is None:
        return None, None
    fact = MemoryFact.objects.select_for_update().filter(pk=snapshot['fact_id']).first()
    job = MemoryFactJob.objects.select_for_update().filter(pk=job_id).first()
    return fact, job


def _terminal(job, state):
    job.state, job.lease_token, job.claimed_at = state, None, None
    job.save(update_fields=['state', 'lease_token', 'claimed_at', 'updated_at'])


@transaction.atomic
def acquire(job_id):
    """One active UUID lease, bounded across both failures and worker crashes."""
    fact, job = _locked(job_id)
    if not fact or not job or job.state not in {'pending', 'deferred'}:
        return None
    if job.state == 'deferred' and job.next_attempt_at > timezone.now():
        return None
    if not eligible(fact, job):
        _terminal(job, 'skipped')
        return None
    if job.attempts >= MAX_ATTEMPTS:
        _terminal(job, 'failed')
        return None
    job.state, job.lease_token, job.claimed_at = 'claimed', uuid.uuid4(), timezone.now()
    job.attempts += 1
    job.save(
        update_fields=['state', 'lease_token', 'claimed_at', 'attempts', 'updated_at']
    )
    return fact, job


def still_live(job):
    """A lease check also refreshes consent and source relationships."""
    fresh = MemoryFactJob.objects.filter(
        pk=job.pk, state='claimed', lease_token=job.lease_token
    ).first()
    return fresh is not None and eligible(fresh.fact, fresh)


@transaction.atomic
def finish(job, *, shield=None, embedding=None, budget_deferred=False):
    """Ignore stale results; never let an old worker revive forgotten content."""
    fact, fresh = _locked(job.pk)
    if (
        not fact
        or not fresh
        or fresh.state != 'claimed'
        or fresh.lease_token != job.lease_token
    ):
        return 'stale'
    if not eligible(fact, fresh):
        _terminal(fresh, 'skipped')
        return 'skipped'
    if shield in {'clear', 'flagged'}:
        fact.shield_state = shield
        fact.injection_flag = shield == 'flagged'
        fact.version += 1  # Exact confirmation preview must be regenerated.
        fact.save(
            update_fields=['shield_state', 'injection_flag', 'version', 'updated_at']
        )
    elif embedding is not None and embedding.vector is not None:
        fact.embedding, fact.embedding_profile = (
            list(embedding.vector),
            embedding.profile,
        )
        fact.save(update_fields=['embedding', 'embedding_profile', 'updated_at'])
    else:
        if budget_deferred:
            fresh.attempts -= 1
        if fresh.attempts >= MAX_ATTEMPTS:
            _terminal(fresh, 'failed')
            return 'failed'
        fresh.state, fresh.lease_token, fresh.claimed_at = 'deferred', None, None
        fresh.next_attempt_at = timezone.now() + timedelta(
            seconds=3600 if budget_deferred else 60 * fresh.attempts
        )
        fresh.save(
            update_fields=[
                'state',
                'lease_token',
                'claimed_at',
                'attempts',
                'next_attempt_at',
                'updated_at',
            ]
        )
        return 'deferred'
    _terminal(fresh, 'complete')
    return 'complete'


def run(job_id):
    """Bound each provider call and recheck eligibility before each outbound hop."""
    memory_worker.heartbeat()
    if not enabled():
        return {'status': 'disabled'}
    acquired = acquire(job_id)
    if acquired is None:
        return {'status': 'skipped'}
    fact, job = acquired
    from ai.core.integrations.memory_providers import embed_memory, shield_documents

    settings = get_settings()
    try:
        if job.kind == 'shield':
            states = []
            # Source and candidate can exceed the combined shield request limit.
            for text in (fact.source_message.content, fact.text):
                if not still_live(job):
                    return {'status': finish(job)}
                result = shield_documents([text], settings=settings)
                states.extend(result.states)
            state = (
                'unavailable'
                if len(states) != 2 or 'unavailable' in states
                else 'flagged'
                if 'flagged' in states
                else 'clear'
            )
            return {'status': finish(job, shield=state)}
        reservation = memory_budget.reserve(
            tokens=len(fact.text.encode('utf-8')) + 256,
            deployment=settings.memory_embedding_deployment,
            thread_id=str(fact.source_thread_id or ''),
            settings=settings,
            purpose='embedding',
        )
        if reservation is None:
            return {'status': finish(job, budget_deferred=True)}
        if not still_live(job):
            from ai.core.integrations.memory_providers import EmbeddingResult

            memory_budget.settle(reservation, EmbeddingResult())
            return {'status': finish(job)}
        result = embed_memory(fact.text, settings=settings)
        memory_budget.settle(reservation, result)
        return {'status': finish(job, embedding=result)}
    except Exception:
        logger.warning('Memory fact preparation deferred')
        return {'status': finish(job)}


def publish(job_id):
    """Durable metadata publication; no synchronous or ingestion fallback."""
    try:
        if not enabled():
            return False
        status = memory_worker.worker_status()
        if status['backpressured'] or not status['heartbeat_fresh']:
            return False
        with transaction.atomic():
            job = (
                MemoryFactJob.objects
                .select_for_update()
                .filter(
                    pk=job_id,
                    state__in=['pending', 'deferred'],
                    next_attempt_at__lte=timezone.now(),
                )
                .first()
            )
            if job is None:
                return False
            task_id = memory_worker.async_task(
                TASK,
                str(job.pk),
                group=memory_worker.CLUSTER,
                cluster=memory_worker.CLUSTER,
                broker=memory_worker._broker(),
                timeout=memory_worker.cluster_config()['timeout'],
                sync=False,
            )
            if not task_id:
                return False
            job.state = 'pending'
            job.next_attempt_at = timezone.now() + timedelta(seconds=60)
            job.save(update_fields=['state', 'next_attempt_at', 'updated_at'])
        return True
    except Exception:
        logger.warning('Memory fact publication deferred')
        return False


def sweep():
    """Discover bounded missing work and recover expired leases on the worker."""
    memory_worker.heartbeat()
    if not enabled():
        return {'status': 'disabled', 'queued': 0}
    now = timezone.now()
    expiry = now - timedelta(seconds=memory_worker.cluster_config()['timeout'] + 180)
    MemoryFactJob.objects.filter(state='claimed', claimed_at__lte=expiry).update(
        state='deferred', lease_token=None, claimed_at=None, next_attempt_at=now
    )
    for kind, filters in (
        (
            'shield',
            Q(lifecycle_state='proposed', shield_state__in=['pending', 'unavailable']),
        ),
        ('embedding', Q(lifecycle_state='active', embedding__isnull=True)),
    ):
        existing = MemoryFactJob.objects.filter(
            fact_id=OuterRef('pk'), fact_version=OuterRef('version'), kind=kind
        )
        candidates = list(
            MemoryFact.objects
            .filter(filters)
            .filter(~Exists(existing))
            .filter(
                Q(valid_until__isnull=True) | Q(valid_until__gt=now),
                valid_from__lte=now,
            )
            .order_by('updated_at', 'pk')
            .values_list('pk', 'version')[:25]
        )
        # Recheck under the same owner/fact locks as deletion before inserting.
        for fact_id, version in candidates:
            with transaction.atomic():
                owner_id = (
                    MemoryFact.objects
                    .filter(pk=fact_id)
                    .values_list('owner_id', flat=True)
                    .first()
                )
                if owner_id is None:
                    continue
                get_user_model().objects.select_for_update().get(pk=owner_id)
                fact = (
                    MemoryFact.objects
                    .select_for_update()
                    .filter(pk=fact_id, version=version)
                    .first()
                )
                if fact is not None:
                    MemoryFactJob.objects.get_or_create(
                        fact=fact, fact_version=version, kind=kind
                    )
    ids = list(
        MemoryFactJob.objects
        .filter(state__in=['pending', 'deferred'], next_attempt_at__lte=now)
        .order_by('next_attempt_at', 'pk')
        .values_list('pk', flat=True)[:50]
    )
    return {'status': 'processed', 'queued': sum(publish(identity) for identity in ids)}
