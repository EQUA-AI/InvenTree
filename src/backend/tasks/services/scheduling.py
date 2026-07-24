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

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone

from tasks.models import (
    KanbanCard,
    KanbanCardDependency,
    KanbanCardPart,
    KanbanColumn,
    WorkOrderCommand,
    WorkOrderDeletionRecord,
    WorkOrderEvent,
    WorkOrderLifecycle,
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


def _require_mutable(work_order: KanbanCard) -> None:
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
        existing = KanbanCard.objects.filter(pk=prior.work_order_id).first()
        if existing is None:
            raise WorkOrderCommandError('Stored create result cannot be replayed')
        return _result_for(existing, 'create', prior.correlation_id, idempotency_key)

    fields = {key: value for key, value in planning.items() if key in _PLAN_FIELDS}
    fields.setdefault('status', KanbanCard.STATUS_BACKLOG)
    fields.setdefault('priority', KanbanCard.PRIORITY_MEDIUM)
    fields.setdefault('work_order_type', WorkOrderType.CORRECTIVE)

    correlation_id = _new_correlation(correlation_id)

    with transaction.atomic():
        work_order = KanbanCard.objects.create(
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
        # A child inherits its parent's machine and cannot diverge (§5.10).
        if (
            work_order.parent_id
            and 'machine_id' in allowed
            and allowed['machine_id'] != work_order.parent.machine_id
        ):
            raise InvalidChild(
                'A child work order cannot use a different machine from its parent.'
            )

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


def _snapshot(work_order: KanbanCard) -> dict[str, Any]:
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
            card_id = op['card_id']
            results.append(
                schedule_work_order(
                    work_order_id=card_id,
                    actor=actor,
                    expected_version=op['expected_version'],
                    idempotency_key=f'{idempotency_key}:{card_id}',
                    scheduled_start=op.get('scheduled_start'),
                    scheduled_end=op.get('scheduled_end'),
                    correlation_id=correlation_id,
                )
            )

    return results


# ── Dependencies ─────────────────────────────────────────────────────────────


def _would_create_cycle(from_card_id: int, to_card_id: int) -> bool:
    """Return whether adding ``from_card -> to_card`` closes a cycle.

    A cycle exists if ``to_card`` can already reach ``from_card`` by following
    existing predecessor->successor edges. Breadth-first over the existing graph;
    the edge set is small, so this stays cheap.
    """
    if from_card_id == to_card_id:
        return True

    # Adjacency: predecessor -> set(successors).
    edges: dict[int, set[int]] = {}
    for f, t in KanbanCardDependency.objects.values_list('from_card_id', 'to_card_id'):
        edges.setdefault(f, set()).add(t)

    frontier = [to_card_id]
    seen = {to_card_id}
    while frontier:
        node = frontier.pop()
        if node == from_card_id:
            return True
        for nxt in edges.get(node, ()):  # successors of node
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)

    return False


def create_dependency(
    *,
    from_card_id: int,
    to_card_id: int,
    actor,
    dependency_type: str = KanbanCardDependency.TYPE_FS,
    lag_minutes: int = 0,
    correlation_id: uuid.UUID | None = None,
) -> KanbanCardDependency:
    """Create a scheduling dependency, rejecting self-loops and cycles.

    Idempotent via the ``(from_card, to_card, type)`` unique constraint: a repeat
    returns the existing edge. Records a ``DEPENDENCY_ADDED`` event on the
    successor (the card that gains a predecessor).
    """
    if dependency_type not in dict(KanbanCardDependency.TYPE_CHOICES):
        raise InvalidDependency(f'Unknown dependency type: {dependency_type}')

    if from_card_id == to_card_id:
        raise InvalidDependency('A work order cannot depend on itself.')

    with transaction.atomic():
        if not KanbanCard.objects.filter(pk=from_card_id).exists():
            raise UnknownWorkOrder(f'No work order {from_card_id}')
        if not KanbanCard.objects.filter(pk=to_card_id).exists():
            raise UnknownWorkOrder(f'No work order {to_card_id}')

        existing = KanbanCardDependency.objects.filter(
            from_card_id=from_card_id,
            to_card_id=to_card_id,
            dependency_type=dependency_type,
        ).first()
        if existing is not None:
            return existing

        if _would_create_cycle(from_card_id, to_card_id):
            raise DependencyCycle(
                f'{from_card_id} -> {to_card_id} would create a dependency cycle'
            )

        dependency = KanbanCardDependency.objects.create(
            from_card_id=from_card_id,
            to_card_id=to_card_id,
            dependency_type=dependency_type,
            lag_minutes=lag_minutes,
        )

        WorkOrderEvent.objects.create(
            work_order_id=to_card_id,
            event_type='DEPENDENCY_ADDED',
            actor=actor if getattr(actor, 'pk', None) else None,
            correlation_id=_new_correlation(correlation_id),
            metadata={
                'from_card': from_card_id,
                'to_card': to_card_id,
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
        dependency = KanbanCardDependency.objects.filter(pk=dependency_id).first()
        if dependency is None:
            return False

        to_card_id = dependency.to_card_id
        metadata = {
            'from_card': dependency.from_card_id,
            'to_card': to_card_id,
            'dependency_type': dependency.dependency_type,
        }
        dependency.delete()

        WorkOrderEvent.objects.create(
            work_order_id=to_card_id,
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
    """Create a child card under ``parent_id``.

    Depth is exactly one: the parent must not itself be a child. The child
    inherits machine and customer from its parent and cannot diverge (§5.10).
    Idempotent on ``idempotency_key`` across the ``create_child`` command.
    """
    if card_kind not in dict(KanbanCard.KIND_CHOICES):
        raise InvalidChild(f'Unknown card kind: {card_kind}')

    payload = {'parent_id': parent_id, 'title': title, 'card_kind': card_kind}
    request_hash = _canonical_hash('create_child', actor, payload)

    prior = WorkOrderCommand.objects.filter(
        command='create_child', idempotency_key=idempotency_key
    ).first()
    if prior is not None:
        if prior.request_hash != request_hash:
            raise IdempotencyConflict(
                'Idempotency key was reused with a different create_child request'
            )
        existing = KanbanCard.objects.filter(pk=prior.work_order_id).first()
        if existing is None:
            raise WorkOrderCommandError('Stored create_child result cannot be replayed')
        return _result_for(
            existing, 'create_child', prior.correlation_id, idempotency_key
        )

    correlation_id = _new_correlation(correlation_id)

    with transaction.atomic():
        parent = KanbanCard.objects.filter(pk=parent_id).first()
        if parent is None:
            raise UnknownWorkOrder(f'No work order {parent_id}')
        if parent.parent_id is not None:
            raise InvalidChild(
                'A child cannot itself have children; depth is exactly one.'
            )

        extra = {
            key: value
            for key, value in planning.items()
            if key in _PLAN_FIELDS - {'machine_id'}
        }

        child = KanbanCard.objects.create(
            title=title,
            status=KanbanCard.STATUS_BACKLOG,
            priority=extra.pop('priority', parent.priority),
            work_order_type=extra.pop('work_order_type', parent.work_order_type),
            machine_id=parent.machine_id,
            customer_id=parent.customer_id,
            parent_id=parent_id,
            card_kind=card_kind,
            **extra,
        )

        return _append_result(
            work_order=child,
            actor=actor,
            command='create_child',
            event_type='CREATED',
            from_status='',
            to_status=child.lifecycle_status,
            reason='',
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result_metadata={'parent_id': parent_id, 'card_kind': card_kind},
        )


def generate_procurement_child(*, parent_id: int, actor) -> KanbanCard | None:
    """Raise a procurement child carrying the parent's parts shortfall.

    Returns the procurement child, or None when the parent has no shortfall.
    Idempotent: if an open procurement child already exists for the parent, its
    shortfall lines are refreshed and it is returned rather than creating a
    second one.
    """
    with transaction.atomic():
        parent = KanbanCard.objects.filter(pk=parent_id).first()
        if parent is None:
            raise UnknownWorkOrder(f'No work order {parent_id}')
        if parent.parent_id is not None:
            raise InvalidChild('Only a top-level work order can raise procurement.')

        shortfall = [
            cp
            for cp in parent.card_parts.select_related('part')
            if cp.allocation_status
            in (
                KanbanCardPart.ALLOCATION_PARTIAL,
                KanbanCardPart.ALLOCATION_INSUFFICIENT,
            )
        ]
        if not shortfall:
            return None

        child = (
            parent.children
            .filter(card_kind=KanbanCard.KIND_PROCUREMENT)
            .exclude(
                lifecycle_status__in=[
                    WorkOrderLifecycle.COMPLETED,
                    WorkOrderLifecycle.CANCELED,
                ]
            )
            .first()
        )
        if child is None:
            child = KanbanCard.objects.create(
                title=f'Procurement for {parent.title}'[:200],
                status=KanbanCard.STATUS_BACKLOG,
                priority=parent.priority,
                machine_id=parent.machine_id,
                customer_id=parent.customer_id,
                parent_id=parent_id,
                card_kind=KanbanCard.KIND_PROCUREMENT,
            )

        for cp in shortfall:
            needed = cp.quantity - cp.allocated_quantity
            if needed <= 0:
                continue
            KanbanCardPart.objects.update_or_create(
                card=child, part=cp.part, defaults={'quantity': needed}
            )

        WorkOrderEvent.objects.create(
            work_order_id=parent_id,
            event_type='CHILD_GENERATED',
            actor=actor if getattr(actor, 'pk', None) else None,
            correlation_id=_new_correlation(None),
            metadata={'child_id': child.pk, 'card_kind': KanbanCard.KIND_PROCUREMENT},
        )

        return child


def incomplete_children(parent_id: int):
    """Return the parent's children that are neither completed nor canceled."""
    return KanbanCard.objects.filter(parent_id=parent_id).exclude(
        lifecycle_status__in=[WorkOrderLifecycle.COMPLETED, WorkOrderLifecycle.CANCELED]
    )
