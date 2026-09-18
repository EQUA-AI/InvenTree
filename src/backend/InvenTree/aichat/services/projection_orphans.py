"""Reverse Search samples and durable, metadata-only orphan repair obligations.

Offset samples are not an index snapshot or whole-index certification. The
existing schemas do not expose a sortable key; never claim gapless traversal.
"""

import hashlib
import json
from itertools import islice

from django.db import transaction

from aichat.models import RagProjectionAudit, RagProjectionGate, RagProjectionOrphan
from aichat.services.projection_audit import FIELDS
from aichat.services.projection_authority import (
    controlled_projection_reason,
    projection_row_reason,
)
from InvenTree.restore_hold import restore_hold_enabled


def _projection(corpus):
    from ai.core.integrations.attachment_search import (
        AttachmentSearchProjection,
        MediaSearchProjection,
    )

    if corpus == 'controlled':
        from ai.core.integrations.controlled_document_search import (
            AzureSelectedDocumentSearch,
        )

        return AzureSelectedDocumentSearch.from_settings()
    if corpus not in {'attachment', 'media'}:
        raise ValueError('Invalid corpus')
    return (
        AttachmentSearchProjection if corpus == 'attachment' else MediaSearchProjection
    ).from_settings()


def _fields(corpus):
    if corpus == 'controlled':
        return [
            'id',
            'document_id',
            'document_revision',
            'source_sha256',
            'scope_key',
            'access_class',
            'asset_id',
            'is_current',
        ]
    return FIELDS


def _reason(row, *, corpus, index_name):
    if corpus == 'controlled':
        # A deliberately superseded chunk is not a serving-authority orphan.
        if row.get('is_current') is False:
            return ''
        return controlled_projection_reason(row, index_name=index_name)
    return projection_row_reason(row, corpus=corpus, index_name=index_name)


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
        corpus not in {'attachment', 'media', 'controlled'}
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
                    filter='is_current eq true' if corpus == 'controlled' else None,
                    select=_fields(corpus),
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
                selected_fields=_fields(finding.corpus),
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
