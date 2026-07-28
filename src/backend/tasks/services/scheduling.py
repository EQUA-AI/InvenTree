"""Transactional command service for work-order scheduling (S5).

This is the single write path the board, calendar, timeline and (later) AI all
go through for planning changes: create, update planning metadata, move
(reschedule), resize (change duration), governed delete, and atomic batch apply.
Every command records a ``WorkOrderCommand`` (idempotency ledger) and a
``WorkOrderEvent`` (audit), reuses ``lifecycle_version`` as the optimistic
concurrency token, and runs under a row lock.

Scope note: unlike ``tasks.services.work_orders`` (the flagged canonical API),
these commands do NOT impose customer scope via ``scope_for_actor``. The
unflagged board they serve is gated by the ``work_order`` ruleset at the endpoint
(RolePermission), and adding scope here would reject any actor without configured
``maintenance_scopes`` — breaking ordinary board writes. Customer scoping stays a
flagged-canonical-API concern until that surface is turned on.

Deferred to S6 (semantics): working-time-aware span/duration math, done-column
immutability, dependency commands, and same-machine conflict detection. The
commands here validate what does not depend on those: a non-inverted window, and
that completed/canceled work is not moved.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone

from tasks.models import (
    KanbanCard,
    KanbanColumn,
    WorkOrder,
    WorkOrderCommand,
    WorkOrderDeletionRecord,
    WorkOrderDependency,
    WorkOrderEvent,
    WorkOrderLifecycle,
    WorkOrderPart,
    WorkOrderType,
)
from tasks.services.calendars import spec_for_card

# Reuse the canonical command primitives so the two services cannot drift on how
# they lock rows, replay idempotent requests, check versions or record results.
from tasks.services.work_orders import (
    CommandResult,
    IdempotencyConflict,
    StaleVersion,
    WorkOrderCommandError,
    _append_result,
    _canonical_hash,
    _locked_work_order,
    _replay_or_none,
    _require_version,
)
from tasks.services.working_time import add_working_minutes, working_minutes_between

logger = logging.getLogger('inventree')

__all__ = [
    'CommandResult',
    'DeletionResult',
    'DependencyCycle',
    'IdempotencyConflict',
    'InvalidChild',
    'InvalidDependency',
    'InvalidSchedule',
    'NotMutable',
    'ProtectedWorkOrder',
    'StaleVersion',
    'UnknownWorkOrder',
    'WorkOrderCommandError',
    'apply_schedule_batch',
    'create_child',
    'create_dependency',
    'create_work_order',
    'delete_dependency',
    'delete_work_order',
    'generate_procurement_child',
    'incomplete_children',
    'materialise_required_parts',
    'resize_work_order',
    'schedule_work_order',
    'update_work_order_plan',
]


class InvalidSchedule(WorkOrderCommandError):  # noqa: N818 - matches sibling error names
    """The requested schedule window is invalid (e.g. end before start)."""

    code = 'INVALID_SCHEDULE'


class NotMutable(WorkOrderCommandError):  # noqa: N818 - matches sibling error names
    """The work order is in a terminal lifecycle state and cannot be planned."""

    code = 'NOT_MUTABLE'


class ProtectedWorkOrder(WorkOrderCommandError):  # noqa: N818 - matches sibling error names
    """The work order cannot be deleted because protected records reference it."""

    code = 'PROTECTED'


class UnknownWorkOrder(WorkOrderCommandError):  # noqa: N818 - matches sibling error names
    """No work order exists for the supplied id."""

    code = 'UNKNOWN_WORK_ORDER'


class DependencyCycle(WorkOrderCommandError):  # noqa: N818 - matches sibling error names
    """Adding the dependency would create a cycle in the schedule graph."""

    code = 'DEPENDENCY_CYCLE'


class InvalidDependency(WorkOrderCommandError):  # noqa: N818 - matches sibling error names
    """The dependency is malformed (e.g. a card depending on itself)."""

    code = 'INVALID_DEPENDENCY'


class InvalidChild(WorkOrderCommandError):  # noqa: N818 - matches sibling error names
    """The child-card request violates a composition rule (depth, inheritance)."""

    code = 'INVALID_CHILD'


@dataclass(frozen=True)
class DeletionResult:
    """Result of a governed delete: the card is gone, the audit record remains."""

    work_order_id: int
    deletion_record_id: int
    reference: str
    correlation_id: uuid.UUID
    idempotency_key: str


_TERMINAL_STATES = {WorkOrderLifecycle.COMPLETED, WorkOrderLifecycle.CANCELED}

# Planning metadata a plain update may set. Lifecycle/identity/execution fields
# are intentionally excluded: those move only through their dedicated commands.
_PLAN_FIELDS = {
    'title',
    'description',
    'priority',
    'machine_id',
    'work_order_type',
    'due_date',
    'assignee',
}


def _coerce_dt(value: Any) -> datetime | None:
    """Accept an aware datetime or None; reject anything else loudly."""
    if value is None or isinstance(value, datetime):
        return value
    raise InvalidSchedule(f'Expected a datetime or None, got {type(value).__name__}')


def _validate_window(start: datetime | None, end: datetime | None) -> None:
    if start is not None and end is not None and end < start:
        raise InvalidSchedule('Scheduled end must not be before scheduled start.')


def _require_mutable(work_order: WorkOrder) -> None:
    if work_order.lifecycle_status in _TERMINAL_STATES:
        raise NotMutable(
            f'Work order {work_order.pk} is {work_order.lifecycle_status} and '
            'cannot be rescheduled or edited.'
        )

    # A card in the board's terminal (done) column is immutable too (§5.8): its
    # only remaining operation is deletion. This is the board-column half of the
    # rule; the lifecycle_status check above is the lifecycle half.
    terminal_key = KanbanColumn.terminal_key()
    if terminal_key and work_order.status == terminal_key:
        raise NotMutable(
            f'Work order {work_order.pk} is in the done column and cannot be '
            'rescheduled or edited.'
        )


def _new_correlation(correlation_id: uuid.UUID | None) -> uuid.UUID:
    return correlation_id or uuid.uuid4()


def create_work_order(
    *,
    actor,
    idempotency_key: str,
    title: str,
    machine_id: int,
    correlation_id: uuid.UUID | None = None,
    **planning: Any,
) -> CommandResult:
    """Create a work order and record its CREATED event.

    Idempotent on ``idempotency_key`` across the ``create`` command: a replay with
    the same key and identical request returns the already-created card rather
    than making a second one.
    """
    payload = {'title': title, 'machine_id': machine_id, **planning}
    request_hash = _canonical_hash('create', actor, payload)

    prior = WorkOrderCommand.objects.filter(
        command='create', idempotency_key=idempotency_key
    ).first()
    if prior is not None:
        if prior.request_hash != request_hash:
            raise IdempotencyConflict(
                'Idempotency key was reused with a different create request'
            )
        # The command is FK'd to the card it created, so the card is prior's
        # work_order — result_ref holds the event pk, not the card pk.
        existing = WorkOrder.objects.filter(pk=prior.work_order_id).first()
        if existing is None:
            raise WorkOrderCommandError('Stored create result cannot be replayed')
        return _result_for(existing, 'create', prior.correlation_id, idempotency_key)

    fields = {key: value for key, value in planning.items() if key in _PLAN_FIELDS}
    fields.setdefault('status', WorkOrder.STATUS_BACKLOG)
    fields.setdefault('priority', WorkOrder.PRIORITY_MEDIUM)
    fields.setdefault('work_order_type', WorkOrderType.CORRECTIVE)

    correlation_id = _new_correlation(correlation_id)

    with transaction.atomic():
        work_order = WorkOrder.objects.create(
            title=title, machine_id=machine_id, **fields
        )
        return _append_result(
            work_order=work_order,
            actor=actor,
            command='create',
            event_type='CREATED',
            from_status='',
            to_status=work_order.lifecycle_status,
            reason=planning.get('reason', ''),
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )


def _result_for(work_order, command, correlation_id, idempotency_key):
    """Build a CommandResult for a replayed create without a new event."""
    return CommandResult(
        work_order_id=work_order.pk,
        event_id=0,
        command=command,
        lifecycle_status=work_order.lifecycle_status,
        lifecycle_version=work_order.lifecycle_version,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        metadata={'replayed': True},
    )


def _mutate(
    *,
    work_order_id: int,
    actor,
    expected_version: int,
    idempotency_key: str,
    command: str,
    event_type: str,
    payload: dict[str, Any],
    apply,
    reason: str = '',
    correlation_id: uuid.UUID | None = None,
) -> CommandResult:
    """Shared skeleton for a versioned, audited, single-card mutation.

    ``apply(work_order)`` performs the field changes and returns a
    ``(result_metadata, update_fields)`` pair; the version bump, save and audit
    are handled here. Wrapped in a transaction so the row lock is valid whether
    or not the caller (a request under ATOMIC_REQUESTS, a batch, or a test) has
    already opened one.
    """
    request_hash = _canonical_hash(command, actor, payload)

    with transaction.atomic():
        work_order = _locked_work_order(work_order_id)

        replay = _replay_or_none(work_order, idempotency_key, request_hash)
        if replay:
            return replay

        _require_version(work_order, expected_version)
        _require_mutable(work_order)

        result_metadata, update_fields = apply(work_order)

        work_order.lifecycle_version += 1
        work_order.save(
            update_fields=[*update_fields, 'lifecycle_version', 'updated_at']
        )

        return _append_result(
            work_order=work_order,
            actor=actor,
            command=command,
            event_type=event_type,
            from_status=work_order.lifecycle_status,
            to_status=work_order.lifecycle_status,
            reason=reason,
            correlation_id=_new_correlation(correlation_id),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result_metadata=result_metadata,
        )


def update_work_order_plan(
    *,
    work_order_id: int,
    actor,
    expected_version: int,
    idempotency_key: str,
    fields: dict[str, Any],
    correlation_id: uuid.UUID | None = None,
) -> CommandResult:
    """Update planning metadata (title, description, priority, machine, type, …)."""
    allowed = {key: value for key, value in fields.items() if key in _PLAN_FIELDS}

    if not allowed:
        raise WorkOrderCommandError('No updatable planning fields supplied.')

    def apply(work_order):
        for key, value in allowed.items():
            setattr(work_order, key, value)
        return {'updated_fields': sorted(allowed)}, list(allowed)

    return _mutate(
        work_order_id=work_order_id,
        actor=actor,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        command='update_plan',
        event_type='PLAN_UPDATED',
        payload={'fields': allowed, 'expected_version': expected_version},
        apply=apply,
        correlation_id=correlation_id,
    )


def schedule_work_order(
    *,
    work_order_id: int,
    actor,
    expected_version: int,
    idempotency_key: str,
    scheduled_start: datetime | None,
    scheduled_end: datetime | None,
    correlation_id: uuid.UUID | None = None,
) -> CommandResult:
    """Move a work order to a new scheduled window.

    A move preserves duration: when only a start is given and the card has an
    ``estimated_minutes`` duration, the end is derived from the card's working
    calendar (nights, weekends and holidays skipped, DST-correct). An explicitly
    supplied end is honoured verbatim, which keeps the literal-window behaviour
    for callers that compute their own span.
    """
    start = _coerce_dt(scheduled_start)
    end = _coerce_dt(scheduled_end)
    _validate_window(start, end)

    def apply(work_order):
        new_start = start
        new_end = end

        if new_end is None and new_start is not None and work_order.estimated_minutes:
            spec = spec_for_card(work_order)
            new_end = add_working_minutes(spec, new_start, work_order.estimated_minutes)
            _validate_window(new_start, new_end)

        work_order.scheduled_start = new_start
        work_order.scheduled_end = new_end
        return (
            {
                'scheduled_start': new_start.isoformat() if new_start else None,
                'scheduled_end': new_end.isoformat() if new_end else None,
                'duration_derived': end is None and new_end is not None,
            },
            ['scheduled_start', 'scheduled_end'],
        )

    return _mutate(
        work_order_id=work_order_id,
        actor=actor,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        command='schedule',
        event_type='SCHEDULED',
        payload={
            'scheduled_start': start.isoformat() if start else None,
            'scheduled_end': end.isoformat() if end else None,
            'expected_version': expected_version,
        },
        apply=apply,
        correlation_id=correlation_id,
    )


def resize_work_order(
    *,
    work_order_id: int,
    actor,
    expected_version: int,
    idempotency_key: str,
    estimated_minutes: int | None = None,
    scheduled_end: datetime | None = None,
    correlation_id: uuid.UUID | None = None,
) -> CommandResult:
    """Change a work order's duration.

    Resizing sets ``estimated_minutes`` and/or moves the end. It is a distinct
    command from ``schedule`` (move) because dragging an edge means "this takes
    longer", not "this starts elsewhere". When an end is dragged without an
    explicit duration, the new duration is the *working* minutes the span
    contains — so a span dragged across a weekend does not inflate the estimate.
    """
    if estimated_minutes is None and scheduled_end is None:
        raise WorkOrderCommandError(
            'resize requires estimated_minutes and/or scheduled_end.'
        )
    if estimated_minutes is not None and estimated_minutes < 0:
        raise InvalidSchedule('estimated_minutes cannot be negative.')

    end = _coerce_dt(scheduled_end)

    def apply(work_order):
        changed = []
        new_minutes = estimated_minutes

        if end is not None:
            _validate_window(work_order.scheduled_start, end)
            work_order.scheduled_end = end
            changed.append('scheduled_end')

            if new_minutes is None and work_order.scheduled_start is not None:
                spec = spec_for_card(work_order)
                new_minutes = round(
                    working_minutes_between(spec, work_order.scheduled_start, end)
                )

        if new_minutes is not None:
            work_order.estimated_minutes = new_minutes
            changed.append('estimated_minutes')

        return (
            {
                'estimated_minutes': new_minutes,
                'scheduled_end': end.isoformat() if end else None,
            },
            changed,
        )

    return _mutate(
        work_order_id=work_order_id,
        actor=actor,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        command='resize',
        event_type='RESIZED',
        payload={
            'estimated_minutes': estimated_minutes,
            'scheduled_end': end.isoformat() if end else None,
            'expected_version': expected_version,
        },
        apply=apply,
        correlation_id=correlation_id,
    )


def delete_work_order(
    *,
    work_order_id: int,
    actor,
    expected_version: int,
    idempotency_key: str,
    reason: str = '',
    correlation_id: uuid.UUID | None = None,
) -> DeletionResult:
    """Governed delete: snapshot the work order, then remove it.

    Before the card (and its cascading events/commands/closeout) is deleted, a
    ``WorkOrderDeletionRecord`` captures its identity, the actor and the reason,
    so the fact of deletion survives. If a protected record (e.g. a closeout
    capture) references the card, deletion is refused with a clear error rather
    than a 500.
    """
    correlation_id = _new_correlation(correlation_id)

    with transaction.atomic():
        work_order = _locked_work_order(work_order_id)

        # Idempotent replay: the card is gone after a successful delete, so a
        # replay is matched against the durable record by idempotency key.
        prior = WorkOrderDeletionRecord.objects.filter(
            work_order_pk=work_order_id, idempotency_key=idempotency_key
        ).first()
        if prior is not None:
            return DeletionResult(
                work_order_id=work_order_id,
                deletion_record_id=prior.pk,
                reference=prior.reference,
                correlation_id=prior.correlation_id,
                idempotency_key=idempotency_key,
            )

        _require_version(work_order, expected_version)

        record = WorkOrderDeletionRecord.objects.create(
            work_order_pk=work_order.pk,
            reference=work_order.reference or '',
            title=work_order.title,
            lifecycle_status=work_order.lifecycle_status,
            machine_id=work_order.machine_id,
            customer_id=work_order.customer_id,
            actor=actor if getattr(actor, 'pk', None) else None,
            reason=reason,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            snapshot=_snapshot(work_order),
        )

        try:
            work_order.delete()
        except ProtectedError as exc:
            raise ProtectedWorkOrder(
                'This work order has protected records (e.g. closeout captures) '
                'and cannot be deleted.'
            ) from exc

        return DeletionResult(
            work_order_id=work_order_id,
            deletion_record_id=record.pk,
            reference=record.reference,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )


def _snapshot(work_order: WorkOrder) -> dict[str, Any]:
    """Serialize the durable fields of a card for forensic recovery."""
    return {
        'title': work_order.title,
        'description': work_order.description,
        'status': work_order.status,
        'priority': work_order.priority,
        'lifecycle_status': work_order.lifecycle_status,
        'lifecycle_version': work_order.lifecycle_version,
        'work_order_type': work_order.work_order_type,
        'machine_id': work_order.machine_id,
        'customer_id': work_order.customer_id,
        'assigned_to_id': work_order.assigned_to_id,
        'assignee': work_order.assignee,
        'scheduled_start': (
            work_order.scheduled_start.isoformat()
            if work_order.scheduled_start
            else None
        ),
        'scheduled_end': (
            work_order.scheduled_end.isoformat() if work_order.scheduled_end else None
        ),
        'estimated_minutes': work_order.estimated_minutes,
        'due_date': (work_order.due_date.isoformat() if work_order.due_date else None),
        'reference': work_order.reference,
        'created_at': work_order.created_at.isoformat(),
        'deleted_snapshot_at': timezone.now().isoformat(),
    }


def apply_schedule_batch(
    *,
    actor,
    idempotency_key: str,
    operations: list[dict[str, Any]],
    correlation_id: uuid.UUID | None = None,
) -> list[CommandResult]:
    """Apply many schedule moves atomically: all succeed or none do.

    Each operation is ``{card_id, expected_version, scheduled_start,
    scheduled_end}``. A per-operation idempotency key is derived from the batch
    key and the card id, so a batch replay does not double-apply. If any
    operation fails, the whole batch rolls back.
    """
    results: list[CommandResult] = []
    correlation_id = _new_correlation(correlation_id)

    with transaction.atomic():
        for op in operations:
            work_order_id = op['card_id']
            results.append(
                schedule_work_order(
                    work_order_id=work_order_id,
                    actor=actor,
                    expected_version=op['expected_version'],
                    idempotency_key=f'{idempotency_key}:{work_order_id}',
                    scheduled_start=op.get('scheduled_start'),
                    scheduled_end=op.get('scheduled_end'),
                    correlation_id=correlation_id,
                )
            )

    return results


# ── Dependencies ─────────────────────────────────────────────────────────────


def _would_create_cycle(predecessor_id: int, successor_id: int) -> bool:
    """Return whether adding ``predecessor -> successor`` closes a cycle.

    A cycle exists if ``successor`` can already reach ``predecessor`` by following
    existing predecessor->successor edges. Breadth-first over the existing graph;
    the edge set is small, so this stays cheap.
    """
    if predecessor_id == successor_id:
        return True

    # Adjacency: predecessor -> set(successors).
    edges: dict[int, set[int]] = {}
    for f, t in WorkOrderDependency.objects.values_list(
        'predecessor_id', 'successor_id'
    ):
        edges.setdefault(f, set()).add(t)

    frontier = [successor_id]
    seen = {successor_id}
    while frontier:
        node = frontier.pop()
        if node == predecessor_id:
            return True
        for nxt in edges.get(node, ()):  # successors of node
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)

    return False


def create_dependency(
    *,
    predecessor_id: int,
    successor_id: int,
    actor,
    dependency_type: str = WorkOrderDependency.TYPE_FS,
    lag_minutes: int = 0,
    correlation_id: uuid.UUID | None = None,
) -> WorkOrderDependency:
    """Create a scheduling dependency, rejecting self-loops and cycles.

    Idempotent via the ``(predecessor, successor, type)`` unique constraint: a repeat
    returns the existing edge. Records a ``DEPENDENCY_ADDED`` event on the
    successor (the card that gains a predecessor).
    """
    if dependency_type not in dict(WorkOrderDependency.TYPE_CHOICES):
        raise InvalidDependency(f'Unknown dependency type: {dependency_type}')

    if predecessor_id == successor_id:
        raise InvalidDependency('A work order cannot depend on itself.')

    with transaction.atomic():
        if not WorkOrder.objects.filter(pk=predecessor_id).exists():
            raise UnknownWorkOrder(f'No work order {predecessor_id}')
        if not WorkOrder.objects.filter(pk=successor_id).exists():
            raise UnknownWorkOrder(f'No work order {successor_id}')

        existing = WorkOrderDependency.objects.filter(
            predecessor_id=predecessor_id,
            successor_id=successor_id,
            dependency_type=dependency_type,
        ).first()
        if existing is not None:
            return existing

        if _would_create_cycle(predecessor_id, successor_id):
            raise DependencyCycle(
                f'{predecessor_id} -> {successor_id} would create a dependency cycle'
            )

        dependency = WorkOrderDependency.objects.create(
            predecessor_id=predecessor_id,
            successor_id=successor_id,
            dependency_type=dependency_type,
            lag_minutes=lag_minutes,
        )

        WorkOrderEvent.objects.create(
            work_order_id=successor_id,
            event_type='DEPENDENCY_ADDED',
            actor=actor if getattr(actor, 'pk', None) else None,
            correlation_id=_new_correlation(correlation_id),
            metadata={
                'predecessor': predecessor_id,
                'successor': successor_id,
                'dependency_type': dependency_type,
                'lag_minutes': lag_minutes,
            },
        )

        return dependency


def delete_dependency(
    *, dependency_id: int, actor, correlation_id: uuid.UUID | None = None
) -> bool:
    """Delete a dependency by id. Returns whether a row was removed."""
    with transaction.atomic():
        dependency = WorkOrderDependency.objects.filter(pk=dependency_id).first()
        if dependency is None:
            return False

        successor_id = dependency.successor_id
        metadata = {
            'predecessor': dependency.predecessor_id,
            'successor': successor_id,
            'dependency_type': dependency.dependency_type,
        }
        dependency.delete()

        WorkOrderEvent.objects.create(
            work_order_id=successor_id,
            event_type='DEPENDENCY_REMOVED',
            actor=actor if getattr(actor, 'pk', None) else None,
            correlation_id=_new_correlation(correlation_id),
            metadata=metadata,
        )

        return True


# ── Child cards / composition ────────────────────────────────────────────────


def create_child(
    *,
    parent_id: int,
    actor,
    idempotency_key: str,
    title: str,
    card_kind: str = KanbanCard.KIND_SUBTASK,
    correlation_id: uuid.UUID | None = None,
    **planning: Any,
) -> CommandResult:
    """Add a tracked piece of work to the job ``parent_id``.

    This used to mint a second work order with its own reference, so breaking a
    job down produced more jobs. It creates a card instead: the job stays one
    authorised, closeable thing, and the board gains a piece to move.

    Idempotent on ``idempotency_key`` across the ``create_child`` command.
    """
    if card_kind not in dict(KanbanCard.KIND_CHOICES):
        raise InvalidChild(f'Unknown card kind: {card_kind}')

    payload = {'parent_id': parent_id, 'title': title, 'card_kind': card_kind}
    request_hash = _canonical_hash('create_child', actor, payload)

    correlation_id = _new_correlation(correlation_id)

    with transaction.atomic():
        try:
            parent = _locked_work_order(parent_id)
        except WorkOrder.DoesNotExist as exc:
            raise UnknownWorkOrder(f'No work order {parent_id}') from exc

        replay = _replay_or_none(parent, idempotency_key, request_hash)
        if replay:
            return replay

        # Only the fields a card can answer for. Priority, type, machine and
        # customer describe the job, and the card inherits them by belonging
        # to it rather than by copying them.
        card = KanbanCard.objects.create(
            work_order=parent,
            title=title,
            card_kind=card_kind,
            status=WorkOrder.STATUS_BACKLOG,
            scheduled_start=planning.get('scheduled_start'),
            scheduled_end=planning.get('scheduled_end'),
            estimated_minutes=planning.get('estimated_minutes'),
        )

        return _append_result(
            work_order=parent,
            actor=actor,
            command='create_child',
            event_type='CARD_CREATED',
            from_status='',
            to_status=parent.lifecycle_status,
            reason='',
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result_metadata={'card_id': card.pk, 'card_kind': card_kind},
        )


def generate_procurement_child(*, parent_id: int, actor) -> KanbanCard | None:
    """Raise a procurement card for the job's parts shortfall.

    Returns the procurement card, or None when there is no shortfall.
    Idempotent: an open procurement card is reused rather than duplicated.

    The card no longer carries copies of the shortfall lines. Parts are required
    by the job and already recorded there; duplicating them onto a second record
    was how one job came to hold two sets of the same requirement.
    """
    with transaction.atomic():
        parent = WorkOrder.objects.filter(pk=parent_id).first()
        if parent is None:
            raise UnknownWorkOrder(f'No work order {parent_id}')

        shortfall = [
            cp
            for cp in parent.work_order_parts.select_related('part')
            if cp.allocation_status
            in (WorkOrderPart.ALLOCATION_PARTIAL, WorkOrderPart.ALLOCATION_INSUFFICIENT)
        ]
        if not shortfall:
            return None

        card = (
            parent.cards
            .filter(card_kind=KanbanCard.KIND_PROCUREMENT, is_active=True)
            .order_by('pk')
            .first()
        )
        if card is None:
            card = KanbanCard.objects.create(
                work_order=parent,
                title=f'Procurement for {parent.title}'[:200],
                card_kind=KanbanCard.KIND_PROCUREMENT,
                status=WorkOrder.STATUS_BACKLOG,
            )

        WorkOrderEvent.objects.create(
            work_order_id=parent_id,
            event_type='CHILD_GENERATED',
            actor=actor if getattr(actor, 'pk', None) else None,
            correlation_id=_new_correlation(None),
            metadata={
                'card_id': card.pk,
                'card_kind': KanbanCard.KIND_PROCUREMENT,
                'shortfall_parts': sorted(cp.part_id for cp in shortfall),
            },
        )

        return card


def incomplete_children(parent_id: int):
    """Return the job's cards that have not reached a terminal column.

    A card has no lifecycle of its own - it is a piece of work on a board - so
    "still open" means active and not yet in a column marked terminal. That is
    the same question the old child-work-order check was asking.
    """
    terminal_keys = list(
        KanbanColumn.objects.filter(is_terminal=True).values_list('key', flat=True)
    )
    cards = KanbanCard.objects.filter(work_order_id=parent_id, is_active=True)
    if terminal_keys:
        cards = cards.exclude(status__in=terminal_keys)
    return cards.exclude(card_kind=KanbanCard.KIND_WORK_ORDER)


def materialise_required_parts(
    *, work_order_id: int, lines, allocate: bool = True
) -> list[WorkOrderPart]:
    """Add required-part lines to a work order, idempotently.

    ``lines`` is an iterable of ``(part_id, quantity)`` pairs. Existing lines for
    the same part are left at their current quantity rather than being reset, so
    a replayed caller (AI generation, an approved work package) never silently
    shrinks a technician's edit. Quantities are validated up front so a bad line
    fails the whole call instead of writing a partial set.

    ``allocate`` runs the stock check for each line. Allocation is advisory here:
    a stock failure records the shortfall on the line and never rolls back the
    requirement itself.
    """
    validated: list[tuple[int, Any]] = []
    seen: set[int] = set()

    for part_id, quantity in lines:
        if not part_id:
            continue
        if part_id in seen:
            continue
        try:
            amount = Decimal(str(quantity))
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise WorkOrderCommandError(
                f'Required-part quantity for part {part_id} is not a number.'
            ) from exc
        if amount <= 0:
            raise WorkOrderCommandError(
                f'Required-part quantity for part {part_id} must be positive.'
            )
        seen.add(part_id)
        validated.append((part_id, amount))

    if not validated:
        return []

    created: list[WorkOrderPart] = []

    for part_id, amount in validated:
        line, _ = WorkOrderPart.objects.get_or_create(
            work_order_id=work_order_id, part_id=part_id, defaults={'quantity': amount}
        )
        created.append(line)

        if allocate:
            try:
                line.check_and_allocate()
            except Exception:
                logger.warning(
                    'Stock allocation failed for work order %s part %s',
                    work_order_id,
                    part_id,
                    exc_info=True,
                )

    return created
