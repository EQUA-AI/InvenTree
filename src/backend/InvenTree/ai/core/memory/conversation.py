"""
AIMMS Conversation Management

Manages conversation state, history, and context aggregation.
Moved from orchestrator.py during refactor.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from ai.core.faults import fault_location

logger = logging.getLogger(__name__)

#: Sentinel distinguishing "provider failed/timed out" from a legitimate None
#: result, so absence in the aggregated context is always deliberate.
_PROVIDER_FAILED = object()


@dataclass
class Message:
    """A single message in the conversation history."""

    role: str  # "user", "assistant", "system"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    workflow_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "workflow_id": self.workflow_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        """Create from dictionary."""
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            workflow_id=data.get("workflow_id"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class ConversationState:
    """
    Tracks conversation state across turns.

    Maintains:
    - Message history with timestamps
    - Turn counting and workflow tracking
    - Handoff state between workflows
    - Context cache for provider data
    - Summarization metadata
    """

    thread_id: str
    user_id: str
    turn_count: int = 0
    last_workflow: str | None = None
    pending_handoff: str | None = None
    context_cache: dict[str, Any] = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    summary: str | None = None
    summary_turn: int = 0  # Turn count when last summarized

    # Configuration
    SUMMARY_THRESHOLD: ClassVar[int] = 10  # Summarize after this many turns
    MAX_CONTEXT_MESSAGES: ClassVar[int] = 20  # Keep last N messages in context

    def increment_turn(self) -> None:
        """Increment the turn counter and update timestamp."""
        self.turn_count += 1
        self.updated_at = datetime.now()

    def add_message(
        self,
        role: str,
        content: str,
        workflow_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        """Add a message to the conversation history."""
        message = Message(
            role=role,
            content=content,
            workflow_id=workflow_id,
            metadata=metadata or {},
        )
        self.messages.append(message)
        self.updated_at = datetime.now()
        return message

    def get_recent_messages(self, count: int | None = None) -> list[Message]:
        """Get recent messages, respecting the max context limit."""
        limit = count or self.MAX_CONTEXT_MESSAGES
        return self.messages[-limit:] if len(self.messages) > limit else self.messages

    def needs_summarization(self) -> bool:
        """Check if conversation needs summarization."""
        turns_since_summary = self.turn_count - self.summary_turn
        return turns_since_summary >= self.SUMMARY_THRESHOLD

    def set_summary(self, summary: str) -> None:
        """Update the conversation summary."""
        self.summary = summary
        self.summary_turn = self.turn_count
        self.updated_at = datetime.now()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization/persistence."""
        return {
            "thread_id": self.thread_id,
            "user_id": self.user_id,
            "turn_count": self.turn_count,
            "last_workflow": self.last_workflow,
            "pending_handoff": self.pending_handoff,
            "context_cache": self.context_cache,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "summary": self.summary,
            "summary_turn": self.summary_turn,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationState:
        """Create from dictionary."""
        state = cls(
            thread_id=data["thread_id"],
            user_id=data["user_id"],
            turn_count=data.get("turn_count", 0),
            last_workflow=data.get("last_workflow"),
            pending_handoff=data.get("pending_handoff"),
            context_cache=data.get("context_cache", {}),
            summary=data.get("summary"),
            summary_turn=data.get("summary_turn", 0),
        )
        state.messages = [Message.from_dict(m) for m in data.get("messages", [])]
        if "created_at" in data:
            state.created_at = datetime.fromisoformat(data["created_at"])
        if "updated_at" in data:
            state.updated_at = datetime.fromisoformat(data["updated_at"])
        return state


class ConversationManager:
    """
    Aggregates per-turn context for the root orchestrator.

    S35: this class is deliberately stateless across requests. The file-JSON
    providers, the legacy file persistence, the per-process ``_conversations``
    cache, and the cross-process aggregated-context cache were all deleted —
    the one surviving provider is a single cheap DB read, which is cheaper
    than any staleness a cache would buy. Durable threads and turns live
    exclusively in the ``aichat`` repository.
    """

    def __init__(self) -> None:
        """Initialize conversation manager (no cross-request state)."""
        self._providers_initialized = False
        self._user_profile_provider = None

    def _init_providers(self) -> None:
        """Lazily initialize context providers."""
        if self._providers_initialized:
            return

        from ai.core.memory import get_user_profile_provider

        self._user_profile_provider = get_user_profile_provider()
        self._providers_initialized = True

    def get_or_create_state(
        self,
        thread_id: str,
        user_id: str = "anonymous",
    ) -> ConversationState:
        """A fresh per-call scratch state; nothing is retained across requests."""
        return ConversationState(thread_id=thread_id, user_id=user_id)

    async def gather_context(
        self,
        query: str,
        thread_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """
        Gather context from all providers.

        Args:
            query: User query
            thread_id: Conversation thread ID
            user_id: User ID

        Returns:
            Aggregated context dictionary
        """
        self._init_providers()

        # A12: providers are independent reads, each under its own timeout —
        # one hung provider must cost at most its own budget, never the
        # turn's. Failures degrade to an absent key and are logged by
        # coordinates only (fault_location), never by message. A None result
        # (e.g. unknown user) is also an absent key: consumers never see a
        # placeholder profile.
        timeout_s = self._provider_timeout_s()
        active = [
            (key, awaitable)
            for key, awaitable in (
                (
                    "user_profile",
                    self._user_profile_provider.get_profile(user_id)
                    if self._user_profile_provider
                    else None,
                ),
            )
            if awaitable is not None
        ]
        results = await asyncio.gather(
            *(self._bounded_provider(key, awaitable, timeout_s) for key, awaitable in active)
        )
        return {
            key: value
            for key, value in results
            if value is not _PROVIDER_FAILED and value is not None
        }

    @staticmethod
    def _provider_timeout_s() -> float:
        """Per-provider budget; configuration-driven with a safe fallback."""
        try:
            from ai.core.config import get_settings

            return get_settings().context_provider_timeout_s
        except Exception:  # pragma: no cover - config absent in minimal envs
            return 5.0

    async def _bounded_provider(self, key: str, awaitable, timeout_s: float):
        """Run one provider under its own timeout, degrading to absence."""
        try:
            return key, await asyncio.wait_for(awaitable, timeout=timeout_s)
        except TimeoutError:
            logger.warning("context provider timed out provider=%s timeout_s=%s", key, timeout_s)
            return key, _PROVIDER_FAILED
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("context provider failed provider=%s %s", key, fault_location(exc))
            return key, _PROVIDER_FAILED
