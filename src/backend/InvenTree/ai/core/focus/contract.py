"""The conversation-focus contract (typed candidates, canonical projections).

The focus answers "what is this conversation about right now?" — a machine,
a board card (UI "Task", wire kind ``kanban_card``) or a work order. It is
deliberately DISTINCT from the other two rails:

* **authorization** (``scope_key``/``scope_hash`` plus the per-record scope
  resolvers) is never widened by a focus — a client id is a candidate, and
  every resolution re-runs the existing authorized readers;
* **analysis scope** (``ai.core.analysis.scope``) keeps its own version and
  fences; an out-of-scope focus does not pull those records into answers.

Stdlib-only: imported by the Django plane (``aichat.services.threads``) and
the AI plane alike, exactly like ``ai.core.analysis.scope``. Server-owned
labels and relationships are resolved in ``ai.core.focus.resolution``; this
module only defines shapes and fail-closed parsing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

SCHEMA_VERSION = 1

KIND_MACHINE = "machine"
KIND_KANBAN_CARD = "kanban_card"
KIND_WORK_ORDER = "work_order"

#: Ordered kinds for the generated wire contract (byte-deterministic).
FOCUS_KINDS: tuple[str, ...] = (KIND_MACHINE, KIND_KANBAN_CARD, KIND_WORK_ORDER)


class FocusValidationError(ValueError):
    """A malformed focus request. Safe to echo to the client."""


class FocusRejected(Exception):
    """An authorization intersection failed or the record is unavailable.

    Deliberately generic: the message never discloses which candidate id
    failed or whether it exists (same decision record as ``ScopeRejected``).
    """

    def __init__(self) -> None:
        super().__init__("Focus update rejected")


class FocusOutOfAnalysisScope(Exception):
    """The authorized target sits OUTSIDE the thread's explicit analysis scope.

    Focus never widens analysis scope: the selection is refused (state
    unchanged) and the client decides whether to keep the context, change
    scope explicitly or start a new conversation.
    """

    def __init__(self) -> None:
        super().__init__("Focus target is outside the analysis scope")


class FocusSetAsideUnavailable(Exception):
    """An unconfirmed decision could not be set aside before a focus change.

    Fail closed: a focus change/clear must never leave an ARMED decision
    confirmable against the OLD target, so when the decision coordinator
    cannot set it aside the change is refused and state is unchanged. Never
    retried; already confirmed/unknown effects stay on the receipt rails.
    """

    def __init__(self) -> None:
        super().__init__("Pending confirmation could not be set aside")


@dataclass(frozen=True, slots=True)
class FocusCandidate:
    """One client-identified candidate reference. Never a grant."""

    kind: str
    id: int


@dataclass(frozen=True, slots=True)
class FocusTarget:
    """The canonical, server-resolved focus projection.

    ``id`` is the PK of ``kind``; ``machine_id``/``work_order_id`` are the
    server-resolved relationships (a card keeps its DISTINCT parent work
    order; a work order keeps its machine). A board card's ``id`` is never
    its work order's id.
    """

    kind: str
    id: int
    label: str
    machine_id: int | None
    work_order_id: int | None


def _strict_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FocusValidationError("id must be a positive integer")
    return value


def normalize_expected_focus_version(value: object) -> int | None:
    """Strictly validate the client's focus stamp.

    ``None`` means "no stamp" (legacy clients stay byte-compatible). A stamp
    must be a REAL non-negative ``int`` — ``bool`` is not an int here, floats
    and numeric strings are not coerced (pydantic's lax int coercion is not
    enough), and negative stamps are invalid.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise FocusValidationError("expected_focus_version must be an integer")
    if value < 0:
        raise FocusValidationError("expected_focus_version must be zero or greater")
    return value


def normalize_focus_request(payload: object) -> FocusCandidate:
    """Validate one client candidate reference into a ``FocusCandidate``.

    Strict: exactly ``{kind, id}`` with a known kind and a positive integer
    id. Extra keys (a client-supplied grant, label or relationship) are
    rejected — those are server-resolved, never accepted from the wire.
    """
    if not isinstance(payload, Mapping):
        raise FocusValidationError("focus must be an object")
    if set(payload) != {"kind", "id"}:
        raise FocusValidationError("focus accepts exactly kind and id")
    kind = payload.get("kind")
    if kind not in FOCUS_KINDS:
        raise FocusValidationError("Unknown focus kind")
    return FocusCandidate(kind=str(kind), id=_strict_id(payload.get("id")))


def focus_payload(target: FocusTarget) -> dict[str, object]:
    """The canonical projection shape used on the wire and inside snapshots."""
    return {
        "kind": target.kind,
        "id": target.id,
        "label": target.label,
        "machine_id": target.machine_id,
        "work_order_id": target.work_order_id,
    }


def focus_to_stored(target: FocusTarget) -> dict[str, object]:
    """The durable JSON shape stored on the thread (version rides the column)."""
    return {"schema_version": SCHEMA_VERSION, **focus_payload(target)}


def focus_from_stored(payload: object) -> FocusTarget | None:
    """Read a stored focus payload back, or ``None``.

    Empty storage is "no focus". Anything malformed or from an unknown
    schema version ALSO degrades to ``None`` — the most restrictive state
    (ask for a target again), so corruption fails closed and can never fall
    back to another numbered record.
    """
    if not isinstance(payload, Mapping) or not payload:
        return None
    if payload.get("schema_version") != SCHEMA_VERSION:
        return None
    try:
        candidate = normalize_focus_request({"kind": payload.get("kind"), "id": payload.get("id")})
        label = payload.get("label")
        machine_id = payload.get("machine_id")
        work_order_id = payload.get("work_order_id")
        if not isinstance(label, str) or not label:
            return None
        for rel in (machine_id, work_order_id):
            if rel is not None and (isinstance(rel, bool) or not isinstance(rel, int) or rel <= 0):
                return None
    except FocusValidationError:
        return None
    return FocusTarget(
        kind=candidate.kind,
        id=candidate.id,
        label=label,
        machine_id=machine_id,
        work_order_id=work_order_id,
    )


def canonical_focus_json(target: FocusTarget) -> str:
    """The canonical (sorted, compact) JSON used for identity comparisons."""
    return json.dumps(
        focus_to_stored(target), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def card_read_payload(
    *,
    kanban_card_id: int,
    work_order_id: int,
    machine_id: int | None,
    title: str,
    card_kind: str,
    board_column_status: str,
    work_order_lifecycle_status: str,
    work_order_reference: str | None,
) -> dict[str, object]:
    """The board-card read projection with BOTH identities named explicitly.

    ``board_column_status`` is the card's board column key (execution
    tracking); ``work_order_lifecycle_status`` is the governed job lifecycle.
    A board-column change must never imply a governed lifecycle transition,
    so the two are separate fields and there is no ambiguous bare ``id``.
    """
    return {
        "kanban_card_id": kanban_card_id,
        "work_order_id": work_order_id,
        "machine_id": machine_id,
        "title": title,
        "card_kind": card_kind,
        "board_column_status": board_column_status,
        "work_order_lifecycle_status": work_order_lifecycle_status,
        "work_order_reference": work_order_reference,
    }


__all__ = [
    "FOCUS_KINDS",
    "KIND_KANBAN_CARD",
    "KIND_MACHINE",
    "KIND_WORK_ORDER",
    "SCHEMA_VERSION",
    "FocusCandidate",
    "FocusOutOfAnalysisScope",
    "FocusRejected",
    "FocusSetAsideUnavailable",
    "FocusTarget",
    "FocusValidationError",
    "canonical_focus_json",
    "card_read_payload",
    "focus_from_stored",
    "focus_payload",
    "focus_to_stored",
    "normalize_expected_focus_version",
    "normalize_focus_request",
]
