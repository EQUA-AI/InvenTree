"""Correlation-id spine helpers (S36).

One server-minted id joins utterance -> route -> tool calls -> proposal ->
WorkOrderEvent. This module owns the pieces every plane shares: the AIMMS
uuid5 namespace, the deterministic voice per-turn child id, and a
ContextVar carrying the active turn's id so infrastructure that used to
mint throwaway uuid4s (reflection middleware) can consume the turn's id
instead.

Ids are telemetry, never authority: nothing here may influence
authorization, routing, or record selection.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

#: Fixed application namespace for deterministic uuid5 mints,
#: uuid5(NAMESPACE_DNS, "aimms.equa.work"). Deriving it at import keeps the
#: constant self-documenting; the value never changes.
NAMESPACE_AIMMS = uuid.uuid5(uuid.NAMESPACE_DNS, "aimms.equa.work")

_current: ContextVar[str] = ContextVar("aimms_correlation_id", default="")


def bind_correlation(correlation_id: str):
    """Bind the active turn's correlation id to this async context."""
    return _current.set(str(correlation_id or ""))


def current_correlation() -> str:
    """The active turn's correlation id, or '' outside a turn."""
    return _current.get()


def voice_turn_correlation(session_correlation_id, turn_key: str) -> str:
    """Deterministic per-turn child of a voice session's correlation id.

    ``uuid5(NAMESPACE_AIMMS, "<session-correlation>:<turn-key>")`` where the
    turn key is the turn's idempotency key (``voice:<session>:<item-id>``) —
    no randomness and no counter ordering, so a replayed or retried turn
    re-derives the same id. The session's own correlation id stays the
    parent attribute on voice telemetry; this child gives each turn its own
    spine entry.
    """
    return str(uuid.uuid5(NAMESPACE_AIMMS, f"{session_correlation_id}:{turn_key}"))


__all__ = [
    "NAMESPACE_AIMMS",
    "bind_correlation",
    "current_correlation",
    "voice_turn_correlation",
]
