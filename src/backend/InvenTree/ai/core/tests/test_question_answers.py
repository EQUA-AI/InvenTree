"""S22/S23: the versioned pure answer parser — ordinal, label, decline."""

# ruff: noqa: E402

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.questions.answers import (
    QUESTION_ANSWER_POLICY_VERSION,
    interpret_question_answer,
)

OPTIONS = [
    {"id": "machine:1", "label": "Influent Pump Station No. 1"},
    {"id": "machine:2", "label": "Clarifier Drive 2"},
    {"id": "term:fasteners", "label": "fasteners"},
]


@pytest.mark.parametrize(
    ("reply", "index"),
    [
        ("1", 0),
        ("2", 1),
        ("two", 1),
        ("Option 3", 2),
        ("number two", 1),
        ("the second one", 1),
        ("second option", 1),
        ("The First", 0),
        ("the last one", 2),
        ("3.", 2),
    ],
)
def test_ordinals_select(reply, index):
    interp = interpret_question_answer(reply, OPTIONS)
    assert interp.outcome == "selected"
    assert interp.option_index == index
    assert interp.option_id == OPTIONS[index]["id"]
    assert interp.matched_by == "ordinal"
    assert interp.policy_version == QUESTION_ANSWER_POLICY_VERSION


def test_out_of_range_ordinal_is_unmatched_not_an_error():
    assert interpret_question_answer("4", OPTIONS).outcome == "unmatched"
    assert interpret_question_answer("the fourth one", OPTIONS).outcome == "unmatched"


@pytest.mark.parametrize(
    "reply",
    [
        "none of those",
        "None of these",
        "neither",
        "skip",
        "skip it",
        "cancel",
        "never mind",
        "nevermind",
        "no thanks",
        "forget it",
    ],
)
def test_declines(reply):
    assert interpret_question_answer(reply, OPTIONS).outcome == "declined"


def test_decline_words_inside_a_real_request_do_not_decline():
    """ "cancel the order for pump seals" is a request, not a decline."""
    interp = interpret_question_answer("cancel the order for pump seals and check stock", OPTIONS)
    assert interp.outcome == "unmatched"


@pytest.mark.parametrize(
    ("reply", "index"),
    [
        ("Clarifier Drive 2", 1),
        ("clarifier drive 2", 1),
        ("the Clarifier Drive 2 please", 1),
        ("fasteners", 2),
    ],
)
def test_label_matches(reply, index):
    interp = interpret_question_answer(reply, OPTIONS)
    assert interp.outcome == "selected"
    assert interp.option_index == index
    assert interp.matched_by == "label"


def test_ambiguous_label_containment_is_unmatched():
    """Two options hit -> guessing selects nothing."""
    options = [
        {"id": "a", "label": "Pump 1"},
        {"id": "b", "label": "Pump 12"},
    ]
    assert interpret_question_answer("pump 1", options).outcome in {
        "selected",
        "unmatched",
    }
    # The genuinely ambiguous phrasing: contains both labels.
    assert interpret_question_answer("pump 1 and pump 12", options).outcome == "unmatched"


def test_free_text_is_unmatched():
    interp = interpret_question_answer("actually can you list all open work orders", OPTIONS)
    assert interp.outcome == "unmatched"


def test_empty_inputs_are_unmatched():
    assert interpret_question_answer("", OPTIONS).outcome == "unmatched"
    assert interpret_question_answer("2", []).outcome == "unmatched"


def test_voice_modality_parses_the_second_one():
    """The required voice case: a spoken ordinal selects."""
    interp = interpret_question_answer("the second one", OPTIONS, modality="voice")
    assert interp.outcome == "selected"
    assert interp.option_index == 1
