"""Durable closeout fan-out ledger and post-commit executors (Feature #15).

Effect intent rows are created inside the completion transaction; execution
happens strictly after commit off the durable ledger. Definitive failure,
retryable failure, and unknown-after-dispatch are distinct states, and an
ambiguous provider outcome is never blind-replayed (FR-CO-011). A failed
fan-out never rolls back a valid completion.
"""

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from tasks.closeout_models import (
    CloseoutEffect,
    CloseoutEffectStatus,
    CloseoutLearningDraft,
)
from tasks.permissions import require_permission
from tasks.services.work_orders import WorkOrderCommandError

VERIFY_CLOSEOUT = 'tasks.verify_closeout'

_LEASE_SECONDS = 300
_BACKOFF_BASE_SECONDS = 60
_RECONCILIATION_GRACE = timedelta(hours=1)


class EffectNotRetryable(WorkOrderCommandError):  # noqa: N818 - established command error name
    """The effect is not in a state an operator may retry."""

    code = 'EFFECT_NOT_RETRYABLE'


class EffectOutcomeUnknown(Exception):  # noqa: N818 - established command error name
    """The provider may have accepted the dispatch; do not blind-replay."""


class EffectRetryableError(Exception):
    """Definitive no-effect failure that is safe to retry."""


def _retry_limit() -> int:
    return int(getattr(settings, 'AIMMS_CLOSEOUT_EFFECT_RETRY_LIMIT', 8))


def _execute_notification(effect: CloseoutEffect) -> str:
    """Notify the people attached to the completed work order."""
    from common.notifications import trigger_notification

    closeout = effect.closeout
    work_order = closeout.work_order
    targets = [
        user
        for user in {work_order.assigned_to, work_order.requested_by}
        if user is not None
    ]
    if not targets:
        return 'no notification targets configured'
    trigger_notification(
        work_order,
        'tasks.closeout_completed',
        targets=targets,
        context={
            'name': f'Work order {work_order.reference or work_order.pk} completed',
            'message': 'A structured closeout was recorded for this work order.',
        },
    )
    return f'notified:{len(targets)}'


def _execute_memory_draft(effect: CloseoutEffect) -> str:
    """Create the governed, draft-only learning candidate (FR-CO-015)."""
    closeout = effect.closeout
    draft, _created = CloseoutLearningDraft.objects.get_or_create(
        closeout=closeout,
        draft_type='problem_solution',
        defaults={
            'payload': {
                'cause': closeout.cause,
                'action': closeout.action,
                'result': closeout.result,
                'verification_summary': closeout.verification_summary,
                'machine': (
                    closeout.work_order.machine.name
                    if closeout.work_order.machine_id
                    else ''
                ),
            },
            'provenance': {
                'closeout_id': closeout.pk,
                'content_hash': closeout.content_hash,
                'effect_key': effect.effect_key,
            },
        },
    )
    return f'draft:{draft.pk}'


EFFECT_EXECUTORS = {
    'notification': _execute_notification,
    'memory_draft': _execute_memory_draft,
}


def _claim_effects(*, closeout_id=None, lease_owner: str) -> list[int]:
    """Atomically lease due pending/retryable rows for this worker."""
    now = timezone.now()
    queryset = CloseoutEffect.objects.filter(
        status__in=[CloseoutEffectStatus.PENDING, CloseoutEffectStatus.RETRYABLE]
    ).filter(models_q_due(now))
    if closeout_id is not None:
        queryset = queryset.filter(closeout_id=closeout_id)
    claimed = []
    for effect_id in list(queryset.values_list('pk', flat=True)):
        updated = CloseoutEffect.objects.filter(
            pk=effect_id,
            status__in=[CloseoutEffectStatus.PENDING, CloseoutEffectStatus.RETRYABLE],
        ).update(
            status=CloseoutEffectStatus.LEASED,
            lease_owner=lease_owner,
            lease_expires_at=now + timedelta(seconds=_LEASE_SECONDS),
        )
        if updated:
            claimed.append(effect_id)
    return claimed


def models_q_due(now):
    """Rows whose retry window has arrived (or that never had one)."""
    from django.db.models import Q

    return Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now)


def _finish(effect, **fields):
    for name, value in fields.items():
        setattr(effect, name, value)
    effect.save(update_fields=[*fields.keys()])


def _execute_one(effect_id: int, lease_owner: str):
    now = timezone.now()
    with transaction.atomic():
        effect = CloseoutEffect.objects.select_for_update().get(pk=effect_id)
        if (
            effect.status != CloseoutEffectStatus.LEASED
            or effect.lease_owner != lease_owner
        ):
            return
        effect.status = CloseoutEffectStatus.DISPATCHING
        effect.attempts += 1
        effect.save(update_fields=['status', 'attempts'])

    executor = EFFECT_EXECUTORS.get(effect.effect_type)
    try:
        if executor is None:
            raise EffectRetryableError(
                f'No executor registered for {effect.effect_type!r}'
            )
        result_reference = executor(effect)
    except EffectOutcomeUnknown as exc:
        _finish(
            effect,
            status=CloseoutEffectStatus.OUTCOME_UNKNOWN,
            last_error=str(exc)[:2000],
            reconciliation_due_at=now + _RECONCILIATION_GRACE,
            lease_owner='',
            lease_expires_at=None,
        )
        return
    except Exception as exc:
        if effect.attempts >= _retry_limit():
            _finish(
                effect,
                status=CloseoutEffectStatus.FAILED,
                last_error=str(exc)[:2000],
                resolved_at=now,
                lease_owner='',
                lease_expires_at=None,
            )
        else:
            backoff = _BACKOFF_BASE_SECONDS * (2 ** (effect.attempts - 1))
            _finish(
                effect,
                status=CloseoutEffectStatus.RETRYABLE,
                last_error=str(exc)[:2000],
                next_retry_at=now + timedelta(seconds=backoff),
                lease_owner='',
                lease_expires_at=None,
            )
        return
    _finish(
        effect,
        status=CloseoutEffectStatus.SUCCEEDED,
        result_reference=str(result_reference)[:255],
        resolved_at=now,
        last_error='',
        lease_owner='',
        lease_expires_at=None,
    )


def execute_pending_effects(*, closeout_id=None) -> int:
    """Execute due effect intents idempotently; returns the processed count.

    Safe to call from ``transaction.on_commit`` and from the scheduled
    sweeper: the ledger, not the enqueue, is the source of truth.
    """
    if not getattr(settings, 'AIMMS_CLOSEOUT_EFFECTS_ENABLED', False):
        return 0
    lease_owner = f'closeout-effects:{uuid.uuid4()}'
    claimed = _claim_effects(closeout_id=closeout_id, lease_owner=lease_owner)
    for effect_id in claimed:
        _execute_one(effect_id, lease_owner)
    return len(claimed)


def release_expired_leases() -> int:
    """Return crashed workers' leases to the pending pool."""
    now = timezone.now()
    return CloseoutEffect.objects.filter(
        status__in=[CloseoutEffectStatus.LEASED, CloseoutEffectStatus.DISPATCHING],
        lease_expires_at__lt=now,
    ).update(
        status=CloseoutEffectStatus.RETRYABLE,
        lease_owner='',
        lease_expires_at=None,
        next_retry_at=now,
    )


def sweep_closeout_effects() -> int:
    """Scheduled sweep: recover leases, then execute everything due."""
    release_expired_leases()
    return execute_pending_effects()


@transaction.atomic
def retry_effect(*, effect_id, actor):
    """Authorized manual retry of a retryable or failed effect."""
    require_permission(actor, VERIFY_CLOSEOUT)
    effect = CloseoutEffect.objects.select_for_update().filter(pk=effect_id).first()
    if effect is None:
        raise EffectNotRetryable('Effect does not exist')
    if effect.status not in {
        CloseoutEffectStatus.RETRYABLE,
        CloseoutEffectStatus.FAILED,
    }:
        raise EffectNotRetryable(f'A {effect.status} effect cannot be manually retried')
    effect.status = CloseoutEffectStatus.PENDING
    effect.next_retry_at = None
    effect.resolved_at = None
    effect.save(update_fields=['status', 'next_retry_at', 'resolved_at'])
    return effect


@transaction.atomic
def abandon_effect(*, effect_id, actor, reason):
    """Reasoned operator decision to stop pursuing an effect."""
    require_permission(actor, VERIFY_CLOSEOUT)
    if not (reason or '').strip():
        raise EffectNotRetryable('Abandoning an effect requires a reason')
    effect = CloseoutEffect.objects.select_for_update().filter(pk=effect_id).first()
    if effect is None:
        raise EffectNotRetryable('Effect does not exist')
    if effect.status in {
        CloseoutEffectStatus.SUCCEEDED,
        CloseoutEffectStatus.ABANDONED,
    }:
        raise EffectNotRetryable(f'A {effect.status} effect cannot be abandoned')
    effect.status = CloseoutEffectStatus.ABANDONED
    effect.last_error = f'abandoned: {reason}'[:2000]
    effect.resolved_at = timezone.now()
    effect.save(update_fields=['status', 'last_error', 'resolved_at'])
    return effect


@transaction.atomic
def resolve_unknown_outcome(*, effect_id, actor, succeeded: bool, evidence: str):
    """Reconcile an ``outcome_unknown`` effect with provider-side proof."""
    require_permission(actor, VERIFY_CLOSEOUT)
    if not (evidence or '').strip():
        raise EffectNotRetryable('Reconciliation requires provider-side evidence')
    effect = CloseoutEffect.objects.select_for_update().filter(pk=effect_id).first()
    if effect is None or effect.status != CloseoutEffectStatus.OUTCOME_UNKNOWN:
        raise EffectNotRetryable('Only an outcome-unknown effect can be reconciled')
    now = timezone.now()
    if succeeded:
        effect.status = CloseoutEffectStatus.SUCCEEDED
        effect.result_reference = f'reconciled: {evidence}'[:255]
        effect.resolved_at = now
    else:
        effect.status = CloseoutEffectStatus.RETRYABLE
        effect.last_error = f'reconciled absent: {evidence}'[:2000]
        effect.next_retry_at = now
    effect.save(
        update_fields=[
            'status',
            'result_reference',
            'last_error',
            'next_retry_at',
            'resolved_at',
        ]
    )
    return effect
