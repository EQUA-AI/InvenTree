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


# ---------------------------------------------------------------------------
# v2 normalized matching — every case below is verbatim (or near-verbatim)
# from the 2026-08-08 live battery, where each of these replies re-armed an
# identical card forever.
# ---------------------------------------------------------------------------

_PUMPS = [
    {"id": "machine:8", "label": "Boiler Feed Pump B"},
    {"id": "machine:23", "label": "Influent Pump Station No. 1"},
]


@pytest.mark.parametrize(
    ("reply", "index"),
    [
        ("Influent Pump Station 1.", 1),
        ("influent pump station one", 1),
        ("influent pump station 1", 1),
        ("the influent pump station", 1),
        ("Influent pump station number one please", 1),
        ("boiler pump", 0),
        ("the boiler feed pump", 0),
    ],
)
def test_normalized_label_variants_select(reply, index):
    """Fillers, punctuation, number words and a missing 'No.' all match."""
    interp = interpret_question_answer(reply, _PUMPS)
    assert interp.outcome == "selected", reply
    assert interp.option_index == index, reply
    assert interp.matched_by == "label"


def test_normalized_matching_respects_serial_suffixed_labels():
    """Machine-candidate labels carry '(serial)'; answers still match."""
    options = [
        {"id": "machine:8", "label": "Boiler Feed Pump B (BFP-B-CR45-2020)"},
        {"id": "machine:23", "label": "Influent Pump Station No. 1 (TC-INF-PS1-001)"},
    ]
    interp = interpret_question_answer("influent pump station 1", options)
    assert interp.outcome == "selected"
    assert interp.option_index == 1


def test_ambiguous_normalized_token_stays_unmatched():
    """'pump' hits both pumps: guessing selects nothing."""
    assert interpret_question_answer("pump", _PUMPS).outcome == "unmatched"
    assert interpret_question_answer("the pump", _PUMPS).outcome == "unmatched"


def test_a_fresh_sentence_is_a_question_not_an_answer():
    """A long reply that merely mentions an option must route normally."""
    reply = (
        "The influent pump station is tripping on high vibrations again. What's the likely cause?"
    )
    assert interpret_question_answer(reply, _PUMPS).outcome == "unmatched"


def test_neither_of_those_declines():
    """The battery's own decline phrasing (A3) must decline."""
    interp = interpret_question_answer("neither of those", _PUMPS)
    assert interp.outcome == "declined"


def test_policy_version_bumped_for_v2_grammar():
    assert QUESTION_ANSWER_POLICY_VERSION == "question-answer-v2"
