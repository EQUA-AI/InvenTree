"""Operator-only user erasure composition for the current chat/voice stores."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from aichat.models import (
    AIRetentionOutbox,
    ChatThread,
    ChatThreadGrant,
    ChatThreadTombstone,
    MessageFeedback,
)
from aichat.services import retention
from aichat.services.voice_retention import (
    SETTLED_CAPTURE_STATES,
    purge_captures,
    purge_sessions,
    selected_ids,
)
from voice.models import VoiceCaptureSession, VoiceSession


def _residuals(user_id):
    tombstones = ChatThreadTombstone.objects.filter(owner_id=user_id)
    return {
        'owned_threads': ChatThread.objects.filter(owner_id=user_id).count(),
        'incoming_grants': ChatThreadGrant.objects.filter(
            grantee_id=user_id, revoked_at__isnull=True
        ).count(),
        'authored_feedback': MessageFeedback.objects.filter(user_id=user_id).count(),
        'voice_sessions': VoiceSession.objects.filter(owner_id=user_id).count(),
        'voice_captures': VoiceCaptureSession.objects.filter(owner_id=user_id).count(),
        'unsettled_captures': VoiceCaptureSession.objects
        .filter(owner_id=user_id)
        .exclude(state__in=SETTLED_CAPTURE_STATES)
        .count(),
        'outstanding_thread_cleanup': AIRetentionOutbox.objects
        .filter(reference__in=tombstones.values('thread_id'))
        .exclude(state='done')
        .count(),
    }


def purge_user_content(user_id, *, dry_run, batch_size):
    """Purge all owner scopes, revoke incoming shares, then verify residuals.

    This function is only for an already authorized operator, not a request's
    caller-supplied id. New writes during execution make the final live-store
    counts incomplete; this operation does not create an account write hold.
    Every retry includes retained owner tombstones and registered probes.
    """
    if type(user_id) is not int or user_id < 1:
        raise ValueError('A positive user id is required')
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError('Batch size must be between 1 and 1000')
    if not get_user_model().objects.filter(pk=user_id).exists():
        raise ValueError('Unknown erasure owner')
    started = timezone.now()
    coverage_gaps = retention.THREAD_DERIVATIVES.uncovered_models()
    report = {
        'schema_version': 1,
        'scope': 'current_chat_and_voice_stores',
        'subject_hash': salted_hmac('aichat.user-erasure.v1', str(user_id)).hexdigest(),
        'started_at': started.isoformat(),
        'account_erasure_complete': False,
        'backup_window': {'status': 'unverified', 'days': None},
        'preserved': [
            'user_account',
            'operational_records',
            'proposal_actor_references',
            'thread_tombstones',
            'grant_audit',
            'quota_audit',
        ],
        'not_covered': [
            'account_anonymization_and_deactivation',
            'legacy_conversations',
            'retrieval_and_usage_detail',
            'mailbox_and_other_ai_domains',
            'provider_state',
            'external_telemetry',
            'backups',
        ],
        'registration_gaps': len(coverage_gaps),
        'before': _residuals(user_id),
    }
    if dry_run or coverage_gaps:
        return {
            **report,
            'status': 'dry_run' if dry_run else 'purge_incomplete',
            'finished_at': timezone.now().isoformat(),
        }

    counts = {'threads_attempted': 0, 'thread_failures': 0, 'cleanup_failures': 0}
    # Audit rows survive. Even expired but not yet revoked grants get a stamp.
    counts['grants_revoked'] = ChatThreadGrant.objects.filter(
        grantee_id=user_id, revoked_at__isnull=True
    ).update(revoked_at=started)
    counts['feedback_removed'] = retention._batched_delete(
        MessageFeedback.objects.filter(user_id=user_id, created_at__lte=started),
        family='user_feedback',
        batch_size=batch_size,
        dry_run=False,
    )
    owned = ChatThread.objects.filter(owner_id=user_id, created_at__lte=started)
    for thread_id in selected_ids(owned, batch_size=batch_size):
        # Recheck ownership after materializing an id page.
        if not owned.filter(pk=thread_id).exists():
            continue
        counts['threads_attempted'] += 1
        try:
            retention.purge_thread_now(
                thread_id, reason=retention.TOMBSTONE_USER_ERASURE
            )
        except Exception:
            counts['thread_failures'] += 1

    captures = VoiceCaptureSession.objects.filter(
        owner_id=user_id, created_at__lte=started
    )
    sessions = VoiceSession.objects.filter(owner_id=user_id, created_at__lte=started)
    # Captures first: their live_session and delivery reviews PROTECT sessions.
    counts.update(purge_captures(captures, batch_size=batch_size))
    counts.update(purge_sessions(sessions, batch_size=batch_size))

    tombstones = ChatThreadTombstone.objects.filter(
        owner_id=user_id, thread_created_at__lte=started
    )
    for pk in selected_ids(tombstones, batch_size=batch_size):
        tombstone = tombstones.filter(pk=pk).first()
        if tombstone is None:
            continue
        try:
            with transaction.atomic():
                receipt = retention.thread_purge_receipt(tombstone.thread_id)
            if receipt['status'] != 'deleted':
                counts['cleanup_failures'] += 1
        except Exception:
            counts['cleanup_failures'] += 1
    residuals = _residuals(user_id)
    failed = any(
        value
        for key, value in counts.items()
        if key.endswith(('_failures', '_failed', '_blocked'))
    )
    return {
        **report,
        'status': 'purge_incomplete' if failed or any(residuals.values()) else 'purged',
        'processed': counts,
        'residuals': residuals,
        'finished_at': timezone.now().isoformat(),
    }
