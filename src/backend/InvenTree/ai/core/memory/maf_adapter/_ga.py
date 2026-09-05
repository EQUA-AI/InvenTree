"""GA-shape providers (agent-framework-core 1.16+; plan §9.2 table).

Importable ONLY where ``HistoryProvider`` exists — the pin raises
ImportError here and ``maf_adapter.__init__`` never imports this module on
that shape. Proven on the ``ga`` entry of ``ai_maf_matrix.yaml``; every
test is ``skipif(MAF_SHAPE != "ga")``.

Three providers, one rule each:

* ``AimmsLedgerHistoryProvider`` — the ONE ``load_messages=True`` loader.
  ``get_messages`` reads the Django ledger through the scoped repository;
  ``save_messages`` is a no-op because ``begin_turn``/``complete_turn``
  already write it.
* ``AimmsMemoryContextProvider`` — ``before_run`` extends the session
  context with the builder's memory block (USER role, never
  instructions); ``after_run`` only enqueues (extraction never runs on
  the request path).
* ``AimmsAuditHistoryProvider`` — ``load_messages=False``,
  ``store_context_messages=True``: the captured context feeds §9.8
  telemetry and golden replays; it never loads.
"""

from __future__ import annotations

from typing import Any

from agent_framework import (  # type: ignore[attr-defined]
    ContextProvider,
    HistoryProvider,
    Message,
    Role,
)
from ai.core.memory.context_assembler import REPLAY_CARRIER

SOURCE_LEDGER = "aimms.ledger"
SOURCE_MEMORY = "aimms.memory"
SOURCE_AUDIT = "aimms.audit"


def _messages_from_dicts(entries: list[dict[str, Any]]) -> list[Any]:
    out: list[Any] = []
    for entry in entries or []:
        role = str(entry.get("role") or "")
        text = str(entry.get("content") or "").strip()
        if role not in ("user", "assistant") or not text:
            continue
        out.append(Message(role=Role.ASSISTANT if role == "assistant" else Role.USER, text=text))
    return out


class AimmsLedgerHistoryProvider(HistoryProvider):
    """The only loader; reads the builder's replay dict, writes nothing."""

    def __init__(self, bundle_getter: Any, *, source_id: str = SOURCE_LEDGER):
        super().__init__(source_id, load_messages=True, store_inputs=False, store_outputs=False)
        self._bundle_getter = bundle_getter

    async def get_messages(
        self, session_id: str | None, *, state: dict[str, Any] | None = None, **kwargs: Any
    ) -> list[Any]:
        if REPLAY_CARRIER == "dict":
            # The dict still replays through the run input; loading here too
            # would replay twice (plan §9.2). Flip REPLAY_CARRIER first.
            return []
        bundle = self._bundle_getter()
        return _messages_from_dicts(bundle.replay_dict() if bundle is not None else [])

    async def save_messages(
        self,
        session_id: str | None,
        messages: Any,
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        return None


class AimmsMemoryContextProvider(ContextProvider):
    """Memory block into the session context; enqueue-only after the run."""

    def __init__(self, bundle_getter: Any, enqueue: Any = None, *, source_id: str = SOURCE_MEMORY):
        super().__init__(source_id)
        self._bundle_getter = bundle_getter
        self._enqueue = enqueue
        self.after_run_calls = 0

    async def before_run(
        self,
        *,
        agent: Any = None,
        session: Any = None,
        context: Any = None,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if REPLAY_CARRIER == "dict" or context is None:
            return
        bundle = self._bundle_getter()
        block = bundle.memory_block() if bundle is not None else None
        if block:
            context.extend_messages(
                self.source_id, [Message(role=Role.USER, text=str(block["content"]))]
            )

    async def after_run(
        self,
        *,
        agent: Any = None,
        session: Any = None,
        context: Any = None,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.after_run_calls += 1
        if self._enqueue is not None:
            # Enqueue only (plan §9.2): extraction runs on the ai-memory cluster.
            self._enqueue()


class AimmsAuditHistoryProvider(HistoryProvider):
    """Captures what other providers contributed; never loads, never replays."""

    def __init__(self, *, source_id: str = SOURCE_AUDIT):
        super().__init__(
            source_id,
            load_messages=False,
            store_inputs=False,
            store_context_messages=True,
            store_outputs=False,
        )
        self.captured: list[Any] = []

    async def get_messages(
        self, session_id: str | None, *, state: dict[str, Any] | None = None, **kwargs: Any
    ) -> list[Any]:
        return []

    async def save_messages(
        self,
        session_id: str | None,
        messages: Any,
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.captured.extend(list(messages or []))


__all__ = [
    "SOURCE_AUDIT",
    "SOURCE_LEDGER",
    "SOURCE_MEMORY",
    "AimmsAuditHistoryProvider",
    "AimmsLedgerHistoryProvider",
    "AimmsMemoryContextProvider",
]
