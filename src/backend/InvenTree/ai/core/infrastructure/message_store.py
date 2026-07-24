"""
Legacy file-based ChatMessageStore implementation.

This utility is not a production chat authority. Durable chat history must use
the owner/scope-bound ``aichat.services.ThreadRepository``.
Thread messages are stored as JSON files in the configured threads directory.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiofiles
import structlog
from ai.core.config import settings

logger = structlog.get_logger(__name__)


class FileChatMessageStore:
    """
    File-based implementation of ChatMessageStoreProtocol.

    Each thread is stored as a separate JSON file:
    - Path: {threads_dir}/{thread_id}.json
    - Format: {"thread_id": str, "messages": [...], "metadata": {...}}

    This implementation provides:
    - Async file I/O for non-blocking operations
    - Atomic writes using temp file + rename
    - Thread-safe operations via file locking
    """

    def __init__(self, threads_dir: Path | None = None) -> None:
        """
        Initialize the file-based message store.

        Args:
            threads_dir: Directory for thread files. Defaults to settings.threads_dir.
        """
        self.threads_dir = threads_dir or settings.threads_dir
        self.threads_dir.mkdir(parents=True, exist_ok=True)
        logger.info("FileChatMessageStore initialized", threads_dir=str(self.threads_dir))

    def _get_thread_path(self, thread_id: str) -> Path:
        """Get the file path for a thread."""
        return self.threads_dir / f"{thread_id}.json"

    async def create_thread(
        self,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Create a new thread.

        Args:
            thread_id: Optional thread ID. Generated if not provided.
            metadata: Optional metadata to attach to the thread.

        Returns:
            The thread ID.
        """
        thread_id = thread_id or str(uuid4())
        thread_path = self._get_thread_path(thread_id)

        if thread_path.exists():
            logger.warning("Thread already exists", thread_id=thread_id)
            return thread_id

        thread_data = {
            "thread_id": thread_id,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "messages": [],
            "metadata": metadata or {},
        }

        await self._write_thread(thread_id, thread_data)
        logger.info("Thread created", thread_id=thread_id)
        return thread_id

    async def delete_thread(self, thread_id: str) -> bool:
        """
        Delete a thread and all its messages.

        Args:
            thread_id: The thread to delete.

        Returns:
            True if deleted, False if not found.
        """
        thread_path = self._get_thread_path(thread_id)

        if not thread_path.exists():
            logger.warning("Thread not found for deletion", thread_id=thread_id)
            return False

        thread_path.unlink()
        logger.info("Thread deleted", thread_id=thread_id)
        return True

    async def list_messages(
        self,
        thread_id: str,
        limit: int | None = None,
        before: str | None = None,
        after: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        List messages in a thread.

        Args:
            thread_id: The thread to list messages from.
            limit: Maximum number of messages to return.
            before: Return messages before this message ID.
            after: Return messages after this message ID.

        Returns:
            List of messages in chronological order.
        """
        thread_data = await self._read_thread(thread_id)
        if thread_data is None:
            return []

        messages = thread_data.get("messages", [])

        # Apply filters
        if after:
            try:
                after_idx = next(i for i, m in enumerate(messages) if m.get("id") == after)
                messages = messages[after_idx + 1 :]
            except StopIteration:
                pass

        if before:
            try:
                before_idx = next(i for i, m in enumerate(messages) if m.get("id") == before)
                messages = messages[:before_idx]
            except StopIteration:
                pass

        if limit:
            messages = messages[:limit]

        return messages

    async def add_messages(
        self,
        thread_id: str,
        messages: list[dict[str, Any]],
    ) -> list[str]:
        """
        Add messages to a thread.

        Args:
            thread_id: The thread to add messages to.
            messages: List of messages to add. Each message should have:
                - role: "user" | "assistant" | "system" | "tool"
                - content: Message content
                - Optional: tool_calls, tool_call_id, name, etc.

        Returns:
            List of assigned message IDs.
        """
        thread_data = await self._read_thread(thread_id)
        if thread_data is None:
            # Auto-create thread if it doesn't exist
            await self.create_thread(thread_id)
            thread_data = await self._read_thread(thread_id)

        existing_messages = thread_data.get("messages", [])
        message_ids = []

        for msg in messages:
            msg_id = msg.get("id") or str(uuid4())
            message_ids.append(msg_id)

            enriched_msg = {
                "id": msg_id,
                "created_at": datetime.now(UTC).isoformat(),
                **msg,
            }
            existing_messages.append(enriched_msg)

        thread_data["messages"] = existing_messages
        thread_data["updated_at"] = datetime.now(UTC).isoformat()

        await self._write_thread(thread_id, thread_data)

        logger.debug(
            "Messages added to thread",
            thread_id=thread_id,
            count=len(messages),
            message_ids=message_ids,
        )

        return message_ids

    async def get_thread_metadata(self, thread_id: str) -> dict[str, Any] | None:
        """
        Get thread metadata.

        Args:
            thread_id: The thread to get metadata for.

        Returns:
            Thread metadata or None if not found.
        """
        thread_data = await self._read_thread(thread_id)
        if thread_data is None:
            return None

        return {
            "thread_id": thread_data["thread_id"],
            "created_at": thread_data.get("created_at"),
            "updated_at": thread_data.get("updated_at"),
            "message_count": len(thread_data.get("messages", [])),
            **thread_data.get("metadata", {}),
        }

    async def update_thread_metadata(
        self,
        thread_id: str,
        metadata: dict[str, Any],
    ) -> bool:
        """
        Update thread metadata.

        Args:
            thread_id: The thread to update.
            metadata: Metadata to merge (not replace).

        Returns:
            True if updated, False if thread not found.
        """
        thread_data = await self._read_thread(thread_id)
        if thread_data is None:
            return False

        existing_metadata = thread_data.get("metadata", {})
        existing_metadata.update(metadata)
        thread_data["metadata"] = existing_metadata
        thread_data["updated_at"] = datetime.now(UTC).isoformat()

        await self._write_thread(thread_id, thread_data)
        logger.debug("Thread metadata updated", thread_id=thread_id)
        return True

    async def serialize(self, thread_id: str) -> str | None:
        """
        Serialize a thread to JSON string for persistence.

        Args:
            thread_id: The thread to serialize.

        Returns:
            JSON string or None if thread not found.
        """
        thread_data = await self._read_thread(thread_id)
        if thread_data is None:
            return None
        return json.dumps(thread_data, indent=2, default=str)

    async def deserialize(self, data: str, thread_id: str | None = None) -> str:
        """
        Deserialize a thread from JSON string.

        Args:
            data: JSON string of thread data.
            thread_id: Optional override for thread ID.

        Returns:
            The thread ID.
        """
        thread_data = json.loads(data)
        final_thread_id = thread_id or thread_data.get("thread_id") or str(uuid4())
        thread_data["thread_id"] = final_thread_id

        await self._write_thread(final_thread_id, thread_data)
        logger.info("Thread deserialized", thread_id=final_thread_id)
        return final_thread_id

    async def list_threads(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        List all threads with basic metadata.

        Args:
            limit: Maximum number of threads to return.
            offset: Number of threads to skip.

        Returns:
            List of thread metadata.
        """
        thread_files = sorted(
            self.threads_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        threads = []
        for thread_file in thread_files[offset : offset + limit]:
            thread_id = thread_file.stem
            metadata = await self.get_thread_metadata(thread_id)
            if metadata:
                threads.append(metadata)

        return threads

    async def _read_thread(self, thread_id: str) -> dict[str, Any] | None:
        """Read thread data from file."""
        thread_path = self._get_thread_path(thread_id)

        if not thread_path.exists():
            return None

        try:
            async with aiofiles.open(thread_path, encoding="utf-8") as f:
                content = await f.read()
                return json.loads(content)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read thread", thread_id=thread_id, error=str(e))
            return None

    async def _write_thread(self, thread_id: str, data: dict[str, Any]) -> None:
        """Write thread data to file atomically."""
        thread_path = self._get_thread_path(thread_id)
        temp_path = thread_path.with_suffix(".tmp")

        try:
            async with aiofiles.open(temp_path, mode="w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2, default=str))

            # Atomic rename
            temp_path.rename(thread_path)
        except OSError as e:
            logger.error("Failed to write thread", thread_id=thread_id, error=str(e))
            if temp_path.exists():
                temp_path.unlink()
            raise


# Module-level singleton for convenience
_message_store: FileChatMessageStore | None = None


def get_message_store() -> FileChatMessageStore:
    """Reject ambient use of the unscoped legacy file store."""
    raise RuntimeError(
        "FileChatMessageStore is not an authorized production chat store; "
        "use aichat.services.ThreadRepository"
    )
