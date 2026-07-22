"""Voice write-confirmation gate (Phase 4, Tier-3 writes).

Voice is structurally read-only by default (see ``ai.core.tools.read_only``): the
whole voice run executes under a fence that fails every write tool closed, and
effect-shaped wording is isolated by the router as advisory intent. This module
is the deterministic core of the *opt-in* write path enabled only by the
off-by-default ``feature_voice_write_confirmation`` flag. It decides, and it
supplies the exact words to speak; it never executes a write and never relaxes
the read-only fence -- when a confirmation succeeds, execution runs through the
same centralized RBAC-gated write tools the text surface uses, and passes the
same capability check.

RBAC precedes confirmation for every write. The caller resolves whether the
actor holds the action's capability and passes it in as ``has_permission``; an
actor without the permission is told they are not allowed and is never offered a
confirmation -- the confirmation gate is layered on top of authorization, never
a substitute for it.

Three action classes, decided from an effect turn the router already isolated:

* ``CONFIRMABLE`` -- a reversible write, allowed after a single verbal
  confirmation (a bare "yes" is accepted);
* ``IRREVERSIBLE`` -- a destructive write (delete/remove/purge/...), allowed only
  after an RBAC check AND a stricter confirmation: the actor must repeat the
  exact server-authored strict phrase (e.g. "confirm delete"); a bare "yes" or a
  plain "confirm" is not enough;
* ``BLOCKED_UNKNOWN`` -- the fail-closed default for anything that does not
  positively read as a recognized effect; never executable.

Effect *detection* lives solely in ``ai.core.agents.voice_routing`` and is not
duplicated here; the caller passes the already-decided ``effect_intent``. This
module adds only the orthogonal irreversibility policy, the confirmation
grammar, the RBAC gate ordering, and the audit record. Static spoken outcomes
come from a versioned allow-list mirroring ``ai.core.voice.status_phrases``; the
action read-back is the only non-static spoken text and is always server-authored
from the proposed action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

#: Bumped whenever a spoken confirmation phrase, the confirmation grammar, or the
#: irreversibility policy changes, so an audit record pins the policy it was
#: decided under.
CONFIRMATION_POLICY_VERSION = "voice-write-confirm-v2"


class WriteActionClass(StrEnum):
    """Severity classification of an effect turn already isolated by the router."""

    CONFIRMABLE = "confirmable"
    IRREVERSIBLE = "irreversible"
    BLOCKED_UNKNOWN = "blocked_unknown"


class ConfirmationReply(StrEnum):
    """How a spoken reply to a read-back is interpreted."""

    AFFIRM = "affirm"
    DECLINE = "decline"
    UNRELATED = "unrelated"


class ConfirmationState(StrEnum):
    """Terminal state of a pending confirmation."""

    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class ConfirmationReason(StrEnum):
    """Why a pending confirmation resolved as it did (audit-safe, bounded)."""

    AFFIRMED = "affirmed"
    DECLINED = "declined"
    NOT_CONFIRMED = "not_confirmed"


class VoiceWriteAuditEventType(StrEnum):
    """The auditable moments in a voice write's life."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    NOT_AUTHORIZED = "not_authorized"
    EXECUTED = "executed"
    EXECUTION_FAILED = "execution_failed"


# Audit ``reason`` strings emitted by ``propose`` for the non-lifecycle outcomes.
_REASON_NOT_AUTHORIZED = "not_authorized"
_REASON_BLOCKED_UNKNOWN = "blocked_unknown"
_REASON_MISSING_CONFIRM_PHRASE = "missing_confirm_phrase"


# ---------------------------------------------------------------------------
# Irreversibility policy
#
# A conservative, fail-closed subset of the router's effect verbs. When unsure,
# voice treats a write as irreversible (higher bar): over-guarding a reversible
# action is a mild annoyance; under-guarding a destructive one is not.
# Deployments may EXTEND this set; they must not shrink it without a safety
# review. "Irreversible" is a distinct concern from "is an effect" (which the
# router owns) -- this is an orthogonal severity gate, not a copy of the effect
# taxonomy.
# ---------------------------------------------------------------------------
_IRREVERSIBLE_PATTERN = re.compile(
    r"\b(?:delete|deleting|deleted|remove|removing|removed|purge|purging|purged|"
    r"destroy|destroying|destroyed|wipe|wiping|wiped|erase|erasing|erased|"
    r"permanently)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Confirmation grammar
#
# Reversible (lenient): a bare "yes"/"yeah"/"okay" confirms, as do explicit
# tokens. Irreversible (strict): the reply must repeat the exact server-authored
# strict phrase (leading assent fillers like "yes," are tolerated, but the
# strict phrase itself must be present); a bare "yes" or plain "confirm" does
# NOT confirm a destructive action. DECLINE matches explicit cancellation for
# both. Everything else is UNRELATED and, like a decline, abandons the pending
# confirmation (fail-closed).
# ---------------------------------------------------------------------------
_LENIENT_AFFIRM_PATTERN = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|sounds good|affirmative|"
    r"confirm(?:ed|s)?|proceed|go ahead|do it|execute(?: it)?|approve[d]?)\b",
    re.IGNORECASE,
)
_DECLINE_PATTERN = re.compile(
    r"^\s*(?:no\b|nope\b|nah\b|cancel|stop|abort|don'?t\b|do not\b|"
    r"never ?mind|scratch that|forget it)",
    re.IGNORECASE,
)
_LEADING_ASSENT_FILLER = re.compile(
    r"^(?:\s*(?:yes|yeah|yep|yup|sure|ok|okay)[,.\s]+)+",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Static spoken allow-list. The action read-back is dynamic (server-authored
# from the proposed action); every other spoken outcome is one of these exact
# strings, mirroring the status_phrases contract.
# ---------------------------------------------------------------------------
CONFIRM_INSTRUCTION = "To confirm, say yes or confirm. To cancel, say cancel."
NOT_AUTHORIZED_PHRASE = "You are not allowed to perform that action."
BLOCKED_UNKNOWN_PHRASE = "I can't make that change by voice."
CANCELLED_PHRASE = "Cancelled. No change was made."
CONFIRMED_PHRASE = "Confirmed."
DONE_PHRASE = "Done."
EXECUTION_FAILED_PHRASE = "Sorry, that change did not go through. Nothing was changed."

#: The complete allow-list of static confirmation phrases (read-backs excluded).
ALLOWED_CONFIRMATION_PHRASES = frozenset({
    CONFIRM_INSTRUCTION,
    NOT_AUTHORIZED_PHRASE,
    BLOCKED_UNKNOWN_PHRASE,
    CANCELLED_PHRASE,
    CONFIRMED_PHRASE,
    DONE_PHRASE,
    EXECUTION_FAILED_PHRASE,
})


@dataclass(frozen=True, slots=True)
class ProposedWriteAction:
    """A write the agent proposes but has not executed.

    ``capability`` is the RBAC capability the write requires; the confirmation
    gate never widens it -- execution still passes the same capability check the
    text surface applies. ``summary`` is the exact human read-back spoken to the
    actor, server-authored from the resolved action. ``confirm_phrase`` is the
    exact strict phrase an irreversible action requires (e.g. "confirm delete");
    it is unused for reversible actions and must be non-empty for an irreversible
    one, or the proposal fails closed.
    """

    capability: str
    summary: str
    action_class: WriteActionClass = WriteActionClass.CONFIRMABLE
    confirm_phrase: str = ""


@dataclass(frozen=True, slots=True)
class PendingVoiceConfirmation:
    """A write awaiting an explicit verbal confirmation turn.

    Bound to one thread and one opaque ``nonce`` so a confirmation cannot be
    replayed onto, or confused with, a different proposal. The immediately
    following turn is the only turn allowed to confirm it; enforcing that
    one-turn window is the caller's responsibility (this record carries no
    clock).
    """

    nonce: str
    thread_id: int
    action: ProposedWriteAction


@dataclass(frozen=True, slots=True)
class ConfirmationOutcome:
    """Resolution of a pending confirmation."""

    state: ConfirmationState
    reason: ConfirmationReason
    spoken: str
    capability: str
    summary: str

    @property
    def confirmed(self) -> bool:
        """Whether the caller should now execute the write (via RBAC tools)."""
        return self.state is ConfirmationState.CONFIRMED


@dataclass(frozen=True, slots=True)
class VoiceWriteAuditEvent:
    """A bounded, log-safe record of one moment in a voice write's life.

    Carries no transcript, tool arguments, or reasoning -- only the capability,
    the server-authored summary, the class, and the outcome, so the audit trail
    never leaks free speech or hidden object detail.
    """

    event: VoiceWriteAuditEventType
    thread_id: int
    capability: str
    summary: str
    action_class: WriteActionClass
    nonce: str = ""
    reason: str = ""
    policy_version: str = CONFIRMATION_POLICY_VERSION

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe mapping for the audit log."""
        return {
            "event": self.event.value,
            "thread_id": self.thread_id,
            "capability": self.capability,
            "summary": self.summary,
            "action_class": self.action_class.value,
            "nonce": self.nonce,
            "reason": self.reason,
            "policy_version": self.policy_version,
        }


def classify_write_intent(content: str, *, effect_intent: bool) -> WriteActionClass:
    """Classify an effect turn already isolated by the router.

    ``effect_intent`` is the router's decision (``RouteReason.EFFECT_INTENT``);
    effect *detection* is not re-implemented here. Destructive wording is
    classified irreversible (a higher confirmation bar) regardless of the effect
    flag; any other recognized effect is confirmable; if the router did not read
    an effect, the result is the fail-closed ``BLOCKED_UNKNOWN``.
    """
    if _IRREVERSIBLE_PATTERN.search(content or ""):
        return WriteActionClass.IRREVERSIBLE
    if effect_intent:
        return WriteActionClass.CONFIRMABLE
    return WriteActionClass.BLOCKED_UNKNOWN


def _normalize_strict(text: str) -> str:
    """Lower-case, drop leading assent fillers, and collapse whitespace."""
    lowered = text.strip().lower()
    lowered = _LEADING_ASSENT_FILLER.sub("", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def interpret_confirmation_reply(
    content: str,
    *,
    required_phrase: str | None = None,
) -> ConfirmationReply:
    """Interpret a spoken reply to a read-back. Fail-closed toward not-confirmed.

    When ``required_phrase`` is given (irreversible actions), only a reply that
    repeats that exact strict phrase affirms; a bare assent does not. Otherwise
    (reversible actions) a bare "yes" affirms, as do explicit confirm tokens.
    """
    text = content or ""
    if required_phrase:
        target = _normalize_strict(required_phrase)
        if target and _normalize_strict(text).startswith(target):
            return ConfirmationReply.AFFIRM
        if _DECLINE_PATTERN.match(text):
            return ConfirmationReply.DECLINE
        return ConfirmationReply.UNRELATED
    if _LENIENT_AFFIRM_PATTERN.match(text):
        return ConfirmationReply.AFFIRM
    if _DECLINE_PATTERN.match(text):
        return ConfirmationReply.DECLINE
    return ConfirmationReply.UNRELATED


def _audit(
    event: VoiceWriteAuditEventType,
    action: ProposedWriteAction,
    *,
    thread_id: int,
    nonce: str = "",
    reason: str = "",
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


def propose(
    action: ProposedWriteAction,
    *,
    thread_id: int,
    nonce: str,
    has_permission: bool,
) -> tuple[PendingVoiceConfirmation | None, str, VoiceWriteAuditEvent]:
    """Prepare the read-back for a proposed write, RBAC-gated.

    ``has_permission`` is the caller's RBAC decision for ``action.capability``.
    Order of checks: unrecognized effect fails closed; then RBAC -- an actor
    without the permission is refused before any confirmation is offered; then
    an irreversible action gets a stricter read-back, a reversible one a lenient
    read-back. Returns the pending confirmation (``None`` for every refusal -- a
    refused action yields no record and can never be confirmed into execution),
    the exact spoken text, and the audit event to record.
    """
    if action.action_class is WriteActionClass.BLOCKED_UNKNOWN:
        return (
            None,
            BLOCKED_UNKNOWN_PHRASE,
            _audit(
                VoiceWriteAuditEventType.BLOCKED,
                action,
                thread_id=thread_id,
                reason=_REASON_BLOCKED_UNKNOWN,
            ),
        )
    if not has_permission:
        # RBAC before confirmation: never read back an action the actor may not
        # perform. Applies to reversible and irreversible writes alike.
        return (
            None,
            NOT_AUTHORIZED_PHRASE,
            _audit(
                VoiceWriteAuditEventType.NOT_AUTHORIZED,
                action,
                thread_id=thread_id,
                reason=_REASON_NOT_AUTHORIZED,
            ),
        )
    if action.action_class is WriteActionClass.IRREVERSIBLE:
        if not action.confirm_phrase.strip():
            # Cannot safely offer a strict confirmation without an exact phrase.
            return (
                None,
                BLOCKED_UNKNOWN_PHRASE,
                _audit(
                    VoiceWriteAuditEventType.BLOCKED,
                    action,
                    thread_id=thread_id,
                    reason=_REASON_MISSING_CONFIRM_PHRASE,
                ),
            )
        pending = PendingVoiceConfirmation(nonce=nonce, thread_id=thread_id, action=action)
        spoken = (
            f"{action.summary} This cannot be undone. To confirm, say "
            f"{action.confirm_phrase}. To cancel, say cancel."
        )
        return (
            pending,
            spoken,
            _audit(
                VoiceWriteAuditEventType.PROPOSED,
                action,
                thread_id=thread_id,
                nonce=nonce,
            ),
        )
    pending = PendingVoiceConfirmation(nonce=nonce, thread_id=thread_id, action=action)
    spoken = f"{action.summary} {CONFIRM_INSTRUCTION}"
    return (
        pending,
        spoken,
        _audit(
            VoiceWriteAuditEventType.PROPOSED,
            action,
            thread_id=thread_id,
            nonce=nonce,
        ),
    )


def resolve(
    pending: PendingVoiceConfirmation,
    reply_content: str,
) -> tuple[ConfirmationOutcome, VoiceWriteAuditEvent]:
    """Resolve a pending confirmation against the next spoken turn.

    Returns the outcome and the audit event. A ``CONFIRMED`` outcome authorizes
    the caller to execute ``pending.action`` through the centralized RBAC-gated
    write tools -- it does not itself perform any effect. An irreversible action
    requires its exact strict phrase; a reversible action accepts a bare "yes".
    """
    action = pending.action
    required = (
        action.confirm_phrase if action.action_class is WriteActionClass.IRREVERSIBLE else None
    )
    reply = interpret_confirmation_reply(reply_content, required_phrase=required)
    if reply is ConfirmationReply.AFFIRM:
        outcome = ConfirmationOutcome(
            state=ConfirmationState.CONFIRMED,
            reason=ConfirmationReason.AFFIRMED,
            spoken=CONFIRMED_PHRASE,
            capability=action.capability,
            summary=action.summary,
        )
        event = _audit(
            VoiceWriteAuditEventType.CONFIRMED,
            action,
            thread_id=pending.thread_id,
            nonce=pending.nonce,
            reason=ConfirmationReason.AFFIRMED.value,
        )
        return outcome, event

    reason = (
        ConfirmationReason.DECLINED
        if reply is ConfirmationReply.DECLINE
        else ConfirmationReason.NOT_CONFIRMED
    )
    outcome = ConfirmationOutcome(
        state=ConfirmationState.CANCELLED,
        reason=reason,
        spoken=CANCELLED_PHRASE,
        capability=action.capability,
        summary=action.summary,
    )
    event = _audit(
        VoiceWriteAuditEventType.CANCELLED,
        action,
        thread_id=pending.thread_id,
        nonce=pending.nonce,
        reason=reason.value,
    )
    return outcome, event
