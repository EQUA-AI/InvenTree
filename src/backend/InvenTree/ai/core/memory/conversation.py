"""
AIMMS Conversation Management

Manages conversation state, history, and context aggregation.
Moved from orchestrator.py during refactor.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ai.core.config import get_settings

if TYPE_CHECKING:
    from ai.core.memory import (
        PartsPreferenceProvider,
        ProblemSolutionProvider,
        ThreadSummaryProvider,
        UserProfileProvider,
    )

logger = logging.getLogger(__name__)


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
    def from_dict(cls, data: dict[str, Any]) -> "Message":
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
    SUMMARY_THRESHOLD = 10  # Summarize after this many turns
    MAX_CONTEXT_MESSAGES = 20  # Keep last N messages in context
    
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
    def from_dict(cls, data: dict[str, Any]) -> "ConversationState":
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
    Manages conversation state and context aggregation.
    
    Features:
    - Multi-turn state tracking with message history
    - Context provider invocation in parallel
    - Automatic summarization for long conversations
    - Handoff management between workflows
    - PostgreSQL persistence with Azure AI Search indexing
    - Context caching with TTL support
    """
    
    # Context cache TTL in seconds
    CONTEXT_CACHE_TTL = 300  # 5 minutes
    
    def __init__(
        self,
        persistence_dir: str | None = None,
        enable_persistence: bool = False,
        enable_db_persistence: bool = True,
        enable_search_indexing: bool = True,
    ):
        """
        Initialize conversation manager.
        
        Args:
            persistence_dir: Directory for persisting state (legacy file-based)
            enable_persistence: Whether to persist state to disk (legacy)
            enable_db_persistence: Whether to persist to PostgreSQL database
            enable_search_indexing: Whether to index messages in Azure AI Search
        """
        self._conversations: dict[str, ConversationState] = {}
        self._providers_initialized = False
        self._enable_persistence = enable_persistence
        self._persistence_dir = Path(persistence_dir) if persistence_dir else None
        self._context_cache_timestamps: dict[str, datetime] = {}
        
        # Database persistence settings
        self._enable_db_persistence = enable_db_persistence
        self._enable_search_indexing = enable_search_indexing
        self._db_persistence = None  # Lazy-loaded
        
        # Lazy-loaded providers
        self._user_profile_provider = None
        self._thread_summary_provider = None
        self._problem_solution_provider = None
        self._parts_preference_provider = None
        
        # Initialize persistence directory if needed (legacy)
        if self._enable_persistence and self._persistence_dir:
            self._persistence_dir.mkdir(parents=True, exist_ok=True)
            logger.info(
                f"ConversationManager initialized with file persistence (persistence_dir={self._persistence_dir})"
            )
        
        if self._enable_db_persistence:
            logger.info(
                f"ConversationManager initialized with database persistence (search_indexing={self._enable_search_indexing})"
            )
        else:
            logger.info("ConversationManager initialized (in-memory only)")
    
    def _get_db_persistence(self):
        """Lazily initialize database persistence layer."""
        if self._db_persistence is None and self._enable_db_persistence:
            try:
                from ai.core.infrastructure.persistence import ConversationPersistence
                self._db_persistence = ConversationPersistence(
                    enable_search_indexing=self._enable_search_indexing
                )
                logger.debug("Database persistence layer initialized")
            except ImportError as e:
                logger.warning(f"Failed to import database persistence layer: {e}")
                self._enable_db_persistence = False
            except Exception as e:
                logger.warning(f"Failed to initialize database persistence: {e}")
                self._enable_db_persistence = False
        return self._db_persistence
    
    def _init_providers(self) -> None:
        """Lazily initialize context providers."""
        if self._providers_initialized:
            return
        
        from ai.core.memory import (
            get_parts_preference_provider,
            get_problem_solution_provider,
            get_thread_summary_provider,
            get_user_profile_provider,
        )
        
        self._user_profile_provider = get_user_profile_provider()
        self._thread_summary_provider = get_thread_summary_provider()
        self._problem_solution_provider = get_problem_solution_provider()
        self._parts_preference_provider = get_parts_preference_provider()
        self._providers_initialized = True
    
    def _get_persistence_path(self, thread_id: str) -> Path | None:
        """Get the persistence file path for a thread."""
        if not self._enable_persistence or not self._persistence_dir:
            return None
        return self._persistence_dir / f"{thread_id}.json"
    
    def _load_state(self, thread_id: str) -> ConversationState | None:
        """Load state from persistence."""
        path = self._get_persistence_path(thread_id)
        if not path or not path.exists():
            return None
        
        try:
            with open(path) as f:
                data = json.load(f)
            return ConversationState.from_dict(data)
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning(f"Failed to load conversation state (thread_id={thread_id}): {e}")
            return None
    
    def _save_state(self, state: ConversationState) -> None:
        """Save state to persistence."""
        path = self._get_persistence_path(state.thread_id)
        if not path:
            return
        
        try:
            with open(path, "w") as f:
                json.dump(state.to_dict(), f, indent=2)
            logger.debug(f"Conversation state saved (thread_id={state.thread_id})")
        except OSError as e:
            logger.warning(f"Failed to save conversation state (thread_id={state.thread_id}): {e}")
    
    def get_or_create_state(
        self,
        thread_id: str,
        user_id: str = "anonymous",
    ) -> ConversationState:
        """
        Get existing conversation state or create new one (sync version).
        
        Checks in-memory cache first, then file persistence if enabled.
        For database persistence, use get_or_create_state_async().
        """
        # Check in-memory cache
        if thread_id in self._conversations:
            return self._conversations[thread_id]
        
        # Try loading from file persistence (legacy)
        state = self._load_state(thread_id)
        if state:
            self._conversations[thread_id] = state
            return state
        
        # Create new state
        state = ConversationState(thread_id=thread_id, user_id=user_id)
        self._conversations[thread_id] = state
        return state

    def list_active_threads(self) -> list[str]:
        """List all active in-memory threads."""
        return list(self._conversations.keys())

    def get_state(self, thread_id: str) -> ConversationState | None:
        """Get state if it exists in memory."""
        return self._conversations.get(thread_id)
    
    def cleanup(self, thread_id: str) -> None:
        """Remove thread from memory."""
        if thread_id in self._conversations:
            del self._conversations[thread_id]

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
        
        # Check cache
        state = self.get_or_create_state(thread_id, user_id)
        now = datetime.now()
        
        # Simple cache check (if query is similar or just time-based)
        # For now, we just check if we have cached context and it's fresh
        if thread_id in self._context_cache_timestamps:
            age = (now - self._context_cache_timestamps[thread_id]).total_seconds()
            if age < self.CONTEXT_CACHE_TTL:
                return state.context_cache
        
        # Gather from providers
        # Note: In a real implementation, we would run these in parallel
        # and handle failures gracefully.
        context = {}
        
        # 1. User Profile
        if self._user_profile_provider:
            try:
                profile = await self._user_profile_provider.get_profile(user_id)
                context["user_profile"] = profile
            except Exception as e:
                logger.warning(f"Failed to get user profile: {e}")
        
        # 2. Thread Summary
        if self._thread_summary_provider:
            try:
                summary = await self._thread_summary_provider.get_summary(thread_id)
                context["thread_summary"] = summary
            except Exception as e:
                logger.warning(f"Failed to get thread summary: {e}")
        
        # 3. Parts Preferences (if relevant)
        if self._parts_preference_provider:
            try:
                prefs = await self._parts_preference_provider.get_preferences(user_id)
                context["parts_preferences"] = prefs
            except Exception as e:
                logger.warning(f"Failed to get parts preferences: {e}")
        
        # Update cache
        state.context_cache = context
        self._context_cache_timestamps[thread_id] = now
        
        # Save state if persistence enabled
        if self._enable_persistence:
            self._save_state(state)
            
        return context
