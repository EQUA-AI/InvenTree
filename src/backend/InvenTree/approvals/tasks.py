"""Background tasks for the AI Agent Approval Queue.

Implements:
- Expiry job (every 60s) — Section 3.1
- Reconciliation job (every 2 min) — Section 11.5
- Retention purge job (daily) — Section 15
"""

from django.db import transaction
from django.utils import timezone

import structlog

from InvenTree.tasks import ScheduledTask, scheduled_task

logger = structlog.get_logger('approvals.tasks')


# ---------------------------------------------------------------------------
# Expiry job — Section 3.1
# ---------------------------------------------------------------------------


@scheduled_task(ScheduledTask.MINUTES, 1)
def check_approval_expiry():
    """Transition expired approvals to 'expired' status.

    Runs every 60 seconds. Processes in batches of 100.
    Only processes non-terminal approvals past their expires_at.
    """
    from .models import (
        TERMINAL_STATUSES,
        Approval,
        ApprovalStatus,
        is_expiry_job_enabled,
    )

    if not is_expiry_job_enabled():
        return

    now = timezone.now()
    batch_size = 100

    # Find non-terminal approvals that have expired
    # Use select_for_update with skip_locked for concurrent safety
    expired_qs = (
        Approval.objects
        .filter(expires_at__isnull=False, expires_at__lte=now)
        .exclude(status__in=list(TERMINAL_STATUSES))
        .order_by('expires_at')[:batch_size]
    )

    count = 0
    for approval in expired_qs:
        try:
            with transaction.atomic():
                # Re-fetch with row lock
                locked = (
                    Approval.objects
                    .select_for_update(skip_locked=True)
                    .filter(pk=approval.pk)
                    .first()
                )

                if not locked:
                    continue

                # Skip if already terminal
                if locked.status in TERMINAL_STATUSES:
                    continue

                # T-4: Use transition_to() for proper event logging
                if not locked.can_transition_to(ApprovalStatus.EXPIRED):
                    # approved/executing can't transition to expired;
                    # the reconciliation job handles those.
                    logger.debug(
                        'expiry_skip_invalid_transition',
                        approval_id=str(locked.pk),
                        status=locked.status,
                    )
                    continue

                old_status = locked.status
                locked.transition_to(
                    ApprovalStatus.EXPIRED,
                    actor_user=None,
                    event_payload={
                        'from_status': old_status,
                        'expires_at': (
                            locked.expires_at.isoformat() if locked.expires_at else None
                        ),
                    },
                )

                count += 1

                logger.info(
                    'approval_expired',
                    approval_id=str(locked.pk),
                    from_status=old_status,
                    agent_run_id=locked.agent_run_id,
                )

        except Exception:
            logger.exception('expiry_task_error', approval_id=str(approval.pk))

    if count > 0:
        logger.info('approval_expiry_batch_complete', expired_count=count)


# ---------------------------------------------------------------------------
# Reconciliation job — Section 11.5
# ---------------------------------------------------------------------------


@scheduled_task(ScheduledTask.MINUTES, 2)
def reconcile_approvals():
    """Detect and recover stuck approvals.

    Checks:
    - Stuck in 'approved' (> 5 min) → retry agent resume
    - Stuck in 'executing' (> 30 min) → transition to failed
    - Expired locks → clear lock fields
    - Orphaned pending → mark as failed
    """
    from django.db.models import Q

    from .models import (
        Approval,
        ApprovalEvent,
        ApprovalStatus,
        EventType,
        get_execution_stuck_threshold_seconds,
        get_resume_stuck_threshold_seconds,
        is_approval_queue_enabled,
        is_expiry_job_enabled,
    )

    if not is_approval_queue_enabled():
        return

    now = timezone.now()

    # --- Stuck in 'approved' ---
    resume_threshold_secs = get_resume_stuck_threshold_seconds()
    resume_threshold = now - timezone.timedelta(seconds=resume_threshold_secs)
    stuck_approved = Approval.objects.filter(
        status=ApprovalStatus.APPROVED, updated_at__lt=resume_threshold
    )

    for approval in stuck_approved:
        try:
            logger.warning(
                'reconciliation_stuck_approved',
                approval_id=str(approval.pk),
                agent_run_id=approval.agent_run_id,
                updated_at=approval.updated_at.isoformat(),
            )
            # TODO: Retry agent resume with idempotency (Phase 4)
            # For now, just log the stuck approval
        except Exception:
            logger.exception(
                'reconciliation_error',
                approval_id=str(approval.pk),
                check='stuck_approved',
            )

    # --- Stuck in 'executing' ---
    execution_threshold_secs = get_execution_stuck_threshold_seconds()
    execution_threshold = now - timezone.timedelta(seconds=execution_threshold_secs)
    stuck_executing = Approval.objects.filter(
        status=ApprovalStatus.EXECUTING, updated_at__lt=execution_threshold
    )

    for approval in stuck_executing:
        try:
            with transaction.atomic():
                locked = Approval.objects.select_for_update().get(pk=approval.pk)
                if locked.status != ApprovalStatus.EXECUTING:
                    continue

                # Emit resume_failed event
                ApprovalEvent.objects.create(
                    approval=locked,
                    event_type=EventType.RESUME_FAILED,
                    actor_user=None,
                    event_payload={
                        'reason': 'execution_stuck',
                        'threshold_seconds': execution_threshold_secs,
                        'stuck_since': locked.updated_at.isoformat(),
                    },
                )

                # T-3: Use transition_to for proper FSM handling
                locked.execution_error = {
                    'reason': 'execution_stuck',
                    'detail': 'Execution did not complete within threshold',
                }
                locked.transition_to(
                    ApprovalStatus.FAILED,
                    actor_user=None,
                    event_payload={
                        'from_status': ApprovalStatus.EXECUTING,
                        'reason': 'execution_stuck',
                    },
                    extra_update_fields=['execution_error'],
                )

                logger.warning(
                    'reconciliation_stuck_executing_failed', approval_id=str(locked.pk)
                )

        except Exception:
            logger.exception(
                'reconciliation_error',
                approval_id=str(approval.pk),
                check='stuck_executing',
            )

    # --- Expired locks cleanup ---
    expired_locks = Approval.objects.filter(
        modification_lock_user__isnull=False, modification_lock_expires_at__lt=now
    )

    for approval in expired_locks:
        try:
            with transaction.atomic():
                locked = Approval.objects.select_for_update().get(pk=approval.pk)
                if (
                    locked.modification_lock_user_id
                    and locked.modification_lock_expires_at
                    and locked.modification_lock_expires_at < now
                ):
                    locked.modification_lock_user = None
                    locked.modification_lock_acquired_at = None
                    locked.modification_lock_expires_at = None
                    locked.save(
                        update_fields=[
                            'modification_lock_user',
                            'modification_lock_acquired_at',
                            'modification_lock_expires_at',
                            'updated_at',
                        ]
                    )

                    # T-5: Emit lock_released event for audit
                    ApprovalEvent.objects.create(
                        approval=locked,
                        event_type=EventType.LOCK_RELEASED,
                        actor_user=None,
                        event_payload={'reason': 'expired_auto_cleanup'},
                    )

                    logger.info(
                        'reconciliation_lock_cleared', approval_id=str(locked.pk)
                    )
        except Exception:
            logger.exception(
                'reconciliation_error',
                approval_id=str(approval.pk),
                check='expired_locks',
            )

    # --- Orphaned pending (no corresponding agent run) ---
    # Phase 1: Flag pending approvals older than 24h as potentially orphaned.
    # Full agent-run liveness check deferred to Phase 4.
    # T-7: Respect is_expiry_job_enabled flag for consistency
    # T-2: Respect expires_at — don't kill approvals with valid future expiry
    if is_expiry_job_enabled():
        orphan_threshold = now - timezone.timedelta(hours=24)
        orphaned_pending = Approval.objects.filter(
            status=ApprovalStatus.PENDING, created_at__lt=orphan_threshold
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__lte=now))

        for approval in orphaned_pending:
            try:
                with transaction.atomic():
                    locked = Approval.objects.select_for_update().get(pk=approval.pk)
                    if locked.status != ApprovalStatus.PENDING:
                        continue

                    # Emit resume_failed event before transitioning
                    ApprovalEvent.objects.create(
                        approval=locked,
                        event_type=EventType.RESUME_FAILED,
                        actor_user=None,
                        event_payload={
                            'reason': 'agent_orphaned',
                            'detail': (
                                'Pending approval with no activity for 24+ hours'
                            ),
                            'created_at': locked.created_at.isoformat(),
                        },
                    )

                    # Use transition_to for proper FSM handling
                    locked.execution_error = {
                        'reason': 'agent_orphaned',
                        'detail': (
                            'Approval was pending for over 24 hours '
                            'with no agent activity'
                        ),
                    }
                    locked.transition_to(
                        ApprovalStatus.FAILED,
                        actor_user=None,
                        event_payload={
                            'from_status': ApprovalStatus.PENDING,
                            'reason': 'agent_orphaned',
                        },
                        extra_update_fields=['execution_error'],
                    )

                    logger.warning(
                        'reconciliation_orphaned_pending',
                        approval_id=str(locked.pk),
                        agent_run_id=locked.agent_run_id,
                        reconciliation_action='orphaned_pending_to_failed',
                    )

            except Exception:
                logger.exception(
                    'reconciliation_error',
                    approval_id=str(approval.pk),
                    check='orphaned_pending',
                )

    logger.info('reconciliation_complete')


# ---------------------------------------------------------------------------
# Retention purge job — Section 15
# ---------------------------------------------------------------------------


@scheduled_task(ScheduledTask.DAILY)
def purge_expired_approvals():
    """Purge terminal approvals older than the retention period.

    Deletes associated events, revisions, and executed_effects.
    Gated behind APPROVAL_RETENTION_PURGE_ENABLED.
    """
    from .models import (
        TERMINAL_STATUSES,
        Approval,
        get_retention_days,
        is_retention_purge_enabled,
    )

    if not is_retention_purge_enabled():
        return

    retention_days = get_retention_days()
    cutoff = timezone.now() - timezone.timedelta(days=retention_days)
    batch_size = 500

    total_purged = 0

    while True:
        # Find batch of purgeable approvals
        purgeable_ids = list(
            Approval.objects.filter(
                status__in=list(TERMINAL_STATUSES),
                resolved_at__isnull=False,
                resolved_at__lt=cutoff,
            ).values_list('pk', flat=True)[:batch_size]
        )

        if not purgeable_ids:
            break

        with transaction.atomic():
            # Delete cascades will handle events, revisions, executed_effects
            deleted_count, _ = Approval.objects.filter(pk__in=purgeable_ids).delete()

            total_purged += deleted_count

        logger.info(
            'retention_purge_batch',
            batch_count=deleted_count,
            total_purged=total_purged,
        )

    if total_purged > 0:
        logger.info(
            'retention_purge_complete',
            total_purged=total_purged,
            retention_days=retention_days,
        )
