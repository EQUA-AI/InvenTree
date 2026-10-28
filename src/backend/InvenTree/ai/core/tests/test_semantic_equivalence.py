"""Equivalent speech at typed boundaries, and near-misses that must not bind."""

import pytest
from ai.core.decisions.adapters.approval_reference import reference as approval_reference
from ai.core.decisions.inventory import _quantity, parse_stock_intent
from ai.core.decisions.resolver import _reference, parse_work_order_intent
from ai.core.focus.commands import parse_focus_command
from ai.core.questions.answers import interpret_question_answer
from ai.core.read_numbers import normalize_read_numbers
from ai.core.tools.catalog_queries import catalog_request
from ai.core.turn.targets import machine_candidates, resolve_target
from ai.core.voice.confirmation import ConfirmationReply, interpret_confirmation_reply
from common.spoken_numbers import canonical_equipment_name


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("eight", "8"),
        ("zero eight", "08"),
        ("twenty-one", "21"),
        ("one hundred and fifteen", "115"),
        ("one thousand and five", "1005"),
        ("1,005", "1005"),
        ("WO-000008", "WO-000008"),
        ("000008", "000008"),
    ],
)
def test_record_reference_slot_preserves_identity(spoken, expected):
    assert _reference(spoken) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Stock under 1,000 units", "Stock under 1000 units"),
        ("Stock above 1,000.25 units", "Stock above 1000.25 units"),
        ("Show stock for part ID 1,005", "Show stock for part id 1005"),
        ("Stock at location ID twenty-one", "Stock at location id 21"),
        ("Parts in category ID eight", "Parts in category id 8"),
        ("Machine ID one hundred fifteen", "machine id 115"),
        ("For machine ID 24, plot work orders", "For machine id 24, plot work orders"),
        ("Stock under 1,000, please", "Stock under 1000, please"),
    ],
)
def test_read_field_spellings_share_one_numeric_interpretation(text, expected):
    parsed = normalize_read_numbers(text)
    assert parsed.text == expected
    assert not parsed.ambiguous


@pytest.mark.parametrize("text", ["Stock under 1,00 units", "Stock under twenty five six units"])
def test_malformed_numeric_fields_remain_unverified(text):
    parsed = normalize_read_numbers(text)
    assert parsed.ambiguous
    assert parsed.text == text


def test_catalog_category_id_words_reach_the_actual_request_parser():
    assert catalog_request("List parts in category ID twenty-one") == {
        "category_id": 21,
        "limit": 100,
        "offset": 0,
    }


@pytest.mark.parametrize(
    "text", ["machine ID thirteen", "machine ID zero eight", "machine ID 1,005"]
)
def test_turn_machine_id_reader_uses_the_same_explicit_field_parser(text):
    expected = {"machine ID thirteen": 13, "machine ID zero eight": 8, "machine ID 1,005": 1005}
    assert machine_candidates(text) == frozenset({expected[text]})


@pytest.mark.parametrize("text", ["machine ID 8,00", "machine ID twenty five six"])
def test_ambiguous_explicit_machine_field_never_falls_back_to_partial_id(text):
    assert machine_candidates(text) == frozenset()
    assert "error" in resolve_target(None, text, None, None, None)


@pytest.mark.parametrize(
    "target,kind,expected",
    [
        ("machine thirteen", "machine", 13),
        ("machine twenty-one", "machine", 21),
        ("machine one hundred fifteen", "machine", 115),
        ("machine zero eight", "machine", 8),
        ("work order one hundred fifteen", "work_order", 115),
        ("work-order twenty-one", "work_order", 21),
        ("task twenty one", "kanban_card", 21),
    ],
)
def test_explicit_kind_focus_does_not_depend_on_small_word_list(target, kind, expected):
    command = parse_focus_command(f"Focus on {target}")
    assert command is not None
    assert (command.kind, command.numeric_id, command.query) == (kind, expected, "")


@pytest.mark.parametrize("name", ["Pump 08", "Pump eight", "Pump08", "SERIAL-008"])
def test_named_focus_never_treats_the_name_suffix_as_a_database_id(name):
    command = parse_focus_command(f"Focus on machine {name}")
    assert command.numeric_id is None
    assert command.query == name


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("one thousand", "1000"),
        ("one thousand and five", "1005"),
        ("one thousand point zero five", "1000.05"),
        ("1,000.25", "1000.25"),
        ("ten point zero zero five", "10.005"),
        ("zero", "0"),
    ],
)
def test_inventory_quantity_slot_handles_exact_words_without_unit_conversion(spoken, expected):
    assert _quantity(spoken) == expected


@pytest.mark.parametrize(
    "text,action,parameters",
    [
        ("Please add ten to stock item eight", "stock.add", {"quantity": "10", "stock_item_id": 8}),
        (
            "Count stock item eight as one thousand",
            "stock.count",
            {"quantity": "1000", "stock_item_id": 8},
        ),
    ],
)
def test_inventory_request_courtesy_and_quantity_do_not_change_target(text, action, parameters):
    intent = parse_stock_intent(text)
    assert intent is not None
    assert intent.action == action
    assert intent.parameters == parameters


@pytest.mark.parametrize("quantity", ["thirty", "thirty-five", "one hundred and five"])
def test_estimated_minutes_accept_spoken_integer(quantity):
    intent = parse_work_order_intent(f"Resize work order eight to {quantity} minutes")
    assert intent is not None
    assert intent.reference == "8"
    assert intent.parameters == {"estimated_minutes": int(_reference(quantity))}


@pytest.mark.parametrize("label", ["work order", "work-order", "WO"])
def test_work_order_request_label_variants_preserve_the_same_intent(label):
    intent = parse_work_order_intent(
        f"Please hold {label} zero eight because waiting for a bearing"
    )
    assert (intent.reference, intent.action, intent.reason) == (
        "08",
        "work_order.hold",
        "waiting for a bearing",
    )


PUMPS = [
    {"id": "machine:18", "label": "Station A / Pump 08"},
    {"id": "machine:19", "label": "Station B / Pump 09"},
]


@pytest.mark.parametrize(
    "reply",
    ["Pump eight", "Pump zero eight", "Pump 8", "Pump No. 008", "the Pump eight please"],
)
@pytest.mark.parametrize("modality", ["text", "voice"])
def test_clarification_labels_accept_same_equipment_aliases_as_search(reply, modality):
    result = interpret_question_answer(reply, PUMPS, modality=modality)
    assert result.outcome == "selected"
    assert result.option_id == "machine:18"


@pytest.mark.parametrize(
    "reply",
    [
        "Pump 8",
        "not the second one",
        "the second one is broken, what happened?",
        "not Pump 80",
        "Pump 80 but not that station",
        "Pump 80 and change the quantity",
    ],
)
def test_partial_numbers_negation_and_new_questions_never_select(reply):
    options = [
        {"id": "machine:80", "label": "Pump 80"},
        {"id": "machine:9", "label": "Pump 9"},
    ]
    assert interpret_question_answer(reply, options).outcome == "unmatched"


def test_equivalent_label_collision_requires_real_disambiguation():
    options = [*PUMPS, {"id": "machine:28", "label": "Station C / Pump 8"}]
    assert interpret_question_answer("Pump eight", options).outcome == "unmatched"
    assert interpret_question_answer("Station C / Pump eight", options).option_id == "machine:28"


def test_long_exact_canonical_label_remains_selectable():
    label = "Cooling water feed Pump 08 in the eastern service building"
    assert (
        interpret_question_answer(label, [{"id": "candidate", "label": label}]).option_id
        == "candidate"
    )


def test_out_of_range_ordinal_is_not_reinterpreted_as_a_machine_number():
    assert (
        interpret_question_answer("four", [{"id": "candidate", "label": "Pump 04"}]).outcome
        == "unmatched"
    )


@pytest.mark.parametrize("number", ["8A", "8-10", "8.5", "80"])
def test_clarification_never_matches_only_part_of_an_identifier(number):
    assert (
        interpret_question_answer(
            "Pump 8", [{"id": "candidate", "label": f"Pump {number}"}]
        ).outcome
        == "unmatched"
    )


def test_ordered_name_numbers_do_not_swap_station_and_pump():
    options = [{"id": "candidate", "label": "Station 8 / Pump 9"}]
    assert interpret_question_answer("Station nine Pump eight", options).outcome == "unmatched"
    assert interpret_question_answer("Station eight Pump nine", options).option_id == "candidate"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Station eight / Pump zero nine", "Station 8 / Pump 9"),
        ("Pump08", "Pump 8"),
        ("Blower one hundred five", "Blower 105"),
        ("Pump 8A", "Pump 8A"),
        ("SERIAL-008", "SERIAL-008"),
        ("Pump eight or nine", "Pump 8 or nine"),
    ],
)
def test_equipment_comparison_forms_remain_field_bounded(name, expected):
    assert canonical_equipment_name(name) == expected


@pytest.mark.parametrize(
    "reply",
    ["Pump eight or nine", "Pump eight and nine", "not Pump eight", "please not the first one"],
)
def test_multi_target_and_negative_clarifications_stay_unmatched(reply):
    assert interpret_question_answer(reply, PUMPS).outcome == "unmatched"


@pytest.mark.parametrize(
    "value",
    [
        "two five six",
        "two litres",
        "about ten",
        "ten to twelve",
        "-2",
        "-two",
        "- 2",
        "one point -two",
        "NaN",
    ],
)
def test_action_quantity_remains_literal_or_requires_review(value):
    assert _quantity(value) is None


@pytest.mark.parametrize(
    "value", ["eight hundred ten five", "8 or 9", "ate", "oh eight", "1,00", "-2", "-two"]
)
def test_ambiguous_identifiers_are_not_repaired(value):
    assert _reference(value) is None


@pytest.mark.parametrize(
    "text",
    [
        "serial eighty-eight",
        "IPN 002.01-PCBA",
        "revision zero eight",
        "model X-008",
        "machine called One Hundred",
    ],
)
def test_opaque_names_and_identifiers_are_not_numeric_fields(text):
    assert normalize_read_numbers(text).text == text


@pytest.mark.parametrize(
    "text", ["do not add ten to stock item eight", "do not hold work order eight"]
)
def test_negated_requests_do_not_become_actions(text):
    assert parse_stock_intent(text) is None
    assert parse_work_order_intent(text) is None


@pytest.mark.parametrize(
    "value", ["a to b two c three d four", "the second request", "a1b2c3d4 or a1b2c3d5"]
)
def test_approval_identifiers_do_not_use_fuzzy_or_ordinal_resolution(value):
    assert approval_reference(value) is None


@pytest.mark.parametrize(
    "reply",
    ["yes", "confirm", "delete it", "confirm delete the other one", "confirm delete but wait"],
)
def test_name_equivalence_does_not_relax_irreversible_confirmation(reply):
    assert (
        interpret_confirmation_reply(reply, required_phrase="confirm delete")
        is not ConfirmationReply.AFFIRM
    )
