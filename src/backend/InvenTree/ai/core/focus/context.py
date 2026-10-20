"""The per-turn conversation-focus carrier (ContextVar, rebind-never-reset).

``begin_turn`` snapshots the thread focus under the row lock
(``TurnRun.focus_snapshot``); ``turn/execution.py`` binds it here beside the
tool-capture ledger and the analysis-scope carrier (the established
precedent for turn-scoped state that must reach any tool body at any
agent-framework depth — ContextVars propagate through ``sync_to_async``).

Contract: the focus is a routing/continuity hint, NEVER authorization. Every
reader still runs its own scope/permission predicates and the analysis-scope
fence stays in force. Rebind every turn (possibly ``None``) so an early-exit
turn cannot leak focus into the next turn.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from ai.core.focus.contract import focus_from_stored, focus_payload


@dataclass(frozen=True, slots=True)
class TurnFocusContext:
    """One turn's immutable conversation-focus view."""

    kind: str
    id: int
    label: str
    machine_id: int | None
    work_order_id: int | None
    focus_version: int

    @property
    def projection(self) -> dict[str, Any]:
        """The canonical hash-free projection (wire shape)."""
        from ai.core.focus.contract import FocusTarget

        return focus_payload(
            FocusTarget(
                kind=self.kind,
                id=self.id,
                label=self.label,
                machine_id=self.machine_id,
                work_order_id=self.work_order_id,
            )
        )


turn_focus_context: ContextVar[TurnFocusContext | None] = ContextVar(
    "aimms_turn_focus", default=None
)


def bind_turn_focus(
    snapshot: dict[str, Any] | None,
) -> TurnFocusContext | None:
    """Bind this turn's focus from the stored snapshot; returns it.

    ``snapshot`` is ``{"focus": <stored projection>, "version": int}`` as
    bound by ``begin_turn`` (None on threads without a focus). Malformed
    snapshots bind ``None`` — fail closed, never a guessed record.
    """
    target = None
    version = 0
    if isinstance(snapshot, dict):
        target = focus_from_stored(snapshot.get("focus"))
        version = int(snapshot.get("version") or 0)
    context = (
        TurnFocusContext(
            kind=target.kind,
            id=target.id,
            label=target.label,
            machine_id=target.machine_id,
            work_order_id=target.work_order_id,
            focus_version=version,
        )
        if target is not None
        else None
    )
    turn_focus_context.set(context)
    return context


def current_turn_focus() -> TurnFocusContext | None:
    """The focus this turn is bound to (None = no confirmed target)."""
    return turn_focus_context.get()


__all__ = ["TurnFocusContext", "bind_turn_focus", "current_turn_focus", "turn_focus_context"]
