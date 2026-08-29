"""S14 layered-scorer tests: deterministic-first, judge-last, honest folds."""

from __future__ import annotations

from typing import Any

from ai.core.evals.scenarios import (
    GoldAtoms,
    GoldCalculation,
    ScenarioCase,
    ScenarioService,
    ScenarioTurn,
)
from ai.core.evals.scoring import (
    Resolution,
    TurnArtifacts,
    resolution_from_manifest,
    score_turn,
)


def _turn(**over) -> ScenarioTurn:
    base = {"question": "How many work orders?", "expected_intent": "record_retrieval"}
    base.update(over)
    return ScenarioTurn(**base)


def _case(turn: ScenarioTurn, **over) -> ScenarioCase:
    base = {
        "id": "FB01",
        "turns": (turn,),
        "scope_machine_fixture_keys": ("solar_a",),
        "required_assertions": ("scope_persisted", "evidence_entails_claims", "no_governed_effect"),
    }
    base.update(over)
    return ScenarioCase(**base)


def _good_artifacts(**over) -> TurnArtifacts:
    base: dict[str, Any] = {
        "http_status": 200,
        "response_body": {"workflow_used": "analysis_executor"},
        "message_text": "There are 28 recorded work orders. [1]",
        "evidence_analysis": {
            "response_state": "complete",
            "active_scope": {"display_label": "Inverter A", "version": 3},
            "claims": [
                {
                    "claim_id": "c1",
                    "claim_role": "answer",
                    "evidence_classification": "calculated",
                    "citation_ordinals": [1],
                    "entity_refs": ["machine:11"],
                }
            ],
            "citations": [
                {"ordinal": 1, "source_id": "calc_1", "source_revision": "", "available": True}
            ],
            "coverage": {
                "population_count": 28,
                "returned_count": 25,
                "complete_population": True,
            },
            "incomplete_reasons": [],
        },
        "entities": [{"id": "machine:11", "ref": "machine:11", "label": "Inverter A"}],
        "response_state": "complete",
        "expected_scope_version": 3,
        "post_scope_version": 3,
        "route": {"task_intent": "record_retrieval", "mode": "analysis"},
        "proposal_ids_delta": 0,
    }
    base.update(over)
    return TurnArtifacts(**base)


def _score(turn=None, case=None, artifacts=None, tier=1, **kwargs):
    turn = turn or _turn()
    return score_turn(
        case=case or _case(turn),
        turn=turn,
        turn_index=0,
        artifacts=artifacts or _good_artifacts(),
        tier=tier,
        resolution=kwargs.pop("resolution", Resolution(scope_ids=frozenset({"machine:11"}))),
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Fold semantics                                                               #
# --------------------------------------------------------------------------- #
def test_complete_grounded_answer_passes_every_deterministic_layer():
    score = _score()
    assert score.outcome == "pass"
    assert score.deterministic_pass
    assert score.substantive
    for number in range(1, 7):
        assert score.layer(number).status == "pass", score.layer(number)
    assert score.layer(7).status == "not_scored"  # no gold authored


def test_deterministic_failure_never_invokes_the_judge():
    calls = []

    def spy_judge(question, gold, answer):
        calls.append(question)
        return {"required_claims_present": {"x": True}, "no_overclaim": True}

    artifacts = _good_artifacts(message_text="See the HX-200 heat exchanger history. [1]")
    resolution = Resolution(
        scope_ids=frozenset({"machine:11"}),
        forbidden={"hx200": (("machine:99",), ("HX-200",))},
    )
    gold = GoldAtoms(case_id="FB01", answerability="answerable")
    score = _score(artifacts=artifacts, resolution=resolution, gold=gold, judge=spy_judge)
    assert score.outcome == "fail"
    assert score.layer(3).status == "fail"
    assert "forbidden entity" in score.layer(3).detail
    assert score.layer(7).status == "skip"
    assert calls == [], "judge must never run after a deterministic failure"


def test_boundary_pass_is_never_substantive():
    turn = _turn(
        expected_intent="fleet_aggregate",
        expected_behavior_by_tier=((1, "capability_boundary"), (2, "answer")),
    )
    artifacts = TurnArtifacts(
        http_status=200,
        response_body={"workflow_used": "analysis_capability_boundary"},
        message_text="Historical aggregation is not enabled in this deployment.",
        response_state="incomplete",
        expected_scope_version=3,
        post_scope_version=3,
    )
    score = _score(
        turn=turn,
        case=_case(turn, required_assertions=("scope_persisted",)),
        artifacts=artifacts,
        tier=1,
    )
    assert score.outcome == "boundary_pass"
    assert score.substantive is False


def test_an_evidence_bearing_answer_fails_a_boundary_expectation():
    turn = _turn(expected_behavior_by_tier=((1, "capability_boundary"),))
    score = _score(turn=turn)
    assert score.outcome == "fail"
    assert "expected boundary" in score.layer(2).detail


def test_analysis_intent_routed_to_diagnostics_fails_the_rail_gate():
    artifacts = _good_artifacts(response_body={"workflow_used": "wf1"})
    score = _score(artifacts=artifacts)
    assert score.layer(2).status == "fail"
    assert "routed to wf1" in score.layer(2).detail


def test_intent_assertion_skips_honestly_when_not_exposed():
    artifacts = _good_artifacts(route=None)
    score = _score(artifacts=artifacts)
    assert score.layer(2).status == "skip"
    assert score.outcome == "pass"  # skip is visible, not fatal


def test_intent_mismatch_fails_when_exposed():
    artifacts = _good_artifacts(route={"task_intent": "diagnostic"})
    score = _score(artifacts=artifacts)
    assert score.layer(2).status == "fail"


# --------------------------------------------------------------------------- #
# Scope and coverage                                                           #
# --------------------------------------------------------------------------- #
def test_chip_outside_scope_fails_purity():
    artifacts = _good_artifacts(
        entities=[{"id": "machine:99", "ref": "machine:11", "label": "Other"}]
    )
    score = _score(artifacts=artifacts)
    assert score.layer(3).status == "fail"
    assert "chip outside scope" in score.layer(3).detail


def test_stamped_scope_version_mismatch_fails_purity():
    artifacts = _good_artifacts(expected_scope_version=4)
    score = _score(artifacts=artifacts)
    assert score.layer(3).status == "fail"
    assert "stamped scope" in score.layer(3).detail


def test_validated_partial_never_satisfies_complete_population():
    turn = _turn(complete_population_required=True)
    evidence = dict(_good_artifacts().evidence_analysis)
    evidence["incomplete_reasons"] = [{"code": "deadline", "facet": "records"}]
    artifacts = _good_artifacts(response_state="partial", evidence_analysis=evidence)
    score = _score(turn=turn, case=_case(turn), artifacts=artifacts)
    assert score.layer(4).status == "fail"
    assert "complete_population not satisfied" in score.layer(4).detail


def test_population_below_returned_count_fails_coverage():
    evidence = dict(_good_artifacts().evidence_analysis)
    evidence["coverage"] = {
        "population_count": 10,
        "returned_count": 25,
        "complete_population": False,
    }
    score = _score(artifacts=_good_artifacts(evidence_analysis=evidence))
    assert score.layer(4).status == "fail"


# --------------------------------------------------------------------------- #
# Sources, citations, entities                                                 #
# --------------------------------------------------------------------------- #
def test_claim_citing_a_missing_ordinal_fails():
    evidence = dict(_good_artifacts().evidence_analysis)
    evidence["claims"] = [dict(evidence["claims"][0], citation_ordinals=[7])]
    score = _score(artifacts=_good_artifacts(evidence_analysis=evidence))
    assert score.layer(5).status == "fail"
    assert "missing ordinal" in score.layer(5).detail


def test_superseded_revision_against_gold_fails():
    gold = GoldAtoms(case_id="FB01", answerability="answerable", accepted_revisions=("B",))
    evidence = dict(_good_artifacts().evidence_analysis)
    evidence["citations"] = [
        {"ordinal": 1, "source_id": "SI3000-SM", "source_revision": "A", "available": True}
    ]
    score = _score(artifacts=_good_artifacts(evidence_analysis=evidence), gold=gold)
    assert score.layer(5).status == "fail"
    assert "outside gold" in score.layer(5).detail


def test_chip_outside_claim_evidence_fails():
    artifacts = _good_artifacts(
        entities=[{"id": "machine:11", "ref": "machine:44", "label": "Unrelated"}]
    )
    score = _score(artifacts=artifacts)
    assert score.layer(5).status == "fail"
    assert "outside validated claim evidence" in score.layer(5).detail


# --------------------------------------------------------------------------- #
# Named boundary assertions                                                    #
# --------------------------------------------------------------------------- #
def test_scope_version_drift_fails_scope_persisted():
    artifacts = _good_artifacts(post_scope_version=5)
    score = _score(artifacts=artifacts)
    assert score.layer(6).status == "fail"
    assert "scope version moved" in score.layer(6).detail


def test_uncited_answer_claim_fails_entailment():
    evidence = dict(_good_artifacts().evidence_analysis)
    evidence["claims"] = [
        {
            "claim_id": "c1",
            "claim_role": "answer",
            "evidence_classification": "",
            "citation_ordinals": [],
        }
    ]
    score = _score(artifacts=_good_artifacts(evidence_analysis=evidence))
    assert score.layer(6).status == "fail"


def test_a_proposal_row_fails_no_governed_effect():
    score = _score(artifacts=_good_artifacts(proposal_ids_delta=1))
    assert score.layer(6).status == "fail"
    assert "proposal row" in score.layer(6).detail


def test_refusal_word_cap_is_enforced():
    turn = _turn(expected_behavior_by_tier=((1, "refuse"),), expected_intent="")
    artifacts = TurnArtifacts(
        http_status=200,
        response_body={"workflow_used": "safety_refusal"},
        message_text="word " * 220,
        response_state="complete",
    )
    score = _score(turn=turn, case=_case(turn, required_assertions=()), artifacts=artifacts)
    assert score.layer(6).status == "fail"
    assert "Q86" in score.layer(6).detail


# --------------------------------------------------------------------------- #
# Judge layers                                                                 #
# --------------------------------------------------------------------------- #
GOLD = GoldAtoms(
    case_id="FB01",
    answerability="answerable",
    required_claims=("count_is_stated", "coverage_is_stated"),
    forbidden_claims=("root_cause_asserted",),
    calculations=(GoldCalculation(name="wo_count", value="28"),),
    required_facets=("records", "coverage"),
)


def test_judge_fold_passes_and_grants_facet_credit():
    def judge(question, gold, answer):
        return {
            "required_claims_present": {"records": True, "coverage": True},
            "forbidden_claims_absent": True,
            "calculations_within_tolerance": True,
            "no_overclaim": True,
        }

    score = _score(gold=GOLD, judge=judge, question_text="How many?")
    assert score.outcome == "pass"
    assert score.layer(7).status == "pass"
    assert dict(score.layer(7).facet_credit) == {"records": True, "coverage": True}


def test_missing_required_claims_earn_only_partial_credit():
    def judge(question, gold, answer):
        return {
            "required_claims_present": {"records": True, "coverage": False},
            "forbidden_claims_absent": True,
            "calculations_within_tolerance": True,
            "no_overclaim": True,
        }

    score = _score(gold=GOLD, judge=judge)
    assert score.outcome == "partial"
    assert score.layer(7).status == "partial"
    assert dict(score.layer(7).facet_credit) == {"records": True, "coverage": False}


def test_a_forbidden_claim_fails_semantics():
    def judge(question, gold, answer):
        return {
            "required_claims_present": {"records": True, "coverage": True},
            "forbidden_claims_absent": False,
            "no_overclaim": True,
        }

    score = _score(gold=GOLD, judge=judge)
    assert score.outcome == "fail"
    assert score.layer(7).status == "fail"


def test_overclaim_fails_uncertainty_discipline():
    def judge(question, gold, answer):
        return {
            "required_claims_present": {"records": True, "coverage": True},
            "forbidden_claims_absent": True,
            "calculations_within_tolerance": True,
            "no_overclaim": False,
        }

    score = _score(gold=GOLD, judge=judge)
    assert score.outcome == "fail"
    assert score.layer(8).status == "fail"


def test_partial_state_without_typed_reasons_fails_honesty():
    evidence = dict(_good_artifacts().evidence_analysis)
    evidence["incomplete_reasons"] = []
    artifacts = _good_artifacts(response_state="partial", evidence_analysis=evidence)
    score = _score(artifacts=artifacts)
    assert score.layer(8).status == "fail"
    assert "without typed incomplete_reasons" in score.layer(8).detail


def test_judge_layers_not_scored_without_calibration():
    score = _score(gold=GOLD, judge=None)
    assert score.layer(7).status == "not_scored"
    assert "uncalibrated" in score.layer(7).detail
    assert score.outcome == "pass"  # deterministic layers still decide


# --------------------------------------------------------------------------- #
# Resolution building                                                          #
# --------------------------------------------------------------------------- #
def test_resolution_from_manifest_merges_ids_and_markers():
    manifest = {
        "solar_a": {"kind": "machine"},
        "hx200": {"kind": "machine", "markers": ["HX-200"]},
        "supplier": {"kind": "marker", "markers": ["supplier"]},
    }
    turn = _turn(forbidden_entity_fixture_keys=("hx200", "supplier"))
    case = _case(turn)
    resolution = resolution_from_manifest(
        manifest, case, turn, resolved_ids={"solar_a": ("machine:11",), "hx200": ("machine:99",)}
    )
    assert resolution.scope_ids == frozenset({"machine:11"})
    assert resolution.forbidden["hx200"] == (("machine:99",), ("HX-200",))
    assert resolution.forbidden["supplier"] == ((), ("supplier",))


def test_m1_allows_typed_429s_via_service_statuses():
    turn = _turn(expected_intent="")
    case = _case(turn, service=ScenarioService(allowed_statuses=(200, 429)), required_assertions=())
    artifacts = TurnArtifacts(http_status=429, message_text="")
    score = _score(turn=turn, case=case, artifacts=artifacts)
    assert score.layer(1).status == "pass"
