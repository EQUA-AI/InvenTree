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
from typing import Any

#: Bumped whenever a spoken confirmation phrase, the confirmation grammar, or the
#: irreversibility policy changes, so an audit record pins the policy it was
#: decided under.
CONFIRMATION_POLICY_VERSION = "voice-write-confirm-v3"


class WriteActionClass(StrEnum):
    """Severity classification of an effect turn already isolated by the router."""

    CONFIRMABLE = "confirmable"
    IRREVERSIBLE = "irreversible"
    BLOCKED_UNKNOWN = "blocked_unknown"


class ConfirmationReply(StrEnum):
    """How a spoken reply to a read-back is interpreted."""

    AFFIRM = "affirm"
    DECLINE = "decline"
    #: Assent or refusal carrying a correction or any other trailing clause
    #: ("yes, but change the quantity to ten", "no, I meant 140"). Never
    #: executes the read-back as proposed; the change must be re-presented.
    AMEND = "amend"
    #: An explicit "not yet" / "wait": neither consent nor refusal.
    DEFER = "defer"
    UNRELATED = "unrelated"


class ConfirmationState(StrEnum):
    """Terminal state of a pending confirmation."""

    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class ConfirmationReason(StrEnum):
    """Why a pending confirmation resolved as it did (audit-safe, bounded)."""

    AFFIRMED = "affirmed"
    DECLINED = "declined"
    AMENDED = "amended"
    DEFERRED = "deferred"
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
# Confirmation grammar (v3: whole-utterance, fail-closed)
#
# A reply confirms only when the WHOLE utterance is an affirmative (reversible)
# or the exact server-authored strict phrase (irreversible), after a documented
# normalization: case, whitespace, trailing punctuation, leading assent fillers
# ("yes, ..."), a leading/trailing courtesy word ("please", "thanks") and one
# tolerated trailing object word ("it", "that", "this", "now"). Anything else
# riding on an assent is NOT consent:
#   * a decline token after the assent ("confirm hold, no wait") -> DECLINE;
#   * a correction/qualification ("yes, but change the quantity to ten",
#     "no, I meant 140", "confirm delete the other one") -> AMEND;
#   * a postponement ("not yet", "confirm delete not yet", "wait") -> DEFER;
#   * any other trailing clause on an assent -> AMEND (mixed assent).
# A decline at the start of the utterance declines unless it carries a
# correction. Everything else is UNRELATED. AMEND/DEFER/UNRELATED never
# authorize execution; the caller decides how to re-present.
# ---------------------------------------------------------------------------
_AFFIRM_TOKENS = frozenset({
    "yes",
    "yeah",
    "yep",
    "yup",
    "sure",
    "sure thing",
    "absolutely",
    "ok",
    "okay",
    "sounds good",
    "affirmative",
    "confirm",
    "confirmed",
    "confirm it",
    "confirm that",
    "confirm this",
    "proceed",
    "go ahead",
    "go for it",
    "do it",
    "execute",
    "execute it",
    "approve",
    "approved",
    "correct",
    "that's right",
    "that is right",
    "that's correct",
    "that is correct",
    "yes please",
    "yes do it",
    "yes confirm",
    "okay do it",
    "ok do it",
})
_ASSENT_PREFIX = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|absolutely|ok(?:ay)?|sounds good|affirmative|"
    r"confirm(?:ed|s)?|proceed|go ahead|go for it|do it|execute(?: it)?|"
    r"approve[d]?|correct|that'?s (?:right|correct)|that is (?:right|correct))\b",
    re.IGNORECASE,
)
_DECLINE_PATTERN = re.compile(
    r"^\s*(?:no\b|nope\b|nah\b|cancel|stop|abort|don'?t\b|do not\b|"
    r"never ?mind|scratch that|forget it)",
    re.IGNORECASE,
)
#: A refusal token anywhere after an assent. "confirm cancel order" is an
#: action phrase, not a refusal, so a token directly after "confirm" is exempt.
_DECLINE_TOKEN = re.compile(
    r"(?<!confirm )\b(?:no|nope|nah|cancel|stop|abort|don'?t|do not|never ?mind|"
    r"scratch that|forget it)\b",
    re.IGNORECASE,
)
#: A refusal as the LAST word wins over an earlier correction word
#: ("yes... actually no" declines; "no, actually make it twenty" amends).
_TRAILING_DECLINE = re.compile(
    r"\b(?:no|nope|nah|cancel|stop|abort|never ?mind|scratch that|forget it)$",
    re.IGNORECASE,
)
_CORRECTION_PATTERN = re.compile(
    r"\b(?:but|instead|actually|i meant|i mean|i said|change|make (?:it|that|them)|"
    r"rather|except|unless|only if|the other|a different|different one|not \d+|"
    r"not the|should be)\b",
    re.IGNORECASE,
)
_DEFER_PATTERN = re.compile(
    r"\b(?:not yet|wait|hold on|hang on|one (?:second|sec|moment|minute)|"
    r"give me a (?:second|sec|moment|minute)|not now|later|stand ?by)\b",
    re.IGNORECASE,
)
_LEADING_ASSENT_FILLER = re.compile(
    r"^(?:\s*(?:yes|yeah|yep|yup|sure|ok|okay)[,.\s]+)+",
    re.IGNORECASE,
)
_LEADING_COURTESY = re.compile(r"^(?:please|kindly)[,\s]+", re.IGNORECASE)
_TRAILING_COURTESY = re.compile(r"[,\s]+(?:please|thanks|thank you)$", re.IGNORECASE)
_TRAILING_OBJECT = re.compile(r"\s+(?:it|that|this|now)$", re.IGNORECASE)
_TRAILING_PUNCT = re.compile(r"[\s.!?,;:]+$")


# ---------------------------------------------------------------------------
# Static spoken allow-list. The action read-back is dynamic (server-authored
# from the proposed action); every other spoken outcome is one of these exact
# strings, mirroring the status_phrases contract.
# ---------------------------------------------------------------------------
CONFIRM_INSTRUCTION = "To confirm, say yes or confirm. To cancel, say cancel."
NOT_AUTHORIZED_PHRASE = "You are not allowed to perform that action."
BLOCKED_UNKNOWN_PHRASE = "I can't make that change by voice."
CANCELLED_PHRASE = "Cancelled. No change was made."
#: Spoken when the reply was clearly an attempt to agree ("yes", "ok") but the
#: action required its exact phrase. Saying only "Cancelled" would read as if the
#: assistant had ignored them.
STRICT_PHRASE_REQUIRED_PHRASE = (
    "That one needs its exact confirmation phrase, so nothing was changed. "
    "Ask again if you still want it."
)
CONFIRMED_PHRASE = "Confirmed."
#: Spoken when the reply carried a correction or a mixed clause: the read-back
#: was NOT applied and the request must be re-presented with the change.
AMEND_PHRASE = "Not applied. Say the full request again with the change, and I'll read it back."
#: Spoken when the reply postponed the decision ("not yet", "wait").
DEFERRED_PHRASE = "Not applied. Ask again when you're ready."
#: Honest result language (voice-UX plan §5.5). Spoken ONLY from recorded
#: outcome state; "nothing was changed" is never claimed without proof.
NOT_APPLIED_PHRASE = "That change was not applied."
NOT_COMPLETED_PHRASE = (
    "That change did not complete. I could not confirm what, if anything, changed."
)
UNKNOWN_RESULT_PHRASE = (
    "I could not verify whether that change completed. Do not repeat it until it has been checked."
)
ACCEPTED_PENDING_PHRASE = "Accepted. Execution is still pending."
PARTIAL_RESULT_PHRASE = "Only part of that change completed. Check the record before repeating it."
DRAFT_PHRASE = "I prepared the change. It has not been applied."
AWAITING_REVIEW_PHRASE = "That request needs review before it runs."

#: The complete allow-list of static confirmation phrases (read-backs excluded).
ALLOWED_CONFIRMATION_PHRASES = frozenset({
    CONFIRM_INSTRUCTION,
    NOT_AUTHORIZED_PHRASE,
    BLOCKED_UNKNOWN_PHRASE,
    CANCELLED_PHRASE,
    CONFIRMED_PHRASE,
    AMEND_PHRASE,
    DEFERRED_PHRASE,
    NOT_APPLIED_PHRASE,
    NOT_COMPLETED_PHRASE,
    UNKNOWN_RESULT_PHRASE,
    ACCEPTED_PENDING_PHRASE,
    PARTIAL_RESULT_PHRASE,
    DRAFT_PHRASE,
    AWAITING_REVIEW_PHRASE,
    STRICT_PHRASE_REQUIRED_PHRASE,
})

# ---------------------------------------------------------------------------
# Templated dynamic speech. The success sentence must name the record and the
# change, which no static allow-list can do; instead the TEMPLATES are fixed
# and every slot is server-derived (record labels, receipts), sanitized and
# bounded. Transcript text never reaches a slot.
# ---------------------------------------------------------------------------
SPOKEN_TEMPLATE_VERSION = "spoken-templates-v1"
SPOKEN_TEMPLATES: dict[str, str] = {
    "succeeded": "{record_label} {change_label}.",
    "completed_summary": "Completed: {summary}.",
}
_SLOT_MAX_CHARS = 120


def _clean_slot(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip().rstrip(".")
    return text[:_SLOT_MAX_CHARS].rstrip()


def assemble_spoken(template_id: str, **slots: Any) -> str:
    """Render one fixed template with sanitized, server-derived slots.

    Raises ``ValueError`` for an unknown template or an empty slot so callers
    choose a fallback deliberately instead of speaking a hole.
    """
    template = SPOKEN_TEMPLATES.get(template_id)
    if template is None:
        raise ValueError(f"unknown spoken template: {template_id}")
    cleaned = {key: _clean_slot(value) for key, value in slots.items()}
    for key in re.findall(r"{(\w+)}", template):
        if not cleaned.get(key):
            raise ValueError(f"empty slot {key!r} for template {template_id!r}")
    return template.format(**cleaned)


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


def _normalize_utterance(text: str) -> str:
    """Lower-case, collapse whitespace, drop trailing punctuation and courtesy words."""
    lowered = re.sub(r"\s+", " ", (text or "").strip().lower())
    lowered = _TRAILING_PUNCT.sub("", lowered)
    lowered = _LEADING_COURTESY.sub("", lowered)
    for _ in range(2):
        lowered = _TRAILING_COURTESY.sub("", lowered)
        lowered = _TRAILING_PUNCT.sub("", lowered)
    return lowered.strip()


def _normalize_strict(text: str) -> str:
    """Normalize and drop leading assent fillers ("yes, confirm delete" -> "confirm delete")."""
    lowered = _normalize_utterance(text)
    lowered = _LEADING_ASSENT_FILLER.sub("", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _without_trailing_object(text: str) -> str:
    """Drop one tolerated trailing object word ("confirm delete it" -> "confirm delete")."""
    return _TRAILING_OBJECT.sub("", text)


def _is_affirm_token(text: str) -> bool:
    return text in _AFFIRM_TOKENS or _without_trailing_object(text) in _AFFIRM_TOKENS


def interpret_confirmation_reply(
    content: str,
    *,
    required_phrase: str | None = None,
) -> ConfirmationReply:
    """Interpret a spoken reply to a read-back. Fail-closed toward not-confirmed.

    Whole-utterance grammar (v3). With ``required_phrase`` (irreversible
    actions) only a reply that IS that exact strict phrase affirms; otherwise a
    bare affirmative affirms. A decline token after an assent declines; a
    correction or any other trailing clause on an assent is ``AMEND``; a
    postponement is ``DEFER``. None of those authorize execution.
    """
    text = _normalize_utterance(content)
    if not text:
        return ConfirmationReply.UNRELATED

    # 1. A reply that starts as a refusal declines -- unless it carries a
    #    correction ("no, I meant 140"), which is an amendment to re-present.
    if _DECLINE_PATTERN.match(text):
        if _CORRECTION_PATTERN.search(text):
            return ConfirmationReply.AMEND
        return ConfirmationReply.DECLINE

    target = _normalize_strict(required_phrase) if required_phrase else ""
    stripped = _normalize_strict(text)
    assent_led = bool(_ASSENT_PREFIX.match(text)) or (bool(target) and stripped.startswith(target))

    if assent_led:
        # 2. Exact whole-utterance match first, so a strict phrase that itself
        #    contains a refusal word ("confirm cancel order") confirms.
        if target:
            if stripped == target or _without_trailing_object(stripped) == target:
                return ConfirmationReply.AFFIRM
            remainder = stripped[len(target) :].strip() if stripped.startswith(target) else stripped
        else:
            if _is_affirm_token(stripped) or _is_affirm_token(text):
                return ConfirmationReply.AFFIRM
            remainder = stripped
        # 3. Assent followed by a refusal ("confirm hold, no wait",
        #    "yes... actually no") -> DECLINE.
        if _DECLINE_TOKEN.search(remainder) and (
            not _CORRECTION_PATTERN.search(remainder) or _TRAILING_DECLINE.search(remainder)
        ):
            return ConfirmationReply.DECLINE
        # 4. Assent carrying a correction or qualification -> AMEND.
        if _CORRECTION_PATTERN.search(remainder):
            return ConfirmationReply.AMEND
        # 5. Assent carrying a postponement ("confirm delete not yet") -> DEFER.
        if _DEFER_PATTERN.search(remainder):
            return ConfirmationReply.DEFER
        # 6. A bare affirmative is not the strict phrase; the caller explains
        #    that the exact phrase is required.
        if target and _is_affirm_token(stripped):
            return ConfirmationReply.UNRELATED
        # 7. Assent plus anything else is a mixed reply: never execute as read.
        return ConfirmationReply.AMEND

    # 8. No assent: a leading postponement defers, a leading correction amends.
    if _DEFER_PATTERN.match(text):
        return ConfirmationReply.DEFER
    if _CORRECTION_PATTERN.match(text):
        return ConfirmationReply.AMEND
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

    if reply is ConfirmationReply.AMEND:
        reason, spoken = ConfirmationReason.AMENDED, AMEND_PHRASE
    elif reply is ConfirmationReply.DEFER:
        reason, spoken = ConfirmationReason.DEFERRED, DEFERRED_PHRASE
    elif reply is ConfirmationReply.DECLINE:
        reason, spoken = ConfirmationReason.DECLINED, CANCELLED_PHRASE
    else:
        reason, spoken = ConfirmationReason.NOT_CONFIRMED, CANCELLED_PHRASE
    outcome = ConfirmationOutcome(
        state=ConfirmationState.CANCELLED,
        reason=reason,
        spoken=spoken,
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
