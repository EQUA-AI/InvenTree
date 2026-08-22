"""Attachment RAG doc ingestion (R1): router, extraction, registry, projection.

Fork-owned pipeline behind the upload substrate: content is sniffed (never
trusted by extension alone), routed, extracted with Document Intelligence
``prebuilt-layout`` Markdown, chunked with the existing deterministic chunker,
embedded with Cohere Embed v4, recorded in the Postgres system of record, and
projected into the attachment-docs Search index with zero-gap supersede.

Trust invariants: everything indexed here carries
``access_class='attachment_uploaded'`` — never ``maintenance_authorized`` —
and unresolved scope refuses to project (denial ≡ nonexistence).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time
from pathlib import PurePosixPath

logger = logging.getLogger('inventree')

#: Owners the doc pipeline ingests in v1.
ALLOWED_MODEL_TYPES = ('part', 'assetmachine')

#: Owners the receivers offload for at all — the doc owners plus the owners
#: whose uploads get *recorded* skips (decision #10, review finding F-07):
#: the retrieval-miss feedback loop needs the demand data.
RECEIVER_MODEL_TYPES = (
    *ALLOWED_MODEL_TYPES,
    'workorder',
    'workorderstepexecution',
    'repairpacket',
)

#: Terminal retry cap (decision #12): DI outages are transient, so failures
#: retry — but never unboundedly. Applies to every non-indexed state
#: (review finding F-04), enforced inside the atomic claim.
MAX_INGEST_ATTEMPTS = 3

#: Signal-side dedupe stamp schema version. Bump whenever router/sniff logic
#: changes semantics: old stamps then never match, so previously mis-skipped
#: content gets exactly one re-offload on its next save.
STAMP_VERSION = 2

#: Skip reasons that are flag-dependent rather than content-terminal: their
#: stamps stop matching once the corresponding flag turns on (revival).
FLAG_DEPENDENT_SKIPS = (
    'ATTACHMENT_SKIP_PIPELINE_DISABLED',
    'ATTACHMENT_SKIP_MEDIA_PIPELINE_DARK',
    'ATTACHMENT_SKIP_VIDEO_PIPELINE_DARK',
)

#: R3: the router's image arm consults ``media_ingest_enabled`` — the SAME
#: helper the receiver revival predicate reads — so dark stamps revive with
#: exactly one re-offload when the flag pair flips, and never before.
MEDIA_ROUTER_HONORS_FLAG = True

#: R4: the router's video arm consults ``media_ingest_enabled`` — the SAME
#: helper the receiver revival predicate reads — so dark video stamps revive
#: with exactly one re-offload wherever this code deploys with the media
#: flags on, and never before.
VIDEO_ROUTER_HONORS_FLAG = True

#: Sniff window: PDF headers may legally sit up to 1024 bytes in.
HEAD_BYTES = 1024

ACCESS_CLASS = 'attachment_uploaded'

#: Trust tier for auto-ingested evidence media (decision #16): a single class
#: for WO/step evidence and machine reference photos alike — never
#: ``maintenance_authorized``.
EVIDENCE_ACCESS_CLASS = 'evidence_recording'

#: Owners whose media the pipeline ingests (spec §5.2); part imagery stays
#: excluded (decision #10, ``ATTACHMENT_SKIP_PART_IMAGE``).
_MEDIA_MODEL_TYPES = ('workorder', 'workorderstepexecution', 'assetmachine')

#: Fallback client code for parts with no MachinePart linkage (§5.1): keeps
#: today's internal visibility until a part is linked to client machines.
INTERNAL_CLIENT_CODE = 'internal'

_MB = 1024 * 1024

# Inline ``Part.from_bytes`` request bound from the R4 design. Source videos
# may be 500 MB, but one high-bitrate 60 s clip must never become an
# unbounded allocation or request payload. A future GCS-URI transport can
# replace this ceiling without changing the segmentation contract.
VIDEO_SEGMENT_MAX_BYTES = 55 * _MB

_DOC_EXTENSIONS = {'.pdf', '.docx', '.md', '.markdown', '.txt'}
_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tif', '.tiff'}
_VIDEO_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.avi', '.mkv', '.webm'}


class AttachmentIngestionError(Exception):
    """A bounded ingestion failure carrying only a value-free code."""

    code = 'ATTACHMENT_INGEST_FAILED'

    def __init__(self, message: str, *, code: str | None = None) -> None:
        """Attach an optional override for the class-level default code."""
        super().__init__(message)
        if code is not None:
            self.code = code


@dataclass(frozen=True)
class RouteDecision:
    """Task-level router outcome for one attachment revision."""

    action: str  # 'ingest' | 'skip'
    pipeline: str  # AttachmentIngestPipeline value best describing the content
    kind: str  # sniffed content kind ('pdf', 'docx', 'text', ...)
    reason: str = ''  # value-free skip code when action == 'skip'


def _sniff_kind(head: bytes) -> str:
    """Classify content by magic bytes; extension alone chooses nothing."""
    if b'%PDF-' in head[:HEAD_BYTES]:
        # The PDF spec permits the signature up to 1024 bytes in (F-21).
        return 'pdf'
    if head.startswith(b'PK\x03\x04'):
        return 'zip_office'
    if head.startswith((b'\x89PNG', b'\xff\xd8\xff', b'GIF8', b'BM')):
        return 'image'
    if head.startswith(b'RIFF'):
        # RIFF is a container family, not a video format (F-21).
        if head[8:12] == b'WEBP':
            return 'image'
        if head[8:12] == b'WAVE':
            return 'audio'
        return 'video'
    if head[4:8] == b'ftyp':
        return 'video'
    if not head:
        return 'empty'
    # A multibyte character cut at the sniff window is still text (E1); UTF-8
    # needs at most 3 trailing bytes trimmed. Only a window-truncated head may
    # trim — a complete small file ending mid-character really is malformed.
    max_trim = 3 if len(head) >= HEAD_BYTES else 0
    for trim in range(max_trim + 1):
        try:
            head[: len(head) - trim].decode('utf-8')
        except UnicodeDecodeError:
            continue
        return 'text'
    return 'binary'


def _image_mime(head: bytes) -> str | None:
    """MIME type for the raster formats the image pipeline ingests (R3).

    Deliberately narrower than ``_sniff_kind``'s ``image`` bucket: Gemini
    media-embedding support for GIF/BMP/TIFF is unverified, so those record a
    terminal ``ATTACHMENT_SKIP_UNSUPPORTED_TYPE`` instead of failing live.
    """
    if head.startswith(b'\x89PNG'):
        return 'image/png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head.startswith(b'RIFF') and head[8:12] == b'WEBP':
        return 'image/webp'
    return None


#: ftyp major brands the video pipeline accepts as MP4-family (R4).
_MP4_BRANDS = {
    b'isom',
    b'iso2',
    b'iso4',
    b'iso5',
    b'iso6',
    b'mp41',
    b'mp42',
    b'avc1',
    b'mp4v',
    b'M4V ',
}


def _video_mime(head: bytes) -> str | None:
    """MIME type for the containers the video pipeline ingests (R4).

    Deliberately narrower than ``_sniff_kind``'s ``video`` bucket: only
    ftyp-based MP4/MOV are verified against Gemini video embedding; RIFF/AVI
    and unknown ftyp brands (incl. audio-only ``M4A ``) record a terminal
    ``ATTACHMENT_SKIP_UNSUPPORTED_TYPE`` instead of failing live.
    """
    if head[4:8] != b'ftyp':
        return None
    brand = head[8:12]
    if brand == b'qt  ':
        return 'video/quicktime'
    if brand in _MP4_BRANDS:
        return 'video/mp4'
    return None


def media_ingest_enabled(settings) -> bool:
    """The media router arm's gate: Django co-gate AND the AI-plane flag.

    One shared predicate by design — the receiver revival clause
    (``receivers._flag_dependent_skip_matches``) must read EXACTLY the
    expression the router enforces, or a partial flip re-offloads every media
    save forever. The Django flag is checked first: it cannot throw, so a
    dark Django plane suppresses without touching AI config.
    """
    from django.conf import settings as django_settings

    if not getattr(django_settings, 'AIMMS_MEDIA_RAG_ENABLED', False):
        return False
    return bool(getattr(settings, 'feature_media_rag_ingest', False))


#: Fallback structural cap when the AI config cannot load: the receiver must
#: still offload so the task fails LOUDLY instead of the config error being
#: silently swallowed on the request path (review finding F-15).
_FALLBACK_STRUCTURAL_CAP_MB = 500


def structural_skip_reason(attachment) -> str | None:
    """Receiver-level (row-free) exclusions shared with the backfill command.

    These are content classes the pipeline never records rows for: link-only
    attachments (SSRF — never fetched), SVGs (sanitized-but-scripted, zero RAG
    value), and files beyond every pipeline cap.
    """
    if not attachment.attachment or not attachment.attachment.name:
        return 'link_only'
    if attachment.attachment.name.lower().endswith('.svg'):
        return 'svg'
    try:
        from ai.core.config import get_settings

        cap_mb = get_settings().rag_max_video_mb
    except Exception:
        cap_mb = _FALLBACK_STRUCTURAL_CAP_MB
    if (attachment.file_size or 0) > cap_mb * _MB:
        return 'oversize'
    return None


def route_attachment(attachment, head: bytes) -> RouteDecision:
    """Route one structurally-acceptable attachment (decisions #10/#11).

    Reachable-but-not-ingested content gets an explicit recorded skip; the
    doc path serves ``part``/``assetmachine`` files only in v1.
    """
    from ai.core.config import get_settings

    name = (attachment.attachment.name or '').lower()
    extension = PurePosixPath(name).suffix
    kind = _sniff_kind(head)
    model_type = attachment.model_type

    if model_type == 'repairpacket':
        return RouteDecision('skip', 'doc', kind, 'ATTACHMENT_SKIP_REPAIRPACKET')
    if kind == 'image' and model_type in _MEDIA_MODEL_TYPES:
        # R3 media arm: evidence photos on WO/step/machine owners.
        if _image_mime(head) is None:
            return RouteDecision(
                'skip', 'image', kind, 'ATTACHMENT_SKIP_UNSUPPORTED_TYPE'
            )
        if not media_ingest_enabled(get_settings()):
            return RouteDecision(
                'skip', 'image', kind, 'ATTACHMENT_SKIP_MEDIA_PIPELINE_DARK'
            )
        return RouteDecision('ingest', 'image', kind)
    if kind == 'video' and model_type in _MEDIA_MODEL_TYPES:
        # R4 video arm: evidence recordings on WO/step/machine owners.
        if _video_mime(head) is None:
            return RouteDecision(
                'skip', 'video', kind, 'ATTACHMENT_SKIP_UNSUPPORTED_TYPE'
            )
        if not media_ingest_enabled(get_settings()):
            return RouteDecision(
                'skip', 'video', kind, 'ATTACHMENT_SKIP_VIDEO_PIPELINE_DARK'
            )
        return RouteDecision('ingest', 'video', kind)
    if model_type in ('workorder', 'workorderstepexecution'):
        if kind == 'audio':
            # Terminal: no pipeline ever ingests bare audio files.
            return RouteDecision(
                'skip', 'doc', kind, 'ATTACHMENT_SKIP_UNSUPPORTED_TYPE'
            )
        # Plausible service reports; revisit via the retrieval-miss ledger.
        return RouteDecision('skip', 'doc', kind, 'ATTACHMENT_SKIP_WORKORDER_DOC')
    if model_type not in ALLOWED_MODEL_TYPES:
        return RouteDecision('skip', 'doc', kind, 'ATTACHMENT_SKIP_UNSUPPORTED_OWNER')

    if kind == 'zip_office' and extension == '.xlsx':
        # Atomic-table chunking would embed a whole sheet as one over-cap
        # chunk; row-windowed table chunking is the R5 item.
        return RouteDecision('skip', 'doc', 'xlsx', 'ATTACHMENT_SKIP_XLSX')
    if kind == 'image':
        # Only parts reach here (media owners took the R3 arm above):
        # part imagery stays excluded (decision #10).
        return RouteDecision('skip', 'image', kind, 'ATTACHMENT_SKIP_PART_IMAGE')
    if kind == 'video':
        # Part videos: no pipeline (current or planned) ingests them.
        return RouteDecision('skip', 'video', kind, 'ATTACHMENT_SKIP_UNSUPPORTED_TYPE')
    if kind == 'audio':
        return RouteDecision('skip', 'doc', kind, 'ATTACHMENT_SKIP_UNSUPPORTED_TYPE')
    if kind == 'empty':
        return RouteDecision('skip', 'doc', kind, 'ATTACHMENT_SKIP_EMPTY_CONTENT')

    if kind == 'pdf':
        doc_kind = 'pdf'
    elif kind == 'zip_office' and extension == '.docx':
        doc_kind = 'docx'
    elif kind == 'text' and extension in ('.md', '.markdown', '.txt'):
        doc_kind = 'text'
    elif kind == 'zip_office':
        return RouteDecision('skip', 'doc', kind, 'ATTACHMENT_SKIP_UNSUPPORTED_TYPE')
    elif extension in _DOC_EXTENSIONS:
        # Extension claims a supported doc but the bytes disagree.
        return RouteDecision('skip', 'doc', kind, 'ATTACHMENT_SKIP_SNIFF_MISMATCH')
    else:
        return RouteDecision('skip', 'doc', kind, 'ATTACHMENT_SKIP_UNSUPPORTED_TYPE')

    settings = get_settings()
    if not settings.feature_attachment_rag_ingest:
        return RouteDecision(
            'skip', 'doc', doc_kind, 'ATTACHMENT_SKIP_PIPELINE_DISABLED'
        )
    return RouteDecision('ingest', 'doc', doc_kind)


def derive_client_codes(model_type: str, model_id: int) -> list[str]:
    """Authorization coordinate for one owning entity (§5.1, decision #5).

    Machine docs carry the machine's client code — a clientless machine is
    deliberately unreachable, so its docs stamp an empty set (fail-closed).
    Part docs carry the distinct client codes of machines the part is
    installed on, falling back to ``internal`` when unlinked.
    """
    if model_type == 'assetmachine':
        from assets.models import AssetMachine

        machine = (
            AssetMachine.objects.select_related('client').filter(pk=model_id).first()
        )
        if machine is None or machine.client is None:
            return []
        return [machine.client.code]
    if model_type == 'part':
        from assets.models import MachinePart

        installs = MachinePart.objects.filter(part_id=model_id)
        if not installs.exists():
            return [INTERNAL_CLIENT_CODE]
        # Linked parts inherit exactly their machines' client codes; a part
        # installed only on clientless machines is *linked but unreachable*
        # and fails closed like the machine arm (review finding F-11) —
        # 'internal' is reserved for genuinely unlinked parts.
        return sorted(
            set(
                installs.filter(machine__client__isnull=False).values_list(
                    'machine__client__code', flat=True
                )
            )
        )
    if model_type == 'workorder':
        from tasks.models import WorkOrder

        work_order = (
            WorkOrder.objects
            .select_related('machine__client')
            .filter(pk=model_id)
            .first()
        )
        return _work_order_client_codes(work_order)
    if model_type == 'workorderstepexecution':
        from tasks.procedure_models import WorkOrderStepExecution

        execution = (
            WorkOrderStepExecution.objects
            .select_related('application__work_order__machine__client')
            .filter(pk=model_id)
            .first()
        )
        if execution is None:
            return []
        return _work_order_client_codes(execution.application.work_order)
    return []


def _work_order_client_codes(work_order) -> list[str]:
    """Evidence scope for one work order, mirroring ``scope_for_work_order``.

    Customer-attributed WOs stamp an empty set (deliberately fail-closed):
    client-scoped actors cannot see those WOs at all and customer scopes
    carry no client codes, so stamping the machine code would
    cross-client-widen. Missing machine/client links likewise fail closed.
    """
    if work_order is None:
        return []
    if work_order.customer_id:
        return []
    machine = work_order.machine
    if machine is None or machine.client is None:
        return []
    return [machine.client.code]


_DOC_TYPE_KEYWORDS = (
    ('manual', 'manual'),
    ('catalog', 'catalogue'),
    ('datasheet', 'datasheet'),
    ('data sheet', 'datasheet'),
    ('data-sheet', 'datasheet'),
    ('drawing', 'drawing'),
    ('dwg', 'drawing'),
    ('spec', 'tech_lit'),
    ('technical', 'tech_lit'),
    ('instruction', 'manual'),
    ('guide', 'manual'),
)


def classify_doc_type(file_name: str, tag_names: list[str]) -> str:
    """Filename + tags heuristic (decision #7); LLM classification is the upgrade."""
    haystack = ' '.join([file_name.lower(), *[t.lower() for t in tag_names]])
    for needle, doc_type in _DOC_TYPE_KEYWORDS:
        if needle in haystack:
            return doc_type
    return 'other'


def _page_starts(result) -> list[int]:
    """Markdown offsets where each DI page begins (best-effort, never fatal)."""
    starts: list[int] = []
    try:
        for page in result.pages or []:
            spans = getattr(page, 'spans', None) or []
            if spans:
                starts.append(int(spans[0].offset))
    except Exception:
        return []
    return starts


def _page_for_offset(page_starts: list[int], offset: int) -> int | None:
    """1-based page containing a markdown offset, when page spans are known."""
    if not page_starts or offset < 0:
        return None
    page = 1
    for index, start in enumerate(page_starts, start=1):
        if offset >= start:
            page = index
        else:
            break
    return page


def _section_page_map(
    markdown: str, sections, page_starts: list[int]
) -> dict[str, int]:
    """Map section_id → starting page by cursoring heading lines in order."""
    if not page_starts:
        return {}
    pages: dict[str, int] = {}
    cursor = 0
    for section in sections:
        if not section.heading_line:
            page = _page_for_offset(page_starts, 0)
            if page is not None:
                pages[section.section_id] = page
            continue
        found = markdown.find(section.heading_line, cursor)
        if found < 0:
            continue
        cursor = found + len(section.heading_line)
        page = _page_for_offset(page_starts, found)
        if page is not None:
            pages[section.section_id] = page
    return pages


def _extract_with_pypdf(data: bytes) -> tuple[str, list[int]]:
    """Explicit-override extraction (decision #12) — plain text, page map."""
    try:
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        page_texts = [(page.extract_text() or '').strip() for page in reader.pages]
    except Exception as exc:
        raise AttachmentIngestionError(
            'pypdf extraction failed', code='ATTACHMENT_EXTRACTION_FAILED'
        ) from exc
    page_starts: list[int] = []
    offset = 0
    parts: list[str] = []
    for text in page_texts:
        page_starts.append(offset)
        parts.append(text)
        offset += len(text) + 2
    return '\n\n'.join(parts), page_starts


def extract_markdown(
    data: bytes, doc_kind: str, *, allow_pypdf: bool = False
) -> tuple[str, str, list[int]]:
    """Extract Markdown, returning ``(markdown, extractor, page_start_offsets)``.

    No silent quality divergence (decision #12): a Document Intelligence
    failure fails the ingest — capped retries cover outages — and pypdf runs
    only under the explicit backfill override.
    """
    if doc_kind == 'text':
        try:
            return data.decode('utf-8-sig'), 'direct', []
        except UnicodeDecodeError as exc:
            raise AttachmentIngestionError(
                'Text source is not UTF-8', code='ATTACHMENT_EXTRACTION_FAILED'
            ) from exc

    from ai.core.integrations.doc_intelligence import get_doc_intelligence_client

    client = get_doc_intelligence_client()
    if client is not None:
        content_type = (
            'application/pdf' if doc_kind == 'pdf' else 'application/octet-stream'
        )
        try:
            result = client.analyze_layout_markdown(data, content_type=content_type)
            return result.content or '', 'di_layout', _page_starts(result)
        except Exception as exc:
            # Value-free by contract: provider errors can carry credentials,
            # so only the fault location is logged (never the message).
            from ai.core.faults import log_fault

            log_fault(
                logger,
                'Document Intelligence extraction failed',
                exc,
                stage='attachment_extract',
                level=logging.WARNING,
            )
            if not (allow_pypdf and doc_kind == 'pdf'):
                raise AttachmentIngestionError(
                    'Document extraction failed', code='ATTACHMENT_EXTRACTION_FAILED'
                ) from None
    elif not (allow_pypdf and doc_kind == 'pdf'):
        raise AttachmentIngestionError(
            'Document Intelligence is not configured',
            code='ATTACHMENT_EXTRACTION_UNAVAILABLE',
        )

    markdown, page_starts = _extract_with_pypdf(data)
    return markdown, 'pypdf_override', page_starts


def _owner_coordinates(model_type: str, model_id: int) -> dict[str, object]:
    """Resolve the part/machine coordinates stamped onto every chunk."""
    coordinates: dict[str, object] = {
        'part_id': None,
        'part_name': '',
        'asset_id': '',
        'machine_name': '',
    }
    if model_type == 'part':
        from part.models import Part

        part = Part.objects.filter(pk=model_id).first()
        coordinates['part_id'] = model_id
        coordinates['part_name'] = part.name if part else ''
    elif model_type == 'assetmachine':
        from assets.models import AssetMachine

        machine = AssetMachine.objects.filter(pk=model_id).first()
        if machine is not None:
            coordinates['asset_id'] = machine.serial or ''
            coordinates['machine_name'] = machine.name
    return coordinates


def search_document_id(attachment_id: int, source_sha256: str, position: int) -> str:
    """Deterministic Search key: ``att-{id}-{sha12}-c{n}`` (idempotent upsert)."""
    return f'att-{attachment_id}-{source_sha256[:12]}-c{position}'


def media_search_document_id(
    attachment_id: int, source_sha256: str, *, segment_index: int | None = None
) -> str:
    """Deterministic media Search key: ``-img`` (image) or ``-s{n}`` (video)."""
    if segment_index is None:
        return f'att-{attachment_id}-{source_sha256[:12]}-img'
    return f'att-{attachment_id}-{source_sha256[:12]}-s{segment_index}'


def _media_owner_coordinates(model_type: str, model_id: int) -> dict[str, object]:
    """Resolve the WO/step/machine coordinates stamped onto media documents."""
    coordinates: dict[str, object] = {
        'work_order_id': None,
        'step_execution_id': None,
        'asset_id': '',
        'machine_name': '',
    }
    work_order = None
    if model_type == 'workorder':
        from tasks.models import WorkOrder

        work_order = (
            WorkOrder.objects.select_related('machine').filter(pk=model_id).first()
        )
        coordinates['work_order_id'] = model_id
    elif model_type == 'workorderstepexecution':
        from tasks.procedure_models import WorkOrderStepExecution

        execution = (
            WorkOrderStepExecution.objects
            .select_related('application__work_order__machine')
            .filter(pk=model_id)
            .first()
        )
        coordinates['step_execution_id'] = model_id
        if execution is not None:
            work_order = execution.application.work_order
            coordinates['work_order_id'] = work_order.pk
    elif model_type == 'assetmachine':
        from assets.models import AssetMachine

        machine = AssetMachine.objects.filter(pk=model_id).first()
        if machine is not None:
            coordinates['asset_id'] = machine.serial or ''
            coordinates['machine_name'] = machine.name
        return coordinates
    machine = work_order.machine if work_order is not None else None
    if machine is not None:
        coordinates['asset_id'] = machine.serial or ''
        coordinates['machine_name'] = machine.name
    return coordinates


def build_search_documents(
    *,
    ingest,
    attachment,
    chunks,
    vectors,
    client_codes: list[str],
    scope_key: str,
    doc_type: str,
    section_pages: dict[str, int],
    embedding_model: str,
    embedding_dimensions: int,
    indexed_at: datetime,
) -> list[dict[str, object]]:
    """Assemble §5.1 documents; the vector is attached per prepared chunk."""
    file_name = PurePosixPath(attachment.attachment.name or '').name
    uploaded_at = None
    if attachment.upload_date:
        uploaded_at = datetime.combine(
            attachment.upload_date, dt_time.min, tzinfo=UTC
        ).isoformat()
    coordinates = _owner_coordinates(ingest.model_type, ingest.model_id)
    indexed_at_text = indexed_at.isoformat()
    documents: list[dict[str, object]] = []
    for position, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
        documents.append({
            'id': search_document_id(
                ingest.attachment_id, ingest.source_sha256, position
            ),
            'attachment_id': ingest.attachment_id,
            'source_sha256': ingest.source_sha256,
            'model_type': ingest.model_type,
            'model_id': ingest.model_id,
            'part_id': coordinates['part_id'],
            'part_name': coordinates['part_name'],
            'asset_id': coordinates['asset_id'],
            'machine_name': coordinates['machine_name'],
            'client_codes': list(client_codes),
            'scope_key': scope_key,
            'access_class': ACCESS_CLASS,
            'is_current': True,
            'doc_type': doc_type,
            'source_file_name': file_name,
            'section_path': chunk.section_path,
            'heading_1': chunk.heading_1,
            'heading_2': chunk.heading_2,
            'heading_3': chunk.heading_3,
            'page_number': section_pages.get(chunk.section_id),
            'chunk_index': position,
            'token_count': chunk.token_count,
            'content': chunk.text,
            'text_vector': vector,
            'language': '',
            'uploaded_at': uploaded_at,
            'indexed_at': indexed_at_text,
            'as_of': indexed_at_text,
            'embedding_model': embedding_model,
            'embedding_dimensions': embedding_dimensions,
        })
    return documents


def extract_image_text(data: bytes, *, mime_type: str) -> str:
    """OCR one evidence photo with DI ``prebuilt-read`` (fail-closed, R3).

    An image with no legible text returns an empty string — a legitimate
    outcome. Provider failure fails the ingest (decision #12 parity: silent
    quality divergence is worse than latency); capped retries cover outages.
    """
    from ai.core.integrations.doc_intelligence import get_doc_intelligence_client

    client = get_doc_intelligence_client()
    if client is None:
        raise AttachmentIngestionError(
            'Document Intelligence is not configured',
            code='ATTACHMENT_EXTRACTION_UNAVAILABLE',
        )
    try:
        result = client.analyze_read_text(data, content_type=mime_type)
    except Exception as exc:
        from ai.core.faults import log_fault

        log_fault(
            logger,
            'Document Intelligence OCR failed',
            exc,
            stage='attachment_extract',
            level=logging.WARNING,
        )
        raise AttachmentIngestionError(
            'Image OCR failed', code='ATTACHMENT_EXTRACTION_FAILED'
        ) from None
    return (result.content or '').strip()


def _exif_recorded_at(data: bytes) -> datetime | None:
    """Best-effort EXIF capture timestamp (DateTimeOriginal, then DateTime).

    Computed at projection time, never stored in the registry — a rebuild can
    re-derive it from source bytes. Never fatal; EXIF times are tz-naive by
    spec and are stamped as UTC for lack of better information.
    """
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            exif = image.getexif()
            raw = None
            try:
                raw = exif.get_ifd(0x8769).get(36867)  # Exif IFD: DateTimeOriginal
            except Exception:
                raw = None
            raw = raw or exif.get(306)  # IFD0: DateTime
        if not raw:
            return None
        return datetime.strptime(str(raw).strip(), '%Y:%m:%d %H:%M:%S').replace(
            tzinfo=UTC
        )
    except Exception:
        return None


@dataclass(frozen=True)
class MediaDocSegment:
    """One projected media document's per-segment values (image or video)."""

    media_type: str
    segment_index: int | None  # None -> the single-image ``-img`` key
    timecode_start_s: float | None
    timecode_end_s: float | None
    caption: str
    ocr_text: str
    vector: object
    thumbnail_path: str


def build_media_documents(
    *,
    ingest,
    attachment,
    segments: Sequence[MediaDocSegment],
    client_codes: list[str],
    scope_key: str,
    recorded_at: datetime | None,
    embedding_model: str,
    embedding_dimensions: int,
    indexed_at: datetime,
) -> list[dict[str, object]]:
    """Assemble the §5.2 media documents (one per image / video segment).

    ``duration_s`` is per-DOCUMENT segment duration, computed at projection
    time from the nominal timecodes (no model field, no migration); images
    carry None throughout the timecode trio.
    """
    file_name = PurePosixPath(attachment.attachment.name or '').name
    uploaded_at = None
    if attachment.upload_date:
        uploaded_at = datetime.combine(
            attachment.upload_date, dt_time.min, tzinfo=UTC
        ).isoformat()
    coordinates = _media_owner_coordinates(ingest.model_type, ingest.model_id)
    documents: list[dict[str, object]] = []
    for segment in segments:
        duration_s = None
        if segment.timecode_start_s is not None and segment.timecode_end_s is not None:
            duration_s = round(segment.timecode_end_s - segment.timecode_start_s, 3)
        documents.append({
            'id': media_search_document_id(
                ingest.attachment_id,
                ingest.source_sha256,
                segment_index=segment.segment_index,
            ),
            'attachment_id': ingest.attachment_id,
            'source_sha256': ingest.source_sha256,
            'media_type': segment.media_type,
            'model_type': ingest.model_type,
            'model_id': ingest.model_id,
            'work_order_id': coordinates['work_order_id'],
            'step_execution_id': coordinates['step_execution_id'],
            'asset_id': coordinates['asset_id'],
            'machine_name': coordinates['machine_name'],
            'client_codes': list(client_codes),
            'scope_key': scope_key,
            'access_class': EVIDENCE_ACCESS_CLASS,
            'is_current': True,
            'timecode_start_s': segment.timecode_start_s,
            'timecode_end_s': segment.timecode_end_s,
            'duration_s': duration_s,
            'segment_index': segment.segment_index or 0,
            'segment_count': len(segments),
            'caption': segment.caption,
            'ocr_text': segment.ocr_text,
            'transcript': '',
            'thumbnail_path': segment.thumbnail_path,
            'source_file_name': file_name,
            'recorded_at': recorded_at.isoformat() if recorded_at else None,
            'uploaded_at': uploaded_at,
            'indexed_at': indexed_at.isoformat(),
            'media_vector': segment.vector,
            'embedding_model': embedding_model,
            'embedding_dimensions': embedding_dimensions,
        })
    return documents


def compute_sha256(data: bytes) -> str:
    """Content identity for the (attachment, sha) registry key."""
    return hashlib.sha256(data).hexdigest()


def _read_attachment_bytes(attachment) -> bytes:
    """Read source bytes via ``default_storage`` — never a hardcoded path."""
    from django.core.files.storage import default_storage

    try:
        with default_storage.open(attachment.attachment.name) as handle:
            return handle.read()
    except Exception as exc:
        raise AttachmentIngestionError(
            'Attachment source cannot be read', code='ATTACHMENT_SOURCE_UNAVAILABLE'
        ) from exc


@contextlib.contextmanager
def _video_source(attachment):
    """Yield a real filesystem path for ffmpeg without buffering the file.

    Stock ``FileSystemStorage`` exposes ``.path()`` (the Azure Files mount);
    a storage backend without local paths spills to a temp file via 1 MB
    blocks (the ``_stream_sha256`` idiom) and cleans up afterwards.
    """
    from django.core.files.storage import default_storage

    name = attachment.attachment.name
    try:
        source_path = default_storage.path(name)
    except NotImplementedError:
        source_path = None
    if source_path is not None:
        yield source_path
        return
    import tempfile

    suffix = PurePosixPath(name or '').suffix or '.bin'
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)  # noqa: SIM115
    try:
        with default_storage.open(name) as source:
            while True:
                block = source.read(_MB)
                if not block:
                    break
                handle.write(block)
        handle.close()
        yield handle.name
    finally:
        import contextlib as _contextlib
        import os

        with _contextlib.suppress(Exception):
            handle.close()
        with _contextlib.suppress(Exception):
            os.unlink(handle.name)


def _read_attachment_head(attachment, length: int = HEAD_BYTES) -> bytes:
    """Read only the sniff window — routing must not need the whole file."""
    from django.core.files.storage import default_storage

    try:
        with default_storage.open(attachment.attachment.name) as handle:
            return handle.read(length) or b''
    except Exception as exc:
        raise AttachmentIngestionError(
            'Attachment source cannot be read', code='ATTACHMENT_SOURCE_UNAVAILABLE'
        ) from exc


def _stream_sha256(attachment) -> str:
    """Content identity without buffering the whole file.

    F-17: skip rows for oversize/media files must not cost 500 MB of RAM.
    """
    from django.core.files.storage import default_storage

    digest = hashlib.sha256()
    try:
        with default_storage.open(attachment.attachment.name) as handle:
            while True:
                block = handle.read(_MB)
                if not block:
                    break
                digest.update(block)
    except Exception as exc:
        raise AttachmentIngestionError(
            'Attachment source cannot be read', code='ATTACHMENT_SOURCE_UNAVAILABLE'
        ) from exc
    return digest.hexdigest()


def _read_video_segment_bytes(
    path: str, *, max_bytes: int = VIDEO_SEGMENT_MAX_BYTES
) -> bytes:
    """Read one private scratch clip with a hard inline-request ceiling."""
    try:
        with open(path, 'rb') as handle:
            data = handle.read(max_bytes + 1)
    except OSError:
        raise AttachmentIngestionError(
            'Video segment cannot be read', code='ATTACHMENT_VIDEO_SEGMENT_FAILED'
        ) from None
    if len(data) > max_bytes:
        raise AttachmentIngestionError(
            'Video segment exceeds inline limit', code='ATTACHMENT_SKIP_VIDEO_OVERSIZE'
        )
    return data


def storage_mtime(attachment) -> str | None:
    """Best-effort storage mtime, captured ONCE at read time.

    Stamped and later compared as an exact isoformat string (both sides come
    from this same code path — never parse-and-compare). Statting at read time
    rather than stamp-write time matters: a mid-task content replacement must
    guarantee a mismatch on the next save (review finding F-02).
    """
    from django.core.files.storage import default_storage

    try:
        return default_storage.get_modified_time(attachment.attachment.name).isoformat()
    except Exception:
        return None


def _stamp_metadata(attachment_id: int, stamp: dict[str, object]) -> None:
    """Write the signal-side dedupe stamp without re-firing post_save.

    Locked read-modify-write (F-22): ``rebuild_attachment`` and user metadata
    edits write the same JSON column concurrently.
    """
    from django.db import transaction

    from common.models import Attachment

    try:
        with transaction.atomic():
            row = (
                Attachment.objects.select_for_update().filter(pk=attachment_id).first()
            )
            if row is None:
                return
            metadata = dict(row.metadata or {})
            metadata['ai_ingest'] = stamp
            Attachment.objects.filter(pk=attachment_id).update(metadata=metadata)
    except Exception:
        # The stamp is an optimization; losing it costs one redundant offload.
        logger.warning('Attachment ingest stamp write failed (ignored)')


def _build_stamp(
    attachment,
    source_sha256: str,
    state: str,
    *,
    reason: str = '',
    mtime: str | None = None,
) -> dict[str, object]:
    """Cheap receiver-side comparison key defeating the documented double-save.

    v2 (review findings F-02/F-03/F-08/F-10): versioned, skip-reason-aware,
    and mtime-anchored so replaced content re-offloads.
    """
    stamp: dict[str, object] = {
        'v': STAMP_VERSION,
        'sha': source_sha256,
        'name': attachment.attachment.name or '',
        'size': attachment.file_size or 0,
        'state': state,
        'reason': reason,
        'at': datetime.now(UTC).isoformat(),
    }
    if mtime is not None:
        stamp['mtime'] = mtime
    return stamp


def _record_skip(
    attachment, decision: RouteDecision, source_sha256: str, *, mtime: str | None = None
):
    """Persist one explicit router skip (decision #10) idempotently."""
    from aichat.models import AttachmentIngest, AttachmentIngestState

    existing = AttachmentIngest.objects.filter(
        attachment_id=attachment.pk, source_sha256=source_sha256
    ).first()
    if existing is not None and existing.state in (
        AttachmentIngestState.INDEXED,
        AttachmentIngestState.FAILED,
        AttachmentIngestState.EXTRACTING,
        AttachmentIngestState.EMBEDDING,
    ):
        # Never demote an indexed revision (its serving documents would be
        # orphaned), erase failure history (F-10/B7), or clobber a live run.
        # No stamp either: FAILED revisions must stay retryable.
        return existing
    row, _created = AttachmentIngest.objects.update_or_create(
        attachment_id=attachment.pk,
        source_sha256=source_sha256,
        defaults={
            'model_type': attachment.model_type,
            'model_id': attachment.model_id,
            'pipeline': decision.pipeline,
            'state': AttachmentIngestState.SKIPPED,
            'error_code': decision.reason,
        },
    )
    _stamp_metadata(
        attachment.pk,
        _build_stamp(
            attachment, source_sha256, 'skipped', reason=decision.reason, mtime=mtime
        ),
    )
    return row


def _fenced_update(row_pk: int, fence_attempts: int, **fields) -> bool:
    """Owner-fenced registry write; False means a stale takeover owns the row.

    The fence token is the ``attempts`` value observed right after the claim:
    any takeover re-claims with ``attempts + 1``, so a late original worker's
    writes match zero rows and it must walk away (never demote the twin's
    fresh state, never delete the twin's documents).
    """
    from django.utils import timezone

    from aichat.models import AttachmentIngest, AttachmentIngestState

    fields.setdefault('updated_at', timezone.now())
    return bool(
        AttachmentIngest.objects.filter(
            pk=row_pk,
            attempts=fence_attempts,
            state__in=[
                AttachmentIngestState.EXTRACTING,
                AttachmentIngestState.EMBEDDING,
            ],
        ).update(**fields)
    )


def _claim_order(row) -> tuple:
    """Winner ordering: newest claim wins; unclaimed (legacy) rows sort oldest."""
    return (row.claimed_at is not None, row.claimed_at or row.created_at, row.pk)


def run_ingest(
    attachment_id: int,
    *,
    allow_pypdf: bool = False,
    embedding_client=None,
    projection=None,
    media_embedding_client=None,
    media_projection=None,
    force: bool = False,
):
    """Ingest one attachment revision end-to-end; returns the registry row.

    Idempotent per (attachment, sha): a re-fired signal or duplicate offload
    short-circuits on the indexed row or fails the atomic claim; a new sha
    supersedes the old revision with the zero-gap upsert-then-prune ordering
    (decision #15), and cross-sha races resolve by claim time with each loser
    removing exactly its own documents.
    """
    from django.conf import settings as django_settings
    from django.db.models import F, Q
    from django.utils import timezone

    from ai.core.config import get_settings
    from aichat.models import (
        AttachmentChunk,
        AttachmentExtractor,
        AttachmentIngest,
        AttachmentIngestPipeline,
        AttachmentIngestState,
        MediaSegment,
    )
    from common.models import Attachment

    if not getattr(django_settings, 'AIMMS_ATTACHMENT_RAG_ENABLED', False):
        return None
    attachment = Attachment.objects.filter(pk=attachment_id).first()
    if attachment is None:
        return None
    if structural_skip_reason(attachment) is not None:
        return None

    # Capture the storage mtime ONCE, at read time (F-02): a mid-task content
    # replacement must guarantee a stamp mismatch on the next save.
    mtime = storage_mtime(attachment)
    head = _read_attachment_head(attachment)
    decision = route_attachment(attachment, head)
    if decision.action == 'skip':
        # Skip rows never buffer the file (F-17): identity is streamed.
        return _record_skip(
            attachment, decision, _stream_sha256(attachment), mtime=mtime
        )

    settings = get_settings()
    is_image = decision.pipeline == AttachmentIngestPipeline.IMAGE
    is_video = decision.pipeline == AttachmentIngestPipeline.VIDEO
    if is_image:
        content_cap = settings.rag_max_image_mb * _MB
        oversize_code = 'ATTACHMENT_SKIP_IMAGE_OVERSIZE'
    elif is_video:
        content_cap = settings.rag_max_video_mb * _MB
        oversize_code = 'ATTACHMENT_SKIP_VIDEO_OVERSIZE'
    else:
        content_cap = settings.rag_max_doc_mb * _MB
        oversize_code = 'ATTACHMENT_SKIP_DOC_OVERSIZE'
    size_hint = attachment.file_size or 0
    if not size_hint:
        from django.core.files.storage import default_storage

        try:
            size_hint = default_storage.size(attachment.attachment.name)
        except Exception:
            size_hint = 0
    if size_hint > content_cap:
        return _record_skip(
            attachment,
            RouteDecision('skip', decision.pipeline, decision.kind, oversize_code),
            _stream_sha256(attachment),
            mtime=mtime,
        )
    scope_key = settings.single_site_policy_key
    if not scope_key:
        raise AttachmentIngestionError(
            'Site scope is unresolved', code='ATTACHMENT_INGEST_SCOPE_UNRESOLVED'
        )

    if is_video:
        # A video is never buffered into RAM (500 MB cap): identity streams
        # and the post-read cap binds on the storage-reported size; ffmpeg
        # reads the storage path directly inside the video branch.
        data = b''
        source_sha256 = _stream_sha256(attachment)
        from django.core.files.storage import default_storage

        try:
            actual_size = default_storage.size(attachment.attachment.name)
        except Exception:
            actual_size = size_hint
        if actual_size > content_cap:
            return _record_skip(
                attachment,
                RouteDecision('skip', decision.pipeline, decision.kind, oversize_code),
                source_sha256,
                mtime=mtime,
            )
    else:
        data = _read_attachment_bytes(attachment)
        source_sha256 = compute_sha256(data)
        if len(data) > content_cap:
            # The pre-read hint was stale or absent; the cap binds on bytes.
            return _record_skip(
                attachment,
                RouteDecision('skip', decision.pipeline, decision.kind, oversize_code),
                source_sha256,
                mtime=mtime,
            )

    indexed_row = AttachmentIngest.objects.filter(
        attachment_id=attachment.pk,
        source_sha256=source_sha256,
        state=AttachmentIngestState.INDEXED,
    ).first()
    if indexed_row is not None and not force:
        # Renew the claim clock: on a content revert this row must outrank any
        # still-in-flight ingest of the replaced revision (critic break #3).
        now = timezone.now()
        AttachmentIngest.objects.filter(
            pk=indexed_row.pk, state=AttachmentIngestState.INDEXED
        ).update(claimed_at=now, updated_at=now)
        _stamp_metadata(
            attachment.pk,
            _build_stamp(attachment, source_sha256, 'indexed', mtime=mtime),
        )
        return indexed_row

    row, _created = AttachmentIngest.objects.get_or_create(
        attachment_id=attachment.pk,
        source_sha256=source_sha256,
        defaults={
            'model_type': attachment.model_type,
            'model_id': attachment.model_id,
            'pipeline': decision.pipeline,
        },
    )
    row_was_indexed = row.state == AttachmentIngestState.INDEXED

    # Atomic claim (F-04/F-06): exactly one worker may own a row at a time;
    # the attempts cap binds in every non-indexed state; SKIPPED is claimable
    # because routing already said 'ingest' for the *current* bytes (revival).
    now = timezone.now()
    stale_before = now - timedelta(seconds=settings.rag_stale_claim_s)
    if force:
        claimable_states = [
            AttachmentIngestState.PENDING,
            AttachmentIngestState.INDEXED,
            AttachmentIngestState.FAILED,
            AttachmentIngestState.SUPERSEDED,
            AttachmentIngestState.SKIPPED,
        ]
    else:
        claimable_states = [
            AttachmentIngestState.PENDING,
            AttachmentIngestState.FAILED,
            AttachmentIngestState.SUPERSEDED,
            AttachmentIngestState.SKIPPED,
        ]
    claim_qs = AttachmentIngest.objects.filter(pk=row.pk).filter(
        Q(state__in=claimable_states)
        | Q(
            state__in=[
                AttachmentIngestState.EXTRACTING,
                AttachmentIngestState.EMBEDDING,
            ],
            updated_at__lt=stale_before,
        )
    )
    if not force:
        claim_qs = claim_qs.filter(attempts__lt=MAX_INGEST_ATTEMPTS)
    claimed = claim_qs.update(
        state=AttachmentIngestState.EXTRACTING,
        attempts=F('attempts') + 1,
        error_code='',
        # The row follows THIS run's route decision: a router-semantics
        # change across deploys may re-route identical bytes, and purge
        # paths pick the serving index by row.pipeline — a stale value
        # would purge the wrong index (review finding, R3).
        pipeline=decision.pipeline,
        updated_at=now,
        claimed_at=now,
    )
    if not claimed:
        # A fresh twin is in flight, the cap is hit, or a twin just indexed.
        return row
    row.refresh_from_db()
    fence = row.attempts

    def _projection_for(pipeline_value):
        """Lazy per-pipeline projection cache (F-19: built only when used)."""
        nonlocal projection, media_projection
        if str(pipeline_value) in (
            AttachmentIngestPipeline.IMAGE,
            AttachmentIngestPipeline.VIDEO,
        ):
            if media_projection is None:
                from ai.core.integrations.attachment_search import MediaSearchProjection

                media_projection = MediaSearchProjection.from_settings()
            return media_projection
        if projection is None:
            from ai.core.integrations.attachment_search import (
                AttachmentSearchProjection,
            )

            projection = AttachmentSearchProjection.from_settings()
        return projection

    try:
        if is_image:
            mime_type = _image_mime(head) or 'application/octet-stream'
            ocr_text = extract_image_text(data, mime_type=mime_type)
            if not _fenced_update(row.pk, fence, extractor=AttachmentExtractor.DI_READ):
                return row  # stale takeover owns the row; walk away

            from ai.core.integrations.image_caption import (
                ImageCaptionError,
                caption_image,
            )

            try:
                caption = caption_image(data, mime_type=mime_type)
            except ImageCaptionError as caption_exc:
                raise AttachmentIngestionError(
                    'Image captioning failed', code=caption_exc.code
                ) from caption_exc

            if not _fenced_update(row.pk, fence, state=AttachmentIngestState.EMBEDDING):
                return row
            if media_embedding_client is None:
                from ai.core.integrations.embeddings_gemini import GeminiEmbeddingClient

                media_embedding_client = GeminiEmbeddingClient.from_settings()
            vector = media_embedding_client.embed_image(data, mime_type=mime_type)

            client_codes = derive_client_codes(
                attachment.model_type, attachment.model_id
            )
            indexed_at = datetime.now(UTC)
            recorded_at = _exif_recorded_at(data)
            # Thumbnail race, layer 1: rebuild_attachment (group 'attachments')
            # usually lands during the slow provider calls above — re-read.
            # Layer 2 is the empty-tolerant contract ('' means no thumbnail);
            # layer 3 is the sweep's heal_media_thumbnails.
            try:
                attachment.refresh_from_db(fields=['thumbnail'])
            except Exception:
                pass
            thumbnail_path = (getattr(attachment.thumbnail, 'name', '') or '')[:512]

            # Atomic rewrite: a stale twin still in flight can interleave
            # here (its fence check passed BEFORE the takeover). The unique
            # (ingest, segment_index) constraint makes the loser's create
            # fail — treat that as losing the race and walk away, leaving
            # the fresh owner's segment intact.
            from django.db import IntegrityError, transaction

            try:
                with transaction.atomic():
                    row.segments.all().delete()
                    MediaSegment.objects.create(
                        ingest=row,
                        media_type='image',
                        segment_index=0,
                        caption=caption,
                        ocr_text=ocr_text,
                        thumbnail_path=thumbnail_path,
                        embedding=vector,
                        search_doc_id=media_search_document_id(
                            row.attachment_id, source_sha256
                        ),
                    )
            except IntegrityError:
                return row
            documents = build_media_documents(
                ingest=row,
                attachment=attachment,
                segments=[
                    MediaDocSegment(
                        media_type='image',
                        segment_index=None,
                        timecode_start_s=None,
                        timecode_end_s=None,
                        caption=caption,
                        ocr_text=ocr_text,
                        vector=vector,
                        thumbnail_path=thumbnail_path,
                    )
                ],
                client_codes=client_codes,
                scope_key=scope_key,
                recorded_at=recorded_at,
                embedding_model=media_embedding_client.model,
                embedding_dimensions=media_embedding_client.dimensions,
                indexed_at=indexed_at,
            )
            own_projection = _projection_for(AttachmentIngestPipeline.IMAGE)
            terminal_fields = {
                'client_codes': client_codes,
                'chunk_count': 0,
                'segment_count': 1,
                'embedding_model': media_embedding_client.model,
                'embedding_dimensions': media_embedding_client.dimensions,
                'search_index_name': own_projection.index_name,
            }
        elif is_video:
            mime_type = _video_mime(head) or 'application/octet-stream'
            import os
            import tempfile

            from django.core.files.base import ContentFile
            from django.core.files.storage import default_storage

            from ai.core.integrations.image_caption import (
                ImageCaptionError,
                caption_image,
            )
            from aichat.services import video_tools

            with _video_source(attachment) as source_path:
                try:
                    probe = video_tools.probe_video(source_path)
                except video_tools.VideoToolError as video_exc:
                    raise AttachmentIngestionError(
                        'Video probe failed', code=video_exc.code
                    ) from video_exc
                skip_reason = ''
                if not probe.has_video_stream:
                    # Audio-only mp4 that survived the brand allowlist.
                    skip_reason = 'ATTACHMENT_SKIP_UNSUPPORTED_TYPE'
                elif probe.duration_s > settings.rag_video_max_duration_s:
                    # Oversize in the time dimension (bounds provider calls).
                    skip_reason = 'ATTACHMENT_SKIP_VIDEO_OVERSIZE'
                if skip_reason:
                    # Owner-authorized skip: this run owns the claim.
                    if _fenced_update(
                        row.pk,
                        fence,
                        state=AttachmentIngestState.SKIPPED,
                        error_code=skip_reason,
                    ):
                        _stamp_metadata(
                            attachment.pk,
                            _build_stamp(
                                attachment,
                                source_sha256,
                                'skipped',
                                reason=skip_reason,
                                mtime=mtime,
                            ),
                        )
                    row.refresh_from_db()
                    return row

                windows = video_tools.plan_segments(
                    probe.duration_s,
                    window_s=settings.rag_video_segment_s,
                    overlap_s=settings.rag_video_overlap_s,
                )
                if not _fenced_update(
                    row.pk, fence, extractor=AttachmentExtractor.FFMPEG
                ):
                    return row  # stale takeover owns the row; walk away
                if media_embedding_client is None:
                    from ai.core.integrations.embeddings_gemini import (
                        GeminiEmbeddingClient,
                    )

                    media_embedding_client = GeminiEmbeddingClient.from_settings()
                client_codes = derive_client_codes(
                    attachment.model_type, attachment.model_id
                )
                indexed_at = datetime.now(UTC)
                recorded_at = probe.recorded_at
                sha12 = source_sha256[:12]
                # The router trusts the ftyp brand, never the uploader's
                # extension. Keep ffmpeg's output container and Gemini's
                # declared MIME tied to that same verified source of truth.
                clip_suffix = '.mov' if mime_type == 'video/quicktime' else '.mp4'
                records: list[MediaDocSegment] = []
                with tempfile.TemporaryDirectory() as workdir:
                    for index, (start, end) in enumerate(windows):
                        # Per-iteration heartbeat: doubles as the stale-
                        # takeover walk-away AND keeps the rag_stale_claim_s
                        # clock fresh across a long loop.
                        if not _fenced_update(
                            row.pk, fence, state=AttachmentIngestState.EMBEDDING
                        ):
                            return row
                        clip_path = os.path.join(workdir, f'clip-{index}{clip_suffix}')
                        frame_path = os.path.join(workdir, f'frame-{index}.jpg')
                        try:
                            video_tools.cut_segment(
                                source_path, start, end - start, clip_path
                            )
                            clip_bytes = _read_video_segment_bytes(clip_path)
                            video_tools.extract_keyframe(
                                clip_path, (end - start) / 2, frame_path
                            )
                        except AttachmentIngestionError as segment_exc:
                            if segment_exc.code != 'ATTACHMENT_SKIP_VIDEO_OVERSIZE':
                                raise
                            if row_was_indexed:
                                # A forced refresh must not demote the
                                # currently serving revision to SKIPPED.
                                raise
                            # This source/window will exceed the inline limit
                            # on every retry, so terminate as an oversize skip.
                            if _fenced_update(
                                row.pk,
                                fence,
                                state=AttachmentIngestState.SKIPPED,
                                error_code=segment_exc.code,
                            ):
                                _purge_keyframe_files(
                                    attachment.pk, sha12=source_sha256[:12]
                                )
                                _stamp_metadata(
                                    attachment.pk,
                                    _build_stamp(
                                        attachment,
                                        source_sha256,
                                        'skipped',
                                        reason=segment_exc.code,
                                        mtime=mtime,
                                    ),
                                )
                            row.refresh_from_db()
                            return row
                        except video_tools.VideoToolError as video_exc:
                            raise AttachmentIngestionError(
                                'Video segmentation failed', code=video_exc.code
                            ) from video_exc
                        with open(frame_path, 'rb') as frame_handle:
                            frame_bytes = frame_handle.read()
                        ocr_text = extract_image_text(
                            frame_bytes, mime_type='image/jpeg'
                        )
                        try:
                            caption = caption_image(frame_bytes, mime_type='image/jpeg')
                        except ImageCaptionError as caption_exc:
                            raise AttachmentIngestionError(
                                'Image captioning failed', code=caption_exc.code
                            ) from caption_exc
                        vector = media_embedding_client.embed_video_segment(
                            clip_bytes, mime_type=mime_type
                        )
                        # Server-derived keyframe path (never uploader text);
                        # delete-then-save defeats storage dedupe-suffixing so
                        # the name stays deterministic across retries. A write
                        # failure is FATAL: an INDEXED segment always has a
                        # real thumbnail_path.
                        rel = f'ai/keyframes/{attachment.pk}/{sha12}-s{index}.jpg'
                        if default_storage.exists(rel):
                            default_storage.delete(rel)
                        stored_rel = default_storage.save(rel, ContentFile(frame_bytes))
                        if not stored_rel or len(stored_rel) > 512:
                            raise AttachmentIngestionError(
                                'Video keyframe storage name is invalid',
                                code='ATTACHMENT_VIDEO_KEYFRAME_FAILED',
                            )
                        records.append(
                            MediaDocSegment(
                                media_type='video_segment',
                                segment_index=index,
                                timecode_start_s=start,
                                timecode_end_s=end,
                                caption=caption,
                                ocr_text=ocr_text,
                                vector=vector,
                                thumbnail_path=stored_rel,
                            )
                        )
                        # Bound peak scratch disk to ~one clip.
                        with contextlib.suppress(OSError):
                            os.unlink(clip_path)
                        with contextlib.suppress(OSError):
                            os.unlink(frame_path)

            # Atomic N-segment rewrite; the (ingest, segment_index) unique
            # constraint turns a stale-twin interleave into an IntegrityError
            # walk-away, leaving the fresh owner's rows intact.
            from django.db import IntegrityError, transaction

            try:
                with transaction.atomic():
                    row.segments.all().delete()
                    MediaSegment.objects.bulk_create([
                        MediaSegment(
                            ingest=row,
                            media_type='video_segment',
                            segment_index=record.segment_index,
                            timecode_start_s=record.timecode_start_s,
                            timecode_end_s=record.timecode_end_s,
                            caption=record.caption,
                            ocr_text=record.ocr_text,
                            thumbnail_path=record.thumbnail_path,
                            embedding=record.vector,
                            search_doc_id=media_search_document_id(
                                row.attachment_id,
                                source_sha256,
                                segment_index=record.segment_index,
                            ),
                        )
                        for record in records
                    ])
            except IntegrityError:
                return row
            documents = build_media_documents(
                ingest=row,
                attachment=attachment,
                segments=records,
                client_codes=client_codes,
                scope_key=scope_key,
                recorded_at=recorded_at,
                embedding_model=media_embedding_client.model,
                embedding_dimensions=media_embedding_client.dimensions,
                indexed_at=indexed_at,
            )
            own_projection = _projection_for(AttachmentIngestPipeline.VIDEO)
            terminal_fields = {
                'client_codes': client_codes,
                'chunk_count': 0,
                'segment_count': len(records),
                'embedding_model': media_embedding_client.model,
                'embedding_dimensions': media_embedding_client.dimensions,
                'search_index_name': own_projection.index_name,
            }
        else:
            markdown, extractor, page_starts = extract_markdown(
                data, decision.kind, allow_pypdf=allow_pypdf
            )
            if not markdown.strip():
                # Owner-authorized skip: the generic _record_skip refuses to touch
                # in-flight rows, but this run owns the claim.
                reason = 'ATTACHMENT_SKIP_EMPTY_CONTENT'
                if _fenced_update(
                    row.pk,
                    fence,
                    state=AttachmentIngestState.SKIPPED,
                    error_code=reason,
                ):
                    _stamp_metadata(
                        attachment.pk,
                        _build_stamp(
                            attachment,
                            source_sha256,
                            'skipped',
                            reason=reason,
                            mtime=mtime,
                        ),
                    )
                row.refresh_from_db()
                return row
            if not _fenced_update(row.pk, fence, extractor=extractor):
                return row  # stale takeover owns the row; walk away

            from ai.core.integrations.controlled_document_ingestion import (
                chunk_markdown_sections,
                parse_markdown_sections,
            )

            sections = parse_markdown_sections(markdown)
            chunks = chunk_markdown_sections(sections)
            section_pages = _section_page_map(markdown, sections, page_starts)

            if not _fenced_update(row.pk, fence, state=AttachmentIngestState.EMBEDDING):
                return row
            if embedding_client is None:
                from ai.core.integrations.embeddings_cohere import CohereEmbeddingClient

                embedding_client = CohereEmbeddingClient.from_settings()
            vectors = embedding_client.embed_documents([chunk.text for chunk in chunks])

            client_codes = derive_client_codes(
                attachment.model_type, attachment.model_id
            )
            doc_type = classify_doc_type(
                PurePosixPath(attachment.attachment.name or '').name,
                _tag_names(attachment),
            )
            indexed_at = datetime.now(UTC)
            documents = build_search_documents(
                ingest=row,
                attachment=attachment,
                chunks=chunks,
                vectors=vectors,
                client_codes=client_codes,
                scope_key=scope_key,
                doc_type=doc_type,
                section_pages=section_pages,
                embedding_model=embedding_client.model,
                embedding_dimensions=embedding_client.dimensions,
                indexed_at=indexed_at,
            )

            row.chunks.all().delete()
            AttachmentChunk.objects.bulk_create([
                AttachmentChunk(
                    ingest=row,
                    chunk_index=position,
                    page_number=section_pages.get(chunk.section_id),
                    section_path=chunk.section_path[:512],
                    content=chunk.text,
                    token_count=chunk.token_count,
                    embedding=vector,
                    search_doc_id=search_document_id(
                        row.attachment_id, source_sha256, position
                    ),
                )
                for position, (chunk, vector) in enumerate(
                    zip(chunks, vectors, strict=True)
                )
            ])

            own_projection = _projection_for(AttachmentIngestPipeline.DOC)
            terminal_fields = {
                'client_codes': client_codes,
                'chunk_count': len(chunks),
                'embedding_model': embedding_client.model,
                'embedding_dimensions': embedding_client.dimensions,
                'search_index_name': own_projection.index_name,
            }

        # Zero-gap supersede (decision #15): new revision live before the old
        # one disappears.
        own_projection.upsert_documents(documents)

        # Winner/loser resolution (F-06/C2): newest CLAIM wins — registry-row
        # age inverts on content reverts. Losers remove exactly their own
        # documents; a blanket prune could hit a revision it never observed.
        # Peers purge from the index matching THEIR pipeline: an attachment
        # whose content was replaced photo↔pdf has revisions in different
        # indexes (R3).
        peers = list(
            AttachmentIngest.objects
            .filter(attachment_id=attachment.pk)
            .exclude(pk=row.pk)
            .exclude(
                state__in=[
                    AttachmentIngestState.DELETED,
                    AttachmentIngestState.SKIPPED,
                    AttachmentIngestState.SUPERSEDED,
                ]
            )
        )
        newest = max([row, *peers], key=_claim_order)
        if newest.pk == row.pk:
            for peer in sorted(
                (p for p in peers if p.source_sha256 != source_sha256),
                key=lambda p: (p.source_sha256, p.pk),
            ):
                # is_current belt-and-braces (F-09) before each purge, so the
                # R2 filter stays correct even mid-failure.
                peer_projection = _projection_for(peer.pipeline)
                peer_projection.mark_sha_stale(
                    attachment_id=attachment.pk, source_sha256=peer.source_sha256
                )
                peer_projection.purge_sha(
                    attachment_id=attachment.pk, source_sha256=peer.source_sha256
                )
                if str(peer.pipeline) == AttachmentIngestPipeline.VIDEO:
                    _purge_keyframe_files(attachment.pk, sha12=peer.source_sha256[:12])
            won = _fenced_update(
                row.pk,
                fence,
                state=AttachmentIngestState.INDEXED,
                error_code='',
                **terminal_fields,
            )
            if not won:
                # A same-sha takeover owns the row now (its run finishes the
                # bookkeeping; the documents are shared, deterministic IDs) —
                # UNLESS the attachment was purged mid-run: then nobody else
                # will remove the documents this run just resurrected, and
                # the orphan sweep skips DELETED tombstones (review finding,
                # R3 — the doc path carried the same race).
                row.refresh_from_db()
                if row.state == AttachmentIngestState.DELETED:
                    own_projection.mark_sha_stale(
                        attachment_id=attachment.pk, source_sha256=source_sha256
                    )
                    own_projection.purge_sha(
                        attachment_id=attachment.pk, source_sha256=source_sha256
                    )
                    if is_video:
                        _purge_keyframe_files(attachment.pk, sha12=source_sha256[:12])
                return row
            AttachmentIngest.objects.filter(attachment_id=attachment.pk).exclude(
                pk=row.pk
            ).exclude(
                state__in=[
                    AttachmentIngestState.DELETED,
                    AttachmentIngestState.SKIPPED,
                    AttachmentIngestState.SUPERSEDED,
                    # Live cross-sha runs classify themselves at their own
                    # winner check; sweeping them mid-run invites churn.
                    AttachmentIngestState.EXTRACTING,
                    AttachmentIngestState.EMBEDDING,
                ]
            ).update(state=AttachmentIngestState.SUPERSEDED)
            _stamp_metadata(
                attachment.pk,
                _build_stamp(attachment, source_sha256, 'indexed', mtime=mtime),
            )
            # Belt for the purge race's other interleaving: the delete
            # receiver's purge can remove the serving documents and only
            # then tombstone rows — if the attachment vanished mid-run, the
            # documents this run upserted must not outlive it (the orphan
            # sweep skips DELETED tombstones). Idempotent against a purge
            # that runs later anyway.
            if not Attachment.objects.filter(pk=attachment.pk).exists():
                own_projection.mark_sha_stale(
                    attachment_id=attachment.pk, source_sha256=source_sha256
                )
                own_projection.purge_sha(
                    attachment_id=attachment.pk, source_sha256=source_sha256
                )
                if is_video:
                    _purge_keyframe_files(attachment.pk, sha12=source_sha256[:12])
        else:
            # A newer claim exists: this revision already lost. Remove exactly
            # our own documents and step aside.
            own_projection.mark_sha_stale(
                attachment_id=attachment.pk, source_sha256=source_sha256
            )
            own_projection.purge_sha(
                attachment_id=attachment.pk, source_sha256=source_sha256
            )
            if is_video:
                _purge_keyframe_files(attachment.pk, sha12=source_sha256[:12])
            _fenced_update(row.pk, fence, state=AttachmentIngestState.SUPERSEDED)
        row.refresh_from_db()
        return row
    except Exception as exc:
        code = getattr(exc, 'code', '') or 'ATTACHMENT_INGEST_FAILED'
        # Fenced (never demote a takeover's fresh state); raise regardless so
        # the failure stays visible to django-q and callers.
        failed_while_owned = _fenced_update(
            row.pk, fence, state=AttachmentIngestState.FAILED, error_code=str(code)[:64]
        )
        if is_video and failed_while_owned and not row_was_indexed:
            # Earlier segments persist keyframes synchronously. A terminal
            # failure owns no serving rows, so remove this revision's partial
            # files; a stale worker whose fence failed must not delete paths
            # a takeover may already be using. A forced re-ingest of an
            # INDEXED revision keeps its existing serving keyframes.
            _purge_keyframe_files(attachment.pk, sha12=source_sha256[:12])
        if isinstance(exc, AttachmentIngestionError):
            raise
        raise AttachmentIngestionError(
            'Attachment ingestion failed', code=str(code)[:64]
        ) from exc


def _tag_names(attachment) -> list[str]:
    """Tag names for the doc-type heuristic; never fatal."""
    try:
        return [str(tag) for tag in attachment.tags.names()]
    except Exception:
        return []


def _purge_keyframe_files(attachment_id: int, *, sha12: str | None = None) -> None:
    """Best-effort removal of derived keyframe files (R4).

    File cleanup must NEVER wedge a purge (the R3 lesson): every failure is
    logged value-free and swallowed. ``sha12`` scopes the delete to one
    superseded revision; without it the whole attachment's keyframes go.
    Empty directories may linger (FileSystemStorage deletes files only) —
    harmless.
    """
    from django.core.files.storage import default_storage

    directory = f'ai/keyframes/{attachment_id}'
    try:
        _dirs, files = default_storage.listdir(directory)
    except Exception:
        return  # never written (or storage cannot list) — nothing to do
    for name in files:
        if sha12 is not None and not name.startswith(f'{sha12}-'):
            continue
        try:
            default_storage.delete(f'{directory}/{name}')
        except Exception as exc:
            from ai.core.faults import log_fault

            log_fault(logger, 'Keyframe cleanup failed', exc, stage='attachment_sweep')


def purge_attachment_artifacts(
    attachment_id: int, *, projection=None, media_projection=None
) -> int:
    """Delete index documents and chunk/segment copies for a removed attachment.

    Registry rows survive as ``deleted`` tombstones (audit trail); chunk and
    segment content rows are removed explicitly (tombstoned rows never
    cascade). Index deletion happens first — rows only reach the terminal
    state once the serving layer can no longer answer with them. Each serving
    index is purged only when rows of its pipeline family exist (F-19: no
    SearchClients on no-op paths).
    """
    from aichat.models import (
        AttachmentChunk,
        AttachmentIngest,
        AttachmentIngestPipeline,
        AttachmentIngestState,
        MediaSegment,
    )

    rows = AttachmentIngest.objects.filter(attachment_id=attachment_id)
    if not rows.exists():
        return 0
    deleted = 0
    if rows.filter(pipeline=AttachmentIngestPipeline.DOC).exists():
        if projection is None:
            from ai.core.integrations.attachment_search import (
                AttachmentSearchProjection,
            )

            projection = AttachmentSearchProjection.from_settings()
        deleted += projection.purge_attachment(attachment_id=attachment_id)
    if (
        rows
        .filter(
            pipeline__in=[
                AttachmentIngestPipeline.IMAGE,
                AttachmentIngestPipeline.VIDEO,
            ]
        )
        # Only rows that could have projected: recorded dark-mode skips must
        # not force a media-index client (the index may not even exist on a
        # media-dark deployment, and a raising purge would wedge the delete
        # path forever — review finding, R3).
        .exclude(
            state__in=[AttachmentIngestState.SKIPPED, AttachmentIngestState.PENDING]
        )
        .exists()
    ):
        if media_projection is None:
            from ai.core.integrations.attachment_search import MediaSearchProjection

            media_projection = MediaSearchProjection.from_settings()
        deleted += media_projection.purge_attachment(attachment_id=attachment_id)
    AttachmentChunk.objects.filter(ingest__attachment_id=attachment_id).delete()
    MediaSegment.objects.filter(ingest__attachment_id=attachment_id).delete()
    _purge_keyframe_files(attachment_id)
    rows.update(state=AttachmentIngestState.DELETED)
    return deleted


def restamp_part_client_codes(part_id: int, *, projection=None) -> int:
    """Recompute and merge ``client_codes`` for a part's indexed docs (§6.5)."""
    from aichat.models import AttachmentIngest, AttachmentIngestState

    rows = AttachmentIngest.objects.filter(
        model_type='part', model_id=part_id, state=AttachmentIngestState.INDEXED
    )
    if not rows.exists():
        return 0
    codes = derive_client_codes('part', part_id)
    touched = 0
    for row in rows:
        if list(row.client_codes or []) == codes:
            continue
        if projection is None:
            # Constructed only once a changed row actually exists (F-19):
            # no-op restamps must not build SearchClients.
            from ai.core.integrations.attachment_search import (
                AttachmentSearchProjection,
            )

            projection = AttachmentSearchProjection.from_settings()
        projection.merge_client_codes(
            attachment_id=row.attachment_id, client_codes=codes
        )
        row.client_codes = codes
        row.save(update_fields=['client_codes', 'updated_at'])
        touched += 1
    return touched


def restamp_machine_client_codes(machine_id: int, *, projection=None) -> int:
    """Re-stamp a machine's docs, media, and installed parts' docs (§6.5)."""
    from aichat.models import (
        AttachmentIngest,
        AttachmentIngestPipeline,
        AttachmentIngestState,
    )
    from assets.models import MachinePart

    touched = 0
    machine_rows = AttachmentIngest.objects.filter(
        model_type='assetmachine',
        model_id=machine_id,
        # Docs only: media rows live in the media index and are re-stamped by
        # the media loop below. Without this filter the doc-index merge
        # no-ops on media rows while still updating the registry, and the
        # media loop's change check then skips them — the media index would
        # keep the OLD tenant's codes forever (review finding, R3).
        pipeline=AttachmentIngestPipeline.DOC,
        state=AttachmentIngestState.INDEXED,
    )
    if machine_rows.exists():
        codes = derive_client_codes('assetmachine', machine_id)
        for row in machine_rows:
            if list(row.client_codes or []) == codes:
                continue
            if projection is None:
                from ai.core.integrations.attachment_search import (
                    AttachmentSearchProjection,
                )

                projection = AttachmentSearchProjection.from_settings()
            projection.merge_client_codes(
                attachment_id=row.attachment_id, client_codes=codes
            )
            row.client_codes = codes
            row.save(update_fields=['client_codes', 'updated_at'])
            touched += 1
    media_projection = None
    media_rows = AttachmentIngest.objects.filter(
        model_type='assetmachine',
        model_id=machine_id,
        pipeline__in=[AttachmentIngestPipeline.IMAGE, AttachmentIngestPipeline.VIDEO],
        state=AttachmentIngestState.INDEXED,
    )
    if media_rows.exists():
        codes = derive_client_codes('assetmachine', machine_id)
        for row in media_rows:
            if media_projection is None:
                from ai.core.integrations.attachment_search import MediaSearchProjection

                media_projection = MediaSearchProjection.from_settings()
            # Scope AND coordinates: a machine rename/serial correction must
            # reach the serving documents (asset_id feeds retrieval narrowing
            # and the cross-machine grounding fence). The projection diffs
            # against the index, so an unchanged document costs one read and
            # no write.
            coordinates = _media_owner_coordinates(row.model_type, row.model_id)
            touched += media_projection.merge_media_metadata(
                attachment_id=row.attachment_id,
                fields={
                    'client_codes': list(codes),
                    'asset_id': coordinates['asset_id'],
                    'machine_name': coordinates['machine_name'],
                },
            )
            if list(row.client_codes or []) != codes:
                row.client_codes = codes
                row.save(update_fields=['client_codes', 'updated_at'])
    # A machine client change reaches WO/step evidence too (their codes derive
    # from this machine unless customer-attributed).
    try:
        from tasks.models import WorkOrder

        work_order_ids = WorkOrder.objects.filter(machine_id=machine_id).values_list(
            'pk', flat=True
        )
    except Exception:
        work_order_ids = []
    for work_order_id in work_order_ids:
        touched += restamp_work_order_media_client_codes(
            work_order_id, media_projection=media_projection
        )
    part_ids = MachinePart.objects.filter(machine_id=machine_id).values_list(
        'part_id', flat=True
    )
    for part_id in part_ids:
        # A projection built above is shared; otherwise the first part with a
        # genuinely changed row builds one (F-19: one client per fan-out, and
        # zero when nothing changed).
        restamped = restamp_part_client_codes(part_id, projection=projection)
        touched += restamped
    return touched


def restamp_work_order_media_client_codes(
    work_order_id: int, *, media_projection=None
) -> int:
    """Re-stamp a work order's indexed evidence media (§6.5, R3).

    Covers WO-owned rows and the WO's step-execution rows; recomputes the
    fail-closed scope per row (a customer attribution or machine/client
    change may shrink codes to ``[]`` — the merge replaces the field, which
    is exactly the fail-closed direction).
    """
    from django.db.models import Q

    from tasks.procedure_models import WorkOrderStepExecution

    from aichat.models import (
        AttachmentIngest,
        AttachmentIngestPipeline,
        AttachmentIngestState,
    )

    media_pipelines = [AttachmentIngestPipeline.IMAGE, AttachmentIngestPipeline.VIDEO]
    step_ids = list(
        WorkOrderStepExecution.objects.filter(
            application__work_order_id=work_order_id
        ).values_list('pk', flat=True)
    )
    rows = AttachmentIngest.objects.filter(
        Q(model_type='workorder', model_id=work_order_id)
        | Q(model_type='workorderstepexecution', model_id__in=step_ids),
        pipeline__in=media_pipelines,
        state=AttachmentIngestState.INDEXED,
    )
    touched = 0
    for row in rows:
        codes = derive_client_codes(row.model_type, row.model_id)
        if media_projection is None:
            # Constructed only once a row actually exists (F-19); the
            # receiver's existence gate keeps no-media saves row-free.
            from ai.core.integrations.attachment_search import MediaSearchProjection

            media_projection = MediaSearchProjection.from_settings()
        # Scope AND coordinates: a WO reassigned to another machine must
        # re-stamp asset_id/machine_name or its photos keep citing (and
        # narrowing under) the old machine. Index-diffed: unchanged
        # documents cost one read, no write.
        coordinates = _media_owner_coordinates(row.model_type, row.model_id)
        touched += media_projection.merge_media_metadata(
            attachment_id=row.attachment_id,
            fields={
                'client_codes': list(codes),
                'asset_id': coordinates['asset_id'],
                'machine_name': coordinates['machine_name'],
            },
        )
        if list(row.client_codes or []) != codes:
            row.client_codes = codes
            row.save(update_fields=['client_codes', 'updated_at'])
    return touched


def reconcile_orphaned_ingests(*, projection=None, dry_run: bool = False) -> int:
    """Purge registry rows whose Attachment no longer exists (F-05/B2 backstop).

    The purge task gets five django-q attempts; beyond that, orphaned serving
    documents would otherwise live forever (denial ≡ nonexistence demands the
    opposite). Also the recovery path for the documented mtime residual.
    """
    from aichat.models import AttachmentIngest, AttachmentIngestState
    from common.models import Attachment

    orphan_ids = list(
        AttachmentIngest.objects
        .exclude(state=AttachmentIngestState.DELETED)
        .exclude(attachment_id__in=Attachment.objects.values_list('pk', flat=True))
        .values_list('attachment_id', flat=True)
        .distinct()
    )
    if dry_run:
        return len(orphan_ids)
    purged = 0
    for attachment_id in orphan_ids:
        try:
            if projection is None:
                from ai.core.integrations.attachment_search import (
                    AttachmentSearchProjection,
                )

                projection = AttachmentSearchProjection.from_settings()
            purge_attachment_artifacts(attachment_id, projection=projection)
            purged += 1
        except Exception as exc:
            from ai.core.faults import log_fault

            log_fault(
                logger,
                'Orphaned attachment ingest purge failed',
                exc,
                stage='attachment_sweep',
            )
    return purged


def heal_media_thumbnails(*, limit: int = 200, media_projection=None) -> int:
    """Backfill thumbnail references for early-indexed media segments (R3).

    Thumbnail race, layer 3: a photo ingested while ``rebuild_attachment``
    was still queued served with an empty ``thumbnail_path``; once the
    thumbnail exists this merges the reference into the segment row and the
    serving document. Metadata-only — no re-extract, no re-embed.
    """
    from aichat.models import (
        AttachmentIngestPipeline,
        AttachmentIngestState,
        MediaSegment,
    )
    from common.models import Attachment

    healed = 0
    # IMAGE only by design: video keyframes are written synchronously in-run
    # (write failure fails the ingest), so an INDEXED video segment can never
    # have an empty thumbnail_path — there is no async producer to wait for.
    segments = MediaSegment.objects.filter(
        thumbnail_path='',
        ingest__state=AttachmentIngestState.INDEXED,
        ingest__pipeline=AttachmentIngestPipeline.IMAGE,
    ).select_related('ingest')[:limit]
    for segment in segments:
        attachment = Attachment.objects.filter(pk=segment.ingest.attachment_id).first()
        if attachment is None:
            continue
        thumbnail_path = (getattr(attachment.thumbnail, 'name', '') or '')[:512]
        if not thumbnail_path:
            continue
        if media_projection is None:
            # Constructed only once a healable row actually exists (F-19).
            from ai.core.integrations.attachment_search import MediaSearchProjection

            media_projection = MediaSearchProjection.from_settings()
        try:
            media_projection.merge_thumbnail(
                search_doc_id=segment.search_doc_id, thumbnail_path=thumbnail_path
            )
            # Inside the same guard: a concurrent purge can delete the
            # segment row, and save(update_fields=...) raises on zero rows —
            # one lost row must not abort the whole batch.
            segment.thumbnail_path = thumbnail_path
            segment.save(update_fields=['thumbnail_path'])
        except Exception as exc:
            from ai.core.faults import log_fault

            log_fault(
                logger, 'Media thumbnail heal failed', exc, stage='attachment_sweep'
            )
            continue
        healed += 1
    return healed


def resume_stalled_ingests() -> dict[str, int]:
    """Recover in-flight rows stranded by worker kills (stale-resume sweep).

    A timeout-killed worker leaves EXTRACTING/EMBEDDING; django-q's redelivery
    no-ops against the fresh claim and acks, so nothing retries organically.
    This sweep re-offers stalled rows below the attempts cap, terminalizes the
    rest as FAILED/STALLED, and reconciles orphans.
    """
    from django.conf import settings as django_settings
    from django.utils import timezone

    from aichat.models import (
        AttachmentIngest,
        AttachmentIngestPipeline,
        AttachmentIngestState,
    )

    counts = {'resumed': 0, 'stalled': 0, 'orphans': 0}
    if getattr(django_settings, 'AIMMS_ATTACHMENT_RAG_ENABLED', False):
        from ai.core.config import get_settings

        stale_before = timezone.now() - timedelta(
            seconds=get_settings().rag_stale_claim_s
        )
        in_flight = [
            AttachmentIngestState.PENDING,
            AttachmentIngestState.EXTRACTING,
            AttachmentIngestState.EMBEDDING,
        ]
        stalled = AttachmentIngest.objects.filter(
            state__in=in_flight, updated_at__lt=stale_before
        )
        resumable = stalled.filter(attempts__lt=MAX_INGEST_ATTEMPTS)
        for attachment_id in resumable.values_list('attachment_id', flat=True):
            from aichat import tasks as aichat_tasks
            from InvenTree.tasks import offload_task

            offload_task(
                aichat_tasks.ingest_attachment,
                attachment_id,
                force_async=True,
                group='ai-ingest',
            )
            counts['resumed'] += 1
        # The state+staleness filter is the atomicity condition: a live worker
        # keeps updated_at fresh via its fenced writes and is never touched.
        terminal_rows = list(
            stalled.filter(attempts__gte=MAX_INGEST_ATTEMPTS).values(
                'pk', 'attachment_id', 'source_sha256', 'pipeline'
            )
        )
        terminalized_at = timezone.now()
        for terminal_row in terminal_rows:
            terminalized = AttachmentIngest.objects.filter(
                pk=terminal_row['pk'],
                state__in=in_flight,
                updated_at__lt=stale_before,
                attempts__gte=MAX_INGEST_ATTEMPTS,
            ).update(
                state=AttachmentIngestState.FAILED,
                error_code='ATTACHMENT_INGEST_STALLED',
                updated_at=terminalized_at,
            )
            if not terminalized:
                continue
            counts['stalled'] += 1
            if terminal_row['pipeline'] == AttachmentIngestPipeline.VIDEO:
                _purge_keyframe_files(
                    terminal_row['attachment_id'],
                    sha12=terminal_row['source_sha256'][:12],
                )
    try:
        from ai.core.config import get_settings as _get_ai_settings

        heal_enabled = media_ingest_enabled(_get_ai_settings())
    except Exception:
        heal_enabled = False
    if heal_enabled:
        try:
            counts['thumbnails'] = heal_media_thumbnails()
        except Exception as exc:
            from ai.core.faults import log_fault

            log_fault(
                logger,
                'Media thumbnail heal sweep failed',
                exc,
                stage='attachment_sweep',
            )
            counts['thumbnails'] = 0
    counts['orphans'] = reconcile_orphaned_ingests()
    return counts
