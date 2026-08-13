"""Turn-scoped tool-event sink (S46).

The invocation-guard middleware wraps every tool call but must not know
turn-service internals; the turn service owns the emitter but must not know
tool dispatch. A ContextVar-bound sink bridges them, following the
``correlation.py`` / ``questions.promotion`` precedents: the turn service
binds a sink around workflow execution (flag-gated), and the middleware —
however deep inside the agent framework — emits through whatever sink is
bound, or does nothing.

Fault discipline: events carry the tool NAME, a server-minted call id,
status, and duration ONLY. Arguments and results are contentful by
definition and never emitted — ``TOOL_CALL_ARGS`` / ``TOOL_CALL_RESULT``
have no call sites, pinned by a source test. Every emit is fail-soft: a
sink failure must never affect the dispatch it observes.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ai.core.streaming import AGUIEvent, EventType

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

#: A hung tool loop must not flood the stream or the stored events.
MAX_TOOL_EVENTS_PER_TURN = 200


@dataclass
class ToolEventSink:
    """Emit content-free tool lifecycle events onto one turn's emitter."""

    emitter: Any
    thread_id: str
    run_id: str
    _emitted: int = field(default=0, init=False)
    #: Calls whose START made it onto the wire. Their END always follows —
    #: a cap that lands mid-pair would strand an open TOOL_CALL_START, which
    #: sticks the activity strip on "running" and makes the AG-UI client's
    #: verifyEvents reject the run's finish (P9 review finding: MAF runs
    #: parallel tool calls, so STARTs and ENDs interleave freely).
    _open: set[str] = field(default_factory=set, init=False)

    async def started(self, tool_call_id: str, tool_name: str) -> None:
        """TOOL_CALL_START: the call exists; nothing about its arguments."""
        if self._emitted >= MAX_TOOL_EVENTS_PER_TURN:
            return
        self._open.add(tool_call_id)
        await self._emit(
            EventType.TOOL_CALL_START,
            {"toolCallId": tool_call_id, "toolCallName": tool_name},
        )

    async def ended(
        self,
        tool_call_id: str,
        tool_name: str,
        status: str,
        duration_ms: float,
    ) -> None:
        """TOOL_CALL_END: ok | denied | error, with wall-clock duration."""
        if tool_call_id in self._open:
            self._open.discard(tool_call_id)
        elif self._emitted >= MAX_TOOL_EVENTS_PER_TURN:
            # No START on the wire and the cap is hit: drop the END too.
            return
        await self._emit(
            EventType.TOOL_CALL_END,
            {
                "toolCallId": tool_call_id,
                "toolCallName": tool_name,
                "status": status,
                "durationMs": round(duration_ms, 1),
            },
        )

    async def _emit(self, event_type: EventType, data: dict[str, Any]) -> None:
        self._emitted += 1
        try:
            await self.emitter.emit(
                AGUIEvent(
                    event_type=event_type,
                    data=data,
                    thread_id=self.thread_id,
                    run_id=self.run_id,
                    agent_name="root_workflow",
                )
            )
        except Exception:  # pragma: no cover - observation must never raise
            logger.debug("tool event emit failed", exc_info=False)


current_tool_event_sink: ContextVar[ToolEventSink | None] = ContextVar(
    "aimms_tool_event_sink", default=None
)


@contextmanager
def bind_tool_event_sink(emitter: Any, thread_id: str, run_id: str) -> Iterator[ToolEventSink]:
    """Bind a sink for one turn's workflow execution."""
    sink = ToolEventSink(emitter=emitter, thread_id=str(thread_id), run_id=str(run_id))
    token = current_tool_event_sink.set(sink)
    try:
        yield sink
    finally:
        current_tool_event_sink.reset(token)


__all__ = [
    "MAX_TOOL_EVENTS_PER_TURN",
    "ToolEventSink",
    "bind_tool_event_sink",
    "current_tool_event_sink",
]
