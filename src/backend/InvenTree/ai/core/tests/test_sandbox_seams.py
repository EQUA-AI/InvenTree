"""P0-11: the test-only sandbox seams behave as documented.

- ``recording_executor`` records calls and never executes anything.
- The write gate accepts it as a ``VoiceWriteExecutor`` and calls it only
  inside the confirmed-write fence.
"""

from __future__ import annotations

import pytest
from ai.core.tests.conftest import RecordingExecutor
from ai.core.voice.confirmation import ProposedWriteAction
from ai.core.voice.write_gate import (
    ExecutableWrite,
    InMemoryPendingWriteStore,
    ResolvedVoiceWrite,
    VoiceWriteExecutionResult,
    VoiceWriteExecutor,
    VoiceWriteGate,
)


class _Principal:
    user_id = 7


class _Resolver:
    def __init__(self, resolved: ResolvedVoiceWrite | None) -> None:
        self.resolved = resolved

    async def resolve(self, content, *, actor, trusted_context):
        return self.resolved


class _AllowAll:
    def allows(self, actor, capability):
        return True


def _resolved() -> ResolvedVoiceWrite:
    return ResolvedVoiceWrite(
        action=ProposedWriteAction(
            capability="inventory.write",
            summary="Add ten bearings to bin A",
        ),
        executable=ExecutableWrite(
            tool_name="add_stock",
            capability="inventory.write",
            arguments={"quantity": 10},
        ),
    )


def test_recording_executor_satisfies_the_protocol(recording_executor):
    assert isinstance(recording_executor, VoiceWriteExecutor)
    assert recording_executor.executed is False


async def test_recording_executor_records_without_executing(recording_executor):
    result = await recording_executor.execute(
        _resolved().executable, actor=_Principal(), trusted_context=None
    )
    assert result.ok is True
    assert result.detail == "recorded"
    assert recording_executor.executed is True
    assert recording_executor.calls[0].executable.tool_name == "add_stock"
    assert recording_executor.calls[0].actor_user_id == 7


async def test_recording_executor_can_raise_for_unknown_outcome():
    executor = RecordingExecutor(raise_exc=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await executor.execute(_resolved().executable, actor=_Principal(), trusted_context=None)
    assert executor.executed is True


async def test_gate_runs_the_recording_executor_inside_the_fence(recording_executor):
    gate = VoiceWriteGate(
        resolver=_Resolver(_resolved()),
        permission=_AllowAll(),
        executor=recording_executor,
        store=InMemoryPendingWriteStore(),
    )
    actor = _Principal()
    proposal = await gate.begin(
        content="add ten bearings to bin A",
        actor=actor,
        thread_id="t1",
        trusted_context=None,
        nonce="nonce-1",
    )
    assert proposal is not None
    assert recording_executor.executed is False

    outcome = await gate.resolve_pending(
        content="yes", actor=actor, thread_id="t1", trusted_context=None
    )
    assert outcome is not None
    assert recording_executor.executed is True
    assert recording_executor.calls[0].fence_open is True


async def test_recording_executor_reports_the_configured_outcome():
    executor = RecordingExecutor(
        outcome=VoiceWriteExecutionResult(ok=False, detail="tool_reported_failure")
    )
    result = await executor.execute(
        _resolved().executable, actor=_Principal(), trusted_context=None
    )
    assert result.ok is False
    assert result.detail == "tool_reported_failure"
