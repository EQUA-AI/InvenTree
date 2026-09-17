"""Shared ordered voice cleanup; protected operational evidence stays intact."""

from django.db import transaction
from django.db.models.deletion import ProtectedError

from voice.models import (
    CaptureState,
    VoiceCaptureReview,
    VoiceTranscriptAcceptance,
    VoiceTranscriptRevision,
)

SETTLED_CAPTURE_STATES = (
    CaptureState.CANCELED,
    CaptureState.FAILED,
    CaptureState.ACCEPTED,
    CaptureState.COMMITTED,
)


def selected_ids(queryset, *, batch_size):
    """Yield bounded primary-key pages without retaining an entire owner graph."""
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError('Batch size must be between 1 and 1000')
    after = None
    while True:
        page = queryset if after is None else queryset.filter(pk__gt=after)
        ids = list(page.order_by('pk').values_list('pk', flat=True)[:batch_size])
        if not ids:
            break
        yield from ids
        after = ids[-1]


def purge_captures(queryset, *, batch_size):
    """Remove settled captures leaf-first, rolling back a blocked capture.

    The parent lock and repeated selection protect ACTIVE/REVIEW transitions.
    One capture is atomic: a cross-capture PROTECT reference or revision cycle
    must not leave its acceptance/review evidence partially removed.
    """
    eligible = queryset.filter(state__in=SETTLED_CAPTURE_STATES)
    deleted = blocked = failed = 0
    for capture_id in selected_ids(eligible, batch_size=batch_size):
        try:
            with transaction.atomic():
                capture = eligible.select_for_update().filter(pk=capture_id).first()
                if capture is None:
                    continue
                VoiceCaptureReview.objects.filter(
                    revision__capture_id=capture_id
                ).delete()
                VoiceTranscriptAcceptance.objects.filter(
                    revision__capture_id=capture_id
                ).delete()
                capture.accepted_revision = None
                capture.save(update_fields=['accepted_revision'])
                remaining = VoiceTranscriptRevision.objects.filter(capture=capture)
                while remaining.exists():
                    leaves = remaining.exclude(
                        pk__in=VoiceTranscriptRevision.objects.filter(
                            supersedes__isnull=False
                        ).values('supersedes_id')
                    )
                    ids = list(leaves.values_list('pk', flat=True)[:batch_size])
                    if not ids:
                        raise ProtectedError('Voice revision purge blocked', [])
                    VoiceTranscriptRevision.objects.filter(pk__in=ids).delete()
                capture.delete()
            deleted += 1
        except ProtectedError:
            blocked += 1
        except Exception:
            # Public reports contain counts, never exception messages or ids.
            failed += 1
    return {
        'voice_captures': deleted,
        'voice_captures_blocked': blocked,
        'voice_captures_failed': failed,
    }


def purge_sessions(queryset, *, batch_size):
    """Delete eligible sessions independently; preserve every PROTECT reference.

    VoiceOperation and approval-delivery receipts are operational records.
    Surviving captures/reviews also retain their own lifecycle. None are
    cascaded or detached to force a session purge through.
    """
    deleted = blocked = failed = 0
    for session_id in selected_ids(queryset, batch_size=batch_size):
        try:
            with transaction.atomic():
                session = queryset.select_for_update().filter(pk=session_id).first()
                if session is None:
                    continue
                session.delete()
            deleted += 1
        except ProtectedError:
            blocked += 1
        except Exception:
            failed += 1
    return {
        'voice_sessions': deleted,
        'voice_sessions_blocked': blocked,
        'voice_sessions_failed': failed,
    }
