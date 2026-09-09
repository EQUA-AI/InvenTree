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
    NOT_AUTHORIZED_PHRASE,
    NOT_COMPLETED_PHRASE,
    PendingVoiceConfirmation,
    ProposedWriteAction,
    VoiceWriteAuditEventType,
    WriteActionClass,
)
from ai.core.voice.write_gate import (
    ExecutableWrite,
    InMemoryPendingWriteStore,
    ResolvedVoiceWrite,
    StoredPendingWrite,
    VoiceWriteExecutionResult,
    VoiceWriteGate,
    WriteResolutionResult,
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
    assert resolution.spoken.startswith("Completed: ")
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
    assert resolution.spoken == NOT_COMPLETED_PHRASE
    assert resolution.audit_events[-1].event is VoiceWriteAuditEventType.EXECUTION_FAILED


# --------------------------------------------------------------------------- #
# Grammar v3 (voice-UX plan A1): AMEND / DEFER replies never reach the executor #
# --------------------------------------------------------------------------- #
import pytest  # noqa: E402
from ai.core.voice.confirmation import AMEND_PHRASE, DEFERRED_PHRASE  # noqa: E402


@pytest.mark.parametrize(
    ("reply", "spoken"),
    (
        ("yes, but change it to twenty", AMEND_PHRASE),
        ("no, I meant the other order", AMEND_PHRASE),
        ("not yet", DEFERRED_PHRASE),
        ("yes, hold on", DEFERRED_PHRASE),
    ),
)
def test_amend_and_defer_replies_never_execute(reply, spoken) -> None:
    executor = _Executor()
    gate = _gate(resolved=_resolved_confirmable(), executor=executor)

    async def run():
        await gate.begin(
            "place an order", actor=_ACTOR, trusted_context=_CTX, thread_id=1, nonce="n1"
        )
        return await gate.resolve_pending(reply, actor=_ACTOR, trusted_context=_CTX, thread_id=1)

    resolution = asyncio.run(run())

    assert resolution is not None
    assert resolution.executed is False
    assert resolution.spoken == spoken
    assert executor.calls == []
    assert resolution.audit_events[-1].event is VoiceWriteAuditEventType.CANCELLED
    assert resolution.audit_events[-1].reason in {"amended", "deferred"}


def test_mixed_assent_under_strict_phrase_never_executes() -> None:
    executor = _Executor()
    gate = _gate(resolved=_resolved_irreversible(), executor=executor)

    async def run():
        await gate.begin(
            "delete work order 42", actor=_ACTOR, trusted_context=_CTX, thread_id=1, nonce="n1"
        )
        return await gate.resolve_pending(
            "confirm delete the other one", actor=_ACTOR, trusted_context=_CTX, thread_id=1
        )

    resolution = asyncio.run(run())

    assert resolution is not None
    assert resolution.executed is False
    assert resolution.spoken == AMEND_PHRASE
    assert executor.calls == []


# --------------------------------------------------------------------------- #
# Honest result model (voice-UX plan A2)                                       #
# --------------------------------------------------------------------------- #
from ai.core.voice.confirmation import (  # noqa: E402
    ACCEPTED_PENDING_PHRASE,
    NOT_APPLIED_PHRASE,
    PARTIAL_RESULT_PHRASE,
    UNKNOWN_RESULT_PHRASE,
)
from ai.core.voice.write_gate import (  # noqa: E402
    VoiceWriteOutcome,
    spoken_for_result,
)


class _OutcomeExecutor:
    def __init__(self, result: VoiceWriteExecutionResult) -> None:
        self.result = result
        self.calls: list[ExecutableWrite] = []

    async def execute(self, executable, *, actor, trusted_context):
        self.calls.append(executable)
        return self.result


def _run_confirm(executor) -> WriteResolutionResult:
    gate = _gate(resolved=_resolved_confirmable(), executor=executor)

    async def run():
        await gate.begin(
            "place an order", actor=_ACTOR, trusted_context=_CTX, thread_id=1, nonce="n1"
        )
        return await gate.resolve_pending("yes", actor=_ACTOR, trusted_context=_CTX, thread_id=1)

    return asyncio.run(run())


@pytest.mark.parametrize(
    ("result", "spoken", "executed", "committed"),
    (
        (
            VoiceWriteExecutionResult(
                ok=False,
                detail="capability_mismatch",
                outcome=VoiceWriteOutcome.FAILED_BEFORE_EFFECT,
                effect_committed=False,
            ),
            NOT_APPLIED_PHRASE,
            False,
            False,
        ),
        (
            VoiceWriteExecutionResult(
                ok=False, detail="execution_failed", outcome=VoiceWriteOutcome.UNKNOWN
            ),
            UNKNOWN_RESULT_PHRASE,
            False,
            None,
        ),
        (
            VoiceWriteExecutionResult(
                ok=False, detail="tool_reported_failure", outcome=VoiceWriteOutcome.NOT_COMPLETED
            ),
            NOT_COMPLETED_PHRASE,
            False,
            None,
        ),
        (
            VoiceWriteExecutionResult(ok=False, detail="legacy"),
            NOT_COMPLETED_PHRASE,
            False,
            None,
        ),
        (
            VoiceWriteExecutionResult(ok=True, outcome=VoiceWriteOutcome.ACCEPTED_PENDING),
            ACCEPTED_PENDING_PHRASE,
            False,
            None,
        ),
        (
            VoiceWriteExecutionResult(ok=False, outcome=VoiceWriteOutcome.PARTIAL),
            PARTIAL_RESULT_PHRASE,
            False,
            None,
        ),
    ),
)
def test_outcomes_speak_honest_phrases(result, spoken, executed, committed) -> None:
    resolution = _run_confirm(_OutcomeExecutor(result))
    assert resolution.spoken == spoken
    assert resolution.executed is executed
    assert resolution.effect_committed is committed
    assert resolution.outcome is result.resolved_outcome
    assert resolution.audit_events[-1].event is VoiceWriteAuditEventType.EXECUTION_FAILED
    assert resolution.audit_events[-1].reason.startswith(result.resolved_outcome.value)


def test_success_names_the_record_and_the_change() -> None:
    result = VoiceWriteExecutionResult(
        ok=True,
        outcome=VoiceWriteOutcome.SUCCEEDED,
        record_label="Purchase order PO-0042",
        change_label="has been created as a draft",
        receipt_ref="order_id:42",
        effect_committed=True,
    )
    resolution = _run_confirm(_OutcomeExecutor(result))
    assert resolution.spoken == "Purchase order PO-0042 has been created as a draft."
    assert resolution.executed is True
    assert resolution.effect_committed is True
    assert resolution.receipt_ref == "order_id:42"
    assert resolution.audit_events[-1].event is VoiceWriteAuditEventType.EXECUTED


def test_success_without_labels_falls_back_to_the_readback_summary() -> None:
    resolution = _run_confirm(_OutcomeExecutor(VoiceWriteExecutionResult(ok=True)))
    assert resolution.spoken == "Completed: Place a purchase order for 10 bearings."
    assert resolution.executed is True
    assert resolution.effect_committed is True


def test_stored_record_label_is_used_when_the_executor_has_none() -> None:
    stored = StoredPendingWrite(
        pending=PendingVoiceConfirmation(
            nonce="n", thread_id=1, action=_resolved_confirmable().action
        ),
        executable=_resolved_confirmable().executable,
        record_label="Bearing 6205",
    )
    result = VoiceWriteExecutionResult(ok=True, change_label="now has the added stock")
    assert spoken_for_result(result, stored) == "Bearing 6205 now has the added stock."


def test_old_generic_phrases_are_gone() -> None:
    import ai.core.voice.confirmation as confirmation

    assert not hasattr(confirmation, "DONE_PHRASE")
    assert not hasattr(confirmation, "EXECUTION_FAILED_PHRASE")
    assert "Done." not in confirmation.ALLOWED_CONFIRMATION_PHRASES
    assert not any(
        "Nothing was changed" in phrase for phrase in confirmation.ALLOWED_CONFIRMATION_PHRASES
    )
