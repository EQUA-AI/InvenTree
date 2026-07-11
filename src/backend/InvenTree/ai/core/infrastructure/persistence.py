"""
Conversation Persistence Layer

This module provides durable storage for AI conversations using:
1. PostgreSQL (via Django ORM) for primary storage
2. Azure AI Search for semantic/vector search

The persistence layer integrates with the existing ConversationManager
to provide transparent durability without changing the interface.

Architecture:
    ConversationManager (in-memory)
            ↓
    ConversationPersistence
            ├── PostgreSQL (Django ORM)
            └── Azure AI Search (indexing)

Usage:
    from ai.core.infrastructure.persistence import ConversationPersistence
    
    persistence = ConversationPersistence()
    
    # Save a thread
    await persistence.save_thread(state)
    
    # Load a thread
    state = await persistence.load_thread(thread_id)
    
    # Search across conversations
    results = await persistence.search_similar_messages("how to fix widget A")
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TYPE_CHECKING

from asgiref.sync import sync_to_async

from ai.core.config import get_settings

if TYPE_CHECKING:
    from ai.core.memory.conversation import ConversationState
    from ai.core.integrations.search import AzureAISearchService, SearchResult

logger = logging.getLogger(__name__)


@dataclass
class PersistedMessage:
    """A message loaded from persistence."""
    
    message_id: str
    role: str
    content: str
    workflow_id: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    token_count: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "workflow_id": self.workflow_id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "metadata": self.metadata,
            "token_count": self.token_count,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class PersistedThread:
    """A thread loaded from persistence."""
    
    thread_id: str
    user_id: str | None
    title: str
    summary: str
    turn_count: int
    summary_turn: int
    last_workflow: str | None
    pending_handoff: str | None
    is_active: bool
    messages: list[PersistedMessage]
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for ConversationState."""
        return {
            "thread_id": self.thread_id,
            "user_id": self.user_id or "anonymous",
            "turn_count": self.turn_count,
            "last_workflow": self.last_workflow,
            "pending_handoff": self.pending_handoff,
            "summary": self.summary,
            "summary_turn": self.summary_turn,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "context_cache": {},
        }


class ConversationPersistence:
    """
    Persistence layer for AI conversations.
    
    Provides:
    - CRUD operations for threads and messages
    - Automatic Azure AI Search indexing
    - Batch operations for efficiency
    - Async interface for non-blocking I/O
    - ConversationState integration
    """
    
    def __init__(self, enable_search_indexing: bool = True):
        """Initialize the persistence layer."""
        self._settings = get_settings()
        self._search_service: AzureAISearchService | None = None
        self._initialized = False
        self._enable_search_indexing = enable_search_indexing
    
    @property
    def persistence_enabled(self) -> bool:
        """Check if PostgreSQL persistence is enabled."""
        return self._settings.conversation_persistence_enabled
    
    @property
    def search_enabled(self) -> bool:
        """Check if Azure AI Search is enabled."""
        return (
            self._enable_search_indexing
            and self._settings.conversation_search_enabled
            and self._settings.azure_search_endpoint
            and self._settings.azure_search_api_key
        )
    
    @property
    def search_service(self) -> "AzureAISearchService":
        """Get or create the search service."""
        if self._search_service is None:
            from ai.core.integrations.search import get_search_service
            self._search_service = get_search_service()
        return self._search_service
    
    async def initialize(self) -> None:
        """Initialize the persistence layer (create indexes, etc.)."""
        if self._initialized:
            return
        
        if self.search_enabled:
            try:
                # Create or update the search index
                await sync_to_async(self.search_service.create_or_update_index)()
                logger.info("Azure AI Search index initialized")
            except Exception as e:
                logger.error(f"Failed to initialize search index: {e}")
        
        self._initialized = True
    
    # ===== Thread Operations =====
    
    async def save_thread(
        self,
        thread_id: str,
        user_id: str | None = None,
        title: str = "",
        summary: str = "",
        turn_count: int = 0,
        summary_turn: int = 0,
        last_workflow: str | None = None,
        pending_handoff: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """
        Save or update a conversation thread.
        
        Returns the thread's primary key.
        """
        if not self.persistence_enabled:
            logger.debug("Persistence disabled, skipping save")
            return 0
        
        # Import Django models (done here to avoid import errors when Django isn't configured)
        from common.ai_models import AIConversationThread
        
        @sync_to_async
        def _save():
            thread, created = AIConversationThread.objects.update_or_create(
                thread_id=thread_id,
                defaults={
                    "user_id": user_id,
                    "title": title,
                    "summary": summary,
                    "turn_count": turn_count,
                    "summary_turn": summary_turn,
                    "last_workflow": last_workflow,
                    "pending_handoff": pending_handoff,
                    "metadata": metadata or {},
                },
            )
            return thread.pk
        
        return await _save()
    
    async def load_thread(self, thread_id: str) -> PersistedThread | None:
        """Load a thread with all its messages."""
        if not self.persistence_enabled:
            return None
        
        from common.ai_models import AIConversationThread
        
        @sync_to_async
        def _load():
            try:
                thread = AIConversationThread.objects.prefetch_related('messages').get(
                    thread_id=thread_id
                )
            except AIConversationThread.DoesNotExist:
                return None
            
            messages = [
                PersistedMessage(
                    message_id=str(m.message_id),
                    role=m.role,
                    content=m.content,
                    workflow_id=m.workflow_id,
                    tool_call_id=m.tool_call_id,
                    tool_name=m.tool_name,
                    metadata=m.message_metadata,
                    token_count=m.token_count,
                    created_at=m.created_at,
                )
                for m in thread.messages.order_by('created_at')
            ]
            
            return PersistedThread(
                thread_id=thread.thread_id,
                user_id=str(thread.user_id) if thread.user_id else None,
                title=thread.title,
                summary=thread.summary,
                turn_count=thread.turn_count,
                summary_turn=thread.summary_turn,
                last_workflow=thread.last_workflow,
                pending_handoff=thread.pending_handoff,
                is_active=thread.is_active,
                messages=messages,
                created_at=thread.created_at,
                updated_at=thread.updated_at,
                metadata=thread.metadata or {},
            )
        
        return await _load()
    
    async def list_threads(
        self,
        user_id: str | None = None,
        active_only: bool = True,
        limit: int = 50,
    ) -> list[PersistedThread]:
        """List threads for a user."""
        if not self.persistence_enabled:
            return []
        
        from common.ai_models import AIConversationThread
        
        @sync_to_async
        def _list():
            queryset = AIConversationThread.objects.all()
            
            if user_id:
                queryset = queryset.filter(user_id=user_id)
            if active_only:
                queryset = queryset.filter(is_active=True)
            
            queryset = queryset.order_by('-updated_at')[:limit]
            
            return [
                PersistedThread(
                    thread_id=t.thread_id,
                    user_id=str(t.user_id) if t.user_id else None,
                    title=t.title,
                    summary=t.summary,
                    turn_count=t.turn_count,
                    summary_turn=t.summary_turn,
                    last_workflow=t.last_workflow,
                    pending_handoff=t.pending_handoff,
                    is_active=t.is_active,
                    messages=[],  # Don't load messages for list
                    created_at=t.created_at,
                    updated_at=t.updated_at,
                    metadata=t.metadata or {},
                )
                for t in queryset
            ]
        
        return await _list()
    
    async def delete_thread(self, thread_id: str) -> bool:
        """Delete a thread and all its messages."""
        if not self.persistence_enabled:
            return False
        
        from common.ai_models import AIConversationThread
        
        @sync_to_async
        def _delete():
            deleted, _ = AIConversationThread.objects.filter(thread_id=thread_id).delete()
            return deleted > 0
        
        success = await _delete()
        
        # Also delete from search index
        if success and self.search_enabled:
            try:
                await sync_to_async(self.search_service.delete_documents_by_thread)(thread_id)
            except Exception as e:
                logger.error(f"Failed to delete from search index: {e}")
        
        return success
    
    # ===== Message Operations =====
    
    async def save_message(
        self,
        thread_id: str,
        message_id: str,
        role: str,
        content: str,
        workflow_id: str | None = None,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        token_count: int = 0,
    ) -> int:
        """
        Save a message to a thread.
        
        Returns the message's primary key.
        """
        if not self.persistence_enabled:
            return 0
        
        from common.ai_models import AIConversationThread, AIConversationMessage
        
        @sync_to_async
        def _save():
            # Get or create the thread
            thread, _ = AIConversationThread.objects.get_or_create(
                thread_id=thread_id
            )
            
            # Create the message
            message, created = AIConversationMessage.objects.update_or_create(
                message_id=message_id,
                defaults={
                    "thread": thread,
                    "role": role,
                    "content": content,
                    "workflow_id": workflow_id,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "message_metadata": metadata or {},
                    "token_count": token_count,
                },
            )
            
            return message.pk, message, thread, created
        
        pk, message, thread, created = await _save()
        
        # Index in Azure AI Search
        if created and self.search_enabled:
            try:
                doc = {
                    "id": str(message_id),
                    "thread_id": thread_id,
                    "user_id": str(thread.user_id) if thread.user_id else "anonymous",
                    "role": role,
                    "content": content,
                    "workflow_id": workflow_id or "",
                    "thread_title": thread.title,
                    "created_at": message.created_at.isoformat(),
                    "token_count": token_count,
                }
                await self.search_service.index_document_async(doc)
                
                # Mark as indexed
                @sync_to_async
                def _mark_indexed():
                    message.is_indexed = True
                    message.search_document_id = str(message_id)
                    message.save(update_fields=['is_indexed', 'search_document_id'])
                
                await _mark_indexed()
                
            except Exception as e:
                logger.error(f"Failed to index message: {e}")
        
        return pk
    
    async def save_messages_batch(
        self,
        thread_id: str,
        messages: list[dict[str, Any]],
    ) -> int:
        """
        Save multiple messages in a batch.
        
        Returns the number of messages saved.
        """
        if not self.persistence_enabled:
            return 0
        
        count = 0
        for msg in messages:
            await self.save_message(
                thread_id=thread_id,
                message_id=msg.get("message_id", msg.get("id", "")),
                role=msg["role"],
                content=msg["content"],
                workflow_id=msg.get("workflow_id"),
                tool_call_id=msg.get("tool_call_id"),
                tool_name=msg.get("tool_name"),
                metadata=msg.get("metadata", {}),
                token_count=msg.get("token_count", 0),
            )
            count += 1
        
        return count
    
    # ===== Search Operations =====
    
    async def search(
        self,
        query: str,
        user_id: str | None = None,
        thread_id: str | None = None,
        role: str | None = None,
        top: int = 10,
        use_semantic: bool = True,
    ) -> list["SearchResult"]:
        """
        Search conversation history.
        
        Uses Azure AI Search for semantic/vector search.
        Falls back to simple database search if search is disabled.
        """
        if self.search_enabled:
            return await self.search_service.search_async(
                query=query,
                user_id=user_id,
                thread_id=thread_id,
                role=role,
                top=top,
                use_semantic=use_semantic,
            )
        
        # Fallback: simple database search
        return await self._db_search(query, user_id, thread_id, role, top)
    
    async def _db_search(
        self,
        query: str,
        user_id: str | None = None,
        thread_id: str | None = None,
        role: str | None = None,
        top: int = 10,
    ) -> list:
        """Fallback database search using PostgreSQL full-text search."""
        if not self.persistence_enabled:
            return []
        
        from common.ai_models import AIConversationMessage
        from django.db.models import Q
        
        @sync_to_async
        def _search():
            queryset = AIConversationMessage.objects.select_related('thread').filter(
                content__icontains=query
            )
            
            if user_id:
                queryset = queryset.filter(thread__user_id=user_id)
            if thread_id:
                queryset = queryset.filter(thread__thread_id=thread_id)
            if role:
                queryset = queryset.filter(role=role)
            
            queryset = queryset.order_by('-created_at')[:top]
            
            # Convert to SearchResult-like dicts
            from ai.core.integrations.search import SearchResult
            
            return [
                SearchResult(
                    message_id=str(m.message_id),
                    thread_id=m.thread.thread_id,
                    user_id=str(m.thread.user_id) if m.thread.user_id else "anonymous",
                    role=m.role,
                    content=m.content,
                    workflow_id=m.workflow_id,
                    thread_title=m.thread.title,
                    created_at=m.created_at,
                    score=0.0,  # No score for DB search
                )
                for m in queryset
            ]
        
        return await _search()
    
    async def find_similar_conversations(
        self,
        query: str,
        user_id: str | None = None,
        top: int = 5,
    ) -> list["SearchResult"]:
        """
        Find similar past conversations for context.
        
        Useful for:
        - Providing context to the LLM
        - Suggesting relevant past solutions
        - Understanding user preferences
        """
        if not self.search_enabled:
            return await self._db_search(query, user_id=user_id, top=top)
        
        return await self.search_service.search_async(
            query=query,
            user_id=user_id,
            role="assistant",  # Focus on assistant responses
            top=top,
            use_semantic=True,
        )
    
    # ===== ConversationState Integration =====
    
    async def save_thread_from_state(
        self,
        state: "ConversationState",
    ) -> int:
        """
        Save a conversation thread from a ConversationState object.
        
        This is the primary method for persisting conversation state
        from the ConversationManager.
        
        Args:
            state: The ConversationState object to persist
        
        Returns:
            The thread's primary key
        """
        return await self.save_thread(
            thread_id=state.thread_id,
            user_id=state.user_id,
            title=state.summary[:100] if state.summary else f"Thread {state.thread_id[:8]}",
            summary=state.summary or "",
            turn_count=state.turn_count,
            summary_turn=state.summary_turn,
            last_workflow=state.last_workflow,
            pending_handoff=state.pending_handoff,
            metadata={
                "context_cache_keys": list(state.context_cache.keys()),
            },
        )
    
    async def load_thread_to_state(
        self,
        thread_id: str,
    ) -> "ConversationState | None":
        """
        Load a thread and convert it to a ConversationState object.
        
        This is the primary method for loading conversation state
        into the ConversationManager.
        
        Args:
            thread_id: The thread ID to load
        
        Returns:
            ConversationState object or None if not found
        """
        persisted = await self.load_thread(thread_id)
        if not persisted:
            return None
        
        # Import here to avoid circular imports
        from ai.core.memory.conversation import ConversationState, Message
        
        state = ConversationState(
            thread_id=persisted.thread_id,
            user_id=persisted.user_id or "anonymous",
            turn_count=persisted.turn_count,
            last_workflow=persisted.last_workflow,
            pending_handoff=persisted.pending_handoff,
            summary=persisted.summary,
            summary_turn=persisted.summary_turn,
            created_at=persisted.created_at,
            updated_at=persisted.updated_at,
        )
        
        # Convert persisted messages to Message objects
        state.messages = [
            Message(
                role=m.role,
                content=m.content,
                timestamp=m.created_at,
                workflow_id=m.workflow_id,
                metadata=m.metadata,
            )
            for m in persisted.messages
        ]
        
        return state
    
    async def save_message_simple(
        self,
        thread_id: str,
        role: str,
        content: str,
        workflow_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """
        Simplified message saving with auto-generated message ID.
        
        Args:
            thread_id: The thread ID
            role: Message role (user, assistant, system)
            content: Message content
            workflow_id: Optional workflow that generated this message
            metadata: Optional metadata
        
        Returns:
            The message's primary key
        """
        message_id = str(uuid.uuid4())
        return await self.save_message(
            thread_id=thread_id,
            message_id=message_id,
            role=role,
            content=content,
            workflow_id=workflow_id,
            metadata=metadata,
        )
    
    async def search_similar_messages(
        self,
        query: str,
        user_id: str | None = None,
        exclude_thread_id: str | None = None,
        workflow_id: str | None = None,
        top_k: int = 5,
    ) -> list["SearchResult"]:
        """
        Search for similar messages across conversations.
        
        This method is designed for the ConversationManager's
        search_similar_conversations method.
        
        Args:
            query: The search query
            user_id: Optional filter by user
            exclude_thread_id: Optional thread to exclude from results
            workflow_id: Optional filter by workflow
            top_k: Number of results to return
        
        Returns:
            List of SearchResult objects
        """
        if self.search_enabled:
            results = await self.search_service.search_async(
                query=query,
                user_id=user_id,
                thread_id=None,  # Don't filter by thread, just exclude later
                role=None,
                top=top_k * 2 if exclude_thread_id else top_k,  # Get extra to filter
                use_semantic=True,
            )
            
            # Filter out excluded thread
            if exclude_thread_id:
                results = [r for r in results if r.thread_id != exclude_thread_id][:top_k]
            
            return results
        
        # Fallback to database search
        return await self._db_search(
            query=query,
            user_id=user_id,
            top=top_k,
        )
    
    async def get_user_threads(
        self,
        user_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Get a user's conversation threads.
        
        Args:
            user_id: The user ID
            limit: Maximum number of threads to return
        
        Returns:
            List of thread summary dictionaries
        """
        threads = await self.list_threads(user_id=user_id, limit=limit)
        return [
            {
                "thread_id": t.thread_id,
                "turn_count": t.turn_count,
                "summary": t.summary,
                "last_workflow": t.last_workflow,
                "updated_at": t.updated_at.isoformat(),
                "title": t.title,
            }
            for t in threads
        ]
    
    async def archive_thread(self, thread_id: str) -> bool:
        """
        Archive a conversation thread.
        
        Args:
            thread_id: The thread to archive
        
        Returns:
            True if archived successfully
        """
        if not self.persistence_enabled:
            return False
        
        from common.ai_models import AIConversationThread
        
        @sync_to_async
        def _archive():
            try:
                thread = AIConversationThread.objects.get(thread_id=thread_id)
                thread.is_active = False
                thread.save(update_fields=['is_active', 'updated_at'])
                return True
            except AIConversationThread.DoesNotExist:
                return False
        
        return await _archive()
    
    async def ensure_search_index(self) -> bool:
        """
        Ensure the Azure AI Search index exists.
        
        Should be called during application startup.
        
        Returns:
            True if index is ready
        """
        if not self.search_enabled:
            return False
        
        try:
            await sync_to_async(self.search_service.create_or_update_index)()
            logger.info("Azure AI Search index ensured")
            return True
        except Exception as e:
            logger.error(f"Failed to ensure search index: {e}")
            return False
    
    # ===== Sync Operations =====
    
    async def sync_to_search(
        self,
        since: datetime | None = None,
        batch_size: int | None = None,
    ) -> int:
        """
        Sync unindexed messages to Azure AI Search.
        
        Returns the number of messages synced.
        """
        if not self.search_enabled:
            return 0
        
        batch_size = batch_size or self._settings.conversation_sync_batch_size
        
        from common.ai_models import AIConversationMessage, AISearchIndexStatus
        
        @sync_to_async
        def _get_unindexed():
            queryset = AIConversationMessage.objects.filter(is_indexed=False)
            if since:
                queryset = queryset.filter(created_at__gte=since)
            return list(queryset.select_related('thread')[:batch_size])
        
        messages = await _get_unindexed()
        
        if not messages:
            return 0
        
        # Build documents for indexing
        docs = [m.to_search_document() for m in messages]
        
        # Index in batch
        try:
            await sync_to_async(self.search_service.index_documents)(docs)
            
            # Mark as indexed
            @sync_to_async
            def _mark_indexed():
                for m in messages:
                    m.is_indexed = True
                    m.search_document_id = str(m.message_id)
                AIConversationMessage.objects.bulk_update(
                    messages,
                    ['is_indexed', 'search_document_id'],
                )
            
            await _mark_indexed()
            
            logger.info(f"Synced {len(messages)} messages to search index")
            return len(messages)
            
        except Exception as e:
            logger.error(f"Failed to sync to search: {e}")
            
            # Update index status with error
            @sync_to_async
            def _record_error():
                status, _ = AISearchIndexStatus.objects.get_or_create(
                    index_name=self._settings.azure_search_index_name,
                )
                status.status = AISearchIndexStatus.Status.ERROR
                status.last_error = str(e)
                status.save()
            
            await _record_error()
            return 0
    
    def get_stats(self) -> dict[str, Any]:
        """Get persistence statistics."""
        stats = {
            "persistence_enabled": self.persistence_enabled,
            "search_enabled": self.search_enabled,
        }
        
        if self.persistence_enabled:
            from common.ai_models import AIConversationThread, AIConversationMessage
            stats["thread_count"] = AIConversationThread.objects.count()
            stats["message_count"] = AIConversationMessage.objects.count()
            stats["indexed_messages"] = AIConversationMessage.objects.filter(is_indexed=True).count()
        
        if self.search_enabled:
            try:
                stats["search_index"] = self.search_service.get_stats()
            except Exception as e:
                stats["search_error"] = str(e)
        
        return stats


# ===== Global Instance =====

_persistence: ConversationPersistence | None = None


def get_persistence() -> ConversationPersistence:
    """Get or create the global persistence instance."""
    global _persistence
    if _persistence is None:
        _persistence = ConversationPersistence()
    return _persistence


# Export all symbols
__all__ = [
    "PersistedMessage",
    "PersistedThread",
    "ConversationPersistence",
    "get_persistence",
]
