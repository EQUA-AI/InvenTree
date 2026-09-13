"""B1: whole-utterance grammar for the shared decision coordinator."""

import pytest
from ai.core.decisions.grammar import (
    DECISION_GRAMMAR_POLICY_VERSION,
    DecisionUtteranceKind,
    classify_decision_utterance,
)


@pytest.mark.parametrize(
    ("reply", "required", "expected"),
    [
        ("yes", None, DecisionUtteranceKind.AFFIRM),
        ("yes please.", None, DecisionUtteranceKind.AFFIRM),
        ("no", None, DecisionUtteranceKind.DECLINE),
        ("yes, but change the quantity to ten", None, DecisionUtteranceKind.AMEND),
        ("no, I meant 140", None, DecisionUtteranceKind.AMEND),
        ("confirm hold, no wait", "confirm hold", DecisionUtteranceKind.DECLINE),
        ("confirm delete not yet", "confirm delete", DecisionUtteranceKind.DEFER),
        ("confirm delete", "confirm delete", DecisionUtteranceKind.AFFIRM),
        ("confirm delete the other one", "confirm delete", DecisionUtteranceKind.AMEND),
        ("to confirm say confirm hold", "confirm hold", DecisionUtteranceKind.UNRELATED),
    ],
)
def test_confirmation_v3_is_the_base_grammar(reply, required, expected):
    allowed = (required,) if expected is DecisionUtteranceKind.AFFIRM and required else ("yes",)
    assert (
        classify_decision_utterance(reply, required_phrase=required, allowed_responses=allowed)
        is expected
    )


@pytest.mark.parametrize(
    "reply",
    ["why", "repeat", "Repeat that.", "please read it back", "what am I confirming?"],
)
def test_review_commands_are_non_consuming(reply):
    result = classify_decision_utterance(reply)
    assert result is DecisionUtteranceKind.REVIEW
    assert result.non_consuming is True
    assert result.refreshes_lifetime is True


def test_auditory_section_read_retains_without_refreshing_lifetime():
    result = classify_decision_utterance("read more")

    assert result is DecisionUtteranceKind.REVIEW_SECTION
    assert result.non_consuming is True
    assert result.refreshes_lifetime is False


@pytest.mark.parametrize("reply", ["what changed?", "what happened to my last action"])
def test_history_commands_are_non_consuming(reply):
    result = classify_decision_utterance(reply)
    assert result is DecisionUtteranceKind.HISTORY
    assert result.non_consuming is True
    assert result.refreshes_lifetime is True


def test_cancel_that_disarms_but_cancel_the_action_rejects_source():
    assert classify_decision_utterance("cancel that") is DecisionUtteranceKind.DISARM
    assert (
        classify_decision_utterance("cancel the action", allowed_responses=("cancel the action",))
        is DecisionUtteranceKind.CANCEL_ACTION
    )
    assert classify_decision_utterance("cancel") is DecisionUtteranceKind.DECLINE


def test_disallowed_positive_and_source_cancel_commands_fail_closed():
    assert classify_decision_utterance("yes") is DecisionUtteranceKind.UNRELATED
    # It remains a safe decline and may disarm, but cannot reject the source.
    assert classify_decision_utterance("cancel the action") is DecisionUtteranceKind.DECLINE


@pytest.mark.parametrize(
    "reply", ["repeat and confirm", "cancel the action for pump 12", "what changed yesterday"]
)
def test_command_prefixes_do_not_capture_longer_utterances(reply):
    assert classify_decision_utterance(reply) is not DecisionUtteranceKind.REVIEW
    assert classify_decision_utterance(reply) is not DecisionUtteranceKind.HISTORY
    assert classify_decision_utterance(reply) is not DecisionUtteranceKind.CANCEL_ACTION


def test_defer_is_non_consuming_but_unrelated_is_not():
    assert classify_decision_utterance("wait").non_consuming is True
    assert classify_decision_utterance("tell me about pump 4").non_consuming is False


def test_safe_negative_outcomes_ignore_allowed_response_gating():
    assert classify_decision_utterance("no") is DecisionUtteranceKind.DECLINE
    assert classify_decision_utterance("no, I meant 140") is DecisionUtteranceKind.AMEND
    assert classify_decision_utterance("wait") is DecisionUtteranceKind.DEFER


def test_policy_is_versioned():
    assert DECISION_GRAMMAR_POLICY_VERSION == "decision-grammar-v2"


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("confirm cancel order", DecisionUtteranceKind.AFFIRM),
        ("confirm cancel order 104", DecisionUtteranceKind.AFFIRM),
        ("confirm cancel order one hundred four", DecisionUtteranceKind.AFFIRM),
        ("confirm cancel order WO-000104", DecisionUtteranceKind.AFFIRM),
        ("confirm cancel order 140", DecisionUtteranceKind.AMEND),
        ("confirm cancel order 104 and delete it", DecisionUtteranceKind.AMEND),
        ("confirm cancel order, but change the reason", DecisionUtteranceKind.AMEND),
        ("confirm cancel order, no wait", DecisionUtteranceKind.DECLINE),
        ("confirm cancel order not yet", DecisionUtteranceKind.DEFER),
        ("no", DecisionUtteranceKind.DECLINE),
        ("cancel", DecisionUtteranceKind.DECLINE),
    ],
)
def test_cancel_order_strict_phrase_and_optional_bound_reference(reply, expected):
    """OD-3: the cancel verb is not a decline inside the exact strict phrase."""
    assert (
        classify_decision_utterance(
            reply,
            required_phrase="confirm cancel order",
            allowed_responses=("confirm cancel order", "no", "change that"),
            target_references=("104", "WO-000104"),
        )
        is expected
    )


@pytest.mark.parametrize("reply", ["yes", "confirm", "confirm cancel", "confirm cancel the order"])
def test_cancel_order_short_assent_never_confirms(reply):
    assert (
        classify_decision_utterance(
            reply,
            required_phrase="confirm cancel order",
            allowed_responses=("confirm cancel order", "no"),
        )
        is not DecisionUtteranceKind.AFFIRM
    )


def test_optional_reference_does_not_infer_a_target_or_bypass_allowed_responses():
    for references, allowed in [((), ("confirm cancel order",)), (("104",), ())]:
        assert (
            classify_decision_utterance(
                "confirm cancel order 104",
                required_phrase="confirm cancel order",
                target_references=references,
                allowed_responses=allowed,
            )
            is not DecisionUtteranceKind.AFFIRM
        )
