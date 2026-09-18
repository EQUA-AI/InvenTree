"""Thread deletion contracts for the PostgreSQL durable-memory store."""

from datetime import timedelta

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from aichat.models import (
    MemoryExtractionClaim,
    MemoryExtractionRun,
    MemoryFact,
    MemoryFactClaim,
    MemoryFactEvent,
)


def store_available():
    """Retention also cleans the SQLite fixture schema."""
    return connection.vendor in {'postgresql', 'sqlite'}


@transaction.atomic
def purge_thread_memory(thread_id):
    """Delete proposals and sever retained confirmed facts from deleted sources."""
    if not store_available():
        return
    linked = Q(source_thread_id=thread_id) | Q(claims__source_thread_id=thread_id)
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
