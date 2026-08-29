"""The per-turn analysis-scope carrier for retrieval (S5, WP-A1).

S1 bound an immutable scope snapshot to every turn (``TurnRun.analysis_scope``)
and then nothing downstream could see it: ``_assemble_workflow_context`` never
forwarded it, so every reader and corpus ran exactly as if the thread had no
scope. This module is the missing carrier — a turn-scoped ContextVar bound in
``turn/execution.py::_run_legacy_workflow`` right beside the tool-capture
ledger (the established precedent for turn-scoped state that must reach any
tool body at any agent-framework depth; ContextVars propagate through
``sync_to_async``).

Contract:

- The context is a *narrowing* input, never authorization. Readers apply it
  AFTER their own scope/permission predicates; ``tasks/scope.py`` stays
  untouched.
- ``tasks/ai_read.py`` and ``assets/ai_read.py`` never import ``ai.*`` — the
  AI-plane tool wrappers read ``current_turn_scope()`` and pass plain kwargs
  down. Corpora (AI-plane modules) read it directly.
- Rebind, never set/reset: every turn binds fresh (possibly ``None``), so an
  early-exit turn cannot leak scope into the next turn on the same task.
- Only ``mode == explicit_assets`` with machine ids activates shadow or
  enforce behavior anywhere; legacy/fleet scopes bind an inert context so
  telemetry can still see the mode.
"""

from __future__ import annotations

import hashlib
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from ai.core.analysis.scope import (
    MODE_EXPLICIT,
    MODE_LEGACY,
    AnalysisScope,
    scope_from_stored,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TurnScopeContext:
    """One turn's immutable analysis-scope view for retrieval surfaces."""

    mode: str
    machine_ids: frozenset[int]
    #: Raw machine serials (index-side ``asset_id`` values) resolved server-
    #: side at bind time. NOT the grounding fence keys — the corpus filter
    #: needs the stored form, the fence needs the normalized form.
    machine_serials: frozenset[str]
    date_from: str | None
    date_to: str | None
    source_classes: frozenset[str]
    scope_hash: str
    scope_version: int
    #: Turn-stable snapshot identity: every retrieval envelope in one turn
    #: carries the same id (S5's minimal §8.3 — full operand materialization
    #: is the S7 executor's job).
    snapshot_id: str
    thread_pk: int | None
    display_label: str
    shadow: bool
    enforce: bool

    @property
    def explicit(self) -> bool:
        """Whether this scope actively narrows retrieval to named assets."""
        return self.mode == MODE_EXPLICIT and bool(self.machine_ids)

    @property
    def active(self) -> bool:
        """Whether any shadow/enforce behavior may run this turn."""
        return self.explicit and (self.shadow or self.enforce)


turn_scope_context: ContextVar[TurnScopeContext | None] = ContextVar(
    "aimms_turn_scope", default=None
)


def _snapshot_id(scope_hash: str, version: int, turn_pk: object) -> str:
    digest = hashlib.sha256(f"{scope_hash}|{version}|{turn_pk}".encode()).hexdigest()
    return f"snap_{digest[:20]}"


def bind_turn_scope(
    snapshot: dict[str, Any] | None,
    *,
    thread_pk: int | None,
    turn_pk: object,
    serials: frozenset[str] = frozenset(),
) -> TurnScopeContext | None:
    """Bind this turn's scope context from the S1 snapshot; returns it.

    ``snapshot`` is the ``{"scope", "version", "hash"}`` dict ``begin_turn``
    bound under the thread row lock (None on threads without typed scope).
    A missing/malformed snapshot binds ``None`` — retrieval behaves exactly
    as before S5. Always rebinds (never set/reset), mirroring
    ``bind_tool_captures``.
    """
    if not isinstance(snapshot, dict) or not snapshot:
        turn_scope_context.set(None)
        return None
    scope: AnalysisScope = scope_from_stored(snapshot.get("scope"))
    try:
        version = int(snapshot.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    stored_hash = str(snapshot.get("hash") or "")
    if scope.mode == MODE_LEGACY and version == 0 and not stored_hash:
        # Nothing typed ever landed on this thread.
        turn_scope_context.set(None)
        return None

    from ai.core.config import get_settings

    settings = get_settings()
    context = TurnScopeContext(
        mode=scope.mode,
        machine_ids=frozenset(scope.machine_ids),
        machine_serials=frozenset(serials),
        date_from=scope.date_from,
        date_to=scope.date_to,
        source_classes=frozenset(scope.source_classes),
        scope_hash=stored_hash,
        scope_version=version,
        snapshot_id=_snapshot_id(stored_hash, version, turn_pk),
        thread_pk=thread_pk,
        display_label=scope.display_label,
        shadow=bool(getattr(settings, "feature_ai_thread_scope_shadow", False)),
        enforce=bool(getattr(settings, "feature_ai_thread_scope_enforce", False)),
    )
    turn_scope_context.set(context)
    return context


def current_turn_scope() -> TurnScopeContext | None:
    """The bound scope context, or None. Never raises."""
    try:
        return turn_scope_context.get()
    except Exception:  # pragma: no cover - observation must never kill a turn
        return None


def scope_miss_for_machine(machine_id: Any) -> dict[str, Any] | None:
    """The typed out-of-analysis-scope miss for one record, or None.

    Enforce mode returns a RECOVERABLE miss payload — it names the scope (the
    server display label, never record content) and tells the model to offer
    a scope change; the record itself is not disclosed. Shadow mode logs a
    content-free line and lets the record through. Unscoped/fleet turns and
    machine-less records always pass.
    """
    scope = current_turn_scope()
    if scope is None or not scope.explicit or machine_id is None:
        return None
    try:
        if int(machine_id) in scope.machine_ids:
            return None
    except (TypeError, ValueError):
        return None
    if not scope.enforce:
        if scope.shadow:
            logger.info("scope.shadow.record_out_of_scope thread=%s", scope.thread_pk)
        return None
    label = scope.display_label or f"{len(scope.machine_ids)} selected assets"
    return {
        "scope_miss": True,
        "code": "out_of_analysis_scope",
        "scope_label": label,
        "message": (
            "This record belongs to a machine outside the conversation's "
            f"analysis scope ({label}). Tell the user it was excluded by the "
            "active scope, and offer to change the scope if they want it "
            "included."
        ),
    }


def resolve_scope_serials(user: Any, machine_ids: Any) -> frozenset[str]:
    """Raw serials of the scope's machines, re-authorized per id.

    Django-only (lazy import), mirroring ``grounding.machine_serials`` but
    WITHOUT fence-key normalization: the controlled-document index stores the
    operator-entered serial verbatim in ``asset_id``, so the enforce-mode
    corpus filter must use the stored form. Any error returns an empty set —
    the corpus then treats the scope as serial-less (``applicability
    unresolved`` under enforce), which narrows rather than widens.
    """
    try:
        from assets.ai_read import authorized_machine

        serials: set[str] = set()
        for machine_id in machine_ids:
            machine = authorized_machine(user, machine_id)
            serial = str(getattr(machine, "serial", "") or "").strip()
            if machine is not None and serial:
                serials.add(serial)
        return frozenset(serials)
    except Exception:
        logger.debug("scope serials unavailable", exc_info=False)
        return frozenset()


__all__ = [
    "TurnScopeContext",
    "bind_turn_scope",
    "current_turn_scope",
    "resolve_scope_serials",
    "scope_miss_for_machine",
    "turn_scope_context",
]
