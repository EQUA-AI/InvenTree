"""
Idempotency Store Implementation

Provides idempotent operation tracking to prevent duplicate external writes.
Essential for HITL workflows and recovery scenarios.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiofiles
import structlog
from ai.core.config import settings

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(UTC)


def _parse_stored_timestamp(value: str) -> datetime:
    """Parse a stored timestamp, treating legacy naive values as UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


class IdempotencyRecord:
    """
    Record of an idempotent operation.

    Attributes:
        idempotency_key: Unique key for the operation
        operation_type: Type of operation (e.g., "create_purchase_order")
        status: "pending" | "completed" | "failed"
        result: Operation result (if completed)
        error: Error message (if failed)
        created_at: Record creation timestamp
        completed_at: Operation completion timestamp
        expires_at: Record expiration timestamp
    """

    def __init__(
        self,
        idempotency_key: str,
        operation_type: str,
        status: str = "pending",
        result: Any | None = None,
        error: str | None = None,
        created_at: str | None = None,
        completed_at: str | None = None,
        expires_at: str | None = None,
        ttl_hours: int = 24,
    ) -> None:
        self.idempotency_key = idempotency_key
        self.operation_type = operation_type
        self.status = status
        self.result = result
        self.error = error
        self.created_at = created_at or _utcnow().isoformat()
        self.completed_at = completed_at
        # Preserve the stored expiry on reload; only derive a fresh window when
        # a record is first created (otherwise records would never expire).
        self.expires_at = expires_at or (_utcnow() + timedelta(hours=ttl_hours)).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Serialize record to dictionary."""
        return {
            "idempotency_key": self.idempotency_key,
            "operation_type": self.operation_type,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IdempotencyRecord":
        """Deserialize record from dictionary."""
        return cls(
            idempotency_key=data["idempotency_key"],
            operation_type=data["operation_type"],
            status=data.get("status", "pending"),
            result=data.get("result"),
            error=data.get("error"),
            created_at=data.get("created_at"),
            completed_at=data.get("completed_at"),
            expires_at=data.get("expires_at"),
        )

    @property
    def is_expired(self) -> bool:
        """Check if the record has expired."""
        expires_at = _parse_stored_timestamp(self.expires_at)
        return _utcnow() > expires_at


class IdempotencyStore:
    """
    File-based idempotency store for tracking unique operations.

    Use this to ensure that operations with side effects (like creating
    purchase orders) are not duplicated during retries or recovery.

    Directory structure:
    - {cache_dir}/idempotency/
        - {idempotency_key}.json

    Example usage:
        ```python
        store = get_idempotency_store()

        # Check if operation already completed
        existing = await store.get(idempotency_key)
        if existing and existing.status == "completed":
            return existing.result  # Return cached result

        # Start operation
        await store.start(idempotency_key, "create_purchase_order")

        try:
            result = await create_purchase_order(...)
            await store.complete(idempotency_key, result)
            return result
        except Exception as e:
            await store.fail(idempotency_key, str(e))
            raise
        ```
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        """
        Initialize the idempotency store.

        Args:
            base_dir: Base directory for idempotency records.
        """
        self.base_dir = (base_dir or settings.cache_dir) / "idempotency"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.info("IdempotencyStore initialized", base_dir=str(self.base_dir))

    def _get_record_path(self, idempotency_key: str) -> Path:
        """Get the file path for a record."""
        # Use hash to avoid filesystem issues with special characters
        safe_key = idempotency_key.replace("/", "_").replace("\\", "_")
        return self.base_dir / f"{safe_key}.json"

    async def get(self, idempotency_key: str) -> IdempotencyRecord | None:
        """
        Get an existing idempotency record.

        Args:
            idempotency_key: The unique operation key.

        Returns:
            IdempotencyRecord or None if not found/expired.
        """
        record_path = self._get_record_path(idempotency_key)

        if not record_path.exists():
            return None

        try:
            async with aiofiles.open(record_path, encoding="utf-8") as f:
                content = await f.read()
                data = json.loads(content)
                record = IdempotencyRecord.from_dict(data)

                # Check expiration
                if record.is_expired:
                    await self.delete(idempotency_key)
                    return None

                return record
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read idempotency record", key=idempotency_key, error=str(e))
            return None

    async def start(
        self,
        idempotency_key: str,
        operation_type: str,
        ttl_hours: int = 24,
    ) -> IdempotencyRecord:
        """
        Start tracking an idempotent operation.

        Args:
            idempotency_key: Unique key for the operation.
            operation_type: Type of operation being performed.
            ttl_hours: Time-to-live for the record.

        Returns:
            The created IdempotencyRecord.

        Raises:
            ValueError: If operation already exists and is pending/completed.
        """
        existing = await self.get(idempotency_key)
        if existing:
            if existing.status == "completed":
                raise ValueError(f"Operation already completed: {idempotency_key}")
            if existing.status == "pending":
                # Could be a stale pending - check if it's old enough to retry
                created = _parse_stored_timestamp(existing.created_at)
                if _utcnow() - created < timedelta(minutes=5):
                    raise ValueError(f"Operation already in progress: {idempotency_key}")
                # Stale pending, allow retry

        record = IdempotencyRecord(
            idempotency_key=idempotency_key,
            operation_type=operation_type,
            status="pending",
            ttl_hours=ttl_hours,
        )

        if existing is None:
            # Atomic claim: O_EXCL create closes the check-then-write race, so
            # two concurrent start() calls cannot both proceed to the external
            # side effect - the loser sees FileExistsError here.
            try:
                self._get_record_path(idempotency_key).parent.mkdir(parents=True, exist_ok=True)
                with Path(self._get_record_path(idempotency_key)).open("x", encoding="utf-8") as handle:
                    json.dump(record.to_dict(), handle, indent=2)
            except FileExistsError:
                raise ValueError(f"Operation already in progress: {idempotency_key}") from None
        else:
            await self._write_record(record)

        logger.info(
            "Idempotent operation started",
            key=idempotency_key,
            operation_type=operation_type,
        )

        return record

    async def complete(
        self,
        idempotency_key: str,
        result: Any,
    ) -> IdempotencyRecord:
        """
        Mark an operation as completed.

        Args:
            idempotency_key: The operation key.
            result: The operation result to cache.

        Returns:
            The updated IdempotencyRecord.
        """
        record = await self.get(idempotency_key)
        if record is None:
            # Create a completed record even if start wasn't called
            record = IdempotencyRecord(
                idempotency_key=idempotency_key,
                operation_type="unknown",
            )

        record.status = "completed"
        record.result = result
        record.completed_at = _utcnow().isoformat()

        await self._write_record(record)

        logger.info(
            "Idempotent operation completed",
            key=idempotency_key,
        )

        return record

    async def fail(
        self,
        idempotency_key: str,
        error: str,
    ) -> IdempotencyRecord:
        """
        Mark an operation as failed.

        Args:
            idempotency_key: The operation key.
            error: Error message.

        Returns:
            The updated IdempotencyRecord.
        """
        record = await self.get(idempotency_key)
        if record is None:
            record = IdempotencyRecord(
                idempotency_key=idempotency_key,
                operation_type="unknown",
            )

        record.status = "failed"
        record.error = error
        record.completed_at = _utcnow().isoformat()

        await self._write_record(record)

        logger.warning(
            "Idempotent operation failed",
            key=idempotency_key,
            error=error,
        )

        return record

    async def delete(self, idempotency_key: str) -> bool:
        """
        Delete an idempotency record.

        Args:
            idempotency_key: The operation key.

        Returns:
            True if deleted, False if not found.
        """
        record_path = self._get_record_path(idempotency_key)

        if not record_path.exists():
            return False

        record_path.unlink()
        return True

    async def cleanup_expired(self) -> int:
        """
        Clean up expired records.

        Returns:
            Number of records deleted.
        """
        deleted = 0

        for record_file in self.base_dir.glob("*.json"):
            try:
                async with aiofiles.open(record_file, encoding="utf-8") as f:
                    content = await f.read()
                    data = json.loads(content)
                    record = IdempotencyRecord.from_dict(data)

                    if record.is_expired:
                        record_file.unlink()
                        deleted += 1
            except Exception:
                continue

        if deleted > 0:
            logger.info("Cleaned up expired idempotency records", count=deleted)

        return deleted

    async def _write_record(self, record: IdempotencyRecord) -> None:
        """Write record to file atomically."""
        record_path = self._get_record_path(record.idempotency_key)
        temp_path = record_path.with_suffix(".tmp")

        try:
            async with aiofiles.open(temp_path, mode="w", encoding="utf-8") as f:
                await f.write(json.dumps(record.to_dict(), indent=2, default=str))

            temp_path.rename(record_path)
        except OSError as e:
            logger.error("Failed to write idempotency record", error=str(e))
            if temp_path.exists():
                temp_path.unlink()
            raise


def generate_idempotency_key(
    operation_type: str,
    workflow_id: str,
    *args: Any,
) -> str:
    """
    Generate a deterministic idempotency key.

    Args:
        operation_type: Type of operation.
        workflow_id: Workflow instance ID.
        *args: Additional key components.

    Returns:
        Idempotency key string.
    """
    components = [operation_type, workflow_id, *[str(a) for a in args]]
    return ":".join(components)


# Module-level singleton for convenience
_idempotency_store: IdempotencyStore | None = None


def get_idempotency_store() -> IdempotencyStore:
    """Get the singleton idempotency store instance."""
    global _idempotency_store
    if _idempotency_store is None:
        _idempotency_store = IdempotencyStore()
    return _idempotency_store
