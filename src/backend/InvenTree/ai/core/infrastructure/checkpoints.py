"""
File-based Checkpoint Storage Implementation

Provides checkpoint persistence for workflow state recovery.
Supports the three checkpoint types defined in AIMMS v2.3:
- INTERNAL_COMPUTE_ONLY: Internal state, no rollback needed
- PRE_EXTERNAL_WRITE: Before external writes, enables rollback
- POST_EXTERNAL_WRITE: After external writes, confirms completion
"""

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiofiles
import structlog
from ai.core.config import settings

logger = structlog.get_logger(__name__)


class CheckpointType(StrEnum):
    """Checkpoint type classification for recovery semantics."""

    INTERNAL_COMPUTE_ONLY = "internal_compute_only"
    """Internal computation state. No external side effects. Can be safely replayed."""

    PRE_EXTERNAL_WRITE = "pre_external_write"
    """Before external write operation. Enables rollback if operation fails."""

    POST_EXTERNAL_WRITE = "post_external_write"
    """After external write confirmed. Cannot rollback. For audit trail."""


class CheckpointData:
    """
    Checkpoint data structure.

    Attributes:
        checkpoint_id: Unique checkpoint identifier
        workflow_id: ID of the workflow instance
        checkpoint_type: Type of checkpoint for recovery semantics
        step_name: Name of the workflow step
        state: Serialized workflow state
        metadata: Additional metadata (timestamps, agent info, etc.)
        created_at: Checkpoint creation timestamp
    """

    def __init__(
        self,
        workflow_id: str,
        checkpoint_type: CheckpointType,
        step_name: str,
        state: dict[str, Any],
        checkpoint_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.checkpoint_id = checkpoint_id or str(uuid4())
        self.workflow_id = workflow_id
        self.checkpoint_type = checkpoint_type
        self.step_name = step_name
        self.state = state
        self.metadata = metadata or {}
        self.created_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Serialize checkpoint to dictionary."""
        return {
            "checkpoint_id": self.checkpoint_id,
            "workflow_id": self.workflow_id,
            "checkpoint_type": self.checkpoint_type.value,
            "step_name": self.step_name,
            "state": self.state,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CheckpointData":
        """Deserialize checkpoint from dictionary."""
        return cls(
            checkpoint_id=data["checkpoint_id"],
            workflow_id=data["workflow_id"],
            checkpoint_type=CheckpointType(data["checkpoint_type"]),
            step_name=data["step_name"],
            state=data["state"],
            metadata=data.get("metadata", {}),
        )


class FileCheckpointStorage:
    """
    File-based checkpoint storage for workflow recovery.

    Directory structure:
    - {checkpoints_dir}/{workflow_id}/
        - {checkpoint_id}.json
        - latest.json (symlink to most recent checkpoint)

    Features:
    - Organized by workflow for efficient recovery queries
    - Latest checkpoint tracking for quick resume
    - Checkpoint type filtering for recovery strategies
    - Atomic writes for consistency
    """

    def __init__(self, checkpoints_dir: Path | None = None) -> None:
        """
        Initialize the file-based checkpoint storage.

        Args:
            checkpoints_dir: Directory for checkpoint files.
        """
        self.checkpoints_dir = checkpoints_dir or settings.checkpoints_dir
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        logger.info("FileCheckpointStorage initialized", checkpoints_dir=str(self.checkpoints_dir))

    def _get_workflow_dir(self, workflow_id: str) -> Path:
        """Get the directory for a workflow's checkpoints."""
        workflow_dir = self.checkpoints_dir / workflow_id
        workflow_dir.mkdir(parents=True, exist_ok=True)
        return workflow_dir

    def _get_checkpoint_path(self, workflow_id: str, checkpoint_id: str) -> Path:
        """Get the file path for a checkpoint."""
        return self._get_workflow_dir(workflow_id) / f"{checkpoint_id}.json"

    async def save_checkpoint(self, checkpoint: CheckpointData) -> str:
        """
        Save a checkpoint to storage.

        Args:
            checkpoint: The checkpoint data to save.

        Returns:
            The checkpoint ID.
        """
        checkpoint_path = self._get_checkpoint_path(
            checkpoint.workflow_id,
            checkpoint.checkpoint_id,
        )

        await self._write_checkpoint(checkpoint_path, checkpoint.to_dict())

        # Update latest pointer
        await self._update_latest(checkpoint.workflow_id, checkpoint.checkpoint_id)

        logger.info(
            "Checkpoint saved",
            checkpoint_id=checkpoint.checkpoint_id,
            workflow_id=checkpoint.workflow_id,
            checkpoint_type=checkpoint.checkpoint_type.value,
            step_name=checkpoint.step_name,
        )

        return checkpoint.checkpoint_id

    async def get_checkpoint(
        self,
        workflow_id: str,
        checkpoint_id: str,
    ) -> CheckpointData | None:
        """
        Get a specific checkpoint.

        Args:
            workflow_id: The workflow ID.
            checkpoint_id: The checkpoint ID.

        Returns:
            CheckpointData or None if not found.
        """
        checkpoint_path = self._get_checkpoint_path(workflow_id, checkpoint_id)
        data = await self._read_checkpoint(checkpoint_path)

        if data is None:
            return None

        return CheckpointData.from_dict(data)

    async def get_latest_checkpoint(self, workflow_id: str) -> CheckpointData | None:
        """
        Get the most recent checkpoint for a workflow.

        Args:
            workflow_id: The workflow ID.

        Returns:
            The latest CheckpointData or None if no checkpoints exist.
        """
        workflow_dir = self._get_workflow_dir(workflow_id)
        latest_path = workflow_dir / "latest.json"

        if not latest_path.exists():
            return None

        data = await self._read_checkpoint(latest_path)
        if data is None:
            return None

        return CheckpointData.from_dict(data)

    async def list_checkpoints(
        self,
        workflow_id: str,
        checkpoint_type: CheckpointType | None = None,
        limit: int = 100,
    ) -> list[CheckpointData]:
        """
        List checkpoints for a workflow.

        Args:
            workflow_id: The workflow ID.
            checkpoint_type: Optional filter by checkpoint type.
            limit: Maximum number of checkpoints to return.

        Returns:
            List of checkpoints ordered by creation time (newest first).
        """
        workflow_dir = self._get_workflow_dir(workflow_id)

        checkpoint_files = sorted(
            [f for f in workflow_dir.glob("*.json") if f.name != "latest.json"],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        checkpoints = []
        for checkpoint_file in checkpoint_files[: limit * 2]:  # Over-fetch for filtering
            data = await self._read_checkpoint(checkpoint_file)
            if data is None:
                continue

            checkpoint = CheckpointData.from_dict(data)

            # Apply type filter
            if checkpoint_type and checkpoint.checkpoint_type != checkpoint_type:
                continue

            checkpoints.append(checkpoint)

            if len(checkpoints) >= limit:
                break

        return checkpoints

    async def get_recovery_checkpoint(
        self,
        workflow_id: str,
    ) -> CheckpointData | None:
        """
        Get the appropriate checkpoint for recovery.

        Recovery strategy:
        1. Find the latest PRE_EXTERNAL_WRITE checkpoint
        2. If none, use the latest INTERNAL_COMPUTE_ONLY checkpoint
        3. Never recover from POST_EXTERNAL_WRITE (already committed)

        Args:
            workflow_id: The workflow ID.

        Returns:
            The best checkpoint for recovery or None.
        """
        # First, try to find a PRE_EXTERNAL_WRITE checkpoint
        pre_write_checkpoints = await self.list_checkpoints(
            workflow_id,
            checkpoint_type=CheckpointType.PRE_EXTERNAL_WRITE,
            limit=1,
        )

        if pre_write_checkpoints:
            logger.info(
                "Recovery checkpoint found (PRE_EXTERNAL_WRITE)",
                workflow_id=workflow_id,
                checkpoint_id=pre_write_checkpoints[0].checkpoint_id,
            )
            return pre_write_checkpoints[0]

        # Fall back to INTERNAL_COMPUTE_ONLY
        internal_checkpoints = await self.list_checkpoints(
            workflow_id,
            checkpoint_type=CheckpointType.INTERNAL_COMPUTE_ONLY,
            limit=1,
        )

        if internal_checkpoints:
            logger.info(
                "Recovery checkpoint found (INTERNAL_COMPUTE_ONLY)",
                workflow_id=workflow_id,
                checkpoint_id=internal_checkpoints[0].checkpoint_id,
            )
            return internal_checkpoints[0]

        logger.warning("No recovery checkpoint found", workflow_id=workflow_id)
        return None

    async def delete_checkpoints(
        self,
        workflow_id: str,
        before: datetime | None = None,
    ) -> int:
        """
        Delete checkpoints for a workflow.

        Args:
            workflow_id: The workflow ID.
            before: Optional - only delete checkpoints created before this time.

        Returns:
            Number of checkpoints deleted.
        """
        workflow_dir = self._get_workflow_dir(workflow_id)

        if not workflow_dir.exists():
            return 0

        deleted = 0
        for checkpoint_file in workflow_dir.glob("*.json"):
            if checkpoint_file.name == "latest.json":
                continue

            if before:
                data = await self._read_checkpoint(checkpoint_file)
                if data:
                    created_at = datetime.fromisoformat(data.get("created_at", ""))
                    # Legacy checkpoints stored naive UTC timestamps; normalize
                    # both sides so aware/naive values compare safely.
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=UTC)
                    cutoff = before if before.tzinfo is not None else before.replace(tzinfo=UTC)
                    if created_at >= cutoff:
                        continue

            checkpoint_file.unlink()
            deleted += 1

        # Clean up latest if all checkpoints deleted
        latest_path = workflow_dir / "latest.json"
        remaining = list(workflow_dir.glob("*.json"))
        remaining = [f for f in remaining if f.name != "latest.json"]

        if not remaining and latest_path.exists():
            latest_path.unlink()

        logger.info(
            "Checkpoints deleted",
            workflow_id=workflow_id,
            count=deleted,
        )

        return deleted

    async def _update_latest(self, workflow_id: str, checkpoint_id: str) -> None:
        """Update the latest checkpoint pointer."""
        workflow_dir = self._get_workflow_dir(workflow_id)
        latest_path = workflow_dir / "latest.json"
        checkpoint_path = self._get_checkpoint_path(workflow_id, checkpoint_id)

        if checkpoint_path.exists():
            # Copy content to latest (not symlink for cross-platform compat)
            data = await self._read_checkpoint(checkpoint_path)
            if data:
                await self._write_checkpoint(latest_path, data)

    async def _read_checkpoint(self, path: Path) -> dict[str, Any] | None:
        """Read checkpoint data from file."""
        if not path.exists():
            return None

        try:
            async with aiofiles.open(path, encoding="utf-8") as f:
                content = await f.read()
                return json.loads(content)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read checkpoint", path=str(path), error=str(e))
            return None

    async def _write_checkpoint(self, path: Path, data: dict[str, Any]) -> None:
        """Write checkpoint data to file atomically."""
        temp_path = path.with_suffix(".tmp")

        try:
            async with aiofiles.open(temp_path, mode="w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2, default=str))

            # Atomic rename
            temp_path.rename(path)
        except OSError as e:
            logger.error("Failed to write checkpoint", path=str(path), error=str(e))
            if temp_path.exists():
                temp_path.unlink()
            raise


# Module-level singleton for convenience
_checkpoint_storage: FileCheckpointStorage | None = None


def get_checkpoint_storage() -> FileCheckpointStorage:
    """Get the singleton checkpoint storage instance."""
    global _checkpoint_storage
    if _checkpoint_storage is None:
        _checkpoint_storage = FileCheckpointStorage()
    return _checkpoint_storage
