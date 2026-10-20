"""Spoken focus commands: parsing, anaphora, and reference continuity.

Deterministic contract pins: an explicit switch must name a kind; a number
buried in a switch phrase is an id candidate but an ordinal answer to a
pending question must never be treated as one; explicit references always
win over pronouns; continuity enrichment binds ONLY bare anaphora to the
turn's recorded focus label.
"""

from __future__ import annotations

from ai.core.focus import commands


def test_explicit_switch_parses_kind_and_numeric_id() -> None:
    for text, kind, numeric in (
        ("talk about machine 3", "machine", 3),
        ("switch to task 11", "kanban_card", 11),
        ("focus on work order 5", "work_order", 5),
        ("Tell me about the card 11.", "kanban_card", 11),
        ("Let's talk about job two", "work_order", 2),
        ("please switch to asset 7", "machine", 7),
    ):
        command = commands.parse_focus_command(text)
        assert command is not None, text
        assert command.kind == kind, text
        assert command.numeric_id == numeric, text


def test_explicit_switch_with_name_query_has_no_id() -> None:
    command = commands.parse_focus_command("talk about the influent pump station")
    assert command is not None
    # Kind-less name references are valid switches; the search covers every
    # kind and the question mechanism disambiguates.
    assert command.kind is None
    assert command.numeric_id is None
    assert "influent" in command.query


def test_only_a_whole_numeric_remainder_is_an_id() -> None:
    """An embedded number is part of the NAME, never a PK.

    "machine Pump 12" is the record NAMED "Pump 12"; binding the 12 as a
    machine PK would silently retarget the switch to a different record.
    Reference tokens ("WO-000123") and mixed names keep their exact text —
    never repaired, stripped or re-tokenized.
    """
    pump = commands.parse_focus_command("talk about machine Pump 12")
    assert pump is not None
    assert pump.kind == "machine"
    assert pump.numeric_id is None
    assert pump.query == "Pump 12"

    wo = commands.parse_focus_command("switch to work order WO-000123")
    assert wo is not None
    assert wo.kind == "work_order"
    assert wo.numeric_id is None
    assert wo.query == "WO-000123"

    multi = commands.parse_focus_command("talk about the card bearing 4 seals 7")
    assert multi is not None
    assert multi.kind == "kanban_card"
    assert multi.numeric_id is None
    assert multi.query == "bearing 4 seals 7"


def test_whole_numeric_remainder_still_binds_the_id() -> None:
    assert commands.parse_focus_command("talk about machine 3").numeric_id == 3
    assert commands.parse_focus_command("switch to task 11.").numeric_id == 11
    assert commands.parse_focus_command("focus on work order 5?").numeric_id == 5
    assert commands.parse_focus_command("talk about machine 3").query == ""


def test_work_order_references_resolve_by_reference_and_title() -> None:
    """Reference and title are both legitimate work-order identities."""
    from ai.core.focus import resolution

    assert resolution.WORK_ORDER_MATCH_FIELDS == (
        "reference__icontains",
        "title__icontains",
    )
    assert resolution.MACHINE_MATCH_FIELDS == ("name__icontains", "serial__icontains")
    assert resolution.CARD_MATCH_FIELDS == ("title__icontains",)


def test_bare_number_with_no_kind_is_never_a_switch() -> None:
    """Machine 3, task 3 and work order 3 are different records."""
    assert commands.parse_focus_command("talk about 3") is None
    assert commands.parse_focus_command("switch to 12") is None


def test_ui_task_label_maps_to_the_card_kind() -> None:
    assert commands.parse_focus_command("switch to task 4").kind == "kanban_card"
    assert commands.parse_focus_command("talk about card 4").kind == "kanban_card"


def test_non_switch_utterances_parse_as_none() -> None:
    for text in (
        "what is its status?",
        "yes",
        "2",
        "the second one",
        "machine 3",  # no explicit switch phrase
        "cancel that",
        "that sounds good",
    ):
        assert commands.parse_focus_command(text) is None, text


def test_bare_anaphora_requires_a_pronoun_request_and_no_entity() -> None:
    assert commands.is_bare_anaphora("what is its status?")
    assert commands.is_bare_anaphora("and what about that one?")
    assert commands.is_bare_anaphora("tell me about it")
    assert not commands.is_bare_anaphora("that sounds good")
    assert not commands.is_bare_anaphora("what is the status of work order 5")
    assert not commands.is_bare_anaphora("talk about machine 3")
    assert not commands.is_bare_anaphora(
        "what happened last week in the plant during the outage"
    )  # too long


def test_anaphora_never_overrides_an_explicit_entity() -> None:
    """A pronoun inside an explicit reference does not make it an anaphor.

    "its" in "the pump and its motor" must not rebind a turn that already
    names a record to whatever the focus happens to be.
    """
    assert not commands.is_bare_anaphora("how is the pump doing and its motor?")
    assert not commands.is_bare_anaphora("what is the status of Pump 12 and its seal?")
    assert not commands.is_bare_anaphora("check machine 7 and its history")
    assert not commands.is_bare_anaphora("is the influent station ok and its bearing?")
    assert commands.is_bare_anaphora("is it ok?")
    assert commands.is_bare_anaphora("and its status?")


def _snapshot() -> dict:
    return {
        "focus": {
            "schema_version": 1,
            "kind": "machine",
            "id": 3,
            "label": "Influent Pump Station No. 1",
            "machine_id": 3,
            "work_order_id": None,
        },
        "version": 2,
    }


def test_continuity_binds_anaphora_to_the_recorded_focus_identity() -> None:
    """Continuity carries the CANONICAL id, not only a (possibly duplicate) label."""
    enriched = commands.continuity_content("what is its status?", _snapshot())
    assert enriched == "what is its status? — Influent Pump Station No. 1 (machine 3)"


def test_continuity_never_rewrites_explicit_or_unfocused_turns() -> None:
    assert (
        commands.continuity_content("what is the status of work order 5?", _snapshot())
        == "what is the status of work order 5?"
    )
    assert commands.continuity_content("what is its status?", None) == "what is its status?"
    assert (
        commands.continuity_content("what is its status?", {"focus": {}, "version": 0})
        == "what is its status?"
    )
