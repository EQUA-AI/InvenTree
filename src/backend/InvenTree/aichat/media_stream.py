"""Authenticated Range-aware streaming for evidence media files (R4).

Fork-owned (upstream ``common/`` untouched). Serving rationale: Django's
DEBUG-only ``/media/`` static serve emits no HTTP 206, so seeking a large
video would download from byte zero even where it "works" — evidence
playback therefore rides this deterministic endpoint regardless of the
deployment's DEBUG posture, for images and video alike.

Source access is rechecked against current role and client grants on every
metadata and byte request. Revisions identify the exact stored bytes. PDF
support is limited to existing ingested attachments; no conversion is implied.

Responses never echo the stored filename or path; errors carry value-free
codes only.
"""

from __future__ import annotations

import hashlib
import re

from django.http import HttpResponse, JsonResponse, StreamingHttpResponse

from rest_framework.views import APIView

from InvenTree.permissions import IsAuthenticatedOrReadScope

_BLOCK = 1024 * 1024

_CONTENT_TYPES = {
    '.pdf': 'application/pdf',
    '.mp4': 'video/mp4',
    '.m4v': 'video/mp4',
    '.mov': 'video/quicktime',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
}

_RANGE_RE = re.compile(r'^bytes=(\d{1,19})?-(\d{1,19})?$')


def _resolve_source(request, attachment_id):
    """Open only authorized indexed bytes, pinned to the requested revision."""
    from django.core.files.storage import default_storage

    from tasks.scope import ScopeError, client_codes_for_actor

    from ai.core.integrations.retrieval_authority import fresh_actor, fresh_role
    from aichat.models import AttachmentIngest
    from aichat.services.attachment_ingestion import derive_client_codes
    from common.models import Attachment

    actor = fresh_actor(request.user)
    if actor is None or not fresh_role(actor, 'work_order'):
        return None
    attachment = Attachment.objects.filter(pk=attachment_id).first()
    if attachment is None or attachment.model_type not in {
        'part',
        'assetmachine',
        'workorder',
        'workorderstepexecution',
    }:
        return None
    if attachment.model_type == 'part' and not fresh_role(actor, 'part'):
        return None
    try:
        if not set(
            derive_client_codes(attachment.model_type, attachment.model_id)
        ).intersection(client_codes_for_actor(actor)):
            return None
    except ScopeError:
        return None
    ingest = (
        AttachmentIngest.objects
        .filter(attachment_id=attachment_id)
        .order_by('-pk')
        .first()
    )
    if (
        ingest is None
        or ingest.state != 'indexed'
        or ingest.pipeline not in {'doc', 'image', 'video'}
    ):
        return None
    requested_revision = request.query_params.get('revision')
    if requested_revision is not None and requested_revision != ingest.source_sha256:
        return None
    name = getattr(attachment.attachment, 'name', '') or ''
    suffix = ('.' + name.rsplit('.', 1)[-1].lower()) if '.' in name else ''
    if suffix not in _CONTENT_TYPES or not default_storage.exists(name):
        return None
    # Hash and serve the same open file. A replaced attachment must never
    # masquerade as the indexed revision, even before ingestion catches up.
    handle = default_storage.open(name, 'rb')
    try:
        digest = hashlib.sha256()
        size = 0
        while block := handle.read(_BLOCK):
            digest.update(block)
            size += len(block)
        revision = digest.hexdigest()
        if revision != ingest.source_sha256:
            handle.close()
            return None
        handle.seek(0)
        return handle, size, _CONTENT_TYPES[suffix], revision
    except Exception:
        handle.close()
        raise


class EvidenceMediaMetadataView(APIView):
    """Resolve a source revision before the viewer requests its bytes."""

    permission_classes = [IsAuthenticatedOrReadScope]

    def get(self, request, attachment_id: int):
        """Return bounded, verified metadata without storage paths."""
        resolved = _resolve_source(request, attachment_id)
        if resolved is None:
            return HttpResponse(status=404)
        handle, _, content_type, revision = resolved
        page_count, page_labels = None, []
        try:
            if content_type == 'application/pdf':
                from pypdf import PdfReader

                reader = PdfReader(handle)
                page_count = len(reader.pages)
                page_labels = [str(label)[:100] for label in reader.page_labels[:10000]]
        except Exception:
            # The original remains available, but no exact page is asserted.
            page_count, page_labels = None, []
        finally:
            handle.close()
        response = JsonResponse({
            'revision': revision,
            'content_type': content_type,
            'page_count': page_count,
            'page_labels': page_labels,
        })
        response['Cache-Control'] = 'private, no-store'
        return response


def _iter_file(handle, *, start: int, end: int):
    """Yield 1 MiB blocks of [start, end] without buffering the file."""
    try:
        handle.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            block = handle.read(min(_BLOCK, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block
    finally:
        handle.close()


class EvidenceMediaStreamView(APIView):
    """Stream one attachment's media file with single-range HTTP 206 support."""

    permission_classes = [IsAuthenticatedOrReadScope]

    def get(self, request, attachment_id: int):
        """Serve the file whole (200) or a single byte range (206/416)."""
        return self._serve(request, attachment_id, include_body=True)

    def head(self, request, attachment_id: int):
        """Return the same media/range metadata without opening the file."""
        return self._serve(request, attachment_id, include_body=False)

    def _serve(self, request, attachment_id: int, *, include_body: bool):
        """Build a whole or ranged response for GET/HEAD."""
        resolved = _resolve_source(request, attachment_id)
        if resolved is None:
            return HttpResponse(status=404)
        handle, size, content_type, revision = resolved

        start, end = 0, size - 1
        status = 200
        range_header = request.headers.get('Range', '')
        match = _RANGE_RE.match(range_header.strip()) if range_header else None
        if match and (match.group(1) or match.group(2)):
            if match.group(1):
                start = int(match.group(1))
                if match.group(2):
                    end = min(int(match.group(2)), size - 1)
            else:
                # suffix range: last N bytes
                length = int(match.group(2))
                start = max(0, size - length)
            if start >= size or start > end:
                handle.close()
                response = HttpResponse(status=416)
                response['Content-Range'] = f'bytes */{size}'
                response['Accept-Ranges'] = 'bytes'
                response['Cache-Control'] = 'private, no-store'
                response['X-Content-Type-Options'] = 'nosniff'
                return response
            status = 206
        # A malformed Range header is ignored (200 full), per RFC 9110.

        if include_body:
            response = StreamingHttpResponse(
                _iter_file(handle, start=start, end=end),
                status=status,
                content_type=content_type,
            )
        else:
            handle.close()
            response = HttpResponse(status=status, content_type=content_type)
        response['ETag'] = f'"{revision}"'
        response['Accept-Ranges'] = 'bytes'
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Content-Length'] = str(end - start + 1)
        if status == 206:
            response['Content-Range'] = f'bytes {start}-{end}/{size}'
        return response
