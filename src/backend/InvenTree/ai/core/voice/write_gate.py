"""Voice write enforcement gate (Phase 4 slice 3, Tier-3 writes).

Orchestrates the opt-in voice write path on top of the deterministic policy core
in ``ai.core.voice.confirmation``. It coordinates four deployment-owned seams --
all fail-closed, so the whole path is inert until a deployment supplies real
implementations AND enables ``feature_voice_write_confirmation``:

* ``resolver``   -- turns a transcript into a concrete, replayable tool call (the
  SAME centralized RBAC write tools the text surface uses); returns ``None`` for
  anything it will not execute. The gate never resolves speech itself.
* ``permission`` -- the RBAC check for a capability; consulted BEFORE a read-back
  and AGAIN before execution (defense in depth -- a grant may change between the
  two turns). The gate never checks RBAC itself.
* ``store``      -- persists one pending confirmation per thread across the two
  turns; ``take`` consumes it, so only the immediately following turn can
  confirm and a confirmation cannot be replayed.
* ``executor``   -- runs the one resolved tool call under a scoped relaxation of
  the read-only fence (``confirmed_write_exception``). The gate never executes a
  tool itself, and nothing but that single pre-resolved call is ever run
  un-fenced.

The read-only fence is relaxed only around the executor, only for a single
confirmed, resolved, and re-authorized call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ai.core.tools.read_only import confirmed_write_exception
from ai.core.voice.confirmation import (
    DONE_PHRASE,
    EXECUTION_FAILED_PHRASE,
    NOT_AUTHORIZED_PHRASE,
    PendingVoiceConfirmation,
    ProposedWriteAction,
    VoiceWriteAuditEvent,
    VoiceWriteAuditEventType,
    propose,
    resolve,
)

if TYPE_CHECKING:
    from ai.core.auth import AIPrincipal


@dataclass(frozen=True, slots=True)
class ExecutableWrite:
    """The concrete, replayable tool call a resolver bound from a transcript.

    JSON-safe so a durable store can persist it across turns. ``capability`` is
    re-checked before execution; ``tool_name``/``arguments`` are opaque to the
    gate and meaningful only to the executor.
    """

    tool_name: str
    capability: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResolvedVoiceWrite:
    """A resolver's output: the audited policy view plus the executable view."""

    action: ProposedWriteAction
    executable: ExecutableWrite


@dataclass(frozen=True, slots=True)
class StoredPendingWrite:
    """What the store holds between the propose turn and the confirm turn."""

    pending: PendingVoiceConfirmation
    executable: ExecutableWrite


@dataclass(frozen=True, slots=True)
class VoiceWriteExecutionResult:
    """The executor's report of running one resolved write."""

    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class WriteProposalResult:
    """Outcome of the propose turn: what to speak and whether we are now waiting."""

    spoken: str
    awaiting_confirmation: bool
    audit_events: tuple[VoiceWriteAuditEvent, ...]


@dataclass(frozen=True, slots=True)
class WriteResolutionResult:
    """Outcome of the confirm turn: what to speak and whether a write ran."""

    spoken: str
    executed: bool
    audit_events: tuple[VoiceWriteAuditEvent, ...]


@runtime_checkable
class VoiceWriteResolver(Protocol):
    """Resolves a transcript into a concrete, RBAC-scoped write, or ``None``."""

    async def resolve(
        self, content: str, *, actor: AIPrincipal, trusted_context: Any
    ) -> ResolvedVoiceWrite | None: ...


@runtime_checkable
class VoiceWritePermission(Protocol):
    """The RBAC decision for a capability -- the SAME authority text uses."""

    def allows(self, actor: AIPrincipal, capability: str) -> bool: ...


@runtime_checkable
class PendingVoiceWriteStore(Protocol):
    """Single-slot, consume-on-read pending store, keyed by thread."""

    def save(self, thread_id: Any, stored: StoredPendingWrite) -> None: ...

    def take(self, thread_id: Any) -> StoredPendingWrite | None: ...


@runtime_checkable
class VoiceWriteExecutor(Protocol):
    """Runs one resolved tool call; called only inside the confirmed-write fence."""

    async def execute(
        self, executable: ExecutableWrite, *, actor: AIPrincipal, trusted_context: Any
    ) -> VoiceWriteExecutionResult: ...


class InMemoryPendingWriteStore:
    """Per-process single-slot store; ``take`` consumes to enforce one-turn use.

    Suitable for tests and single-process deployments. A durable, cross-process
    store (surviving restarts and shared across workers) is a deployment seam.
    """

    def __init__(self) -> None:
        self._slots: dict[Any, StoredPendingWrite] = {}

    def save(self, thread_id: Any, stored: StoredPendingWrite) -> None:
        self._slots[thread_id] = stored

    def take(self, thread_id: Any) -> StoredPendingWrite | None:
        return self._slots.pop(thread_id, None)


class _DenyPermission:
    """Fail-closed default: no capability is granted."""

    def allows(self, actor: AIPrincipal, capability: str) -> bool:
        return False


class _NullResolver:
    """Fail-closed default: nothing is ever resolved into a write."""

    async def resolve(
        self, content: str, *, actor: AIPrincipal, trusted_context: Any
    ) -> ResolvedVoiceWrite | None:
        return None


class _UnavailableExecutor:
    """Fail-closed default: no executor configured, so no write can run."""

    async def execute(
        self, executable: ExecutableWrite, *, actor: AIPrincipal, trusted_context: Any
    ) -> VoiceWriteExecutionResult:
        return VoiceWriteExecutionResult(ok=False, detail="no executor configured")


def _event(
    event: VoiceWriteAuditEventType,
    action: ProposedWriteAction,
    *,
    thread_id: int,
    nonce: str,
    reason: str,
) -> VoiceWriteAuditEvent:
    return VoiceWriteAuditEvent(
        event=event,
        thread_id=thread_id,
        capability=action.capability,
        summary=action.summary,
        action_class=action.action_class,
        nonce=nonce,
        reason=reason,
    )


@dataclass(frozen=True, slots=True)
class VoiceWriteGate:
    """Sequences the four seams and applies the confirmation policy.

    Constructed with fail-closed defaults; a deployment injects real seams. With
    the defaults, ``begin`` resolves nothing (returns ``None``) and no write can
    ever execute -- the gate is safe to attach unconfigured.
    """

    resolver: VoiceWriteResolver = field(default_factory=_NullResolver)
    permission: VoiceWritePermission = field(default_factory=_DenyPermission)
    executor: VoiceWriteExecutor = field(default_factory=_UnavailableExecutor)
    store: PendingVoiceWriteStore = field(default_factory=InMemoryPendingWriteStore)

    async def begin(
        self,
        content: str,
        *,
        actor: AIPrincipal,
        trusted_context: Any,
        thread_id: int,
        nonce: str,
    ) -> WriteProposalResult | None:
        """Propose a write for the current effect turn, RBAC-gated.

        Returns ``None`` when the resolver declines (not a write we will act on),
        so the caller falls through to its normal advisory handling. Otherwise
        returns the read-back (or refusal) to speak; a pending confirmation is
        stored only when the actor was authorized and the action is confirmable.
        """
        resolved = await self.resolver.resolve(
            content, actor=actor, trusted_context=trusted_context
        )
        if resolved is None:
            return None
        has_permission = bool(self.permission.allows(actor, resolved.action.capability))
        pending, spoken, audit = propose(
            resolved.action,
            thread_id=thread_id,
            nonce=nonce,
            has_permission=has_permission,
        )
        if pending is not None:
            self.store.save(
                thread_id,
                StoredPendingWrite(pending=pending, executable=resolved.executable),
            )
        return WriteProposalResult(
            spoken=spoken,
            awaiting_confirmation=pending is not None,
            audit_events=(audit,),
        )

    async def resolve_pending(
        self,
        content: str,
        *,
        actor: AIPrincipal,
        trusted_context: Any,
        thread_id: int,
    ) -> WriteResolutionResult | None:
        """Interpret this turn as a confirmation reply to a stored proposal.

        Returns ``None`` when there is no pending write for the thread, so the
        caller proceeds with normal routing. The pending record is consumed on
        read, so a later turn cannot confirm a stale proposal.
        """
        stored = self.store.take(thread_id)
        if stored is None:
            return None
        outcome, decision_audit = resolve(stored.pending, content)
        events: list[VoiceWriteAuditEvent] = [decision_audit]
        if not outcome.confirmed:
            return WriteResolutionResult(
                spoken=outcome.spoken, executed=False, audit_events=tuple(events)
            )
        # Re-authorize at execution time: a grant may have changed since propose.
        if not self.permission.allows(actor, stored.executable.capability):
            events.append(
                _event(
                    VoiceWriteAuditEventType.NOT_AUTHORIZED,
                    stored.pending.action,
                    thread_id=thread_id,
                    nonce=stored.pending.nonce,
                    reason="not_authorized_at_execute",
                )
            )
            return WriteResolutionResult(
                spoken=NOT_AUTHORIZED_PHRASE, executed=False, audit_events=tuple(events)
            )
        # The one and only place the read-only fence is relaxed: a single
        # confirmed, resolved, re-authorized tool call.
        with confirmed_write_exception():
            result = await self.executor.execute(
                stored.executable, actor=actor, trusted_context=trusted_context
            )
        events.append(
            _event(
                VoiceWriteAuditEventType.EXECUTED
                if result.ok
                else VoiceWriteAuditEventType.EXECUTION_FAILED,
                stored.pending.action,
                thread_id=thread_id,
                nonce=stored.pending.nonce,
                reason=result.detail or ("executed" if result.ok else "failed"),
            )
        )
        return WriteResolutionResult(
            spoken=DONE_PHRASE if result.ok else EXECUTION_FAILED_PHRASE,
            executed=result.ok,
            audit_events=tuple(events),
        )
