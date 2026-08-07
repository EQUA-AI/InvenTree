"""
AIMMS Streaming and Events Module

Provides streaming events for AG-UI (Agent-User Interface) protocol.
Replaces ag_ui.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

logger = logging.getLogger(__name__)


class EventType(Enum):
    """
    Types of AG-UI events.

    Uses SCREAMING_SNAKE_CASE values to match AG-UI protocol specification.
    @see https://docs.ag-ui.com/concepts/events
    """

    # Lifecycle events
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    RUN_CANCELLED = "RUN_CANCELLED"

    # Agent state events
    AGENT_THINKING = "AGENT_THINKING"
    AGENT_EXECUTING = "AGENT_EXECUTING"
    AGENT_WAITING = "AGENT_WAITING"
    AGENT_HANDOFF = "AGENT_HANDOFF"

    # Text message events (AG-UI standard)
    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"
    TEXT_MESSAGE_CHUNK = "TEXT_MESSAGE_CHUNK"

    # Tool events (AG-UI standard)
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"

    # Structured question (S22): a turn that COMPLETES by asking. First-class
    # and persisted so replay reproduces the card; its data must never carry
    # top-level content/delta keys (stale clients would render them as text).
    QUESTION = "QUESTION"

    # HITL events
    HITL_REQUIRED = "HITL_REQUIRED"
    HITL_APPROVED = "HITL_APPROVED"
    HITL_REJECTED = "HITL_REJECTED"
    HITL_TIMEOUT = "HITL_TIMEOUT"

    # Progress events
    PROGRESS_UPDATE = "PROGRESS_UPDATE"
    STEP_STARTED = "STEP_STARTED"
    STEP_FINISHED = "STEP_FINISHED"

    # Workflow events
    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_STEP = "WORKFLOW_STEP"

    # State management events (AG-UI standard)
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    STATE_DELTA = "STATE_DELTA"
    MESSAGES_SNAPSHOT = "MESSAGES_SNAPSHOT"

    # Cache events
    CACHE_HIT = "CACHE_HIT"
    CACHE_MISS = "CACHE_MISS"

    # Error events
    ERROR = "ERROR"
    WARNING = "WARNING"

    # Special events (AG-UI standard)
    RAW = "RAW"
    CUSTOM = "CUSTOM"


@dataclass
class AGUIEvent:
    """
    Base AG-UI event structure.

    All events include:
    - event_id: Unique identifier
    - event_type: Type of event
    - timestamp: When the event occurred
    - thread_id: Associated conversation thread
    - run_id: Current run identifier
    - data: Event-specific payload
    """

    event_type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    thread_id: str = ""
    run_id: str = ""
    agent_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize event to AG-UI protocol format.

        The 'type' field is the primary identifier per AG-UI spec.
        """
        # Base AG-UI event structure
        result = {
            "type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "threadId": self.thread_id,
            "runId": self.run_id,
        }

        # Merge in event-specific data
        result.update(self.data)

        # Add optional fields if present
        if self.agent_name:
            result["agentName"] = self.agent_name
        if self.event_id:
            result["eventId"] = self.event_id

        return result

    def to_json(self) -> str:
        """Serialize event to JSON."""
        return json.dumps(self.to_dict())

    def to_sse(self) -> str:
        """Format as Server-Sent Event."""
        return f"event: {self.event_type.value}\ndata: {self.to_json()}\n\n"


class EventHandler(Protocol):
    """Protocol for event handlers."""

    async def handle(self, event: AGUIEvent) -> None:
        """Handle an event."""
        ...


class EventEmitter(ABC):
    """Abstract base class for event emitters."""

    @abstractmethod
    async def emit(self, event: AGUIEvent) -> None:
        """Emit an event."""
        ...

    @abstractmethod
    async def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        """
        Subscribe to events.

        Returns:
            Unsubscribe function
        """
        ...


class InMemoryEventEmitter(EventEmitter):
    """
    In-memory event emitter for local development.

    Broadcasts events to all subscribed handlers.
    """

    def __init__(self):
        self._handlers: list[EventHandler] = []
        self._event_queue: asyncio.Queue[AGUIEvent] = asyncio.Queue()
        self._running = False

    async def emit(self, event: AGUIEvent) -> None:
        """Emit an event to all handlers."""
        logger.debug(
            f"Emitting event: {event.event_type.value} "
            f"[thread={event.thread_id} run={event.run_id}]"
        )

        # Notify all handlers
        for handler in self._handlers:
            try:
                await handler.handle(event)
            except Exception as e:
                logger.error(f"Event handler error: {e}")

    async def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        """Subscribe to events."""
        self._handlers.append(handler)

        def unsubscribe():
            if handler in self._handlers:
                self._handlers.remove(handler)

        return unsubscribe

    def clear_handlers(self) -> None:
        """Remove all handlers."""
        self._handlers.clear()


class SSEEventStream:
    """
    Server-Sent Events stream for HTTP clients.

    Usage:
        stream = SSEEventStream(emitter, thread_id="...")
        async for sse_data in stream.events():
            yield sse_data  # In FastAPI streaming response
    """

    def __init__(self, emitter: EventEmitter, thread_id: str | None = None):
        self.emitter = emitter
        self.thread_id = thread_id
        self._queue: asyncio.Queue[AGUIEvent | None] = asyncio.Queue()
        self._unsubscribe: Callable[[], None] | None = None

    async def start(self) -> None:
        """Start listening for events."""
        if self._unsubscribe is not None:
            return
        handler = QueueEventHandler(self._queue, self.thread_id)
        self._unsubscribe = await self.emitter.subscribe(handler)

    async def stop(self) -> None:
        """Stop listening for events."""
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

        # Signal end of stream
        await self._queue.put(None)

    async def events(self) -> AsyncIterator[str]:
        """
        Yield SSE-formatted events.
        """
        await self.start()

        try:
            while True:
                try:
                    # Wait for event with timeout for keepalive
                    event = await asyncio.wait_for(self._queue.get(), timeout=15.0)
                    if event is None:
                        break
                    yield event.to_sse()
                except TimeoutError:
                    # Send keepalive comment to prevent connection timeout
                    yield ": ping\n\n"
        finally:
            await self.stop()


@dataclass
class QueueEventHandler:
    """Event handler that puts events in a queue."""

    queue: asyncio.Queue[AGUIEvent | None]
    thread_id: str | None = None

    async def handle(self, event: AGUIEvent) -> None:
        """Handle event by putting in queue."""
        # Filter by thread_id if specified
        if self.thread_id and event.thread_id and event.thread_id != self.thread_id:
            return

        await self.queue.put(event)


class EventCollector:
    """Event handler that collects events in memory.

    Useful for tests and diagnostics where emitted events need to be
    inspected after the fact.
    """

    def __init__(self, thread_id: str | None = None):
        self.thread_id = thread_id
        self._events: list[AGUIEvent] = []

    async def handle(self, event: AGUIEvent) -> None:
        """Handle event by recording it."""
        # Filter by thread_id if specified
        if self.thread_id and event.thread_id and event.thread_id != self.thread_id:
            return

        self._events.append(event)

    @property
    def events(self) -> list[AGUIEvent]:
        """All collected events, in emission order."""
        return list(self._events)

    def get_events(self, event_type: EventType | None = None) -> list[AGUIEvent]:
        """Return collected events, optionally filtered by type."""
        if event_type is None:
            return list(self._events)
        return [e for e in self._events if e.event_type == event_type]

    def clear(self) -> None:
        """Discard all collected events."""
        self._events.clear()


class RunContext:
    """
    Context manager for a single agent run.

    Provides convenient methods for emitting events
    and tracking run state.
    """

    def __init__(
        self,
        emitter: EventEmitter | None = None,
        thread_id: str = "",
        agent_name: str = "",
        workflow_id: str = "",
        run_id: str | None = None,
    ):
        self.emitter = emitter
        self.thread_id = thread_id
        self.agent_name = agent_name
        self.workflow_id = workflow_id
        self.run_id = run_id or str(uuid.uuid4())
        self._step_count = 0
        self._message_id: str | None = None  # Track current message for text events

    async def __aenter__(self) -> RunContext:
        """Start the run."""
        await self.emit(
            EventType.RUN_STARTED,
            {
                "workflow_id": self.workflow_id,
                "message": f"Starting run with {self.agent_name or 'agent'}",
            },
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        """End the run."""
        if exc_type:
            await self.emit_error(str(exc_val))
            return False
        return True

    async def emit(
        self,
        event_type: EventType,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Emit an event."""
        if self.emitter is None:
            logger.debug(f"No emitter configured, skipping event: {event_type.value}")
            return

        event = AGUIEvent(
            event_type=event_type,
            data=data or {},
            thread_id=self.thread_id,
            run_id=self.run_id,
            agent_name=self.agent_name,
        )
        await self.emitter.emit(event)

    # ===== Lifecycle Events =====

    async def emit_run_started(self, agent_name: str = "") -> None:
        """Emit RUN_STARTED event."""
        if agent_name:
            self.agent_name = agent_name
        await self.emit(
            EventType.RUN_STARTED,
            {
                "agent_name": self.agent_name,
                "workflow_id": self.workflow_id,
                "message": f"Starting run with {self.agent_name or 'agent'}",
            },
        )

    async def emit_run_finished(self, result: Any = None) -> None:
        """Emit RUN_FINISHED event."""
        await self.emit(
            EventType.RUN_FINISHED,
            {
                "result": str(result)[:1000] if result else None,
            },
        )

    # ===== Agent State Events =====

    async def emit_agent_thinking(self, message: str = "") -> None:
        """Emit AGENT_THINKING event."""
        await self.emit(EventType.AGENT_THINKING, {"message": message})

    async def emit_agent_executing(self, action: str = "") -> None:
        """Emit AGENT_EXECUTING event."""
        await self.emit(EventType.AGENT_EXECUTING, {"action": action})

    async def emit_thinking(self, message: str = "") -> None:
        """Emit agent thinking event (alias for emit_agent_thinking)."""
        await self.emit_agent_thinking(message)

    async def emit_executing(self, action: str = "") -> None:
        """Emit agent executing event (alias for emit_agent_executing)."""
        await self.emit_agent_executing(action)

    async def emit_waiting(self, reason: str = "") -> None:
        """Emit agent waiting event."""
        await self.emit(EventType.AGENT_WAITING, {"reason": reason})

    async def emit_handoff(self, from_agent: str, to_agent: str, reason: str = "") -> None:
        """Emit agent handoff event."""
        await self.emit(
            EventType.AGENT_HANDOFF,
            {
                "fromAgent": from_agent,
                "toAgent": to_agent,
                "reason": reason,
            },
        )

    # ===== Text Message Events =====

    async def emit_text_start(self, message_id: str | None = None, role: str = "assistant") -> str:
        """
        Emit text message start event (TEXT_MESSAGE_START).

        Args:
            message_id: Optional message ID. If not provided, generates one.
            role: Message role (default: "assistant")

        Returns:
            The message ID being used
        """
        self._message_id = message_id or str(uuid.uuid4())
        await self.emit(
            EventType.TEXT_MESSAGE_START,
            {
                "messageId": self._message_id,
                "role": role,
            },
        )
        return self._message_id

    async def emit_text_end(self, message_id: str | None = None) -> None:
        """Emit text message end event (TEXT_MESSAGE_END)."""
        await self.emit(
            EventType.TEXT_MESSAGE_END,
            {
                "messageId": message_id or self._message_id or str(uuid.uuid4()),
            },
        )
        self._message_id = None

    async def emit_tool_started(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> str:
        """
        Emit tool call started event.

        Returns:
            toolCallId for tracking this tool call
        """
        tool_call_id = str(uuid.uuid4())
        await self.emit(
            EventType.TOOL_CALL_START,
            {
                "toolCallId": tool_call_id,
                "toolCallName": tool_name,
                "arguments": arguments or {},
            },
        )
        return tool_call_id

    async def emit_tool_args(self, tool_call_id: str, delta: str) -> None:
        """Emit tool call arguments chunk (TOOL_CALL_ARGS)."""
        await self.emit(
            EventType.TOOL_CALL_ARGS,
            {
                "toolCallId": tool_call_id,
                "delta": delta,
            },
        )

    async def emit_tool_completed(
        self, tool_call_id: str, tool_name: str, result: Any = None
    ) -> None:
        """Emit tool call completed event."""
        await self.emit(
            EventType.TOOL_CALL_END,
            {
                "toolCallId": tool_call_id,
                "toolCallName": tool_name,
            },
        )
        # Also emit the result if provided
        if result is not None:
            await self.emit(
                EventType.TOOL_CALL_RESULT,
                {
                    "toolCallId": tool_call_id,
                    "content": str(result)[:500],  # Truncate for events
                },
            )

    async def emit_tool_failed(self, tool_name: str, error: str) -> None:
        """Emit tool call failed event."""
        await self.emit(
            EventType.RUN_ERROR,
            {
                "toolCallName": tool_name,
                "message": error,
                "code": "TOOL_CALL_FAILED",
            },
        )

    async def emit_hitl_required(
        self,
        action: str,
        details: dict[str, Any] | None = None,
        timeout_seconds: int = 300,
    ) -> None:
        """Emit HITL approval required event."""
        await self.emit(
            EventType.HITL_REQUIRED,
            {
                "action": action,
                "details": details or {},
                "timeout_seconds": timeout_seconds,
            },
        )

    async def emit_hitl_approved(self, action: str, approver: str = "") -> None:
        """Emit HITL approved event."""
        await self.emit(
            EventType.HITL_APPROVED,
            {
                "action": action,
                "approver": approver,
            },
        )

    async def emit_hitl_rejected(self, action: str, reason: str = "", rejecter: str = "") -> None:
        """Emit HITL rejected event."""
        await self.emit(
            EventType.HITL_REJECTED,
            {
                "action": action,
                "reason": reason,
                "rejecter": rejecter,
            },
        )

    async def emit_text_delta(self, delta: str, message_id: str | None = None) -> None:
        """Emit streaming text delta (TEXT_MESSAGE_CONTENT)."""
        await self.emit(
            EventType.TEXT_MESSAGE_CONTENT,
            {
                "messageId": message_id or self._message_id or str(uuid.uuid4()),
                "delta": delta,
            },
        )

    async def emit_text_completed(self, text: str, message_id: str | None = None) -> None:
        """Emit text completion event (TEXT_MESSAGE_END)."""
        await self.emit(
            EventType.TEXT_MESSAGE_END,
            {
                "messageId": message_id or self._message_id or str(uuid.uuid4()),
                "text": text,
            },
        )

    async def emit_progress(self, current: int, total: int, message: str = "") -> None:
        """Emit progress update."""
        await self.emit(
            EventType.PROGRESS_UPDATE,
            {
                "current": current,
                "total": total,
                "percentage": (current / total * 100) if total > 0 else 0,
                "message": message,
            },
        )

    async def emit_step_started(self, step_name: str, step_number: int | None = None) -> None:
        """Emit step started event."""
        self._step_count += 1
        await self.emit(
            EventType.STEP_STARTED,
            {
                "stepName": step_name,
                "stepNumber": step_number or self._step_count,
            },
        )

    async def emit_step_completed(self, step_name: str, result: Any = None) -> None:
        """Emit step completed event."""
        await self.emit(
            EventType.STEP_FINISHED,
            {
                "stepName": step_name,
                "result": str(result)[:500] if result else None,
            },
        )

    async def emit_workflow_started(self, workflow_id: str, workflow_name: str = "") -> None:
        """Emit workflow started event."""
        await self.emit(
            EventType.WORKFLOW_STARTED,
            {
                "workflow_id": workflow_id,
                "workflow_name": workflow_name,
            },
        )

    async def emit_workflow_completed(self, workflow_id: str, success: bool = True) -> None:
        """Emit workflow completed event."""
        await self.emit(
            EventType.WORKFLOW_COMPLETED,
            {
                "workflow_id": workflow_id,
                "success": success,
            },
        )

    async def emit_cache_hit(self, query: str, similarity: float = 1.0) -> None:
        """Emit cache hit event."""
        await self.emit(
            EventType.CACHE_HIT,
            {
                "query": query[:100],
                "similarity": similarity,
            },
        )

    async def emit_cache_miss(self, query: str) -> None:
        """Emit cache miss event."""
        await self.emit(
            EventType.CACHE_MISS,
            {
                "query": query[:100],
            },
        )

    async def emit_completed(self, result: Any = None) -> None:
        """Emit run completed event."""
        await self.emit(
            EventType.RUN_FINISHED,
            {
                "result": str(result)[:1000] if result else None,
            },
        )

    async def emit_failed(self, error: str) -> None:
        """Emit run failed event."""
        await self.emit(EventType.RUN_ERROR, {"message": error})

    async def emit_error(self, error: str, recoverable: bool = False) -> None:
        """Emit error event."""
        await self.emit(
            EventType.ERROR,
            {
                "error": error,
                "recoverable": recoverable,
            },
        )

    async def emit_warning(self, warning: str) -> None:
        """Emit warning event."""
        await self.emit(EventType.WARNING, {"warning": warning})


# Shared event emitter instance
_event_emitter: InMemoryEventEmitter | None = None


def get_event_emitter() -> InMemoryEventEmitter:
    """Get or create the shared event emitter."""
    global _event_emitter
    if _event_emitter is None:
        _event_emitter = InMemoryEventEmitter()
    return _event_emitter


def create_run_context(
    thread_id: str,
    agent_name: str = "",
    workflow_id: str = "",
) -> RunContext:
    """Create a run context with the shared emitter."""
    return RunContext(
        emitter=get_event_emitter(),
        thread_id=thread_id,
        agent_name=agent_name,
        workflow_id=workflow_id,
    )
