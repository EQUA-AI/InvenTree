"""Scope- and record-bound selection of governed AI retrieval documents."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from aichat.models import ControlledDocument, ControlledDocumentState
from aichat.services import context as context_service


class ControlledDocumentUnavailable(context_service.ContextError):
    """The requested document is absent, stale, or unrelated to the context."""

    code = 'CONTROLLED_DOCUMENT_UNAVAILABLE'


@dataclass(frozen=True)
class SelectedControlledDocument:
    """A reauthorized immutable document coordinate safe for a trusted context."""

    document: ControlledDocument

    @property
    def selection_id(self) -> str:
        """Opaque UUID exposed to the browser and signed context token."""
        return str(self.document.selection_id)

    def payload(self) -> dict[str, str]:
        """Return the small allow-listed UI and context representation."""
        return {
            'selection_id': self.selection_id,
            'title': self.document.title,
            'document_id': self.document.document_id,
            'revision': self.document.revision,
            'source_sha256': self.document.source_sha256,
            'access_class': self.document.access_class,
        }


def _context_machine(record: Any):
    """Return the machine pinned by a machine or work-order scoped context."""
    machine = getattr(record, 'machine', None)
    if machine is not None:
        return machine
    if hasattr(record, 'serial'):
        return record
    return None


def _matches_context(*, document: ControlledDocument, record: Any) -> bool:
    """Require the document's governed asset and work-order coordinates to match."""
    machine = _context_machine(record)
    machine_serial = str(getattr(machine, 'serial', '') or '')
    if not machine_serial or document.asset_id != machine_serial:
        return False
    reference = getattr(record, 'reference', None)
    return not (
        reference and document.work_order_id and document.work_order_id != reference
    )


def resolve_selected_document(
    *, selection_id: str, scope_key: str, scope_hash: str, record: Any
) -> SelectedControlledDocument:
    """Resolve one current indexed document after record and scope authorization.

    Callers must supply a freshly authorized record and server-derived scope;
    browser-provided document text, revisions, and asset identifiers are never
    consulted.
    """
    try:
        selection_uuid = uuid.UUID(str(selection_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ControlledDocumentUnavailable('controlled document unavailable') from exc
    try:
        document = ControlledDocument.objects.get(
            selection_id=selection_uuid,
            scope_key=scope_key,
            scope_hash=scope_hash,
            state=ControlledDocumentState.INDEXED,
            is_current=True,
        )
    except ControlledDocument.DoesNotExist as exc:
        raise ControlledDocumentUnavailable('controlled document unavailable') from exc
    if not _matches_context(document=document, record=record):
        raise ControlledDocumentUnavailable('controlled document unavailable')
    return SelectedControlledDocument(document=document)


def reauthorize_selected_document(
    *, user, context_type: str, object_id: str, selection_id: str
) -> SelectedControlledDocument:
    """Reauthorize the record, scope, and selected document for every use."""
    record = context_service.reauthorize_context(
        user, context_type=context_type, object_id=object_id
    )
    scope_key, scope_hash = context_service.actor_scope_strings(user)
    return resolve_selected_document(
        selection_id=selection_id,
        scope_key=scope_key,
        scope_hash=scope_hash,
        record=record,
    )
