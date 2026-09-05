"""The pinned-SDK adapter: a ContextProvider that carries NO replay (M1 PR C).

Double-replay hazard (plan §9.2): while ``REPLAY_CARRIER == "dict"`` the
``conversation_history`` dict already replays the transcript through
``replay_messages``; a provider returning ``Context(messages=...)`` would
replay it twice, and ``Context(instructions=...)`` would render the memory
block as a SYSTEM message on this SDK (GR-19/GR-34 forbid a system-role
memory carrier). So ``invoking()`` returns an empty ``Context()`` and
``invoked()`` only records the ledger. Replay moves here only when the
dict is retired — never both at once.
"""

from __future__ import annotations

from typing import Any

from agent_framework import Context, ContextProvider
from ai.core.memory.context_assembler import REPLAY_CARRIER


def _count(messages: Any) -> int:
    if messages is None:
        return 0
    if isinstance(messages, (list, tuple)):
        return len(messages)
    return 1


class AimmsPinContextProvider(ContextProvider):
    """Records provider invocations; injects nothing while the dict replays."""

    def __init__(self) -> None:
        self.provider_calls = 0
        self.events: list[dict[str, Any]] = []
        self.threads_created: list[str | None] = []

    async def thread_created(self, thread_id: str | None) -> None:
        self.threads_created.append(thread_id)

    async def invoking(self, messages: Any, **kwargs: Any) -> Context:
        self.provider_calls += 1
        if REPLAY_CARRIER == "dict":
            return Context()
        # Reserved for the post-M1 carrier flip: Context(messages=[memory_block]).
        return Context()  # pragma: no cover

    async def invoked(
        self,
        request_messages: Any,
        response_messages: Any = None,
        invoke_exception: Exception | None = None,
        **kwargs: Any,
    ) -> None:
        self.events.append({
            "request_messages": _count(request_messages),
            "response_messages": _count(response_messages),
            "error": type(invoke_exception).__name__ if invoke_exception else None,
        })


__all__ = ["AimmsPinContextProvider"]
