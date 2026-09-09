"""Shared pytest fixtures for the ai/core island.

Voice-UX plan P0-11 (sandbox executor seams): every decision/write-gate test
runs the canonical confirm flow against a RECORDING executor so no test can
reach a real tool, mailbox, order or stock record. The fixture is the single
sanctioned stand-in for ``VoiceWriteExecutor`` in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from ai.core.tools.read_only import read_only_tools_active
from ai.core.voice.write_gate import ExecutableWrite, VoiceWriteExecutionResult


@dataclass
class RecordedCall:
    """One executor invocation as seen by the recording executor."""

    executable: ExecutableWrite
    actor_user_id: Any
    fence_open: bool


@dataclass
class RecordingExecutor:
    """A ``VoiceWriteExecutor`` that records instead of executing.

    ``outcome`` is returned verbatim for every call; ``raise_exc`` makes the
    call raise instead (the "unknown outcome" path). Nothing here touches a
    tool, the network or the database.
    """

    outcome: VoiceWriteExecutionResult = field(
        default_factory=lambda: VoiceWriteExecutionResult(ok=True, detail="recorded")
    )
    raise_exc: BaseException | None = None
    calls: list[RecordedCall] = field(default_factory=list)

    async def execute(
        self, executable: ExecutableWrite, *, actor: Any, trusted_context: Any
    ) -> VoiceWriteExecutionResult:
        self.calls.append(
            RecordedCall(
                executable=executable,
                actor_user_id=getattr(actor, "user_id", None),
                # True when the confirmed-write fence is OPEN (read-only fence
                # not active) at the moment of execution.
                fence_open=not read_only_tools_active(),
            )
        )
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.outcome

    @property
    def executed(self) -> bool:
        return bool(self.calls)


@pytest.fixture
def recording_executor() -> RecordingExecutor:
    """A fresh recording executor per test."""
    return RecordingExecutor()
