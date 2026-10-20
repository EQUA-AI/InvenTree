"""Retain original bytes separately from editable attachment/transcript records."""

import hashlib
from datetime import timezone as datetime_timezone
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from tasks.report_models import CaptureOriginal


def original_summary(row):
    """Return only a protected app route; never expose storage credentials/URLs."""
    started = row.started_at
    if started is not None:
        # This field stores UTC, including in the native USE_TZ=False test mode.
        if timezone.is_naive(started):
            started = started.replace(tzinfo=datetime_timezone.utc)
        started = started.astimezone(datetime_timezone.utc).isoformat()
    return {
        'id': row.pk,
        'filename': row.filename,
        'content_type': row.content_type,
        'content_hash': row.content_hash,
        'byte_size': row.byte_size,
        'started_at': started,
        'duration_ms': row.duration_ms,
        'url': f'/api/tasks/work-orders/{row.work_order_id}/capture-media/{row.pk}/',
    }


def retain_original(
    *,
    work_order,
    actor,
    file,
    content_type='',
    started_at=None,
    duration_ms=None,
    voice_capture=None,
    artifact=None,
):
    """Copy exact bytes; timestamps/duration are supplied facts, never inferred."""
    if started_at is not None:
        if timezone.is_naive(started_at):
            raise ValueError('Recording start time must include a timezone')
        started_at = started_at.astimezone(datetime_timezone.utc)
        if not settings.USE_TZ:
            started_at = started_at.replace(tzinfo=None)
    from common.settings import get_global_setting

    maximum = int(
        get_global_setting('INVENTREE_UPLOAD_MAX_SIZE', create=False) * 1024 * 1024
    )
    capture_limit = settings.AIMMS_CAPTURE_MAX_BYTES
    if capture_limit < 0:
        raise ValueError('Capture size limit must be nonnegative')
    if capture_limit:
        maximum = min(maximum, capture_limit)
    data = file.read(maximum + 1)
    if not data or len(data) > maximum:
        raise ValueError('Original media is empty or exceeds the capture size limit')
    digest = hashlib.sha256(data).hexdigest()
    if voice_capture:
        existing = CaptureOriginal.objects.filter(voice_capture=voice_capture).first()
        if existing:
            if (
                existing.content_hash != digest
                or existing.started_at != started_at
                or existing.duration_ms != duration_ms
            ):
                raise ValueError('This session already retains a different original')
            return existing
    row = CaptureOriginal(
        work_order=work_order,
        voice_capture=voice_capture,
        artifact=artifact,
        filename=Path(file.name).name[:255],
        content_type=content_type[:100],
        content_hash=digest,
        byte_size=len(data),
        started_at=started_at,
        duration_ms=duration_ms,
        recorded_by=actor,
    )
    row.file.save(row.filename, ContentFile(data), save=False)
    try:
        row.save()
    except Exception:
        row.file.delete(save=False)
        raise
    return row
