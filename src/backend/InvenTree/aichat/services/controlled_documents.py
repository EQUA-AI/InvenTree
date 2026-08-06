"""Scoped lifecycle operations for governed AI retrieval documents."""

from __future__ import annotations

import re
from datetime import date

from django.db import transaction
from django.utils import timezone

from aichat.models import ControlledDocument, ControlledDocumentState

_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')


class ControlledDocumentError(Exception):
    """Base class carrying a stable controlled-document error code."""

    code = 'CONTROLLED_DOCUMENT_INVALID'


class ControlledDocumentNotFound(ControlledDocumentError):  # noqa: N818
    """Unknown document revision, or one outside the trusted scope."""

    code = 'CONTROLLED_DOCUMENT_NOT_FOUND'


class ControlledDocumentStateConflict(ControlledDocumentError):  # noqa: N818
    """Lifecycle transition is not allowed from the document's current state."""

    code = 'CONTROLLED_DOCUMENT_STATE_CONFLICT'


class ControlledDocumentSourceMismatch(ControlledDocumentError):  # noqa: N818
    """The source bytes no longer match the registered immutable fingerprint."""

    code = 'CONTROLLED_DOCUMENT_SOURCE_MISMATCH'


def _required(value: str, field_name: str, max_length: int) -> str:
    """Validate an API-sized required registry coordinate."""
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ControlledDocumentError(f'{field_name} is invalid')
    return value


def _validate_source_sha256(source_sha256: str) -> str:
    """Validate the exact lower-case SHA-256 source fingerprint."""
    if not isinstance(source_sha256, str) or not _SHA256_RE.fullmatch(source_sha256):
        raise ControlledDocumentError('source_sha256 is invalid')
    return source_sha256


def _scoped_document(
    *, scope_key: str, scope_hash: str, document_id: str, revision: str
) -> ControlledDocument:
    """Resolve a document only within its server-derived scope boundary."""
    try:
        return ControlledDocument.objects.get(
            scope_key=scope_key,
            scope_hash=scope_hash,
            document_id=document_id,
            revision=revision,
        )
    except ControlledDocument.DoesNotExist as exc:
        raise ControlledDocumentNotFound('no such controlled document') from exc


def register_document(
    *,
    document_id: str,
    revision: str,
    title: str,
    document_class: str,
    scope_key: str,
    scope_hash: str,
    access_class: str,
    source_filename: str,
    source_location: str,
    source_sha256: str,
    revision_date: date | None = None,
    facility: str = '',
    process_area: str = '',
    asset_id: str = '',
    child_asset_id: str = '',
    work_order_id: str = '',
    repair_packet_id: str = '',
    created_by=None,
    approved_by=None,
) -> ControlledDocument:
    """Register a source revision before it is submitted for indexing."""
    _required(document_id, 'document_id', 128)
    _required(revision, 'revision', 64)
    _required(title, 'title', 255)
    _required(document_class, 'document_class', 128)
    _required(scope_key, 'scope_key', 255)
    _required(scope_hash, 'scope_hash', 64)
    _required(access_class, 'access_class', 64)
    _required(source_filename, 'source_filename', 255)
    _required(source_location, 'source_location', 1024)
    _validate_source_sha256(source_sha256)

    with transaction.atomic():
        existing = (
            ControlledDocument.objects
            .select_for_update()
            .filter(scope_key=scope_key, document_id=document_id, revision=revision)
            .first()
        )
        if existing is not None:
            if existing.source_sha256 != source_sha256:
                raise ControlledDocumentSourceMismatch('source fingerprint changed')
            return existing
        return ControlledDocument.objects.create(
            document_id=document_id,
            revision=revision,
            title=title,
            document_class=document_class,
            scope_key=scope_key,
            scope_hash=scope_hash,
            access_class=access_class,
            source_filename=source_filename,
            source_location=source_location,
            source_sha256=source_sha256,
            revision_date=revision_date,
            facility=facility,
            process_area=process_area,
            asset_id=asset_id,
            child_asset_id=child_asset_id,
            work_order_id=work_order_id,
            repair_packet_id=repair_packet_id,
            created_by=created_by,
            approved_by=approved_by,
        )


def start_indexing(
    *, scope_key: str, scope_hash: str, document_id: str, revision: str
) -> ControlledDocument:
    """Claim a draft or failed revision for a new indexing attempt."""
    with transaction.atomic():
        document = _scoped_document(
            scope_key=scope_key,
            scope_hash=scope_hash,
            document_id=document_id,
            revision=revision,
        )
        document = ControlledDocument.objects.select_for_update().get(pk=document.pk)
        if document.state not in {
            ControlledDocumentState.DRAFT,
            ControlledDocumentState.FAILED,
        }:
            raise ControlledDocumentStateConflict('document cannot be indexed')
        document.state = ControlledDocumentState.INDEXING
        document.indexing_error_code = ''
        document.save(update_fields=['state', 'indexing_error_code', 'updated_at'])
        return document


def mark_indexed(
    *,
    scope_key: str,
    scope_hash: str,
    document_id: str,
    revision: str,
    source_sha256: str,
    search_index_name: str,
    embedding_model: str = '',
    embedding_dimensions: int = 0,
) -> ControlledDocument:
    """Publish an indexed revision and atomically supersede its predecessor."""
    _validate_source_sha256(source_sha256)
    _required(search_index_name, 'search_index_name', 128)
    if not isinstance(embedding_model, str) or len(embedding_model) > 128:
        raise ControlledDocumentError('embedding_model is invalid')
    if not isinstance(embedding_dimensions, int) or embedding_dimensions < 0:
        raise ControlledDocumentError('embedding_dimensions is invalid')

    with transaction.atomic():
        document = _scoped_document(
            scope_key=scope_key,
            scope_hash=scope_hash,
            document_id=document_id,
            revision=revision,
        )
        document = ControlledDocument.objects.select_for_update().get(pk=document.pk)
        if document.state != ControlledDocumentState.INDEXING:
            raise ControlledDocumentStateConflict('document is not indexing')
        if document.source_sha256 != source_sha256:
            raise ControlledDocumentSourceMismatch('source fingerprint changed')

        ControlledDocument.objects.select_for_update().filter(
            scope_key=scope_key,
            scope_hash=scope_hash,
            document_id=document_id,
            is_current=True,
        ).exclude(pk=document.pk).update(
            is_current=False,
            state=ControlledDocumentState.SUPERSEDED,
            updated_at=timezone.now(),
        )
        document.state = ControlledDocumentState.INDEXED
        document.is_current = True
        document.search_index_name = search_index_name
        document.indexed_at = timezone.now()
        document.indexing_error_code = ''
        document.embedding_model = embedding_model
        document.embedding_dimensions = embedding_dimensions
        document.save(
            update_fields=[
                'state',
                'is_current',
                'search_index_name',
                'indexed_at',
                'indexing_error_code',
                'embedding_model',
                'embedding_dimensions',
                'updated_at',
            ]
        )
        return document


def begin_reindex(
    *, scope_key: str, scope_hash: str, document_id: str, revision: str
) -> ControlledDocument:
    """Return an INDEXED revision to INDEXING for a governed re-embed (S17).

    ``is_current`` is deliberately left set: the revision keeps answering until
    ``mark_indexed`` republishes it with fresh vectors, and a failure lands in
    the same FAILED state any indexing failure does.
    """
    with transaction.atomic():
        document = _scoped_document(
            scope_key=scope_key,
            scope_hash=scope_hash,
            document_id=document_id,
            revision=revision,
        )
        document = ControlledDocument.objects.select_for_update().get(pk=document.pk)
        if document.state != ControlledDocumentState.INDEXED:
            raise ControlledDocumentStateConflict('document is not indexed')
        document.state = ControlledDocumentState.INDEXING
        document.indexing_error_code = ''
        document.save(update_fields=['state', 'indexing_error_code', 'updated_at'])
        return document


def indexed_embedding_models() -> list[str]:
    """Return the distinct embedding models stamped on current indexed revisions.

    Blank entries are revisions indexed before the stamp existed (S17); callers
    treat them as unknown, not as a match.
    """
    return list(
        ControlledDocument.objects
        .filter(state=ControlledDocumentState.INDEXED, is_current=True)
        .values_list('embedding_model', flat=True)
        .distinct()
    )


def mark_failed(
    *, scope_key: str, scope_hash: str, document_id: str, revision: str, error_code: str
) -> ControlledDocument:
    """Record a bounded indexing failure without ever exposing the revision."""
    _required(error_code, 'error_code', 64)

    with transaction.atomic():
        document = _scoped_document(
            scope_key=scope_key,
            scope_hash=scope_hash,
            document_id=document_id,
            revision=revision,
        )
        document = ControlledDocument.objects.select_for_update().get(pk=document.pk)
        if document.state != ControlledDocumentState.INDEXING:
            raise ControlledDocumentStateConflict('document is not indexing')
        document.state = ControlledDocumentState.FAILED
        document.is_current = False
        document.indexing_error_code = error_code
        document.save(
            update_fields=['state', 'is_current', 'indexing_error_code', 'updated_at']
        )
        return document


def get_indexed_document(
    *, scope_key: str, scope_hash: str, document_id: str, revision: str
) -> ControlledDocument:
    """Return one indexed revision or fail closed within the trusted scope."""
    document = _scoped_document(
        scope_key=scope_key,
        scope_hash=scope_hash,
        document_id=document_id,
        revision=revision,
    )
    if document.state != ControlledDocumentState.INDEXED:
        raise ControlledDocumentNotFound('no such controlled document')
    return document
