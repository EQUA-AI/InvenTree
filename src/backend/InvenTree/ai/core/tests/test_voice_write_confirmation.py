"""Phase 4: deterministic voice write-confirmation gate.

Covers the pure core -- irreversibility classification, the RBAC-before-confirm
ordering, the confirmation grammar (lenient for reversible, strict phrase for
irreversible), the propose/resolve state machine, and the bounded audit record.
No Django, no network: this is the modality-neutral decision layer that sits in
front of the centralized RBAC write tools; execution wiring is tested separately.
"""

from __future__ import annotations

import dataclasses

import pytest
from ai.core.voice.confirmation import (
    ALLOWED_CONFIRMATION_PHRASES,
    BLOCKED_UNKNOWN_PHRASE,
    CANCELLED_PHRASE,
    CONFIRM_INSTRUCTION,
    CONFIRMATION_POLICY_VERSION,
    CONFIRMED_PHRASE,
    NOT_AUTHORIZED_PHRASE,
    ConfirmationReason,
    ConfirmationReply,
    ConfirmationState,
    PendingVoiceConfirmation,
    ProposedWriteAction,
    VoiceWriteAuditEventType,
    WriteActionClass,
    classify_write_intent,
    interpret_confirmation_reply,
    propose,
    resolve,
)


# --------------------------------------------------------------------------- #
# classify_write_intent                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "content",
    (
        "Delete the kanban card",
        "Please remove work order 42",
        "Purge the old records",
        "destroy that packet",
        "wipe the readings",
        "erase the note",
        "Cancel and delete it permanently",
    ),
)
def test_destructive_wording_is_irreversible(content) -> None:
    """Destructive verbs raise the confirmation bar, regardless of the effect flag."""
    assert classify_write_intent(content, effect_intent=True) is (WriteActionClass.IRREVERSIBLE)
    assert classify_write_intent(content, effect_intent=False) is (WriteActionClass.IRREVERSIBLE)


@pytest.mark.parametrize(
    "content",
    (
        "Create a repair task for the pump",
        "Update the stock count to twelve",
        "Place a purchase order for ten bearings",
        "Hold work order WO-42",
        "Send an email to the supervisor",
    ),
)
def test_reversible_effect_is_confirmable(content) -> None:
    """A reversible write the router flagged as an effect is confirmable."""
    assert classify_write_intent(content, effect_intent=True) is (WriteActionClass.CONFIRMABLE)


def test_non_effect_is_blocked_unknown() -> None:
    """Without an effect decision the gate fails closed, never confirmable."""
    assert classify_write_intent("Show repair order 42", effect_intent=False) is (
        WriteActionClass.BLOCKED_UNKNOWN
    )


def test_empty_content_is_safe() -> None:
    assert classify_write_intent("", effect_intent=False) is (WriteActionClass.BLOCKED_UNKNOWN)


# --------------------------------------------------------------------------- #
# interpret_confirmation_reply -- reversible (lenient)                         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "reply",
    (
        "yes",
        "yeah",
        "yep",
        "sure",
        "okay",
        "ok",
        "sounds good",
        "confirm",
        "proceed",
        "go ahead",
        "do it",
    ),
)
def test_reversible_accepts_bare_and_explicit_affirmatives(reply) -> None:
    """Per the signed-off contract, a bare 'yes' confirms a reversible write."""
    assert interpret_confirmation_reply(reply) is ConfirmationReply.AFFIRM


@pytest.mark.parametrize(
    "reply",
    ("no", "nope", "cancel", "stop", "abort", "don't", "never mind", "forget it"),
)
def test_declines(reply) -> None:
    assert interpret_confirmation_reply(reply) is ConfirmationReply.DECLINE


def test_new_request_reply_is_unrelated() -> None:
    assert interpret_confirmation_reply("what's the stock level") is (ConfirmationReply.UNRELATED)


# --------------------------------------------------------------------------- #
# interpret_confirmation_reply -- irreversible (strict phrase)                 #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "reply",
    (
        "confirm delete",
        "Confirm delete.",
        "yes, confirm delete",
        "okay, confirm delete it",
    ),
)
def test_strict_phrase_confirms_irreversible(reply) -> None:
    assert interpret_confirmation_reply(reply, required_phrase="confirm delete") is (
        ConfirmationReply.AFFIRM
    )


@pytest.mark.parametrize(
    "reply",
    (
        "yes",
        "yeah",
        "confirm",  # a plain confirm is NOT enough for a destructive action
        "ok",
        "delete",
    ),
)
def test_bare_assent_does_not_confirm_irreversible(reply) -> None:
    assert interpret_confirmation_reply(reply, required_phrase="confirm delete") is (
        ConfirmationReply.UNRELATED
    )


def test_decline_still_works_under_strict_phrase() -> None:
    assert interpret_confirmation_reply("cancel", required_phrase="confirm delete") is (
        ConfirmationReply.DECLINE
    )


# --------------------------------------------------------------------------- #
# propose -- RBAC gate + read-backs                                           #
# --------------------------------------------------------------------------- #
def _confirmable() -> ProposedWriteAction:
    return ProposedWriteAction(
        capability="inventory.write",
        summary="Place a purchase order for 10 bearings",
    )


def _irreversible() -> ProposedWriteAction:
    return ProposedWriteAction(
        capability="workorder.delete",
        summary="Delete work order 42",
        action_class=WriteActionClass.IRREVERSIBLE,
        confirm_phrase="confirm delete",
    )


def test_propose_confirmable_reads_back_and_creates_pending() -> None:
    pending, spoken, event = propose(_confirmable(), thread_id=7, nonce="n1", has_permission=True)

    assert isinstance(pending, PendingVoiceConfirmation)
    assert pending.nonce == "n1"
    assert spoken == "Place a purchase order for 10 bearings " + CONFIRM_INSTRUCTION
    assert event.event is VoiceWriteAuditEventType.PROPOSED


def test_propose_without_permission_refuses_before_confirmation() -> None:
    """RBAC precedes confirmation: no permission -> refused, no pending, no read-back."""
    pending, spoken, event = propose(_confirmable(), thread_id=7, nonce="n1", has_permission=False)

    assert pending is None
    assert spoken == NOT_AUTHORIZED_PHRASE
    assert event.event is VoiceWriteAuditEventType.NOT_AUTHORIZED


def test_propose_irreversible_with_permission_uses_strict_readback() -> None:
    pending, spoken, event = propose(_irreversible(), thread_id=7, nonce="n1", has_permission=True)

    assert isinstance(pending, PendingVoiceConfirmation)
    assert "This cannot be undone." in spoken
    assert "confirm delete" in spoken
    assert event.event is VoiceWriteAuditEventType.PROPOSED


def test_propose_irreversible_without_permission_is_refused() -> None:
    """A destructive action the actor may not perform never reaches confirmation."""
    pending, spoken, event = propose(_irreversible(), thread_id=7, nonce="n1", has_permission=False)

    assert pending is None
    assert spoken == NOT_AUTHORIZED_PHRASE
    assert event.event is VoiceWriteAuditEventType.NOT_AUTHORIZED


def test_propose_irreversible_without_phrase_fails_closed() -> None:
    action = ProposedWriteAction(
        capability="workorder.delete",
        summary="Delete work order 42",
        action_class=WriteActionClass.IRREVERSIBLE,
    )
    pending, spoken, event = propose(action, thread_id=7, nonce="n1", has_permission=True)

    assert pending is None
    assert spoken == BLOCKED_UNKNOWN_PHRASE
    assert event.event is VoiceWriteAuditEventType.BLOCKED


def test_propose_unknown_yields_no_pending() -> None:
    action = ProposedWriteAction(
        capability="",
        summary="",
        action_class=WriteActionClass.BLOCKED_UNKNOWN,
    )
    pending, spoken, event = propose(action, thread_id=7, nonce="n1", has_permission=True)

    assert pending is None
    assert spoken == BLOCKED_UNKNOWN_PHRASE
    assert event.event is VoiceWriteAuditEventType.BLOCKED


# --------------------------------------------------------------------------- #
# resolve                                                                     #
# --------------------------------------------------------------------------- #
def _pending_confirmable() -> PendingVoiceConfirmation:
    return PendingVoiceConfirmation(nonce="n1", thread_id=7, action=_confirmable())


def _pending_irreversible() -> PendingVoiceConfirmation:
    return PendingVoiceConfirmation(nonce="n1", thread_id=7, action=_irreversible())


def test_resolve_bare_yes_confirms_reversible() -> None:
    outcome, event = resolve(_pending_confirmable(), "yes")

    assert outcome.confirmed is True
    assert outcome.reason is ConfirmationReason.AFFIRMED
    assert outcome.spoken == CONFIRMED_PHRASE
    # Confirmation never widens authority: the capability carries through for the
    # caller's RBAC-gated execution.
    assert outcome.capability == "inventory.write"
    assert event.event is VoiceWriteAuditEventType.CONFIRMED


def test_resolve_decline_cancels() -> None:
    outcome, event = resolve(_pending_confirmable(), "cancel")

    assert outcome.state is ConfirmationState.CANCELLED
    assert outcome.reason is ConfirmationReason.DECLINED
    assert outcome.spoken == CANCELLED_PHRASE
    assert event.event is VoiceWriteAuditEventType.CANCELLED


def test_resolve_irreversible_requires_strict_phrase() -> None:
    # A bare yes must NOT execute a destructive action.
    weak, _ = resolve(_pending_irreversible(), "yes")
    assert weak.confirmed is False
    assert weak.reason is ConfirmationReason.NOT_CONFIRMED

    strong, event = resolve(_pending_irreversible(), "confirm delete")
    assert strong.confirmed is True
    assert event.event is VoiceWriteAuditEventType.CONFIRMED


def test_resolve_new_request_cancels_pending() -> None:
    outcome, _event = resolve(_pending_confirmable(), "what's the stock level")

    assert outcome.state is ConfirmationState.CANCELLED
    assert outcome.reason is ConfirmationReason.NOT_CONFIRMED


# --------------------------------------------------------------------------- #
# invariants                                                                  #
# --------------------------------------------------------------------------- #
def test_all_static_outcomes_are_allow_listed() -> None:
    """Every spoken outcome except the dynamic read-back is on the allow-list."""
    for phrase in (
        CONFIRM_INSTRUCTION,
        NOT_AUTHORIZED_PHRASE,
        BLOCKED_UNKNOWN_PHRASE,
        CANCELLED_PHRASE,
        CONFIRMED_PHRASE,
    ):
        assert phrase in ALLOWED_CONFIRMATION_PHRASES


def test_records_are_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _pending_confirmable().nonce = "n2"  # type: ignore[misc]
    outcome, _ = resolve(_pending_confirmable(), "yes")
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.state = ConfirmationState.CANCELLED  # type: ignore[misc]


def test_audit_record_is_bounded_and_json_safe() -> None:
    _pending, _spoken, event = propose(_confirmable(), thread_id=7, nonce="n1", has_permission=True)
    data = event.to_dict()

    # Only bounded, non-transcript fields -- no args, no reasoning, no speech.
    assert set(data) == {
        "event",
        "thread_id",
        "capability",
        "summary",
        "action_class",
        "nonce",
        "reason",
        "policy_version",
    }
    assert data["policy_version"] == CONFIRMATION_POLICY_VERSION
    assert data["action_class"] == WriteActionClass.CONFIRMABLE.value


# --------------------------------------------------------------------------- #
# Grammar v3 (voice-UX plan A1): whole-utterance, mixed replies never execute  #
# --------------------------------------------------------------------------- #
from ai.core.voice.confirmation import AMEND_PHRASE, DEFERRED_PHRASE  # noqa: E402


def test_policy_version_is_v3() -> None:
    assert CONFIRMATION_POLICY_VERSION == "voice-write-confirm-v3"


@pytest.mark.parametrize(
    ("reply", "required", "expected"),
    (
        # qualified assent / corrections -> AMEND
        ("yes, but change the quantity to ten", None, ConfirmationReply.AMEND),
        ("no, I meant 140", None, ConfirmationReply.AMEND),
        ("confirm delete the other one", "confirm delete", ConfirmationReply.AMEND),
        ("yes and also order belts", None, ConfirmationReply.AMEND),
        ("change it to ten", None, ConfirmationReply.AMEND),
        ("actually make it twenty", None, ConfirmationReply.AMEND),
        ("confirm delete everything", "confirm delete", ConfirmationReply.AMEND),
        # a refusal after the assent -> DECLINE
        ("confirm hold, no wait", None, ConfirmationReply.DECLINE),
        ("yes... actually no", None, ConfirmationReply.DECLINE),
        ("don't do it", None, ConfirmationReply.DECLINE),
        ("cancel that", None, ConfirmationReply.DECLINE),
        ("confirm delete, cancel", "confirm delete", ConfirmationReply.DECLINE),
        # strict phrases that contain a refusal word still confirm themselves
        ("confirm cancel order", "confirm cancel order", ConfirmationReply.AFFIRM),
        ("yes, confirm cancel order please", "confirm cancel order", ConfirmationReply.AFFIRM),
        ("confirm cancel order, no wait", "confirm cancel order", ConfirmationReply.DECLINE),
        ("confirm cancel order and reorder", "confirm cancel order", ConfirmationReply.AMEND),
        # postponement -> DEFER
        ("not yet", None, ConfirmationReply.DEFER),
        ("wait", "confirm delete", ConfirmationReply.DEFER),
        ("confirm delete not yet", "confirm delete", ConfirmationReply.DEFER),
        ("yes, hold on", None, ConfirmationReply.DEFER),
        # the assistant's own instruction echoed back is not consent
        ("to confirm say confirm hold", None, ConfirmationReply.UNRELATED),
        ("to confirm, say confirm delete", "confirm delete", ConfirmationReply.UNRELATED),
        # documented tolerated tails still confirm
        ("yes please.", None, ConfirmationReply.AFFIRM),
        ("sure thing", None, ConfirmationReply.AFFIRM),
        ("go ahead, thanks", None, ConfirmationReply.AFFIRM),
        ("please confirm delete", "confirm delete", ConfirmationReply.AFFIRM),
        ("confirm hold please", "confirm hold", ConfirmationReply.AFFIRM),
        ("Confirm hold.", "confirm hold", ConfirmationReply.AFFIRM),
        ("okay, confirm delete it", "confirm delete", ConfirmationReply.AFFIRM),
    ),
)
def test_grammar_v3_classification(reply, required, expected) -> None:
    assert interpret_confirmation_reply(reply, required_phrase=required) is expected


def test_prefix_match_no_longer_confirms_a_strict_phrase() -> None:
    """v2 accepted any utterance that STARTED with the phrase; v3 requires the whole utterance."""
    assert (
        interpret_confirmation_reply(
            "confirm delete and then reorder", required_phrase="confirm delete"
        )
        is not ConfirmationReply.AFFIRM
    )


def test_leading_affirm_no_longer_confirms_a_reversible_write() -> None:
    """v2 matched 'yes' at the start; v3 treats the trailing clause as an amendment."""
    assert interpret_confirmation_reply("yes, but only five") is ConfirmationReply.AMEND


def test_resolve_amend_cancels_with_amended_reason_and_phrase() -> None:
    pending = _pending_confirmable()
    outcome, event = resolve(pending, "yes, but change the quantity to ten")
    assert outcome.state is ConfirmationState.CANCELLED
    assert outcome.confirmed is False
    assert outcome.reason is ConfirmationReason.AMENDED
    assert outcome.spoken == AMEND_PHRASE
    assert event.event is VoiceWriteAuditEventType.CANCELLED
    assert event.reason == "amended"


def test_resolve_defer_cancels_with_deferred_reason_and_phrase() -> None:
    pending = _pending_confirmable()
    outcome, event = resolve(pending, "not yet")
    assert outcome.state is ConfirmationState.CANCELLED
    assert outcome.reason is ConfirmationReason.DEFERRED
    assert outcome.spoken == DEFERRED_PHRASE
    assert event.reason == "deferred"


def test_new_phrases_are_allow_listed() -> None:
    assert AMEND_PHRASE in ALLOWED_CONFIRMATION_PHRASES
    assert DEFERRED_PHRASE in ALLOWED_CONFIRMATION_PHRASES


# --------------------------------------------------------------------------- #
# Templated dynamic speech (voice-UX plan A2)                                  #
# --------------------------------------------------------------------------- #
from ai.core.voice.confirmation import (  # noqa: E402
    NOT_APPLIED_PHRASE,
    NOT_COMPLETED_PHRASE,
    UNKNOWN_RESULT_PHRASE,
    assemble_spoken,
)


def test_assemble_spoken_renders_fixed_templates_with_clean_slots() -> None:
    assert (
        assemble_spoken(
            "succeeded", record_label="  Work order  140 ", change_label="is now on hold."
        )
        == "Work order 140 is now on hold."
    )
    assert assemble_spoken("completed_summary", summary="Add stock with quantity 10") == (
        "Completed: Add stock with quantity 10."
    )


def test_assemble_spoken_bounds_slots_and_flattens_whitespace() -> None:
    spoken = assemble_spoken("completed_summary", summary="x" * 500 + "\n\nsecond line")
    assert len(spoken) <= len("Completed: .") + 120
    assert "\n" not in spoken


@pytest.mark.parametrize(
    ("template", "slots"),
    (
        ("succeeded", {"record_label": "", "change_label": "is now on hold"}),
        ("succeeded", {"record_label": "WO-1"}),
        ("no_such_template", {"summary": "x"}),
    ),
)
def test_assemble_spoken_refuses_holes_and_unknown_templates(template, slots) -> None:
    with pytest.raises(ValueError):
        assemble_spoken(template, **slots)


def test_honest_result_phrases_are_allow_listed_and_never_claim_nothing_changed() -> None:
    for phrase in (NOT_APPLIED_PHRASE, NOT_COMPLETED_PHRASE, UNKNOWN_RESULT_PHRASE):
        assert phrase in ALLOWED_CONFIRMATION_PHRASES
    assert "Nothing was changed" not in NOT_COMPLETED_PHRASE
    assert "Nothing was changed" not in UNKNOWN_RESULT_PHRASE
