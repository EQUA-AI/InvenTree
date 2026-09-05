"""S14 battery scenario schema tests: loaders, validators, the blinding fence.

Always-on structural tests — no Django, no network, no gold content.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ai.core.evals import scenarios
from ai.core.evals.scenarios import (
    BATTERY_DIR,
    BatteryFile,
    RepeatTranche,
    ScenarioCase,
    ScenarioTurn,
    assert_outside_repo,
    load_battery,
    load_fixture_keys,
    load_gold,
    load_questions,
    planned_request_count,
    validate_battery,
)

FIXTURE_KEYS = load_fixture_keys()


def _case(**over) -> ScenarioCase:
    base = {
        "id": "FB99",
        "turns": (ScenarioTurn(question="How many work orders?"),),
        "scope_machine_fixture_keys": ("solar_a",),
    }
    base.update(over)
    return ScenarioCase(**base)


def _battery(cases, dataset="fixture", tranche=None) -> BatteryFile:
    return BatteryFile(
        schema_version=1,
        dataset=dataset,
        fixture_set_versions=("aimms-analysis-fixtures-v1",),
        cases=tuple(cases),
        repeat_tranche=tranche,
    )


# --------------------------------------------------------------------------- #
# The committed files                                                          #
# --------------------------------------------------------------------------- #
def test_fixture_battery_loads_and_validates_clean():
    battery = load_battery(BATTERY_DIR / "fixture_battery.yaml")
    assert battery.dataset == "fixture"
    assert len(battery.cases) >= 14, "fixture battery must not silently shrink"
    assert validate_battery(battery, FIXTURE_KEYS) == []


def test_solar_battery_loads_and_validates_clean():
    battery = load_battery(BATTERY_DIR / "solar_battery.yaml")
    assert battery.dataset == "solar"
    # 86 standalone + M1..M7 continuous-thread scenarios.
    assert len(battery.cases) == 93
    assert validate_battery(battery, FIXTURE_KEYS) == []


def test_solar_battery_carries_no_question_text():
    """The blinding fence, checked directly against the committed file."""
    battery = load_battery(BATTERY_DIR / "solar_battery.yaml")
    for case in battery.cases:
        for turn in case.turns:
            assert turn.question == "", case.id
            assert turn.question_ref, case.id


def test_the_repeat_tranche_is_preregistered_in_file():
    battery = load_battery(BATTERY_DIR / "solar_battery.yaml")
    assert battery.repeat_tranche is not None
    assert battery.repeat_tranche.case_ids == (
        "Q21",
        "Q22",
        "Q25",
        "Q69",
        "Q83",
        "Q86",
        "M6",
        "M7",
    )
    assert battery.repeat_tranche.repetitions == 20


def test_s0_mapped_expectations_survive_into_the_solar_battery():
    battery = load_battery(BATTERY_DIR / "solar_battery.yaml")
    assert battery.case("Q37").turns[0].expected_intent == "fleet_aggregate"
    assert battery.case("Q61").turns[0].expected_intent == "manual_wo_comparison"
    assert battery.case("Q64").turns[0].expected_intent == "record_retrieval"
    # Scope-leak cases carry their forbidden keys.
    assert "hx200" in battery.case("Q01").turns[0].forbidden_entity_fixture_keys
    # Boundary refusals stay refusals at tier 1.
    assert battery.case("Q76").turns[0].behavior_for_tier(1) == "refuse"
    # Tier-2 items are boundary-only at tier 1 (never substantive).
    assert battery.case("Q37").turns[0].behavior_for_tier(1) == "capability_boundary"
    assert battery.case("Q37").turns[0].behavior_for_tier(2) == "answer"
    # M6 later turns forbid the stale-domain markers.
    m6 = battery.case("M6")
    assert m6.is_multi_turn
    assert "supplier" in m6.turns[1].forbidden_entity_fixture_keys


def test_fixture_keys_cover_every_marker_kind():
    for key, descriptor in FIXTURE_KEYS.items():
        assert descriptor.get("kind") in (
            "machine",
            "document",
            "attachment",
            "work_order",
            "marker",
        ), key
        if descriptor.get("kind") == "marker":
            assert descriptor.get("markers"), key


# --------------------------------------------------------------------------- #
# Validator semantics                                                          #
# --------------------------------------------------------------------------- #
def test_solar_file_with_inline_text_fails_validation():
    battery = _battery(
        [_case(id="Q99", turns=(ScenarioTurn(question="verbatim solar text"),))],
        dataset="solar",
    )
    problems = validate_battery(battery, FIXTURE_KEYS)
    assert any("verbatim question text" in p.problem for p in problems)


def test_exactly_one_question_source_per_turn():
    both = _case(turns=(ScenarioTurn(question="x", question_ref="q01"),))
    neither = _case(turns=(ScenarioTurn(),))
    for case in (both, neither):
        problems = validate_battery(_battery([case]), FIXTURE_KEYS)
        assert any("exactly one of question/question_ref" in p.problem for p in problems)


def test_m_prefix_and_multi_turn_must_coincide():
    single_m = _case(id="M9")
    multi_q = _case(
        id="Q99",
        turns=(ScenarioTurn(question="a"), ScenarioTurn(question="b")),
    )
    for case in (single_m, multi_q):
        problems = validate_battery(_battery([case]), FIXTURE_KEYS)
        assert any("must coincide" in p.problem for p in problems)


def test_unknown_names_are_each_rejected():
    bad = _case(
        id="FB98",
        scope_machine_fixture_keys=("no_such_key",),
        required_assertions=("no_such_assertion",),
        turns=(
            ScenarioTurn(
                question="x",
                expected_intent="no_such_intent",
                expected_behavior_by_tier=((1, "no_such_behavior"), (9, "answer")),
                forbidden_entity_fixture_keys=("also_missing",),
            ),
        ),
    )
    problems = {p.problem for p in validate_battery(_battery([bad]), FIXTURE_KEYS)}
    assert any("unknown scope fixture key" in p for p in problems)
    assert any("unknown assertion" in p for p in problems)
    assert any("unknown intent" in p for p in problems)
    assert any("unknown behavior" in p for p in problems)
    assert any("tier keys must be 1..3" in p for p in problems)
    assert any("unknown forbidden fixture key" in p for p in problems)


def test_tranche_ids_must_exist():
    battery = _battery([_case()], tranche=RepeatTranche(case_ids=("QMISSING",)))
    problems = validate_battery(battery, FIXTURE_KEYS)
    assert any("not in this battery" in p.problem for p in problems)


def test_behavior_for_tier_picks_the_highest_matching_key():
    turn = ScenarioTurn(
        question="x",
        expected_behavior_by_tier=((1, "capability_boundary"), (2, "answer")),
    )
    assert turn.behavior_for_tier(1) == "capability_boundary"
    assert turn.behavior_for_tier(2) == "answer"
    assert turn.behavior_for_tier(3) == "answer"
    assert ScenarioTurn(question="x").behavior_for_tier(1) == ""


# --------------------------------------------------------------------------- #
# Planned request arithmetic (the §13.6-vs-Q47 resolution)                     #
# --------------------------------------------------------------------------- #
def test_planned_request_count_is_computed_from_the_files():
    clarify = _case(
        id="FB97",
        turns=(ScenarioTurn(question="x", clarification_followup_ref="c1"),),
    )
    multi = _case(
        id="M8",
        turns=(ScenarioTurn(question="a"), ScenarioTurn(question="b")),
    )
    battery = _battery(
        [_case(), clarify, multi],
        tranche=RepeatTranche(case_ids=("FB99", "M8"), repetitions=3),
    )
    # 1 + (1 + 1 clarification) + 2 = 5 without the tranche.
    assert planned_request_count([battery]) == 5
    # Tranche adds 3x1 + 3x2 = 9.
    assert planned_request_count([battery], include_tranche=True) == 14


def test_committed_batteries_fit_the_300_request_reservation_per_run():
    solar = load_battery(BATTERY_DIR / "solar_battery.yaml")
    fixture = load_battery(BATTERY_DIR / "fixture_battery.yaml")
    # One full pass and one tranche run each fit the reservation separately.
    assert planned_request_count([solar]) <= 300
    assert (
        planned_request_count([solar], include_tranche=True) - planned_request_count([solar]) <= 300
    )
    assert planned_request_count([fixture], include_tranche=True) <= 300


# --------------------------------------------------------------------------- #
# Private-store loading (structure only — gold content is human work)          #
# --------------------------------------------------------------------------- #
def test_gold_loader_round_trips_a_valid_file(tmp_path: Path):
    (tmp_path / "gold").mkdir()
    (tmp_path / "gold" / "Q01.yaml").write_text(
        """
answerability: answerable
minimum_tier: 1
accepted_source_ids: [SI3000-SM]
accepted_revisions: [B]
required_claims: [count_is_stated]
forbidden_claims: [root_cause_asserted]
calculations:
  - {name: wo_count, value: "28", operands: [work_orders], date_field: actual_completed_at,
     timezone: America/Chicago, tolerance: exact}
required_facets: [records, coverage]
clarification: Which inverter do you mean?
gold_revision: gold-v1
""",
        encoding="utf-8",
    )
    gold = load_gold(tmp_path, "Q01")
    assert gold is not None
    assert gold.answerability == "answerable"
    assert gold.calculations[0].value == "28"
    assert gold.gold_revision == "gold-v1"


def test_absent_gold_is_none_not_an_error(tmp_path: Path):
    (tmp_path / "gold").mkdir()
    assert load_gold(tmp_path, "Q77") is None


def test_gold_with_bad_answerability_raises(tmp_path: Path):
    (tmp_path / "gold").mkdir()
    (tmp_path / "gold" / "Q02.yaml").write_text("answerability: maybe\n", encoding="utf-8")
    with pytest.raises(ValueError, match="answerability"):
        load_gold(tmp_path, "Q02")


def test_questions_loader_shape(tmp_path: Path):
    (tmp_path / "questions.yaml").write_text(
        "questions:\n  q01: What is the count?\n", encoding="utf-8"
    )
    assert load_questions(tmp_path) == {"q01": "What is the count?"}


def test_private_store_paths_inside_the_repo_are_refused(tmp_path: Path):
    with pytest.raises(ValueError, match="outside"):
        assert_outside_repo(Path(scenarios.__file__).parent)
    assert assert_outside_repo(tmp_path) == tmp_path.resolve()


# --------------------------------------------------------------------------- #
# D1/D2 (M1 gate): the memory battery and its schema additions                #
# --------------------------------------------------------------------------- #
def test_memory_battery_loads_validates_and_covers_every_rail():
    battery = load_battery(BATTERY_DIR / "memory_battery.yaml")
    assert battery.is_memory_battery
    assert battery.dataset == "fixture"
    assert len(battery.cases) == 12, "the preregistered case count must not drift silently"
    assert validate_battery(battery, FIXTURE_KEYS) == []
    # 57 turns per pass; every case is a fresh continuous thread naming a rail.
    assert planned_request_count([battery]) == 57
    assert all(case.fresh_thread and case.is_multi_turn for case in battery.cases)
    assert {case.rail for case in battery.cases} == set(scenarios.RAILS)
    # Over five passes every rail clears the 25-follow-up floor (GR-45).
    followups = dict.fromkeys(scenarios.RAILS, 0)
    for case in battery.cases:
        followups[case.rail] += (len(case.turns) - 1) * 5
    assert followups == {"wf8": 130, "rbac_run": 35, "reasoning": 35, "routing": 25}
    # The reasoning rail is flag-gated (skips, never fails, when dark).
    for case in battery.cases:
        if case.rail == "reasoning":
            assert case.requires_flags == ("FEATURE_VOICE_LIVE_DIAGNOSIS",)
        else:
            assert case.requires_flags == ()
    # The compaction case crosses COMPACTION_MIN_BACKLOG=16 and asserts recall.
    compaction = battery.case("M-MEM-10")
    assert len(compaction.turns) == 20
    last = compaction.turns[-1]
    assert last.required_entity_fixture_keys == ("mem_reading_corrected",)
    assert "mem_reading_superseded" in last.forbidden_entity_fixture_keys
    assert last.expect_conversation_summary_present is True
    # The routing cases REPORT summary presence on their fragments.
    assert battery.case("M-MEM-07").turns[1].expect_conversation_summary_present is True
    assert battery.case("M-MEM-08").turns[1].expected_workflow == "wf8"


def test_memory_m_cases_must_declare_a_rail():
    case = ScenarioCase(
        id="M-MEM-99",
        turns=(ScenarioTurn(question="a"), ScenarioTurn(question="b")),
    )
    battery = BatteryFile(
        schema_version=1,
        dataset="fixture",
        fixture_set_versions=(),
        cases=(case,),
        source="memory_battery.yaml",
    )
    problems = {p.problem for p in validate_battery(battery, FIXTURE_KEYS)}
    assert any("must declare a rail" in p for p in problems)
    # The same case in a non-memory file needs no rail.
    assert validate_battery(_battery([case]), FIXTURE_KEYS) == []


def test_new_vocabulary_is_each_validated():
    bad = _case(
        id="M-MEM-98",
        rail="wf42",
        requires_flags=("voice_live",),
        turns=(
            ScenarioTurn(
                question="a",
                expected_workflow="wf99",
                required_entity_fixture_keys=("no_such_key", "hx200"),
                forbidden_entity_fixture_keys=("hx200",),
            ),
            ScenarioTurn(question="b"),
        ),
    )
    problems = {p.problem for p in validate_battery(_battery([bad]), FIXTURE_KEYS)}
    assert any("unknown rail" in p for p in problems)
    assert any("not a FEATURE_* name" in p for p in problems)
    assert any("unknown expected_workflow" in p for p in problems)
    assert any("unknown required fixture key" in p for p in problems)
    assert any("both required and forbidden" in p for p in problems)


def test_summary_expectation_parses_as_tri_state(tmp_path: Path):
    path = tmp_path / "memory_x.yaml"
    path.write_text(
        """
schema_version: 1
dataset: fixture
cases:
  - id: M-1
    rail: routing
    turns:
      - question: a
      - question: b
        expect_conversation_summary_present: true
      - question: c
        expect_conversation_summary_present: false
""",
        encoding="utf-8",
    )
    battery = load_battery(path)
    assert battery.is_memory_battery
    turns = battery.case("M-1").turns
    assert turns[0].expect_conversation_summary_present is None
    assert turns[1].expect_conversation_summary_present is True
    assert turns[2].expect_conversation_summary_present is False
