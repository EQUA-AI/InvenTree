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

import inspect
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ai.core.tools.read_only import confirmed_write_exception
from ai.core.voice import status_phrases
from ai.core.voice.confirmation import (
    ACCEPTED_PENDING_PHRASE,
    AWAITING_REVIEW_PHRASE,
    DRAFT_PHRASE,
    NOT_APPLIED_PHRASE,
    NOT_AUTHORIZED_PHRASE,
    NOT_COMPLETED_PHRASE,
    PARTIAL_RESULT_PHRASE,
    STRICT_PHRASE_REQUIRED_PHRASE,
    UNKNOWN_RESULT_PHRASE,
    ConfirmationReason,
    ConfirmationReply,
    PendingVoiceConfirmation,
    ProposedWriteAction,
    VoiceWriteAuditEvent,
    VoiceWriteAuditEventType,
    assemble_spoken,
    interpret_confirmation_reply,
    propose,
    resolve,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from ai.core.auth import AIPrincipal


logger = logging.getLogger(__name__)


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
    #: Server-derived human label of the target record ("work order 140",
    #: "Pump seal kit"), used in the spoken success sentence. Never transcript.
    record_label: str = ""


@dataclass(frozen=True, slots=True)
class StoredPendingWrite:
    """What the store holds between the propose turn and the confirm turn."""

    pending: PendingVoiceConfirmation
    executable: ExecutableWrite
    record_label: str = ""


class VoiceWriteOutcome(StrEnum):
    """Recorded outcome of one confirmed write (voice-UX plan §5.5).

    Only ``SUCCEEDED`` may be spoken as done; ``FAILED_BEFORE_EFFECT`` may be
    spoken as "not applied" only when the executor proves nothing ran;
    ``NOT_COMPLETED``/``UNKNOWN``/``PARTIAL`` never claim "nothing changed".
    """

    DRAFT = "draft"
    AWAITING_REVIEW = "awaiting_review"
    ACCEPTED_PENDING = "accepted_pending"
    SUCCEEDED = "succeeded"
    FAILED_BEFORE_EFFECT = "failed_before_effect"
    NOT_COMPLETED = "not_completed"
    UNKNOWN = "unknown"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class VoiceWriteExecutionResult:
    """The executor's report of running one resolved write.

    ``ok`` is kept for executors that only know success/failure; such a
    failure resolves to ``NOT_COMPLETED`` (never "nothing changed"). Executors
    that can prove more set ``outcome`` and ``effect_committed`` explicitly.
    """

    ok: bool
    detail: str = ""
    outcome: VoiceWriteOutcome | None = None
    record_label: str = ""
    change_label: str = ""
    receipt_ref: str = ""
    effect_committed: bool | None = None

    @property
    def resolved_outcome(self) -> VoiceWriteOutcome:
        if self.outcome is not None:
            return self.outcome
        return VoiceWriteOutcome.SUCCEEDED if self.ok else VoiceWriteOutcome.NOT_COMPLETED

    @property
    def committed(self) -> bool | None:
        """Whether the effect is known to have committed (``None`` = unknown)."""
        if self.effect_committed is not None:
            return self.effect_committed
        outcome = self.resolved_outcome
        if outcome is VoiceWriteOutcome.SUCCEEDED:
            return True
        if outcome is VoiceWriteOutcome.FAILED_BEFORE_EFFECT:
            return False
        return None


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
    outcome: VoiceWriteOutcome | None = None
    effect_committed: bool | None = None
    receipt_ref: str = ""
    #: A3: the reply was unrelated, so the pending write was set aside and the
    #: turn must still be routed normally; ``spoken`` is then a status phrase
    #: to say BEFORE the routed answer, not a captured-turn canonical.
    route_normally: bool = False


_OUTCOME_PHRASES: dict[VoiceWriteOutcome, str] = {
    VoiceWriteOutcome.DRAFT: DRAFT_PHRASE,
    VoiceWriteOutcome.AWAITING_REVIEW: AWAITING_REVIEW_PHRASE,
    VoiceWriteOutcome.ACCEPTED_PENDING: ACCEPTED_PENDING_PHRASE,
    VoiceWriteOutcome.FAILED_BEFORE_EFFECT: NOT_APPLIED_PHRASE,
    VoiceWriteOutcome.NOT_COMPLETED: NOT_COMPLETED_PHRASE,
    VoiceWriteOutcome.UNKNOWN: UNKNOWN_RESULT_PHRASE,
    VoiceWriteOutcome.PARTIAL: PARTIAL_RESULT_PHRASE,
}


def spoken_for_result(result: VoiceWriteExecutionResult, stored: StoredPendingWrite) -> str:
    """The honest spoken outcome for one execution result.

    Success names the record and the change from server-derived labels; when a
    label is missing it falls back to the read-back summary, never to a bare
    "Done". Every other outcome is a fixed, allow-listed phrase.
    """
    outcome = result.resolved_outcome
    if outcome is not VoiceWriteOutcome.SUCCEEDED:
        return _OUTCOME_PHRASES[outcome]
    record_label = result.record_label or stored.record_label
    if record_label and result.change_label:
        try:
            return assemble_spoken(
                "succeeded", record_label=record_label, change_label=result.change_label
            )
        except ValueError:
            pass
    return assemble_spoken("completed_summary", summary=stored.pending.action.summary)


@runtime_checkable
class VoiceWriteResolver(Protocol):
    """Resolves a transcript into a concrete, RBAC-scoped write, or ``None``."""

    async def resolve(
        self, content: str, *, actor: AIPrincipal, trusted_context: Any
    ) -> ResolvedVoiceWrite | None: ...


@runtime_checkable
class VoiceWritePermission(Protocol):
    """The RBAC decision for a capability -- the SAME authority text uses."""

    def allows(self, actor: AIPrincipal, capability: str) -> bool | Awaitable[bool]: ...


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

    async def _allows(self, actor: AIPrincipal, capability: str) -> bool:
        decision = self.permission.allows(actor, capability)
        if inspect.isawaitable(decision):
            decision = await decision
        return bool(decision)

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
        has_permission = await self._allows(actor, resolved.action.capability)
        pending, spoken, audit = propose(
            resolved.action,
            thread_id=thread_id,
            nonce=nonce,
            has_permission=has_permission,
        )
        if pending is not None:
            self.store.save(
                thread_id,
                StoredPendingWrite(
                    pending=pending,
                    executable=resolved.executable,
                    record_label=resolved.record_label,
                ),
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
            if outcome.reason is ConfirmationReason.NOT_CONFIRMED:
                logger.info("voice.write_confirmation.audit %s", decision_audit.to_dict())
                if interpret_confirmation_reply(content) is ConfirmationReply.AFFIRM:
                    # They did agree -- just not with the exact phrase a strict
                    # action requires. Say so, rather than a bare "Cancelled"
                    # that reads as if they had been ignored.
                    return WriteResolutionResult(
                        spoken=STRICT_PHRASE_REQUIRED_PHRASE,
                        executed=False,
                        audit_events=tuple(events),
                    )
                # Otherwise the speaker moved on. The proposal is abandoned
                # (consumed above, so it can never be confirmed later), but the
                # turn is theirs: a captured "Cancelled." would swallow a real
                # question. Route it normally -- and say, audibly and in the
                # audit trail, that the change was set aside (voice-UX A3).
                events.append(
                    _event(
                        VoiceWriteAuditEventType.CANCELLED,
                        stored.pending.action,
                        thread_id=thread_id,
                        nonce=stored.pending.nonce,
                        reason="abandoned_by_unrelated_turn",
                    )
                )
                return WriteResolutionResult(
                    spoken=status_phrases.SET_ASIDE,
                    executed=False,
                    audit_events=tuple(events),
                    route_normally=True,
                )
            return WriteResolutionResult(
                spoken=outcome.spoken, executed=False, audit_events=tuple(events)
            )
        # Re-authorize at execution time: a grant may have changed since propose.
        if not await self._allows(actor, stored.executable.capability):
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
        outcome = result.resolved_outcome
        succeeded = outcome is VoiceWriteOutcome.SUCCEEDED
        reason = outcome.value if not result.detail else f"{outcome.value}:{result.detail}"
        events.append(
            _event(
                VoiceWriteAuditEventType.EXECUTED
                if succeeded
                else VoiceWriteAuditEventType.EXECUTION_FAILED,
                stored.pending.action,
                thread_id=thread_id,
                nonce=stored.pending.nonce,
                reason=reason[:120],
            )
        )
        return WriteResolutionResult(
            spoken=spoken_for_result(result, stored),
            executed=succeeded,
            audit_events=tuple(events),
            outcome=outcome,
            effect_committed=result.committed,
            receipt_ref=result.receipt_ref,
        )
