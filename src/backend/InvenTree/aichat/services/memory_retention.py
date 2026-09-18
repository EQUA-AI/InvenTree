"""Thread deletion contracts for the PostgreSQL durable-memory store."""

from datetime import timedelta

from django.db import connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from aichat.models import (
    ChatThreadTombstone,
    MemoryExtractionClaim,
    MemoryExtractionRun,
    MemoryFact,
    MemoryFactClaim,
    MemoryFactEvent,
    MemoryFactJob,
)


def linked_facts(thread_id):
    """Source provenance determines the set; the caller still binds its owner."""
    return MemoryFact.objects.filter(
        Q(source_thread_id=thread_id) | Q(claims__source_thread_id=thread_id)
    ).distinct()


def deletion_holds_fact(fact):
    """A persisted optional-delete choice stops serving before bounded cleanup."""
    sources = fact.claims.filter(source_thread_id__isnull=False).values(
        'source_thread_id'
    )
    return (
        ChatThreadTombstone.objects
        .filter(forget_confirmed_memories=True)
        .filter(Q(thread_id=fact.source_thread_id) | Q(thread_id__in=sources))
        .exists()
    )


@transaction.atomic
def forget_thread_batch(stone, *, batch_size=200):
    """Forget before severance; the caller retains the root until this is complete."""
    from django.contrib.auth import get_user_model

    from aichat.services.memory_lifecycle import (
        LIVE_STATES,
        _forget_locked,
        _invalidate_summaries,
    )

    if not stone.forget_confirmed_memories:
        return True
    owner = (
        get_user_model().objects.select_for_update().filter(pk=stone.owner_id).first()
    )
    if owner is None:
        return False
    identities = list(
        linked_facts(stone.thread_id)
        .filter(owner=owner, lifecycle_state__in=LIVE_STATES)
        .order_by('pk')
        .values_list('pk', flat=True)[:batch_size]
    )
    for fact in (
        MemoryFact.objects.select_for_update().filter(pk__in=identities).order_by('pk')
    ):
        _forget_locked(
            fact,
            actor_id=stone.deleted_by_id,
            reason='forget',
            deleted_at=stone.deleted_at,
        )
    if identities:
        _invalidate_summaries(owner.pk)
    return (
        not linked_facts(stone.thread_id)
        .filter(owner=owner, lifecycle_state__in=LIVE_STATES)
        .exists()
    )


def store_available():
    """Retention also cleans the SQLite fixture schema."""
    return connection.vendor in {'postgresql', 'sqlite'}


@transaction.atomic
def purge_thread_memory(thread_id):
    """Delete proposals and sever retained confirmed facts from deleted sources."""
    if not store_available():
        return
    from django.contrib.auth import get_user_model

    from aichat.services.memory_lifecycle import _scrub_proposals

    stone = ChatThreadTombstone.objects.filter(thread_id=thread_id).first()
    if stone and not forget_thread_batch(stone):
        raise ValueError('Linked memory cleanup is pending')
    linked = Q(source_thread_id=thread_id) | Q(claims__source_thread_id=thread_id)
    owner_ids = MemoryFact.objects.filter(linked).values('owner_id')
    list(
        get_user_model()
        .objects.select_for_update()
        .filter(pk__in=owner_ids)
        .order_by('pk')
    )
    # The memory action scope is owner-wide, so proposal.thread_id is empty.
    # Scrub copied preview/provenance explicitly before deleting or severing.
    for identity in (
        MemoryFact.objects.filter(linked).values_list('pk', flat=True).distinct()
    ):
        _scrub_proposals(identity)
    MemoryFact.objects.filter(linked, lifecycle_state='proposed').distinct().delete()
    rows = list(
        MemoryFact.objects.filter(linked).distinct().values('id', 'owner_id', 'version')
    )
    now = timezone.now()
    MemoryFactClaim.objects.filter(source_thread_id=thread_id).update(
        source_thread=None,
        source_message=None,
        attachment_id=None,
        source_model='',
        source_object_id='',
        source_field='',
        severed_at=now,
    )
    MemoryFact.objects.filter(source_thread_id=thread_id).update(
        source_thread=None, source_message=None
    )
    MemoryFactEvent.objects.bulk_create([
        MemoryFactEvent(
            owner_id=row['owner_id'],
            fact_id=row['id'],
            action='sever',
            version=row['version'],
        )
        for row in rows
    ])
    MemoryExtractionClaim.objects.filter(thread_id=thread_id).delete()
    MemoryExtractionRun.objects.filter(thread_id=thread_id).delete()


def thread_memory_residual(thread_id):
    """Retained confirmed facts count as clean only after source severance."""
    if not store_available():
        return 0
    return (
        MemoryFact.objects.filter(source_thread_id=thread_id).count()
        + MemoryFactClaim.objects.filter(source_thread_id=thread_id).count()
        + MemoryExtractionClaim.objects.filter(thread_id=thread_id).count()
        + MemoryExtractionRun.objects.filter(thread_id=thread_id).count()
    )


def purge_memory_runs(*, dry_run=False, batch_size=200):
    """Run diagnostics share the existing ninety-day detail-retention policy."""
    if not store_available():
        return {'memory_extraction_runs': 0}
    from aichat.services.retention import _batched_delete

    count = _batched_delete(
        MemoryExtractionRun.objects.filter(
            created_at__lt=timezone.now() - timedelta(days=90)
        ),
        family='memory_extraction_runs',
        batch_size=batch_size,
        dry_run=dry_run,
    )
    return {'memory_extraction_runs': count}


def purge_memory_fact_jobs(*, dry_run=False, batch_size=200):
    """Keep current-version retry guards; expire obsolete metadata after 90 days."""
    from aichat.services.retention import _batched_delete

    rows = MemoryFactJob.objects.filter(
        updated_at__lt=timezone.now() - timedelta(days=90)
    ).filter(
        ~Q(fact_version=F('fact__version'))
        | Q(
            fact__lifecycle_state__in=[
                'forgotten',
                'withdrawn',
                'expired',
                'superseded',
            ]
        )
    )
    return {
        'memory_fact_jobs': _batched_delete(
            rows, family='memory_fact_jobs', batch_size=batch_size, dry_run=dry_run
        )
    }
