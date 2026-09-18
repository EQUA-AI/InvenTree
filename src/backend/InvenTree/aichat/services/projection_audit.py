"""Bounded, metadata-only checks of attachment and media Search projections."""

import hashlib
import json
from datetime import timedelta
from itertools import islice

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from ai.core.config import get_settings
from aichat.models import (
    AIUsageMonthlyAggregate,
    AttachmentIngest,
    RagProjectionAudit,
    RagProjectionGate,
    RagProjectionOrphan,
    RagProjectionRepair,
)
from InvenTree.restore_hold import restore_hold_enabled

FIELDS = [
    'id',
    'attachment_id',
    'source_sha256',
    'model_type',
    'model_id',
    'client_codes',
    'scope_key',
    'access_class',
    'is_current',
]
MAX_DOCUMENTS = 100


@transaction.atomic
def release_gate(*, corpus, expected_blocked_at):
    """Explicit operator action after repairs and newer clean evidence; never automatic."""
    from datetime import datetime

    if corpus not in {'attachment', 'media', 'controlled'} or restore_hold_enabled():
        raise ValueError('Projection release unavailable')
    try:
        expected = datetime.fromisoformat(expected_blocked_at)
    except (TypeError, ValueError):
        raise ValueError('Invalid stop receipt') from None
    gate = (
        RagProjectionGate.objects
        .select_for_update()
        .filter(corpus=corpus, blocked=True)
        .first()
    )
    if gate is None or gate.updated_at != expected:
        raise ValueError('Stop receipt changed')
    if (
        RagProjectionRepair.objects.filter(corpus=corpus, resolved=False).exists()
        or RagProjectionOrphan.objects.filter(corpus=corpus, resolved=False).exists()
    ):
        raise ValueError('Open projection repairs remain')
    latest = (
        RagProjectionAudit.objects
        .filter(corpus=corpus)
        .order_by('-created_at', '-pk')
        .first()
    )
    if (
        latest is None
        or latest.outcome != 'clean_sample'
        or latest.created_at <= gate.updated_at
    ):
        raise ValueError('New clean sample required')
    gate.blocked = False
    gate.save(update_fields=['blocked', 'updated_at'])
    RagProjectionAudit.objects.create(corpus=corpus, outcome='operator_release')
    return {'status': 'released', 'corpus': corpus}


def _inspect(ingest, projection, corpus):
    from aichat.services.attachment_ingestion import derive_client_codes
    from common.models import Attachment

    attachment = Attachment.objects.filter(pk=ingest.attachment_id).first()
    model_type = attachment.model_type if attachment else ingest.model_type
    model_id = attachment.model_id if attachment else ingest.model_id
    clients = derive_client_codes(model_type, model_id) if attachment else []
    expected = {
        'attachment_id': ingest.attachment_id,
        'source_sha256': ingest.source_sha256,
        'model_type': model_type,
        'model_id': model_id,
        'client_codes': sorted(clients),
        'scope_key': get_settings().single_site_policy_key,
        'access_class': 'attachment_uploaded'
        if corpus == 'attachment'
        else 'evidence_recording',
        'is_current': True,
    }
    signature = hashlib.sha256(
        json.dumps(expected, sort_keys=True).encode()
    ).hexdigest()
    rows = list(
        islice(
            projection.client().search(
                search_text='*',
                filter=f'attachment_id eq {int(ingest.attachment_id)}',
                select=FIELDS,
                top=MAX_DOCUMENTS + 1,
                connection_timeout=5,
                read_timeout=10,
                retry_total=0,
            ),
            MAX_DOCUMENTS + 1,
        )
    )
    critical = any(
        attachment is None
        or ingest.state == 'deleted'
        or any(
            row.get(key) != expected[key]
            for key in (
                'attachment_id',
                'model_type',
                'model_id',
                'scope_key',
                'access_class',
            )
        )
        or not isinstance(row.get('client_codes'), list)
        or any(not isinstance(code, str) for code in row.get('client_codes', []))
        or set(row.get('client_codes', [])) - set(clients)
        for row in rows
    )
    relation = ingest.chunks if corpus == 'attachment' else ingest.segments
    expected_ids = (
        list(relation.values_list('search_doc_id', flat=True)[: MAX_DOCUMENTS + 1])
        if ingest.state == 'indexed' and attachment
        else []
    )
    truncated = len(rows) > MAX_DOCUMENTS or len(expected_ids) > MAX_DOCUMENTS
    drift = (
        critical
        or set(expected_ids) != {row.get('id') for row in rows}
        or any(
            row.get('source_sha256') != expected['source_sha256']
            or row.get('is_current') is not True
            or sorted(row.get('client_codes') or []) != expected['client_codes']
            for row in rows
        )
    )
    return bool(drift), bool(critical), truncated, signature


def verify_projection(*, corpus, sample=20, after=0, record=False, projection=None):
    """Inspect one keyset page. Recording may latch recall, never repair or release it."""
    if (
        corpus not in {'attachment', 'media'}
        or type(sample) is not int
        or not 1 <= sample <= 100
        or type(after) is not int
        or after < 0
    ):
        raise ValueError('Invalid audit selection')
    if restore_hold_enabled():
        return {'outcome': 'restore_hold', 'sampled': 0}
    owned_projection = projection is None
    if projection is None:
        from ai.core.integrations.attachment_search import (
            AttachmentSearchProjection,
            MediaSearchProjection,
        )

        projection = (
            AttachmentSearchProjection
            if corpus == 'attachment'
            else MediaSearchProjection
        ).from_settings()
    rows = AttachmentIngest.objects.filter(
        pk__gt=after, search_index_name=projection.index_name
    )
    rows = (
        rows.filter(pipeline='doc')
        if corpus == 'attachment'
        else rows.filter(pipeline__in=['image', 'video'])
    )
    # Inspect each attachment's most recently claimed revision, including a
    # deletion ledger. Old superseded revisions cannot define current authority.
    from django.db.models import OuterRef, Subquery

    latest = (
        AttachmentIngest.objects
        .filter(
            attachment_id=OuterRef('attachment_id'),
            search_index_name=projection.index_name,
        )
        .order_by(F('claimed_at').desc(nulls_last=True), '-pk')
        .values('pk')[:1]
    )
    page = list(rows.filter(pk=Subquery(latest)).order_by('pk')[: sample + 1])
    result = {
        'corpus': corpus,
        'sampled': 0,
        'drift': 0,
        'critical': 0,
        'errors': 0,
        'truncated': False,
    }
    try:
        for ingest in page[:sample]:
            result['sampled'] += 1
            try:
                drift, critical, truncated, signature = _inspect(
                    ingest, projection, corpus
                )
                result['drift'] += int(drift)
                result['critical'] += int(critical)
                result['truncated'] |= truncated
                if record:
                    with transaction.atomic():
                        if critical:
                            RagProjectionGate.objects.update_or_create(
                                corpus=corpus, defaults={'blocked': True}
                            )
                        if drift or truncated:
                            RagProjectionRepair.objects.update_or_create(
                                ingest=ingest,
                                defaults={
                                    'corpus': corpus,
                                    'reason': 'critical'
                                    if critical
                                    else 'incomplete'
                                    if truncated
                                    else 'drift',
                                    'snapshot_hash': signature,
                                    'resolved': False,
                                },
                            )
                        else:
                            RagProjectionRepair.objects.filter(ingest=ingest).update(
                                resolved=True
                            )
            except Exception:
                result['errors'] += 1
    finally:
        if owned_projection:
            projection.close()
    result['outcome'] = (
        'incomplete'
        if result['errors'] or result['truncated']
        else 'drift'
        if result['drift']
        else 'clean_sample'
        if result['sampled']
        else 'no_sample'
    )
    if record:
        RagProjectionAudit.objects.create(**result)
    result['next_after'] = page[sample - 1].pk if len(page) > sample else None
    return result


def scheduled_audit():
    """Default-off daily rotating samples; no code deployment activates providers."""
    from django.core.cache import cache

    if not get_settings().feature_rag_projection_audit or restore_hold_enabled():
        return {'status': 'disabled'}
    results = {}
    for corpus in ('attachment', 'media', 'controlled'):
        key = f'aimms:projection-audit:{corpus}:cursor'
        try:
            report = {'outcome': 'not_applicable'}
            if corpus != 'controlled':
                report = verify_projection(
                    corpus=corpus, after=cache.get(key, 0), record=True
                )
                cache.set(key, report.get('next_after') or 0, timeout=7 * 86400)
            from aichat.services.projection_orphans import scan_orphans

            reverse_key = f'aimms:projection-audit:{corpus}:reverse-cursor'
            reverse = scan_orphans(
                corpus=corpus, offset=cache.get(reverse_key, 0), record=True
            )
            if not reverse.get('errors'):
                cache.set(
                    reverse_key, reverse.get('next_offset') or 0, timeout=7 * 86400
                )
            results[corpus] = {'forward': report, 'reverse': reverse}
        except Exception:
            RagProjectionAudit.objects.create(
                corpus=corpus, outcome='incomplete', errors=1
            )
            results[corpus] = {'outcome': 'incomplete'}
    return results


@transaction.atomic
def purge_audits(*, dry_run=False, batch_size=200):
    """Roll old counts up atomically before detail deletion; keep unresolved work."""
    rows = RagProjectionAudit.objects.filter(
        created_at__lt=timezone.now() - timedelta(days=90)
    )
    if dry_run:
        return {'rag_projection_audits': rows.count()}
    page = list(rows.select_for_update().order_by('pk')[:batch_size])
    for row in page:
        for field in ('sampled', 'drift', 'critical', 'errors'):
            aggregate, _ = AIUsageMonthlyAggregate.objects.get_or_create(
                month=row.created_at.date().replace(day=1),
                source='rag_projection_audits',
                user_id=0,
                dimension=f'{row.corpus}:{field}',
            )
            AIUsageMonthlyAggregate.objects.filter(pk=aggregate.pk).update(
                turn_count=F('turn_count') + getattr(row, field)
            )
    RagProjectionAudit.objects.filter(pk__in=[row.pk for row in page]).delete()
    RagProjectionRepair.objects.filter(
        resolved=True, updated_at__lt=timezone.now() - timedelta(days=90)
    ).delete()
    RagProjectionOrphan.objects.filter(
        resolved=True, updated_at__lt=timezone.now() - timedelta(days=90)
    ).delete()
    return {'rag_projection_audits': len(page)}
