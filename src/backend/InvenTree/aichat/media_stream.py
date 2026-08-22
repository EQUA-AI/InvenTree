"""Authenticated Range-aware streaming for evidence media files (R4).

Fork-owned (upstream ``common/`` untouched). Serving rationale: Django's
DEBUG-only ``/media/`` static serve emits no HTTP 206, so seeking a large
video would download from byte zero even where it "works" — evidence
playback therefore rides this deterministic endpoint regardless of the
deployment's DEBUG posture, for images and video alike.

Auth posture: matches the PRE-EXISTING ``/api/attachment/{id}`` read posture
(authenticated, NOT object-scoped — any authenticated user can already fetch
any attachment record and its media URL), so this endpoint widens nothing.
Deliberately scope-neutral in v1; diverging from the attachment API would be
a separate, two-surface security change.

Responses never echo the stored filename or path; errors carry value-free
codes only.
"""

from __future__ import annotations

import re

from django.http import HttpResponse, StreamingHttpResponse

from rest_framework.views import APIView

from InvenTree.permissions import IsAuthenticatedOrReadScope

_BLOCK = 1024 * 1024

_CONTENT_TYPES = {
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
        from django.core.files.storage import default_storage

        from aichat.models import (
            AttachmentIngest,
            AttachmentIngestPipeline,
            AttachmentIngestState,
        )
        from common.models import Attachment

        is_indexed_media = AttachmentIngest.objects.filter(
            attachment_id=attachment_id,
            pipeline__in=[
                AttachmentIngestPipeline.IMAGE,
                AttachmentIngestPipeline.VIDEO,
            ],
            state=AttachmentIngestState.INDEXED,
        ).exists()
        attachment = Attachment.objects.filter(pk=attachment_id).first()
        name = getattr(getattr(attachment, 'attachment', None), 'name', '') or ''
        suffix = ('.' + name.rsplit('.', 1)[-1].lower()) if '.' in name else ''
        if (
            not is_indexed_media
            or attachment is None
            or not name
            or suffix not in _CONTENT_TYPES
            or not default_storage.exists(name)
        ):
            return HttpResponse(status=404)

        size = default_storage.size(name)
        content_type = _CONTENT_TYPES[suffix]

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
                response = HttpResponse(status=416)
                response['Content-Range'] = f'bytes */{size}'
                response['Accept-Ranges'] = 'bytes'
                response['Cache-Control'] = 'private, no-store'
                response['X-Content-Type-Options'] = 'nosniff'
                return response
            status = 206
        # A malformed Range header is ignored (200 full), per RFC 9110.

        if include_body:
            handle = default_storage.open(name)
            response = StreamingHttpResponse(
                _iter_file(handle, start=start, end=end),
                status=status,
                content_type=content_type,
            )
        else:
            response = HttpResponse(status=status, content_type=content_type)
        response['Accept-Ranges'] = 'bytes'
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Content-Length'] = str(end - start + 1)
        if status == 206:
            response['Content-Range'] = f'bytes {start}-{end}/{size}'
        return response
