"""Reverse Search samples and durable, metadata-only orphan repair obligations.

Offset samples are not an index snapshot or whole-index certification. The
existing schemas do not expose a sortable key; never claim gapless traversal.
"""

import hashlib
import json
from itertools import islice

from django.db import transaction
from django.db.models import F

from aichat.models import (
    AttachmentIngest,
    RagProjectionAudit,
    RagProjectionGate,
    RagProjectionOrphan,
)
from aichat.services.projection_audit import FIELDS
from InvenTree.restore_hold import restore_hold_enabled


def _projection(corpus):
    from ai.core.integrations.attachment_search import (
        AttachmentSearchProjection,
        MediaSearchProjection,
    )

    if corpus not in {'attachment', 'media'}:
        raise ValueError('Invalid corpus')
    return (
        AttachmentSearchProjection if corpus == 'attachment' else MediaSearchProjection
    ).from_settings()


def _reason(row, *, corpus, index_name):
    """Rebuild authority from native records; Search metadata grants nothing."""
    from ai.core.config import get_settings
    from aichat.services.attachment_ingestion import derive_client_codes
    from common.models import Attachment

    attachment_id = row.get('attachment_id')
    if type(attachment_id) is not int or attachment_id < 1:
        return 'invalid_source'
    source = Attachment.objects.filter(pk=attachment_id).first()
    if source is None:
        return 'missing_source'
    ingest = (
        AttachmentIngest.objects
        .filter(attachment_id=attachment_id, search_index_name=index_name)
        .order_by(F('claimed_at').desc(nulls_last=True), '-pk')
        .first()
    )
    if ingest is None:
        return 'missing_ledger'
    pipelines = {'doc'} if corpus == 'attachment' else {'image', 'video'}
    if ingest.state != 'indexed' or ingest.pipeline not in pipelines:
        return 'inactive_ledger'
    expected = {
        'model_type': source.model_type,
        'model_id': source.model_id,
        'scope_key': get_settings().single_site_policy_key,
        'access_class': 'attachment_uploaded'
        if corpus == 'attachment'
        else 'evidence_recording',
        'source_sha256': ingest.source_sha256,
        'is_current': True,
    }
    if any(row.get(key) != value for key, value in expected.items()):
        return 'metadata_drift'
    clients = row.get('client_codes')
    if (
        not isinstance(clients, list)
        or any(not isinstance(x, str) for x in clients)
        or set(clients) != set(derive_client_codes(source.model_type, source.model_id))
    ):
        return 'scope_drift'
    relation = ingest.chunks if corpus == 'attachment' else ingest.segments
    if not relation.filter(search_doc_id=row.get('id')).exists():
        return 'missing_chunk'
    return ''


def _identity(corpus, index_name, document_id):
    return hashlib.sha256(
        json.dumps([corpus, index_name, document_id]).encode()
    ).hexdigest()


@transaction.atomic
def _record(*, corpus, index_name, row, reason):
    # Serialize against operator release. Orphans outlive deleted ingest rows.
    RagProjectionGate.objects.update_or_create(
        corpus=corpus, defaults={'blocked': True}
    )
    document_id = row.get('id')
    if not isinstance(document_id, str) or not 1 <= len(document_id) <= 1024:
        raise ValueError('Invalid projection identity')
    RagProjectionOrphan.objects.update_or_create(
        identity=_identity(corpus, index_name, document_id),
        defaults={
            'corpus': corpus,
            'index_name': index_name,
            'document_id': document_id,
            'reason': reason,
            'resolved': False,
        },
    )


def scan_orphans(*, corpus, sample=20, offset=0, record=False, projection=None):
    """Check at most 100 Search-origin rows, with bounded offset and no bodies."""
    if (
        corpus not in {'attachment', 'media'}
        or type(sample) is not int
        or not 1 <= sample <= 100
        or type(offset) is not int
        or not 0 <= offset <= 100000
    ):
        raise ValueError('Invalid reverse sample')
    if restore_hold_enabled():
        return {'outcome': 'restore_hold', 'sampled': 0}
    owned = projection is None
    projection = projection or _projection(corpus)
    report = {
        'corpus': corpus,
        'sampled': 0,
        'drift': 0,
        'critical': 0,
        'errors': 0,
        'truncated': False,
    }
    more = False
    try:
        rows = list(
            islice(
                projection.client().search(
                    search_text='*',
                    select=FIELDS,
                    skip=offset,
                    top=sample + 1,
                    connection_timeout=5,
                    read_timeout=10,
                    retry_total=0,
                ),
                sample + 1,
            )
        )
        more = len(rows) > sample
        for row in rows[:sample]:
            report['sampled'] += 1
            try:
                reason = _reason(row, corpus=corpus, index_name=projection.index_name)
                if reason:
                    report['drift'] += 1
                    report['critical'] += 1
                    if record:
                        _record(
                            corpus=corpus,
                            index_name=projection.index_name,
                            row=row,
                            reason=reason,
                        )
            except Exception:
                report['errors'] += 1
    except Exception:
        report['errors'] += 1
    finally:
        if owned:
            projection.close()
    report['truncated'] = more and offset + sample > 100000
    report['outcome'] = (
        'incomplete'
        if report['errors'] or report['truncated']
        else 'drift'
        if report['drift']
        else 'clean_sample'
        if report['sampled']
        else 'no_sample'
    )
    if record:
        if report['errors']:
            RagProjectionGate.objects.update_or_create(
                corpus=corpus, defaults={'blocked': True}
            )
        RagProjectionAudit.objects.create(**report)
    report['next_offset'] = (
        offset + sample if more and not report['truncated'] else None
    )
    report['coverage'] = 'sample_only'
    return report


def recheck_orphan(*, identity, projection=None):
    """Only current native validity or exact Search 404 resolves an obligation.

    No document is deleted, no gate released, and no moved/retired index silently
    substituted for the one in the finding. Provider failures preserve the stop.
    """
    from azure.core.exceptions import ResourceNotFoundError

    if restore_hold_enabled():
        raise ValueError('Restore hold')
    finding = RagProjectionOrphan.objects.get(pk=identity)
    owned = projection is None
    projection = projection or _projection(finding.corpus)
    try:
        if projection.index_name != finding.index_name:
            raise ValueError(
                'Finding index is no longer configured; explicit adapter required'
            )
        try:
            row = projection.client().get_document(
                key=finding.document_id,
                selected_fields=FIELDS,
                connection_timeout=5,
                read_timeout=10,
                retry_total=0,
            )
        except ResourceNotFoundError:
            # A missing index must not be mistaken for an absent document.
            # The same index must still answer a read before accepting the 404.
            list(
                islice(
                    projection.client().search(
                        search_text='*',
                        select=['id'],
                        top=1,
                        connection_timeout=5,
                        read_timeout=10,
                        retry_total=0,
                    ),
                    1,
                )
            )
            reason = ''
        else:
            if row.get('id') != finding.document_id:
                raise ValueError('Projection identity changed')
            reason = _reason(row, corpus=finding.corpus, index_name=finding.index_name)
        with transaction.atomic():
            RagProjectionGate.objects.select_for_update().get(corpus=finding.corpus)
            current = RagProjectionOrphan.objects.select_for_update().get(pk=identity)
            if current.updated_at != finding.updated_at:
                raise ValueError('Finding changed during recheck')
            current.resolved = not reason
            if reason:
                current.reason = reason
            current.save(update_fields=['resolved', 'reason', 'updated_at'])
        return {'status': 'resolved' if not reason else 'pending', 'identity': identity}
    finally:
        if owned:
            projection.close()
