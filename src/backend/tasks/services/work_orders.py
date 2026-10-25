"""Transactional command services for maintenance work orders."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from tasks.models import WorkOrder, WorkOrderCommand, WorkOrderEvent, WorkOrderLifecycle
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


def _locked_work_order(work_order_id: int) -> WorkOrder:
    # Lock only the WorkOrder base row (of=('self',)); machine/assigned_to are
    # nullable FKs, and PostgreSQL rejects FOR UPDATE across their outer joins.
    return (
        WorkOrder.objects
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


def require_work_order_machine_change(work_order, machine_id):
    """Keep a packet-owned Work attached to its packet's asset."""
    if machine_id != work_order.machine_id:
        _require_no_packet(work_order)


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
    required_trades: list[str] | None = None,
    trade_requirements_confirmed: bool | None = None,
    override_reason: str = '',
    allow_override: bool = False,
    kanban_card_id: int | None = None,
) -> CommandResult:
    """Assign a typed user while preserving legacy free-text ``assignee``."""
    from django.contrib.auth.models import User

    from repair.models import RepairPacket
    from tasks.models import KanbanCard, KanbanColumn
    from tasks.permissions import PLAN_WORKORDER
    from tasks.services.staff_assignment import validate_assignment
    from users.staff_directory import require_directory

    # Packet services lock the packet before its work order. Match that order.
    packet = (
        RepairPacket.objects
        .select_for_update()
        .filter(work_order_id=work_order_id)
        .first()
    )
    work_order = _locked_work_order(work_order_id)
    require_directory(actor)
    current_actor = User.objects.get(pk=actor.pk)
    require_permission(current_actor, ASSIGN_WORKORDER)
    _require_scope(actor, work_order)
    assigned_to_id = getattr(assigned_to, 'pk', assigned_to)
    payload = {
        'work_order_id': work_order.pk,
        'assigned_to_id': assigned_to_id,
        'expected_version': expected_version,
        'reason': reason,
    }
    if (
        required_trades is not None
        or trade_requirements_confirmed is not None
        or override_reason
        or kanban_card_id
    ):
        payload.update(
            required_trades=required_trades,
            trade_requirements_confirmed=trade_requirements_confirmed,
            override_reason=override_reason,
            kanban_card_id=kanban_card_id,
        )
    request_hash = _canonical_hash('assign', actor, payload)
    replay = _replay_or_none(work_order, idempotency_key, request_hash)
    if replay:
        return replay
    _require_version(work_order, expected_version)
    if work_order.lifecycle_status in {
        WorkOrderLifecycle.COMPLETED,
        WorkOrderLifecycle.CANCELED,
    }:
        raise IllegalTransition('A terminal work order cannot be reassigned.')
    terminal_column = KanbanColumn.terminal_key()
    if terminal_column and work_order.status == terminal_column:
        raise IllegalTransition('A finished work order cannot be reassigned.')
    if packet is not None and packet.status in {'closed', 'canceled'}:
        raise IllegalTransition('A terminal repair packet cannot be reassigned.')
    if required_trades is not None or trade_requirements_confirmed is not None:
        require_permission(current_actor, PLAN_WORKORDER)
    trades = (
        required_trades if required_trades is not None else work_order.required_trades
    )
    confirmed = (
        trade_requirements_confirmed
        if trade_requirements_confirmed is not None
        else work_order.trade_requirements_confirmed
    )
    card = None
    if kanban_card_id is not None:
        card = (
            KanbanCard.objects
            .select_for_update()
            .filter(pk=kanban_card_id, work_order=work_order)
            .first()
        )
        if card is None:
            raise WorkOrderCommandError(
                'Board task does not belong to this work order.'
            )
        if terminal_column and card.status == terminal_column:
            raise IllegalTransition('A finished board task cannot be reassigned.')
    effective_user_id = (
        assigned_to_id or work_order.assigned_to_id
        if card is not None
        else assigned_to_id
    )
    decision = validate_assignment(
        actor,
        work_order,
        effective_user_id,
        required_trades=trades,
        requirements_confirmed=confirmed,
        override_reason=override_reason,
        allow_override=allow_override,
    )
    trades = sorted(trades)
    readiness = evaluate_work_order_readiness(
        work_order, action='assign', actor=actor, expected_version=expected_version
    )
    if not readiness.ready:
        raise ReadinessBlocked(readiness)

    from django.contrib.auth import get_user_model

    from tasks.models import WorkHandover
    from tasks.work_execution.common import person

    responsibility_metadata = {}
    if card is None:
        incoming = (
            get_user_model().objects.filter(pk=assigned_to_id, is_active=True).first()
        )
        if assigned_to_id is not None and incoming is None:
            raise WorkOrderCommandError('Incoming owner must be active.')
        pending = work_order.handovers.order_by('-pk').first()
        assignment_changed = work_order.assigned_to_id != assigned_to_id
        old_assignee_id = work_order.assigned_to_id
        responsible_owner_id = (
            (
                pending.incoming_owner_id
                if pending.accepted_at
                else pending.previous_owner_id
            )
            if pending
            else old_assignee_id
        )
        if pending is None and responsible_owner_id is None:
            last_assignment = (
                work_order.events.filter(event_type='ASSIGNED').order_by('-pk').first()
            )
            if last_assignment:
                responsible_owner_id = (
                    last_assignment.metadata.get('result_metadata') or {}
                ).get('responsible_owner_id')
        work_order.assigned_to_id = assigned_to_id
    else:
        old_assignee_id = card.assigned_to_id
        card.assigned_to_id = assigned_to_id
        card.save(update_fields=['assigned_to', 'updated_at'])
    work_order.required_trades = trades
    work_order.trade_requirements_confirmed = confirmed
    work_order.lifecycle_version += 1
    work_order.save(
        update_fields=[
            'assigned_to',
            'required_trades',
            'trade_requirements_confirmed',
            'lifecycle_version',
            'updated_at',
        ]
    )
    if card is None:
        responsible = get_user_model().objects.filter(pk=responsible_owner_id).first()
        responsible_owner_id = responsible.pk if responsible else None
        disposition = 'unchanged'
        if assignment_changed:
            if pending and pending.accepted_at is None and pending.withdrawn_at is None:
                pending.withdrawn_at = timezone.now()
                pending.save(update_fields=['withdrawn_at'])
            disposition = (
                'released'
                if assigned_to_id is None
                else 'restored'
                if assigned_to_id == responsible_owner_id
                else 'proposed'
            )
        if assignment_changed and disposition == 'proposed':
            WorkHandover.objects.create(
                work_order=work_order,
                previous_owner_id=responsible_owner_id,
                incoming_owner_id=assigned_to_id,
                proposed_by=actor,
                proposed_version=work_order.lifecycle_version,
                previous_owner_name=person(responsible) or '',
                incoming_owner_name=person(incoming) or '',
                proposed_by_name=person(actor) or '',
            )
        responsibility_metadata = {
            'responsibility_disposition': disposition,
            'responsible_owner_id': responsible_owner_id,
            'responsible_owner_name': person(responsible),
        }
    return _append_result(
        work_order=work_order,
        actor=actor,
        command='assign',
        event_type='CARD_ASSIGNED' if card is not None else 'ASSIGNED',
        from_status=work_order.lifecycle_status,
        to_status=work_order.lifecycle_status,
        reason=reason,
        correlation_id=correlation_id or uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result_metadata={
            'previous_assigned_to_id': old_assignee_id,
            'assigned_to_id': assigned_to_id,
            'kanban_card_id': kanban_card_id,
            'effective_assigned_to_id': effective_user_id,
            'required_trades': trades,
            'trade_requirements_confirmed': confirmed,
            **decision,
            **responsibility_metadata,
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


@transaction.atomic
def accept_handover(
    *,
    work_order_id,
    actor,
    expected_version,
    idempotency_key,
    reason='',
    correlation_id=None,
):
    """Only the incoming owner can accept; retries retain the original receipt."""
    from django.core.exceptions import PermissionDenied

    work = _locked_work_order(work_order_id)
    if not getattr(actor, 'is_active', False):
        raise PermissionDenied('Active incoming owner required.')
    _require_scope(actor, work)
    require_permission(actor, EXECUTE_WORKORDER)
    if (
        type(expected_version) is not int
        or not isinstance(idempotency_key, str)
        or not idempotency_key.strip()
        or len(idempotency_key) > 128
    ):
        raise WorkOrderCommandError(
            'A version and stable idempotency key are required.'
        )
    payload = {'expected_version': expected_version, 'reason': reason}
    request_hash = _canonical_hash('accept_handover', actor, payload)
    replay = _replay_or_none(work, idempotency_key, request_hash)
    if replay:
        return replay
    _require_version(work, expected_version)
    handover = work.handovers.order_by('-pk').first()
    if (
        handover is None
        or handover.withdrawn_at is not None
        or handover.incoming_owner_id != actor.pk
        or work.assigned_to_id != actor.pk
    ):
        raise PermissionDenied('Only the proposed incoming owner may accept handover.')
    if handover.accepted_at is not None:
        raise CommandConflict('HANDOVER_ALREADY_ACCEPTED')
    work.lifecycle_version += 1
    work.save(update_fields=['lifecycle_version', 'updated_at'])
    from tasks.work_execution.common import person

    handover.accepted_by_name = person(actor) or ''
    handover.accepted_by = actor
    handover.accepted_at = timezone.now()
    handover.accepted_version = work.lifecycle_version
    handover.save(
        update_fields=[
            'accepted_by',
            'accepted_at',
            'accepted_version',
            'accepted_by_name',
        ]
    )
    return _append_result(
        work_order=work,
        actor=actor,
        command='accept_handover',
        event_type='HANDOVER_ACCEPTED',
        from_status=work.lifecycle_status,
        to_status=work.lifecycle_status,
        reason=reason,
        correlation_id=correlation_id or uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result_metadata={
            'handover_id': handover.pk,
            'accepted_by': actor.pk,
            'accepted_at': handover.accepted_at.isoformat(),
        },
    )
