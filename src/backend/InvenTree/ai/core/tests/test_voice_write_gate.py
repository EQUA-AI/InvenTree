"""Phase 4 slice 3: voice write enforcement gate.

Exercises the orchestration of the four fail-closed seams (resolver, permission,
store, executor) and the scoped read-only fence relaxation, with fakes. No
Django, no network, no real tools: the gate sequences seams and applies the
confirmation policy; the seams themselves are deployment-wired.
"""

from __future__ import annotations

import asyncio

from ai.core.tools.read_only import read_only_tool_fence, read_only_tools_active
from ai.core.voice.confirmation import (
    DONE_PHRASE,
    EXECUTION_FAILED_PHRASE,
    NOT_AUTHORIZED_PHRASE,
    ProposedWriteAction,
    VoiceWriteAuditEventType,
    WriteActionClass,
)
from ai.core.voice.write_gate import (
    ExecutableWrite,
    InMemoryPendingWriteStore,
    ResolvedVoiceWrite,
    VoiceWriteExecutionResult,
    VoiceWriteGate,
)

_ACTOR = object()
_CTX = object()


# --------------------------------------------------------------------------- #
# fakes                                                                        #
# --------------------------------------------------------------------------- #
class _Resolver:
    def __init__(self, resolved: ResolvedVoiceWrite | None) -> None:
        self._resolved = resolved

    async def resolve(self, content, *, actor, trusted_context):
        return self._resolved


class _Allow:
    def allows(self, actor, capability):
        return True


class _Deny:
    def allows(self, actor, capability):
        return False


class _AllowThenDeny:
    """Authorized at propose, revoked by execution time."""

    def __init__(self) -> None:
        self.calls = 0

    def allows(self, actor, capability):
        self.calls += 1
        return self.calls == 1


class _Executor:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[ExecutableWrite] = []
        self.fence_during: bool | None = None

    async def execute(self, executable, *, actor, trusted_context):
        self.fence_during = read_only_tools_active()
        self.calls.append(executable)
        return VoiceWriteExecutionResult(ok=self.ok)


def _resolved_confirmable() -> ResolvedVoiceWrite:
    return ResolvedVoiceWrite(
        action=ProposedWriteAction(
            capability="inventory.write",
            summary="Place a purchase order for 10 bearings",
        ),
        executable=ExecutableWrite(
            tool_name="create_purchase_order",
            capability="inventory.write",
            arguments={"qty": 10},
        ),
    )


def _resolved_irreversible() -> ResolvedVoiceWrite:
    return ResolvedVoiceWrite(
        action=ProposedWriteAction(
            capability="workorder.delete",
            summary="Delete work order 42",
            action_class=WriteActionClass.IRREVERSIBLE,
            confirm_phrase="confirm delete",
        ),
        executable=ExecutableWrite(
            tool_name="delete_work_order",
            capability="workorder.delete",
            arguments={"id": 42},
        ),
    )


def _gate(*, resolved=None, permission=None, executor=None, store=None) -> VoiceWriteGate:
    return VoiceWriteGate(
        resolver=_Resolver(resolved),
        permission=permission or _Allow(),
        executor=executor or _Executor(),
        store=store or InMemoryPendingWriteStore(),
    )


# --------------------------------------------------------------------------- #
# fail-closed defaults                                                         #
# --------------------------------------------------------------------------- #
def test_default_gate_resolves_nothing() -> None:
    gate = VoiceWriteGate()  # all fail-closed defaults
    result = asyncio.run(
        gate.begin("delete everything", actor=_ACTOR, trusted_context=_CTX, thread_id=1, nonce="n1")
    )
    assert result is None


def test_resolve_pending_with_no_pending_returns_none() -> None:
    gate = _gate(resolved=None)
    result = asyncio.run(
        gate.resolve_pending("yes", actor=_ACTOR, trusted_context=_CTX, thread_id=1)
    )
    assert result is None


# --------------------------------------------------------------------------- #
# RBAC precedes confirmation                                                   #
# --------------------------------------------------------------------------- #
def test_begin_without_permission_refuses_and_stores_nothing() -> None:
    store = InMemoryPendingWriteStore()
    gate = _gate(resolved=_resolved_confirmable(), permission=_Deny(), store=store)
    result = asyncio.run(
        gate.begin("place an order", actor=_ACTOR, trusted_context=_CTX, thread_id=1, nonce="n1")
    )
    assert result is not None
    assert result.awaiting_confirmation is False
    assert result.spoken == NOT_AUTHORIZED_PHRASE
    assert result.audit_events[0].event is VoiceWriteAuditEventType.NOT_AUTHORIZED
    # Nothing to confirm later.
    assert store.take(1) is None


# --------------------------------------------------------------------------- #
# reversible happy path + fence scoping                                       #
# --------------------------------------------------------------------------- #
def test_confirmable_propose_then_bare_yes_executes_under_scoped_fence() -> None:
    store = InMemoryPendingWriteStore()
    executor = _Executor(ok=True)
    gate = _gate(resolved=_resolved_confirmable(), executor=executor, store=store)

    async def run():
        proposal = await gate.begin(
            "place an order",
            actor=_ACTOR,
            trusted_context=_CTX,
            thread_id=1,
            nonce="n1",
        )
        # The confirm turn runs inside the whole-run read-only fence.
        with read_only_tool_fence():
            assert read_only_tools_active() is True
            resolution = await gate.resolve_pending(
                "yes", actor=_ACTOR, trusted_context=_CTX, thread_id=1
            )
            fence_after = read_only_tools_active()
        return proposal, resolution, fence_after

    proposal, resolution, fence_after = asyncio.run(run())

    assert proposal.awaiting_confirmation is True
    assert resolution.executed is True
    assert resolution.spoken == DONE_PHRASE
    assert executor.calls[0].tool_name == "create_purchase_order"
    # The fence was relaxed only for the executor, then restored.
    assert executor.fence_during is False
    assert fence_after is True
    assert resolution.audit_events[-1].event is VoiceWriteAuditEventType.EXECUTED


def test_pending_is_consumed_and_cannot_be_replayed() -> None:
    store = InMemoryPendingWriteStore()
    gate = _gate(resolved=_resolved_confirmable(), store=store)

    async def run():
        await gate.begin(
            "place an order", actor=_ACTOR, trusted_context=_CTX, thread_id=1, nonce="n1"
        )
        first = await gate.resolve_pending("yes", actor=_ACTOR, trusted_context=_CTX, thread_id=1)
        second = await gate.resolve_pending("yes", actor=_ACTOR, trusted_context=_CTX, thread_id=1)
        return first, second

    first, second = asyncio.run(run())
    assert first is not None and first.executed is True
    assert second is None  # consumed on the first take


def test_decline_cancels_without_executing() -> None:
    executor = _Executor()
    gate = _gate(resolved=_resolved_confirmable(), executor=executor)

    async def run():
        await gate.begin(
            "place an order", actor=_ACTOR, trusted_context=_CTX, thread_id=1, nonce="n1"
        )
        return await gate.resolve_pending("cancel", actor=_ACTOR, trusted_context=_CTX, thread_id=1)

    resolution = asyncio.run(run())
    assert resolution.executed is False
    assert executor.calls == []
    assert resolution.audit_events[0].event is VoiceWriteAuditEventType.CANCELLED


# --------------------------------------------------------------------------- #
# irreversible: strict phrase required                                        #
# --------------------------------------------------------------------------- #
def test_irreversible_requires_strict_phrase_before_executing() -> None:
    executor = _Executor()
    gate = _gate(resolved=_resolved_irreversible(), executor=executor)

    async def run():
        proposal = await gate.begin(
            "delete work order 42", actor=_ACTOR, trusted_context=_CTX, thread_id=1, nonce="n1"
        )
        weak = await gate.resolve_pending("yes", actor=_ACTOR, trusted_context=_CTX, thread_id=1)
        # A bare yes consumed the pending; re-propose for the strict attempt.
        await gate.begin(
            "delete work order 42", actor=_ACTOR, trusted_context=_CTX, thread_id=1, nonce="n2"
        )
        strong = await gate.resolve_pending(
            "confirm delete", actor=_ACTOR, trusted_context=_CTX, thread_id=1
        )
        return proposal, weak, strong

    proposal, weak, strong = asyncio.run(run())
    assert "This cannot be undone." in proposal.spoken
    assert weak.executed is False  # bare yes did not confirm a destructive action
    assert strong.executed is True
    # Exactly one execution -- only the strict "confirm delete" ran.
    assert len(executor.calls) == 1
    assert executor.calls[0].tool_name == "delete_work_order"


# --------------------------------------------------------------------------- #
# re-authorization + execution failure                                        #
# --------------------------------------------------------------------------- #
def test_permission_revoked_between_turns_blocks_execution() -> None:
    executor = _Executor()
    gate = _gate(resolved=_resolved_confirmable(), permission=_AllowThenDeny(), executor=executor)

    async def run():
        await gate.begin(
            "place an order", actor=_ACTOR, trusted_context=_CTX, thread_id=1, nonce="n1"
        )
        return await gate.resolve_pending("yes", actor=_ACTOR, trusted_context=_CTX, thread_id=1)

    resolution = asyncio.run(run())
    assert resolution.executed is False
    assert resolution.spoken == NOT_AUTHORIZED_PHRASE
    assert executor.calls == []
    assert resolution.audit_events[-1].event is VoiceWriteAuditEventType.NOT_AUTHORIZED


def test_executor_failure_is_reported_and_not_claimed_as_done() -> None:
    gate = _gate(resolved=_resolved_confirmable(), executor=_Executor(ok=False))

    async def run():
        await gate.begin(
            "place an order", actor=_ACTOR, trusted_context=_CTX, thread_id=1, nonce="n1"
        )
        return await gate.resolve_pending("yes", actor=_ACTOR, trusted_context=_CTX, thread_id=1)

    resolution = asyncio.run(run())
    assert resolution.executed is False
    assert resolution.spoken == EXECUTION_FAILED_PHRASE
    assert resolution.audit_events[-1].event is VoiceWriteAuditEventType.EXECUTION_FAILED
