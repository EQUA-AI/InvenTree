"""Candidate → canonical focus resolution through the EXISTING authorized readers.

The three readers below are the single seam to the business plane:

* ``read_machine``       wraps ``assets.ai_read.authorized_machine``
* ``read_work_order``    wraps ``tasks.ai_read.authorized_work_order``
* ``read_kanban_card``   wraps ``tasks.ai_read.authorized_kanban_card``
  (returns ``(card, parent_work_order)``)

Each already enforces the actual actor policy (maintenance scope, kill
switches) and returns ``None`` on denial without disclosing existence. This
module never grants anything: a client id is a candidate, the reader decides,
and every unresolved outcome — missing, foreign, revoked, broken parent
relationship, reader error — collapses into the SAME generic
``FocusRejected``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai.core.focus.contract import (
    KIND_KANBAN_CARD,
    KIND_MACHINE,
    KIND_WORK_ORDER,
    FocusCandidate,
    FocusRejected,
    FocusTarget,
    FocusValidationError,
)


def read_machine(user: Any, machine_id: int) -> Any | None:
    """One machine the actor may read right now, or None (fail closed)."""
    from assets.ai_read import authorized_machine

    return authorized_machine(user, machine_id)


def read_work_order(user: Any, work_order_id: int) -> Any | None:
    """One work order the actor may read right now, or None (fail closed)."""
    from tasks.ai_read import authorized_work_order

    return authorized_work_order(user, work_order_id)


def read_kanban_card(user: Any, card_id: int) -> tuple[Any, Any] | None:
    """One board card plus its authorized parent work order, or None."""
    from tasks.ai_read import authorized_kanban_card

    return authorized_kanban_card(user, card_id)


#: Candidate search is bounded: a spoken reference is offered at most this
#: many options (the question-card ceiling is 4).
SEARCH_LIMIT = 4

#: Name/reference fields searched per kind. A work order is identifiable by
#: its reference ("WO-000123") or title; a machine by name or serial; a card
#: by title. Matching is a HINT only — every candidate is re-authorized.
MACHINE_MATCH_FIELDS: tuple[str, ...] = ("name__icontains", "serial__icontains")
WORK_ORDER_MATCH_FIELDS: tuple[str, ...] = ("reference__icontains", "title__icontains")
CARD_MATCH_FIELDS: tuple[str, ...] = ("title__icontains",)


def _match_query(fields: tuple[str, ...], query: str):
    from django.db.models import Q

    combined = Q()
    for field in fields:
        combined |= Q(**{field: query})
    return combined


def _machine_scope_filter(user: Any):
    from tasks.scope import machine_scope_filter

    return machine_scope_filter(user)


def _work_order_scope_filter(user: Any):
    from tasks.scope import work_order_scope_filter

    return work_order_scope_filter(user)


def search_machines(user: Any, query: str, limit: int = SEARCH_LIMIT, *, _model=None) -> list[int]:
    """Raw machine PK hints for a name/serial search (bounded, never grants).

    A SEARCH HINT through the actor's machine scope filter — never
    authorization: every hint is re-authorized again before it can become a
    candidate (``search_focus_candidates``).
    """
    if user is None or not query:
        return []
    if _model is None:
        from assets.models import AssetMachine

        _model = AssetMachine
    try:
        rows = (
            _model.objects
            .filter(_machine_scope_filter(user))
            .filter(_match_query(MACHINE_MATCH_FIELDS, query))
            .order_by("pk")[:limit]
        )
    except Exception:
        return []
    return [int(row.pk) for row in rows]


def search_work_orders(
    user: Any, query: str, limit: int = SEARCH_LIMIT, *, _model=None
) -> list[int]:
    """Raw work-order PK hints for a reference/title search (bounded)."""
    if user is None or not query:
        return []
    if _model is None:
        from tasks.models import WorkOrder

        _model = WorkOrder
    try:
        rows = (
            _model.objects
            .filter(_work_order_scope_filter(user))
            .filter(_match_query(WORK_ORDER_MATCH_FIELDS, query))
            .order_by("pk")[:limit]
        )
    except Exception:
        return []
    return [int(row.pk) for row in rows]


def search_cards(user: Any, query: str, limit: int = SEARCH_LIMIT, *, _model=None) -> list[int]:
    """Raw board-card PK hints for a title search (bounded).

    The parent work order's authorized rows define the search population
    (``authorized_kanban_card`` re-checks the pair on every resolution);
    the card PK stays distinct from its parent.
    """
    if user is None or not query:
        return []
    if _model is None:
        from tasks.models import KanbanCard, WorkOrder

        _model = KanbanCard
        _work_order_model = WorkOrder
    else:
        from tasks.models import WorkOrder as _work_order_model
    try:
        authorized = _work_order_model.objects.filter(_work_order_scope_filter(user))
        rows = (
            _model.objects
            .filter(work_order__in=authorized, is_active=True)
            .filter(_match_query(CARD_MATCH_FIELDS, query))
            .order_by("pk")[:limit]
        )
    except Exception:
        return []
    return [int(row.pk) for row in rows]


@dataclass(frozen=True, slots=True)
class FocusCandidatePage:
    """One bounded candidate page: canonical projections + truncation truth.

    ``has_more`` is computed from an actual server-side limit+1 fetch — a
    truncated page must NEVER look like a unique match to the caller.
    """

    candidates: tuple[FocusTarget, ...]
    has_more: bool

    @property
    def returned_count(self) -> int:
        return len(self.candidates)


def _authorized_target(user: Any, kind: str, pk: int) -> FocusTarget | None:
    """Re-authorize one raw hint and project the CANONICAL label (deny generic)."""
    try:
        return resolve_focus_candidate(user, FocusCandidate(kind=kind, id=pk))
    except FocusRejected:
        return None


def search_focus_candidates(
    user: Any,
    kind: str | None,
    query: str,
    numeric_id: int | None = None,
    *,
    limit: int = SEARCH_LIMIT,
) -> FocusCandidatePage:
    """Authorized candidate projections for a spoken reference.

    A numeric id resolves to at most ONE candidate (or none when the reader
    denies it — unknown and unauthorized are indistinguishable). A name hint
    searches the actor's scoped rows and EVERY hit is re-authorized before
    its label is exposed. ``kind=None`` searches every kind — but a bare
    numeric id with no kind returns NOTHING (never guess between machine 3,
    task 3 and work order 3). The fetch is bounded at ``limit + 1`` per
    search so ``has_more`` is real server-side truncation, not a guess.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise FocusValidationError("limit must be a positive integer")
    if numeric_id is not None:
        if kind not in (KIND_MACHINE, KIND_KANBAN_CARD, KIND_WORK_ORDER):
            return FocusCandidatePage((), False)
        target = _authorized_target(user, str(kind), numeric_id)
        return FocusCandidatePage((target,) if target is not None else (), False)
    query = (query or "").strip()
    if not query and kind is None:
        # Kind-less switches need an actual name ("talk about the pump") —
        # nothing enumerable to disambiguate.
        return FocusCandidatePage((), False)
    searchers = {
        KIND_MACHINE: search_machines,
        KIND_WORK_ORDER: search_work_orders,
        KIND_KANBAN_CARD: search_cards,
    }
    kinds = [kind] if kind in searchers else list(searchers)
    truncated = False
    targets: list[FocusTarget] = []
    seen: set[tuple[str, int]] = set()
    for each_kind in kinds:
        try:
            pks = searchers[each_kind](user, query, limit + 1)
        except Exception:
            continue
        if len(pks) > limit:
            truncated = True
        for pk in pks:
            if len(targets) >= limit:
                truncated = True
                break
            target = _authorized_target(user, str(each_kind), pk)
            if target is None or (target.kind, target.id) in seen:
                continue
            seen.add((target.kind, target.id))
            targets.append(target)
    return FocusCandidatePage(tuple(targets[:limit]), truncated)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def resolve_focus_candidate(user: Any, candidate: FocusCandidate) -> FocusTarget:
    """Authorize one candidate and project it canonically.

    Raises ``FocusRejected`` for every failure, without distinguishing
    unknown from unauthorized. Labels and the machine/work-order
    relationships are resolved here from the reader's rows — a client can
    never supply them.
    """
    try:
        if candidate.kind == KIND_MACHINE:
            machine = read_machine(user, candidate.id)
            if machine is None:
                raise FocusRejected()
            return FocusTarget(
                kind=KIND_MACHINE,
                id=candidate.id,
                label=str(getattr(machine, "name", "") or f"Machine {candidate.id}"),
                machine_id=_int_or_none(getattr(machine, "pk", None)) or candidate.id,
                work_order_id=None,
            )
        if candidate.kind == KIND_WORK_ORDER:
            work_order = read_work_order(user, candidate.id)
            if work_order is None:
                raise FocusRejected()
            label = (
                getattr(work_order, "reference", None)
                or getattr(work_order, "title", None)
                or f"Work order {candidate.id}"
            )
            return FocusTarget(
                kind=KIND_WORK_ORDER,
                id=candidate.id,
                label=str(label),
                machine_id=_int_or_none(getattr(work_order, "machine_id", None)),
                work_order_id=_int_or_none(getattr(work_order, "pk", None)) or candidate.id,
            )
        # KIND_KANBAN_CARD: the card keeps BOTH identities. A missing parent
        # relationship is a rejection — never fall back to a similarly
        # numbered work order.
        pair = read_kanban_card(user, candidate.id)
        if pair is None:
            raise FocusRejected()
        card, work_order = pair
        if card is None or work_order is None:
            raise FocusRejected()
        return FocusTarget(
            kind=KIND_KANBAN_CARD,
            id=candidate.id,
            label=str(getattr(card, "title", "") or f"Task {candidate.id}"),
            machine_id=_int_or_none(getattr(work_order, "machine_id", None)),
            work_order_id=_int_or_none(getattr(card, "work_order_id", None))
            or _int_or_none(getattr(work_order, "pk", None)),
        )
    except FocusRejected:
        raise
    except Exception as exc:
        # A reader blowing up must not disclose why; it is not a grant and
        # never becomes one by exception.
        raise FocusRejected() from exc


__all__ = [
    "CARD_MATCH_FIELDS",
    "MACHINE_MATCH_FIELDS",
    "SEARCH_LIMIT",
    "WORK_ORDER_MATCH_FIELDS",
    "FocusCandidatePage",
    "read_kanban_card",
    "read_machine",
    "read_work_order",
    "resolve_focus_candidate",
    "search_cards",
    "search_focus_candidates",
    "search_machines",
    "search_work_orders",
]
