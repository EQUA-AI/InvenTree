"""Transactional command services for maintenance work orders."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from tasks.models import (
    KanbanCard,
    WorkOrderCommand,
    WorkOrderEvent,
    WorkOrderLifecycle,
)
from tasks.permissions import (
    ASSIGN_WORKORDER,
    EXECUTE_WORKORDER,
    TRANSITION_WORKORDER,
    require_permission,
    transition_permission,
)
from tasks.scope import ScopeError, require_work_order_scope
from tasks.services.finalization import PacketFinalization, is_packet_finalization
from tasks.services.readiness import (
    PACKET_OWNS_LIFECYCLE,
    WorkOrderReadiness,
    evaluate_work_order_readiness,
)


class WorkOrderCommandError(Exception):
    """Base exception for rejected work-order commands."""


class IdempotencyConflict(WorkOrderCommandError):  # noqa: N818 - established command error name
    """An idempotency key was reused for a different canonical request."""

    code = 'IDEMPOTENCY_CONFLICT'


class StaleVersion(WorkOrderCommandError):  # noqa: N818 - established command error name
    """The caller's expected lifecycle version is no longer current."""

    code = 'STALE_VERSION'


class IllegalTransition(WorkOrderCommandError):  # noqa: N818 - established command error name
    """The requested lifecycle edge is not in the work-order state machine."""

    code = 'ILLEGAL_TRANSITION'


class CommandConflict(WorkOrderCommandError):  # noqa: N818 - established command error name
    """Another aggregate owns the requested operation."""

    def __init__(self, code: str):
        """Carry the owning aggregate's stable conflict code."""
        self.code = code
        super().__init__(code)


class WorkOrderScopeError(WorkOrderCommandError):
    """The actor/work-order scope could not be proven equal."""

    code = 'SCOPE_MISMATCH'


class ReadinessBlocked(WorkOrderCommandError):  # noqa: N818 - established command error name
    """The unified readiness decision contains blocking results."""

    code = 'READINESS_BLOCKED'

    def __init__(self, readiness: WorkOrderReadiness):
        """Attach the full readiness decision for error rendering."""
        self.readiness = readiness
        super().__init__(', '.join(blocker.code for blocker in readiness.blockers))


@dataclass(frozen=True)
class CommandResult:
    """Stable result returned for both first execution and exact replay."""

    work_order_id: int
    event_id: int
    command: str
    lifecycle_status: str
    lifecycle_version: int
    correlation_id: uuid.UUID
    idempotency_key: str
    metadata: dict[str, Any] | None = None


LEGAL_TRANSITIONS = {
    WorkOrderLifecycle.DRAFT: {WorkOrderLifecycle.PLANNED, WorkOrderLifecycle.CANCELED},
    WorkOrderLifecycle.PLANNED: {WorkOrderLifecycle.READY, WorkOrderLifecycle.CANCELED},
    WorkOrderLifecycle.READY: {
        WorkOrderLifecycle.IN_PROGRESS,
        WorkOrderLifecycle.PLANNED,
        WorkOrderLifecycle.CANCELED,
    },
    WorkOrderLifecycle.IN_PROGRESS: {
        WorkOrderLifecycle.ON_HOLD,
        WorkOrderLifecycle.VERIFYING,
    },
    WorkOrderLifecycle.ON_HOLD: {
        WorkOrderLifecycle.IN_PROGRESS,
        WorkOrderLifecycle.CANCELED,
    },
    WorkOrderLifecycle.VERIFYING: {
        WorkOrderLifecycle.IN_PROGRESS,
        WorkOrderLifecycle.COMPLETED,
    },
    WorkOrderLifecycle.COMPLETED: set(),
    WorkOrderLifecycle.CANCELED: set(),
}


def _canonical_hash(command: str, actor, payload: dict[str, Any]) -> str:
    canonical = {
        'command': command,
        'actor_id': getattr(actor, 'pk', None),
        'payload': payload,
    }
    return hashlib.sha256(
        json.dumps(
            canonical, sort_keys=True, separators=(',', ':'), default=str
        ).encode()
    ).hexdigest()


def _locked_work_order(work_order_id: int) -> KanbanCard:
    # Lock only the KanbanCard base row (of=('self',)); machine/assigned_to are
    # nullable FKs, and PostgreSQL rejects FOR UPDATE across their outer joins.
    return (
        KanbanCard.objects
        .select_for_update(of=('self',))
        .select_related('machine', 'assigned_to')
        .get(pk=work_order_id)
    )


def _replay_or_none(work_order, idempotency_key, request_hash):
    prior = WorkOrderCommand.objects.filter(
        work_order=work_order, idempotency_key=idempotency_key
    ).first()
    if prior is None:
        return None
    if prior.request_hash != request_hash:
        raise IdempotencyConflict('Idempotency key was reused with a different request')
    try:
        event_id = int(prior.result_ref)
        event = WorkOrderEvent.objects.get(pk=event_id, work_order=work_order)
    except (TypeError, ValueError, WorkOrderEvent.DoesNotExist) as exc:
        raise WorkOrderCommandError('Stored command result cannot be replayed') from exc
    return CommandResult(
        work_order_id=work_order.pk,
        event_id=event.pk,
        command=prior.command,
        lifecycle_status=event.metadata.get('lifecycle_status', event.to_status),
        lifecycle_version=event.metadata['lifecycle_version'],
        correlation_id=prior.correlation_id,
        idempotency_key=idempotency_key,
        metadata=event.metadata.get('result_metadata', {}),
    )


def _require_version(work_order, expected_version):
    if work_order.lifecycle_version != expected_version:
        raise StaleVersion(
            f'Expected version {expected_version}, current version '
            f'{work_order.lifecycle_version}'
        )


def _require_scope(actor, work_order):
    try:
        require_work_order_scope(actor, work_order)
    except ScopeError as exc:
        error = WorkOrderScopeError(str(exc))
        if 'unresolved' in str(exc).lower():
            error.code = 'SCOPE_UNRESOLVED'
        raise error from exc


def _require_no_packet(work_order):
    # ``repair_packet`` is the confirmed RepairPacket.work_order related_name.
    if hasattr(work_order, 'repair_packet'):
        raise CommandConflict(PACKET_OWNS_LIFECYCLE)


def _append_result(
    *,
    work_order,
    actor,
    command,
    event_type,
    from_status,
    to_status,
    reason,
    correlation_id,
    idempotency_key,
    request_hash,
    result_metadata=None,
):
    metadata = {
        'lifecycle_status': work_order.lifecycle_status,
        'lifecycle_version': work_order.lifecycle_version,
        'result_metadata': result_metadata or {},
    }
    event = WorkOrderEvent.objects.create(
        work_order=work_order,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        reason=reason,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        metadata=metadata,
    )
    WorkOrderCommand.objects.create(
        work_order=work_order,
        command=command,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        request_hash=request_hash,
        status='succeeded',
        result_ref=str(event.pk),
    )
    return CommandResult(
        work_order_id=work_order.pk,
        event_id=event.pk,
        command=command,
        lifecycle_status=work_order.lifecycle_status,
        lifecycle_version=work_order.lifecycle_version,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        metadata=result_metadata or {},
    )


def _action_for_transition(from_status, to_status):
    mapping = {
        WorkOrderLifecycle.PLANNED: (
            'readiness_drift' if from_status == WorkOrderLifecycle.READY else 'plan'
        ),
        WorkOrderLifecycle.READY: 'mark_ready',
        WorkOrderLifecycle.IN_PROGRESS: (
            'resume' if from_status == WorkOrderLifecycle.ON_HOLD else 'start'
        ),
        WorkOrderLifecycle.ON_HOLD: 'hold',
        WorkOrderLifecycle.VERIFYING: 'verify',
        WorkOrderLifecycle.COMPLETED: 'complete',
        WorkOrderLifecycle.CANCELED: 'cancel',
    }
    return mapping[to_status]


def _validate_transition(from_status, to_status):
    if to_status not in WorkOrderLifecycle.values:
        raise IllegalTransition(f'Unknown lifecycle state: {to_status}')
    if to_status not in LEGAL_TRANSITIONS.get(from_status, set()):
        raise IllegalTransition(
            f'Illegal lifecycle transition: {from_status} -> {to_status}'
        )


def _transition_locked(
    *,
    work_order,
    to_status,
    actor,
    expected_version,
    idempotency_key,
    reason,
    correlation_id,
    command,
    required_permission=None,
    packet_finalization=None,
):
    payload = {
        'work_order_id': work_order.pk,
        'to_status': to_status,
        'expected_version': expected_version,
        'reason': reason,
    }
    request_hash = _canonical_hash(command, actor, payload)
    replay = _replay_or_none(work_order, idempotency_key, request_hash)
    if replay:
        return replay

    _require_version(work_order, expected_version)
    require_permission(actor, required_permission or transition_permission(to_status))
    _require_scope(actor, work_order)
    # A packet's work order transitions only through the packet's own service,
    # which drives both aggregates together. ``packet_finalization`` is that
    # service identifying itself with a token only it can mint; it suppresses
    # this single check and nothing else, so safety, parts and readiness still
    # decide the outcome.
    if not is_packet_finalization(packet_finalization, work_order):
        _require_no_packet(work_order)
    from_status = work_order.lifecycle_status
    _validate_transition(from_status, to_status)
    readiness = evaluate_work_order_readiness(
        work_order,
        action=_action_for_transition(from_status, to_status),
        actor=actor,
        expected_version=expected_version,
        packet_finalization=packet_finalization,
    )
    if not readiness.ready:
        raise ReadinessBlocked(readiness)

    work_order.lifecycle_status = to_status
    work_order.lifecycle_version += 1
    update_fields = ['lifecycle_status', 'lifecycle_version', 'updated_at']
    if (
        to_status == WorkOrderLifecycle.IN_PROGRESS
        and work_order.actual_started_at is None
    ):
        work_order.actual_started_at = timezone.now()
        update_fields.append('actual_started_at')
    if to_status == WorkOrderLifecycle.COMPLETED:
        work_order.actual_completed_at = timezone.now()
        update_fields.append('actual_completed_at')
    if to_status == WorkOrderLifecycle.ON_HOLD:
        work_order.hold_reason = reason
        update_fields.append('hold_reason')
    if to_status != WorkOrderLifecycle.ON_HOLD and work_order.hold_reason:
        work_order.hold_reason = ''
        update_fields.append('hold_reason')
    work_order.save(update_fields=update_fields)
    return _append_result(
        work_order=work_order,
        actor=actor,
        command=command,
        event_type=command.upper(),
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )


@transaction.atomic
def transition_work_order(
    *,
    work_order_id: int,
    to_status: str,
    actor,
    expected_version: int,
    idempotency_key: str,
    reason: str = '',
    correlation_id: uuid.UUID | None = None,
    packet_finalization: PacketFinalization | None = None,
) -> CommandResult:
    """Apply one legal lifecycle transition.

    ``packet_finalization`` is a capability token minted only by
    ``repair.services``, which owns the lifecycle of a packet's work order and
    moves both aggregates together. It is not a flag a caller can assert - see
    :mod:`tasks.services.finalization`.
    """
    work_order = _locked_work_order(work_order_id)
    return _transition_locked(
        work_order=work_order,
        to_status=to_status,
        actor=actor,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        reason=reason,
        correlation_id=correlation_id or uuid.uuid4(),
        command='transition',
        packet_finalization=packet_finalization,
    )


@transaction.atomic
def assign_work_order(
    *,
    work_order_id: int,
    assigned_to,
    actor,
    expected_version: int,
    idempotency_key: str,
    reason: str = '',
    correlation_id: uuid.UUID | None = None,
) -> CommandResult:
    """Assign a typed user while preserving legacy free-text ``assignee``."""
    work_order = _locked_work_order(work_order_id)
    assigned_to_id = getattr(assigned_to, 'pk', assigned_to)
    payload = {
        'work_order_id': work_order.pk,
        'assigned_to_id': assigned_to_id,
        'expected_version': expected_version,
        'reason': reason,
    }
    request_hash = _canonical_hash('assign', actor, payload)
    replay = _replay_or_none(work_order, idempotency_key, request_hash)
    if replay:
        return replay
    _require_version(work_order, expected_version)
    require_permission(actor, ASSIGN_WORKORDER)
    _require_scope(actor, work_order)
    _require_no_packet(work_order)
    readiness = evaluate_work_order_readiness(
        work_order, action='assign', actor=actor, expected_version=expected_version
    )
    if not readiness.ready:
        raise ReadinessBlocked(readiness)

    old_assignee_id = work_order.assigned_to_id
    work_order.assigned_to_id = assigned_to_id
    work_order.lifecycle_version += 1
    work_order.save(update_fields=['assigned_to', 'lifecycle_version', 'updated_at'])
    return _append_result(
        work_order=work_order,
        actor=actor,
        command='assign',
        event_type='ASSIGNED',
        from_status=work_order.lifecycle_status,
        to_status=work_order.lifecycle_status,
        reason=reason,
        correlation_id=correlation_id or uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result_metadata={
            'previous_assigned_to_id': old_assignee_id,
            'assigned_to_id': assigned_to_id,
        },
    )


@transaction.atomic
def hold_work_order(
    *,
    work_order_id: int,
    actor,
    expected_version: int,
    idempotency_key: str,
    reason: str,
    correlation_id: uuid.UUID | None = None,
) -> CommandResult:
    """Immediately place in-progress work on hold with a recorded reason."""
    if not reason.strip():
        raise WorkOrderCommandError('A hold reason is required')
    work_order = _locked_work_order(work_order_id)
    result = _transition_locked(
        work_order=work_order,
        to_status=WorkOrderLifecycle.ON_HOLD,
        actor=actor,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        reason=reason,
        correlation_id=correlation_id or uuid.uuid4(),
        command='hold',
        required_permission=EXECUTE_WORKORDER,
    )
    return result


@transaction.atomic
def resume_work_order(
    *,
    work_order_id: int,
    actor,
    expected_version: int,
    idempotency_key: str,
    reason: str = '',
    correlation_id: uuid.UUID | None = None,
) -> CommandResult:
    """Resume held work after full start readiness re-evaluation."""
    work_order = _locked_work_order(work_order_id)
    return _transition_locked(
        work_order=work_order,
        to_status=WorkOrderLifecycle.IN_PROGRESS,
        actor=actor,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        reason=reason,
        correlation_id=correlation_id or uuid.uuid4(),
        command='resume',
        required_permission=EXECUTE_WORKORDER,
    )


@transaction.atomic
def cancel_work_order(
    *,
    work_order_id: int,
    actor,
    expected_version: int,
    idempotency_key: str,
    reason: str,
    correlation_id: uuid.UUID | None = None,
) -> CommandResult:
    """Cancel a standalone order; reservation release is added in the kit phase."""
    if not reason.strip():
        raise WorkOrderCommandError('A cancellation reason is required')
    work_order = _locked_work_order(work_order_id)
    return _transition_locked(
        work_order=work_order,
        to_status=WorkOrderLifecycle.CANCELED,
        actor=actor,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        reason=reason,
        correlation_id=correlation_id or uuid.uuid4(),
        command='cancel',
        required_permission=TRANSITION_WORKORDER,
    )
