"""Deterministic schedule planner (Phase 6b, plan §6b).

Given a set of candidate work orders, computes a valid placement in time and
returns a list of before/after operations — it never writes. The AI-assisted
flow and a plain "auto-schedule" button both drive this; the AI only chooses the
*request*, the planner alone decides the times, so an LLM can never invent a
date, machine or duration.

Determinism: identical inputs and source data produce identical output. The
placement is a greedy pass in dependency-topological order, ties broken by a
stable ranking (priority, due date, id), so there is no randomness.

Hard constraints honoured:

* working-time aware — placements start on a working instant and span the
  required working minutes under the card's calendar (nights/weekends/holidays
  skipped, DST-correct);
* dependencies — FS / SS / FF / SF with working-time ``lag_minutes``;
* no same-machine overlap (and optionally no same-assignee overlap);
* completed / canceled / explicitly locked work is never moved.

Missing information degrades gracefully: a card with no duration is left
unscheduled with a warning, never given an invented duration.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from tasks.models import KanbanCard, KanbanCardDependency, WorkOrderLifecycle
from tasks.services.calendars import spec_for_card
from tasks.services.working_time import (
    add_working_minutes,
    next_working_instant,
    subtract_working_minutes,
)

_TERMINAL = {WorkOrderLifecycle.COMPLETED, WorkOrderLifecycle.CANCELED}
_MAX_CONFLICT_STEPS = 500

# Lower sorts earlier (higher priority scheduled first).
_PRIORITY_RANK = {'high': 0, 'medium': 1, 'low': 2}


@dataclass
class PlanRequest:
    """A typed planning request. The AI fills this in; it cannot set times."""

    candidate_ids: list[int]
    horizon_start: datetime
    locked_ids: frozenset[int] = frozenset()
    allow_move_existing: bool = True
    check_assignee: bool = False


@dataclass
class PlanOperation:
    """One proposed reschedule: a card's before/after window."""

    card_id: int
    old_start: datetime | None
    old_end: datetime | None
    new_start: datetime
    new_end: datetime


@dataclass
class PlanResult:
    """The planner's output: proposed operations, warnings, and what it skipped."""

    operations: list[PlanOperation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unscheduled: list[int] = field(default_factory=list)


def _topological_order(candidate_ids, deps_in, cards):
    """Return candidate ids predecessors-first, ties broken by a stable rank.

    Kahn's algorithm over the dependency edges that lie *within* the candidate
    set. The ready set is drained in rank order (priority, due date, id) so the
    result is deterministic. A residual cycle (should not occur — creation
    prevents cycles) is appended in rank order rather than dropped.
    """
    candidates = set(candidate_ids)
    indegree = dict.fromkeys(candidate_ids, 0)
    successors = defaultdict(list)
    for to_id, edges in deps_in.items():
        for from_id, _type, _lag in edges:
            if from_id in candidates:
                indegree[to_id] += 1
                successors[from_id].append(to_id)

    def rank(cid):
        card = cards[cid]
        due = card.due_date.isoformat() if card.due_date else '9999-12-31'
        return (_PRIORITY_RANK.get(card.priority, 1), due, cid)

    ready = sorted((c for c in candidate_ids if indegree[c] == 0), key=rank)
    order = []
    seen = set()
    while ready:
        node = ready.pop(0)
        order.append(node)
        seen.add(node)
        for succ in successors.get(node, ()):
            indegree[succ] -= 1
            if indegree[succ] == 0:
                ready.append(succ)
        ready.sort(key=rank)

    # Any node not emitted was part of a cycle; append deterministically.
    for cid in sorted(set(candidate_ids) - seen, key=rank):
        order.append(cid)
    return order


def _dependency_earliest_start(spec, dtype, f_start, f_end, lag, duration):
    """Earliest start for a successor from one incoming dependency edge.

    lag is working-time. FF/SF constrain the successor's *end*, so the start is
    derived by walking ``duration`` working minutes back from the required end.
    """
    if dtype == KanbanCardDependency.TYPE_FS:
        return add_working_minutes(spec, f_end, lag)
    if dtype == KanbanCardDependency.TYPE_SS:
        return add_working_minutes(spec, f_start, lag)
    if dtype == KanbanCardDependency.TYPE_FF:
        required_end = add_working_minutes(spec, f_end, lag)
        return subtract_working_minutes(spec, required_end, duration)
    # SF: successor end >= predecessor start + lag.
    required_end = add_working_minutes(spec, f_start, lag)
    return subtract_working_minutes(spec, required_end, duration)


def _overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def _place_without_conflict(spec, start, duration, intervals):
    """Advance ``start`` until [start, start+duration] clears every interval.

    ``intervals`` are the (start, end) windows already claimed on the resource.
    Returns the placed (start, end).
    """
    for _ in range(_MAX_CONFLICT_STEPS):
        start = next_working_instant(spec, start)
        end = add_working_minutes(spec, start, duration)
        clash = next(
            (
                (istart, iend)
                for istart, iend in intervals
                if _overlaps(start, end, istart, iend)
            ),
            None,
        )
        if clash is None:
            return start, end
        # Jump past the conflicting interval and retry.
        start = clash[1]
    return start, add_working_minutes(spec, start, duration)


def plan_schedule(request: PlanRequest) -> PlanResult:
    """Compute a deterministic, valid placement for the candidate work orders."""
    cards = {
        card.id: card
        for card in KanbanCard.objects.filter(
            id__in=request.candidate_ids
        ).select_related('machine', 'assigned_to')
    }

    deps_in = defaultdict(list)
    for dep in KanbanCardDependency.objects.filter(
        to_card_id__in=request.candidate_ids, from_card_id__in=request.candidate_ids
    ):
        deps_in[dep.to_card_id].append((
            dep.from_card_id,
            dep.dependency_type,
            dep.lag_minutes,
        ))

    order = _topological_order(list(cards.keys()), deps_in, cards)

    result = PlanResult()
    placed: dict[int, tuple[datetime, datetime]] = {}
    machine_intervals: dict[int, list] = defaultdict(list)
    assignee_intervals: dict[int, list] = defaultdict(list)

    def register(card, start, end):
        placed[card.id] = (start, end)
        if card.machine_id:
            machine_intervals[card.machine_id].append((start, end))
        if request.check_assignee and card.assigned_to_id:
            assignee_intervals[card.assigned_to_id].append((start, end))

    for card_id in order:
        card = cards[card_id]

        if card.lifecycle_status in _TERMINAL:
            continue

        # Locked or (frozen) already-scheduled cards keep their slot but still
        # occupy their resource so movable cards schedule around them.
        frozen = card_id in request.locked_ids or (
            not request.allow_move_existing
            and card.scheduled_start
            and card.scheduled_end
        )
        if frozen:
            if card.scheduled_start and card.scheduled_end:
                register(card, card.scheduled_start, card.scheduled_end)
            continue

        if not card.estimated_minutes:
            result.warnings.append(
                f'Work order {card_id} has no estimated duration; not scheduled.'
            )
            result.unscheduled.append(card_id)
            continue

        spec = spec_for_card(card)
        duration = card.estimated_minutes

        earliest = request.horizon_start
        for from_id, dtype, lag in deps_in.get(card_id, ()):
            if from_id in placed:
                f_start, f_end = placed[from_id]
                dep_start = _dependency_earliest_start(
                    spec, dtype, f_start, f_end, lag, duration
                )
                earliest = max(earliest, dep_start)

        intervals = list(machine_intervals[card.machine_id]) if card.machine_id else []
        if request.check_assignee and card.assigned_to_id:
            intervals += assignee_intervals[card.assigned_to_id]

        start, end = _place_without_conflict(spec, earliest, duration, intervals)
        register(card, start, end)

        # Only emit an operation when the placement actually changes something.
        if card.scheduled_start != start or card.scheduled_end != end:
            result.operations.append(
                PlanOperation(
                    card_id=card_id,
                    old_start=card.scheduled_start,
                    old_end=card.scheduled_end,
                    new_start=start,
                    new_end=end,
                )
            )

    return result
