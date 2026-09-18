"""Shared current native metadata policy for audits and excerpt reauthorization."""

from django.db.models import F

from aichat.models import AttachmentIngest


def projection_row_reason(row, *, corpus, index_name):
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


def controlled_projection_reason(row, *, index_name):
    """Rebuild current controlled-source authority without selecting body text."""
    from ai.core.config import get_settings
    from aichat.models import ControlledDocument

    scope_key = get_settings().single_site_policy_key
    if not scope_key or row.get('scope_key') != scope_key:
        return 'scope_drift'
    document = ControlledDocument.objects.filter(
        scope_key=scope_key,
        document_id=row.get('document_id'),
        revision=row.get('document_revision'),
        search_index_name=index_name,
    ).first()
    if document is None:
        return 'missing_registry'
    if (
        not document.is_current
        or document.state != 'indexed'
        or row.get('is_current') is not True
    ):
        return 'inactive_registry'
    if row.get('source_sha256') != document.source_sha256:
        return 'source_drift'
    if (
        document.access_class != 'maintenance_authorized'
        or row.get('access_class') != document.access_class
        or str(row.get('asset_id') or '') != document.asset_id
    ):
        return 'metadata_drift'
    return ''
