"""Thread deletion contracts for the PostgreSQL durable-memory store."""

from datetime import timedelta

from django.db import connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from aichat.models import (
    MemoryExtractionClaim,
    MemoryExtractionRun,
    MemoryFact,
    MemoryFactClaim,
    MemoryFactEvent,
    MemoryFactJob,
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
