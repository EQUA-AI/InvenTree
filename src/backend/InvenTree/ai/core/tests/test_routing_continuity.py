"""M1 (E35): routing continuity for follow-up fragments — pure rule, no Django."""

from __future__ import annotations

import pytest
from ai.core.memory.routing_continuity import (
    MAX_FOLLOWUP_WORDS,
    RAIL_WORKFLOWS,
    continuation_workflow,
    is_acknowledgement,
    is_followup_fragment,
)

# Every follow-up the memory battery asks on a rail case (D2), by prior rail.
_BATTERY_FOLLOWUPS = [
    ("wf2", "Compare the first two you listed."),
    ("wf2", "Is either of them obsolete?"),
    ("wf3", "What is the lead time for that one?"),
    ("wf3", "Summarise what you found so far."),
    ("wf1", "What was replaced on it the last time this happened?"),
    ("wf1", "Did that fix it?"),
    ("wf1", "Which lugs should be re-torqued for that?"),
    ("wf1", "Is the fault still ongoing after that step?"),
    ("wf3", "And for the surge arrester?"),
    ("wf3", "Which of those has the shorter lead time?"),
    ("wf2", "Back to the isolator - what else fits it?"),
    ("wf2", "Which was the first one you mentioned?"),
    ("wf2", "Which of those is rated the highest?"),
    ("wf2", "Is the first one obsolete?"),
    ("wf2", "Summarise the ones you mentioned."),
    ("wf1", "I checked that and it is fine. What next?"),
    ("wf1", "What was replaced during its last repair?"),
    ("wf1", "Does that explain the alarm?"),
]


@pytest.mark.parametrize(("prior", "message"), _BATTERY_FOLLOWUPS)
def test_every_battery_followup_stays_on_its_rail(prior, message):
    assert is_followup_fragment(message), message
    assert continuation_workflow(message, prior) == prior


@pytest.mark.parametrize("message", ["Ok.", "okay", "Thanks!", "Got it.", "yes", "Fine, thank you"])
def test_acknowledgements_never_inherit_a_rail(message):
    assert is_acknowledgement(message)
    assert continuation_workflow(message, "wf2") == ""


@pytest.mark.parametrize(
    "message",
    [
        "Find alternatives for the SI-3000 AC surge arrester part.",
        "Find suppliers for the SI-3000 cabinet air filter.",
        "Diagnose a DC overvoltage alarm on string 3 of this inverter.",
        "How many work orders are recorded for Inverter A in total?",
        "Send that to the supplier by email.",
        "Create a work order for it.",
        "Research the specifications for the SI-3000 inverter coolant pump.",
    ],
)
def test_messages_naming_a_new_intent_go_back_through_the_router(message):
    assert continuation_workflow(message, "wf2") == ""


def test_long_messages_carry_their_own_intent():
    words = " ".join(["it"] * (MAX_FOLLOWUP_WORDS + 1))
    assert not is_followup_fragment(words)
    assert continuation_workflow(words, "wf2") == ""


@pytest.mark.parametrize("prior", ["", "wf8", "general", "wf4", "wf6", "analysis_executor"])
def test_only_conversation_rails_are_sticky(prior):
    assert prior not in RAIL_WORKFLOWS
    assert continuation_workflow("Compare the first two you listed.", prior) == ""


def test_wf8_threads_are_unaffected():
    """The lookup assistant replays history itself; no pin is ever needed."""
    for message in (
        "What documents are on file for it?",
        "And the superseded one?",
        "Just the open ones.",
        "Now the same for the other one.",
    ):
        assert continuation_workflow(message, "") == ""
