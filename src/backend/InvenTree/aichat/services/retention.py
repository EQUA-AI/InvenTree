"""S16 retention purges: the Q48/§8.11 matrix as code.

One service owns every data-deleting path in the AI program. Scheduled
expiry and immediate user deletion share the same per-thread core, so the
tombstone/grant reconciliation is tested once and cannot diverge. Every
destructive function is idempotent, batched (``PURGE_BATCH_SIZE`` rows per
transaction, the ``approvals.tasks.purge_expired_approvals`` precedent),
and dry-run capable (identical selection, counts only, zero writes).

Retention day-counts are deliberately module constants, not settings:
they are owner-adopted policy that should change via review, never via
environment drift. The only flag is ``FEATURE_AI_RETENTION_JOBS`` (dark
by default), which gates the scheduled DB purges — the uploads TTL sweep
and the outbox drain run ungated (denial ≡ nonexistence: an orphaned file
for a deleted thread must not wait on a feature flag).

Explicitly EXCLUDED from every purge here:

- ``AIPilotStopLatch`` / ``AIPilotStopApproval`` — permanent safety
  governance record (§15.4).
- ``AIQuotaPolicy`` / ``AIQuotaAssignment`` — versioned policy/config
  audit; ``assignment.policy`` is PROTECT by design.
- ``ControlledDocument`` + attachment ingest machinery — governed-source
  lifecycle owns them (their own purge/reconcile services exist).
- ``assets`` health models — customer-lifetime by documented design.
- Live cache counters (own TTLs) and external telemetry (sink policy).
- Battery journals — human-run ``ai.core.evals.prune_journals`` CLI; the
  private store is not server-mounted.

Legal hold is NOT implemented. If it becomes required it must be an
explicit authorized state with an owner and an expiry (reserve the
``_AIMMS_RETENTION_LEGAL_HOLD`` setting name), checked in the selection
queries — never an implicit purge failure.
"""

import json
import logging
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from aichat.models import (
    TERMINAL_PROPOSAL_STATES,
    AIQuotaAuditEvent,
    AIQuotaReservation,
    AIQuotaReservationState,
    AIRequestRejection,
    AIRetentionOutbox,
    AIUsageMonthlyAggregate,
    ChatActionProposal,
    ChatEvidenceSet,
    ChatEvidenceSetMember,
    ChatMessage,
    ChatThread,
    ChatThreadGrant,
    ChatThreadGrantTombstone,
    ChatThreadTombstone,
    ChatTurn,
    MessageFeedback,
    ProposalState,
    RetrievalMiss,
)

logger = logging.getLogger('inventree')

#: Q48: transcripts, turns, evidence, feedback, voice, terminal proposals,
#: tombstones.
RETENTION_TRANSCRIPT_DAYS = 400
#: Q48: usage metadata detail, RetrievalMiss, AIRequestRejection, settled
#: quota reservations.
RETENTION_DETAIL_DAYS = 90
#: Q48: sanitized monthly aggregates.
RETENTION_AGGREGATE_MONTHS = 13
#: Must equal ``ai.core.app.UPLOAD_TTL_HOURS`` (pinned by test).
UPLOAD_TTL_HOURS = 24
#: Rows/artifacts per transaction (impl plan §8.11).
PURGE_BATCH_SIZE = 500
OUTBOX_MAX_ATTEMPTS = 10
#: ``_``-prefixed InvenTreeSetting holding the last real run's report JSON.
LAST_RUN_SETTING = '_AIMMS_RETENTION_LAST_RUN'

TOMBSTONE_USER_DELETE = 'user_delete'
TOMBSTONE_RETENTION_EXPIRY = 'retention_expiry'

_THREAD_DIR_CHARSET = set(
    'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-'
)


def _cutoff(days: int) -> datetime:
    return timezone.now() - timedelta(days=days)


def _batched_delete(queryset, *, family: str, batch_size: int, dry_run: bool) -> int:
    """Delete a queryset in bounded per-transaction batches.

    Returns the primary-row count on dry runs and the total deleted row
    count (cascades included) on real runs.
    """
    if dry_run:
        return queryset.count()
    model = queryset.model
    total = 0
    while True:
        pks = list(queryset.values_list('pk', flat=True)[:batch_size])
        if not pks:
            break
        with transaction.atomic():
            deleted, _ = model.objects.filter(pk__in=pks).delete()
        total += deleted
        logger.info('retention_purge_batch family=%s rows=%d', family, deleted)
    return total


# ---------------------------------------------------------------------------
# Thread family (400 days + the immediate user-deletion path)
# ---------------------------------------------------------------------------


def purge_thread_now(
    thread_id: str,
    *,
    actor_user_id: int | None = None,
    reason: str = TOMBSTONE_USER_DELETE,
) -> dict:
    """Immediately purge one thread's content, leaving only the tombstone.

    The path ``ThreadRepository.delete`` calls — the caller has already
    enforced the ownership boundary. Raises ``ChatThread.DoesNotExist``
    for an unknown id.
    """
    thread = ChatThread.objects.get(pk=thread_id)
    return _purge_thread(
        thread, reason=reason, actor_user_id=actor_user_id, batch_size=PURGE_BATCH_SIZE
    )


def purge_expired_threads(
    *,
    days: int = RETENTION_TRANSCRIPT_DAYS,
    batch_size: int = PURGE_BATCH_SIZE,
    dry_run: bool = False,
) -> dict:
    """Purge threads whose last activity is older than the 400-day window.

    Cutoff basis is ``updated_at`` (last activity), so an old thread that
    is still in use is never truncated mid-conversation.
    """
    cutoff = _cutoff(days)
    expired = ChatThread.objects.filter(updated_at__lt=cutoff)
    if dry_run:
        return {'threads': expired.count()}
    purged = 0
    for thread_id in list(expired.values_list('pk', flat=True)):
        thread = ChatThread.objects.filter(pk=thread_id).first()
        if thread is None:
            continue
        _purge_thread(
            thread,
            reason=TOMBSTONE_RETENTION_EXPIRY,
            actor_user_id=None,
            batch_size=batch_size,
        )
        purged += 1
    return {'threads': purged}


def _purge_thread(
    thread, *, reason: str, actor_user_id: int | None, batch_size: int
) -> dict:
    """Purge one thread's full content graph, tombstone first.

    Order matters: the bulk child families are deleted in bounded batches
    BEFORE the final transaction (an evidence set may hold up to 25k
    members), then one atomic step locks the thread row, transfers grant
    audit to tombstones, deletes the grant rows (``ChatThreadGrant.thread``
    is PROTECT — a naked ``thread.delete()`` raises), and deletes the
    thread; any straggler rows appended mid-purge ride that final cascade.
    """
    thread_id = thread.pk
    counts = {
        'messages': ChatMessage.objects.filter(thread_id=thread_id).count(),
        'turns': ChatTurn.objects.filter(thread_id=thread_id).count(),
    }

    _batched_delete(
        ChatEvidenceSetMember.objects.filter(set__turn__thread_id=thread_id),
        family='evidence_members',
        batch_size=batch_size,
        dry_run=False,
    )
    _batched_delete(
        ChatEvidenceSet.objects.filter(turn__thread_id=thread_id),
        family='evidence_sets',
        batch_size=batch_size,
        dry_run=False,
    )
    _batched_delete(
        MessageFeedback.objects.filter(message__thread_id=thread_id),
        family='feedback',
        batch_size=batch_size,
        dry_run=False,
    )
    _batched_delete(
        ChatTurn.objects.filter(thread_id=thread_id),
        family='turns',
        batch_size=batch_size,
        dry_run=False,
    )
    _batched_delete(
        ChatMessage.objects.filter(thread_id=thread_id),
        family='messages',
        batch_size=batch_size,
        dry_run=False,
    )

    purge_voice_for_thread(thread_id, batch_size=batch_size)
    scrub_proposals_for_thread(thread_id)

    with transaction.atomic():
        locked = ChatThread.objects.select_for_update().filter(pk=thread_id).first()
        if locked is None:
            return {'thread': thread_id, 'already_purged': True, **counts}
        tombstone, _created = ChatThreadTombstone.objects.get_or_create(
            thread_id=thread_id,
            defaults={
                'owner_id': locked.owner_id,
                'namespace': locked.namespace,
                'scope_hash': locked.scope_hash,
                'thread_created_at': locked.created_at,
                'reason': reason,
                'deleted_by_id': actor_user_id,
                'message_count': counts['messages'],
                'turn_count': counts['turns'],
            },
        )
        grants = list(ChatThreadGrant.objects.filter(thread_id=thread_id))
        if grants:
            tombstone.had_grants = True
            tombstone.save(update_fields=['had_grants'])
            now = timezone.now()
            ChatThreadGrantTombstone.objects.bulk_create([
                ChatThreadGrantTombstone(
                    tombstone=tombstone,
                    grantee_id=grant.grantee_id,
                    granted_by_id=grant.granted_by_id,
                    access=grant.access,
                    granted_at=grant.created_at,
                    expires_at=grant.expires_at,
                    # An unrevoked grant is closed by the purge itself.
                    revoked_at=grant.revoked_at or now,
                )
                for grant in grants
            ])
            ChatThreadGrant.objects.filter(
                pk__in=[grant.pk for grant in grants]
            ).delete()
        locked.delete()
        enqueue_outbox('upload_dir', thread_id)
        transaction.on_commit(lambda: _try_remove_upload_dir(thread_id))

    logger.info(
        'retention_purge_complete family=threads thread=%s reason=%s '
        'messages=%d turns=%d',
        thread_id,
        reason,
        counts['messages'],
        counts['turns'],
    )
    return {'thread': thread_id, 'reason': reason, **counts}


def purge_tombstones(
    *,
    days: int = RETENTION_TRANSCRIPT_DAYS,
    batch_size: int = PURGE_BATCH_SIZE,
    dry_run: bool = False,
) -> dict:
    """Purge tombstones 400 days after the deletion they record."""
    count = _batched_delete(
        ChatThreadTombstone.objects.filter(deleted_at__lt=_cutoff(days)),
        family='tombstones',
        batch_size=batch_size,
        dry_run=dry_run,
    )
    return {'tombstones': count}


# ---------------------------------------------------------------------------
# Proposals (dangling by design: thread_id is a plain CharField)
# ---------------------------------------------------------------------------


def scrub_proposals_for_thread(thread_id: str) -> dict:
    """Close and content-scrub a purged thread's proposals.

    Proposals do not cascade (no FK), so thread purge must handle them:
    pending rows are expired with a typed failure code, and every row for
    the thread loses its content-bearing fields. The ``receipt`` is kept —
    it is the server-derived record of a real-world effect, and the
    ``aichat_proposal_executed_receipt`` constraint requires it.
    """
    expired = ChatActionProposal.objects.filter(
        thread_id=thread_id, state=ProposalState.PROPOSED
    ).update(state=ProposalState.EXPIRED, failure_code='thread_deleted')
    scrubbed = ChatActionProposal.objects.filter(thread_id=thread_id).update(
        reason='', intent={}, preview={}
    )
    return {'expired': expired, 'scrubbed': scrubbed}


def purge_terminal_proposals(
    *,
    days: int = RETENTION_TRANSCRIPT_DAYS,
    batch_size: int = PURGE_BATCH_SIZE,
    dry_run: bool = False,
) -> dict:
    """Purge terminal proposals 400 days after their last transition.

    Closes the recorded Q48 gap ("proposal expiry never deletes"): the
    sweep task still only expires; deletion is retention's job alone.
    """
    count = _batched_delete(
        ChatActionProposal.objects.filter(
            state__in=TERMINAL_PROPOSAL_STATES, updated_at__lt=_cutoff(days)
        ),
        family='proposals',
        batch_size=batch_size,
        dry_run=dry_run,
    )
    return {'proposals': count}


# ---------------------------------------------------------------------------
# Voice (400-day family; PROTECT chains force explicit ordering)
# ---------------------------------------------------------------------------


def purge_voice_for_thread(
    thread_id: str, *, batch_size: int = PURGE_BATCH_SIZE
) -> dict:
    """Delete a purged thread's voice sessions (spoken summaries ride along).

    ``VoiceSession.thread_id`` is a plain CharField that would dangle —
    and its utterances carry spoken content bound to the purged thread.
    Captures are work-order-bound, not thread-bound; they keep their own
    400-day clock in :func:`purge_expired_voice`.
    """
    from voice.models import VoiceSession

    count = _batched_delete(
        VoiceSession.objects.filter(thread_id=thread_id),
        family='voice_sessions',
        batch_size=batch_size,
        dry_run=False,
    )
    return {'voice_sessions': count}


def purge_expired_voice(
    *,
    days: int = RETENTION_TRANSCRIPT_DAYS,
    batch_size: int = PURGE_BATCH_SIZE,
    dry_run: bool = False,
) -> dict:
    """Purge ended voice sessions and settled captures past 400 days.

    Captures still ACTIVE/REVIEW are never touched. The transcript
    revision chain is self-referential PROTECT, so deletion is leaf-first:
    acceptances, then the ``accepted_revision`` pins, then revisions no
    surviving row supersedes, repeating until the chain is gone.
    """
    from voice.models import (
        TERMINAL_SESSION_STATES,
        CaptureState,
        VoiceCaptureSession,
        VoiceSession,
        VoiceTranscriptAcceptance,
        VoiceTranscriptRevision,
    )

    cutoff = _cutoff(days)
    sessions_qs = VoiceSession.objects.filter(
        state__in=TERMINAL_SESSION_STATES, ended_at__lt=cutoff
    )
    settled_states = (
        CaptureState.CANCELED,
        CaptureState.FAILED,
        CaptureState.ACCEPTED,
        CaptureState.COMMITTED,
    )
    captures_qs = VoiceCaptureSession.objects.filter(
        state__in=settled_states, updated_at__lt=cutoff
    )

    if dry_run:
        return {
            'voice_sessions': sessions_qs.count(),
            'voice_captures': captures_qs.count(),
        }

    sessions = _batched_delete(
        sessions_qs, family='voice_sessions', batch_size=batch_size, dry_run=False
    )

    capture_ids = list(captures_qs.values_list('pk', flat=True))
    captures = 0
    if capture_ids:
        with transaction.atomic():
            VoiceTranscriptAcceptance.objects.filter(
                revision__capture_id__in=capture_ids
            ).delete()
            VoiceCaptureSession.objects.filter(pk__in=capture_ids).update(
                accepted_revision=None
            )
        # Leaf-first: a revision may be deleted only once nothing in the
        # target set still supersedes it. A pass that deletes nothing while
        # rows remain means an out-of-set PROTECT reference — fail loudly.
        while True:
            remaining = VoiceTranscriptRevision.objects.filter(
                capture_id__in=capture_ids
            )
            leaves = remaining.exclude(
                pk__in=remaining.filter(supersedes__isnull=False).values(
                    'supersedes_id'
                )
            )
            pks = list(leaves.values_list('pk', flat=True)[:batch_size])
            if not pks:
                if remaining.exists():
                    raise RuntimeError(
                        'voice revision purge stalled: rows remain with no '
                        'deletable leaf (out-of-set supersedes reference?)'
                    )
                break
            with transaction.atomic():
                VoiceTranscriptRevision.objects.filter(pk__in=pks).delete()
        captures = _batched_delete(
            VoiceCaptureSession.objects.filter(pk__in=capture_ids),
            family='voice_captures',
            batch_size=batch_size,
            dry_run=False,
        )
    return {'voice_sessions': sessions, 'voice_captures': captures}


# ---------------------------------------------------------------------------
# 90-day detail family
# ---------------------------------------------------------------------------


def _month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    next_start = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, next_start


def _month_start_dt(day: date) -> datetime:
    """Midnight starting ``day``, matching the project's USE_TZ posture."""
    value = datetime.combine(day, datetime.min.time())
    if timezone.is_aware(timezone.now()):
        return timezone.make_aware(value)
    return value


def _expired_months(queryset, *, field: str, cutoff: datetime) -> list[date]:
    """First-of-month dates whose entire month is older than the cutoff."""
    months = set()
    for value in queryset.datetimes(field, 'month'):
        start, next_start = _month_bounds(value.date())
        if _month_start_dt(next_start) <= cutoff:
            months.add(start)
    return sorted(months)


def scrub_usage_detail(
    *,
    days: int = RETENTION_DETAIL_DAYS,
    batch_size: int = PURGE_BATCH_SIZE,
    dry_run: bool = False,
) -> dict:
    """Aggregate-then-scrub per-turn usage metadata past 90 days.

    Month-grained and crash-safe by ordering: for each fully expired
    month, ALL aggregate rows commit in one transaction BEFORE the first
    ``metadata['usage']`` key is popped, so a rerun after a partial scrub
    resumes scrubbing without recomputing from partially-scrubbed data.
    Every other metadata blob (``evidence_gate``, ``grounding``,
    ``evidence_analysis`` — code/verdict/count-only) survives to the
    400-day thread purge; only the usage detail class leaves here.
    """
    cutoff = _cutoff(days)
    usage_qs = ChatMessage.objects.filter(metadata__has_key='usage')
    months = _expired_months(
        usage_qs.filter(created_at__lt=cutoff), field='created_at', cutoff=cutoff
    )
    if dry_run:
        return {
            'usage_messages': usage_qs.filter(created_at__lt=cutoff).count(),
            'months': [month.isoformat() for month in months],
        }

    scrubbed_total = 0
    for month in months:
        start, next_start = _month_bounds(month)
        month_qs = usage_qs.filter(
            created_at__gte=_month_start_dt(start),
            created_at__lt=_month_start_dt(next_start),
        )
        if not AIUsageMonthlyAggregate.objects.filter(
            month=start, source='turn_usage'
        ).exists():
            _aggregate_usage_month(start, month_qs)
        scrubbed_total += _scrub_usage_rows(month_qs, batch_size=batch_size)
    return {'usage_messages': scrubbed_total, 'months': [m.isoformat() for m in months]}


def _aggregate_usage_month(month: date, month_qs) -> None:
    """Write one month's turn-usage aggregate rows in a single transaction."""
    buckets: dict[tuple[int | None, str], dict] = {}

    def bucket(user_id: int | None, dimension: str) -> dict:
        key = (user_id, dimension)
        if key not in buckets:
            buckets[key] = {
                'turn_count': 0,
                'input_tokens': 0,
                'output_tokens': 0,
                'cached_input_tokens': 0,
                'total_tokens': 0,
            }
        return buckets[key]

    for row in month_qs.values('thread__owner_id', 'metadata').iterator():
        usage = (row['metadata'] or {}).get('usage') or {}
        totals = usage.get('totals') or {}
        if not isinstance(totals, dict):
            continue
        user_id = row['thread__owner_id']
        total_bucket = bucket(user_id, '')
        total_bucket['turn_count'] += 1
        for key in (
            'input_tokens',
            'output_tokens',
            'cached_input_tokens',
            'total_tokens',
        ):
            value = totals.get(key)
            if isinstance(value, int):
                total_bucket[key] += value
        for event in usage.get('events') or []:
            if not isinstance(event, dict):
                continue
            source_bucket = bucket(user_id, str(event.get('source') or '-'))
            for key in (
                'input_tokens',
                'output_tokens',
                'cached_input_tokens',
                'total_tokens',
            ):
                value = event.get(key)
                if isinstance(value, int):
                    source_bucket[key] += value

    with transaction.atomic():
        AIUsageMonthlyAggregate.objects.bulk_create([
            AIUsageMonthlyAggregate(
                month=month,
                source='turn_usage',
                user_id=user_id,
                dimension=dimension,
                **values,
            )
            for (user_id, dimension), values in buckets.items()
        ])
    logger.info(
        'retention_usage_aggregated month=%s rows=%d', month.isoformat(), len(buckets)
    )


def _scrub_usage_rows(month_qs, *, batch_size: int) -> int:
    """Pop ``metadata['usage']`` from one month's messages, batched."""
    scrubbed = 0
    while True:
        batch = list(month_qs.only('pk', 'metadata')[:batch_size])
        if not batch:
            break
        for message in batch:
            message.metadata.pop('usage', None)
        with transaction.atomic():
            ChatMessage.objects.bulk_update(batch, ['metadata'])
        scrubbed += len(batch)
        logger.info('retention_purge_batch family=usage_detail rows=%d', len(batch))
    return scrubbed


def purge_retrieval_misses(
    *,
    days: int = RETENTION_DETAIL_DAYS,
    batch_size: int = PURGE_BATCH_SIZE,
    dry_run: bool = False,
) -> dict:
    """Purge RetrievalMiss rows (stored query text) past 90 days."""
    count = _batched_delete(
        RetrievalMiss.objects.filter(created_at__lt=_cutoff(days)),
        family='retrieval_misses',
        batch_size=batch_size,
        dry_run=dry_run,
    )
    return {'retrieval_misses': count}


def purge_request_rejections(
    *,
    days: int = RETENTION_DETAIL_DAYS,
    batch_size: int = PURGE_BATCH_SIZE,
    dry_run: bool = False,
) -> dict:
    """Purge the content-free rejection ledger past 90 days (hygiene)."""
    count = _batched_delete(
        AIRequestRejection.objects.filter(created_at__lt=_cutoff(days)),
        family='rejections',
        batch_size=batch_size,
        dry_run=dry_run,
    )
    return {'rejections': count}


def purge_quota_reservations(
    *,
    days: int = RETENTION_DETAIL_DAYS,
    batch_size: int = PURGE_BATCH_SIZE,
    dry_run: bool = False,
) -> dict:
    """Aggregate then purge settled/expired reservation mirrors past 90 days.

    RESERVED rows are never touched — the five-minute reconciliation task
    owns expiring them first.
    """
    cutoff = _cutoff(days)
    qs = AIQuotaReservation.objects.filter(
        state__in=(AIQuotaReservationState.SETTLED, AIQuotaReservationState.EXPIRED),
        created_at__lt=cutoff,
    )
    if dry_run:
        return {'quota_reservations': qs.count()}

    for month in _expired_months(qs, field='created_at', cutoff=cutoff):
        if AIUsageMonthlyAggregate.objects.filter(
            month=month, source='quota_reservation'
        ).exists():
            continue
        start, next_start = _month_bounds(month)
        buckets: dict[tuple[int | None, str], dict] = {}
        month_rows = qs.filter(
            created_at__gte=_month_start_dt(start),
            created_at__lt=_month_start_dt(next_start),
        )
        for row in month_rows.values(
            'user_id', 'purpose', 'reserved_tokens', 'settled_tokens'
        ).iterator():
            key = (row['user_id'], row['purpose'] or '')
            bucket = buckets.setdefault(
                key, {'turn_count': 0, 'reserved_tokens': 0, 'settled_tokens': 0}
            )
            bucket['turn_count'] += 1
            bucket['reserved_tokens'] += row['reserved_tokens'] or 0
            bucket['settled_tokens'] += row['settled_tokens'] or 0
        with transaction.atomic():
            AIUsageMonthlyAggregate.objects.bulk_create([
                AIUsageMonthlyAggregate(
                    month=month,
                    source='quota_reservation',
                    user_id=user_id,
                    dimension=dimension,
                    **values,
                )
                for (user_id, dimension), values in buckets.items()
            ])

    count = _batched_delete(
        qs, family='quota_reservations', batch_size=batch_size, dry_run=False
    )
    return {'quota_reservations': count}


def purge_quota_audit_events(
    *,
    days: int = RETENTION_TRANSCRIPT_DAYS,
    batch_size: int = PURGE_BATCH_SIZE,
    dry_run: bool = False,
) -> dict:
    """Purge quota management audit events past 400 days.

    400, not 90: these are content-free management audit (who assigned
    which policy) — kin to grant tombstones, not to usage detail.
    """
    count = _batched_delete(
        AIQuotaAuditEvent.objects.filter(created_at__lt=_cutoff(days)),
        family='quota_audit',
        batch_size=batch_size,
        dry_run=dry_run,
    )
    return {'quota_audit': count}


def purge_usage_aggregates(
    *,
    months: int = RETENTION_AGGREGATE_MONTHS,
    batch_size: int = PURGE_BATCH_SIZE,
    dry_run: bool = False,
) -> dict:
    """Purge the sanitized monthly aggregates past thirteen months."""
    cutoff_month = _month_bounds((timezone.now() - timedelta(days=31 * months)).date())[
        0
    ]
    count = _batched_delete(
        AIUsageMonthlyAggregate.objects.filter(month__lt=cutoff_month),
        family='usage_aggregates',
        batch_size=batch_size,
        dry_run=dry_run,
    )
    return {'usage_aggregates': count}


# ---------------------------------------------------------------------------
# ai_uploads: 24-hour TTL + orphan reconciliation + outbox
# ---------------------------------------------------------------------------


def _upload_root() -> Path:
    from django.conf import settings as django_settings

    return Path(django_settings.MEDIA_ROOT) / 'ai_uploads'


def _valid_thread_dirname(name: str) -> bool:
    return bool(name) and len(name) <= 80 and set(name) <= _THREAD_DIR_CHARSET


def enqueue_outbox(kind: str, reference: str) -> None:
    """Record one owed external deletion; duplicate pendings are one row."""
    from django.db import IntegrityError

    try:
        # Savepoint: a duplicate-pending IntegrityError must never poison
        # an enclosing transaction (the thread purge calls this inside one).
        with transaction.atomic():
            AIRetentionOutbox.objects.create(
                kind=kind, reference=reference, next_attempt_at=timezone.now()
            )
    except IntegrityError:
        pass  # A pending row for this target already exists.


def _try_remove_upload_dir(thread_id: str) -> bool:
    """Best-effort inline removal; the outbox row is the retry backstop."""
    try:
        target = _upload_root() / thread_id
        if target.is_dir():
            shutil.rmtree(target)
        AIRetentionOutbox.objects.filter(
            kind='upload_dir', reference=thread_id, state='pending'
        ).update(state='done', completed_at=timezone.now())
        return True
    except OSError:
        return False


def sweep_upload_dirs(
    *, ttl_hours: int = UPLOAD_TTL_HOURS, dry_run: bool = False
) -> dict:
    """Enforce the 24-hour upload TTL and reconcile orphaned thread dirs.

    Runs ungated (the ``sweep_attachment_rag`` precedent): a file for a
    deleted thread must not wait on a feature flag. Per-file failures are
    counted, value-free, and never stop the sweep.
    """
    root = _upload_root()
    cutoff = timezone.now().timestamp() - ttl_hours * 3600
    removed = kept = failures = orphans = invalid = 0
    if not root.is_dir():
        return {'removed': 0, 'kept': 0, 'failures': 0, 'orphans': 0, 'invalid': 0}

    live_ids = None
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if not _valid_thread_dirname(entry.name):
            invalid += 1
            if not dry_run:
                enqueue_outbox('upload_dir', entry.name[:255])
            continue
        for file in sorted(entry.iterdir()):
            try:
                if file.is_file() and file.stat().st_mtime < cutoff:
                    if not dry_run:
                        file.unlink()
                    removed += 1
                else:
                    kept += 1
            except OSError:
                failures += 1
        if live_ids is None:
            live_ids = set(ChatThread.objects.values_list('pk', flat=True))
        if entry.name not in live_ids:
            orphans += 1
            if not dry_run:
                enqueue_outbox('upload_dir', entry.name)
        elif not dry_run:
            try:
                entry.rmdir()  # Removes only now-empty directories.
            except OSError:
                pass
    if removed or orphans or failures:
        logger.info(
            'retention_upload_sweep removed=%d kept=%d orphans=%d '
            'invalid=%d failures=%d dry_run=%s',
            removed,
            kept,
            orphans,
            invalid,
            failures,
            dry_run,
        )
    return {
        'removed': removed,
        'kept': kept,
        'failures': failures,
        'orphans': orphans,
        'invalid': invalid,
    }


def process_retention_outbox(*, batch_size: int = 100) -> dict:
    """Drive owed external deletions to completion with capped backoff.

    A missing target is success (idempotent). At ``OUTBOX_MAX_ATTEMPTS``
    the row goes ``failed_permanent`` and is surfaced as the failure
    metric in the operations report.
    """
    now = timezone.now()
    done = retried = failed = 0
    rows = list(
        AIRetentionOutbox.objects.filter(
            state='pending', next_attempt_at__lte=now
        ).order_by('next_attempt_at')[:batch_size]
    )
    root = _upload_root()
    for row in rows:
        succeeded = False
        error_code = ''
        if row.kind == 'upload_dir':
            target = root / row.reference
            # Containment: the reference must resolve to a direct child of
            # the upload root (the ``resolve_upload_path`` discipline).
            if not _valid_thread_dirname(row.reference) or target.parent != root:
                # Invalid references (also enqueued by the sweep for
                # malformed dirnames) get one careful manual look, not rmtree.
                error_code = 'invalid_reference'
            else:
                try:
                    if target.is_dir():
                        shutil.rmtree(target)
                    succeeded = True
                except OSError as exc:
                    error_code = type(exc).__name__
        else:
            error_code = 'unknown_kind'

        if succeeded:
            row.state = 'done'
            row.completed_at = timezone.now()
            row.save(update_fields=['state', 'completed_at', 'updated_at'])
            done += 1
            continue
        row.attempts += 1
        row.last_error_code = error_code[:64]
        if row.attempts >= OUTBOX_MAX_ATTEMPTS:
            row.state = 'failed_permanent'
            failed += 1
            logger.error(
                'retention_outbox_failed kind=%s reference=%s attempts=%d code=%s',
                row.kind,
                row.reference,
                row.attempts,
                error_code,
            )
        else:
            backoff = min(2**row.attempts * 60, 86400)
            row.next_attempt_at = timezone.now() + timedelta(seconds=backoff)
            retried += 1
        row.save(
            update_fields=[
                'state',
                'attempts',
                'last_error_code',
                'next_attempt_at',
                'updated_at',
            ]
        )
    return {'done': done, 'retried': retried, 'failed_permanent': failed}


# ---------------------------------------------------------------------------
# Orchestration and status
# ---------------------------------------------------------------------------

#: Family name -> callable(dry_run=..., batch_size-defaulted). Order is the
#: execution order; content-bearing families first.
FAMILIES = {
    'threads': purge_expired_threads,
    'voice': purge_expired_voice,
    'proposals': purge_terminal_proposals,
    'usage_detail': scrub_usage_detail,
    'retrieval_misses': purge_retrieval_misses,
    'rejections': purge_request_rejections,
    'quota_reservations': purge_quota_reservations,
    'quota_audit': purge_quota_audit_events,
    'usage_aggregates': purge_usage_aggregates,
    'tombstones': purge_tombstones,
    'uploads': sweep_upload_dirs,
    'outbox': lambda dry_run=False: (
        {'skipped': 'dry_run'} if dry_run else process_retention_outbox()
    ),
}


def run_all(*, dry_run: bool = False, families: set[str] | None = None) -> dict:
    """Run every (or the named) retention families; one failure never stops the rest."""
    report: dict = {
        'started_at': timezone.now().isoformat(),
        'dry_run': dry_run,
        'families': {},
        'errors': {},
    }
    for name, func in FAMILIES.items():
        if families is not None and name not in families:
            continue
        try:
            report['families'][name] = func(dry_run=dry_run)
        except Exception as exc:
            report['errors'][name] = type(exc).__name__
            logger.exception('retention_family_failed family=%s', name)
    report['finished_at'] = timezone.now().isoformat()
    if not dry_run:
        _write_last_run(report)
    return report


def _write_last_run(report: dict) -> None:
    """Persist the run receipt for the operations report (best-effort)."""
    try:
        from common.models import InvenTreeSetting

        InvenTreeSetting.set_setting(LAST_RUN_SETTING, json.dumps(report), None)
    except Exception:
        logger.exception('retention_receipt_write_failed')


def last_run() -> dict | None:
    """The most recent real run's report, or None."""
    try:
        from common.models import InvenTreeSetting

        raw = InvenTreeSetting.get_setting(LAST_RUN_SETTING, '')
        return json.loads(raw) if raw else None
    except Exception:
        return None


def retention_status() -> dict:
    """Cheap read-only status for the operations report and gate evidence."""
    cutoff_transcript = _cutoff(RETENTION_TRANSCRIPT_DAYS)
    cutoff_detail = _cutoff(RETENTION_DETAIL_DAYS)
    receipt = last_run()
    last_run_age_days = None
    if receipt and receipt.get('finished_at'):
        try:
            finished = datetime.fromisoformat(receipt['finished_at'])
            last_run_age_days = round(
                (timezone.now() - finished).total_seconds() / 86400, 2
            )
        except ValueError:
            pass

    oldest_upload_age_hours = None
    root = _upload_root()
    if root.is_dir():
        mtimes = [
            file.stat().st_mtime
            for entry in root.iterdir()
            if entry.is_dir()
            for file in entry.iterdir()
            if file.is_file()
        ]
        if mtimes:
            oldest_upload_age_hours = round(
                (timezone.now().timestamp() - min(mtimes)) / 3600, 2
            )

    return {
        'last_run_age_days': last_run_age_days,
        'last_run': receipt,
        'backlog': {
            'threads': ChatThread.objects.filter(
                updated_at__lt=cutoff_transcript
            ).count(),
            'usage_messages': ChatMessage.objects.filter(
                metadata__has_key='usage', created_at__lt=cutoff_detail
            ).count(),
            'retrieval_misses': RetrievalMiss.objects.filter(
                created_at__lt=cutoff_detail
            ).count(),
            'rejections': AIRequestRejection.objects.filter(
                created_at__lt=cutoff_detail
            ).count(),
        },
        'outbox': {
            'pending': AIRetentionOutbox.objects.filter(state='pending').count(),
            'failed_permanent': AIRetentionOutbox.objects.filter(
                state='failed_permanent'
            ).count(),
        },
        'oldest_upload_age_hours': oldest_upload_age_hours,
    }
