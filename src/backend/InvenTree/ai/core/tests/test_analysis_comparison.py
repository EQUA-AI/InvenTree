"""S9 WP-C12: deterministic statuses, the comparison answer, C09's new rule.

The gate itself is pinned Django-side (``aichat/tests/test_comparison_gate``);
here the derivation is pure and the executor runs over seam fakes.
"""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.analysis import executor as executor_module
from ai.core.analysis.comparison import ComparisonCandidate, derive_step_statuses
from ai.core.analysis.evidence import (
    EvidenceStore,
    FactValue,
    fact_from_procedure_application,
    facts_from_group_rows,
    facts_from_work_order_row,
)
from ai.core.analysis.executor import run_analysis
from ai.core.analysis.snapshot import AnalysisRetrievalIncomplete
from ai.core.analysis.validator import CheckOutcome, check_contradiction
from ai.core.tests.test_analysis_executor import RecordingEmitter, _run, _service

AS_OF = "2026-08-28T00:00:00+00:00"


def _candidate(*, steps, deviations=(), route="structured"):
    return ComparisonCandidate(
        work_order_id=41,
        route=route,
        evidence={"work_order": {"work_order_id": 41}},
        application={"application_id": 7} if route == "structured" else None,
        steps={"steps": list(steps), "population_count": len(steps)}
        if route == "structured"
        else None,
        deviations={"deviations": list(deviations)},
        manual_document=None if route == "structured" else object(),
        drift=False,
        completed_at="2026-01-15T12:00:00",
    )


def _step(sequence, *, status="completed", passed=True, step_key=None):
    return {
        "step_key": step_key or f"key-{sequence}",
        "sequence": sequence,
        "status": status,
        "passed": passed,
    }


class TestStatusDerivation:
    def test_present_records_drive_every_status(self) -> None:
        candidate = _candidate(
            steps=[
                _step(1),
                _step(2, status="failed", passed=False),
                _step(3, status="pending", passed=None),
                _step(4, status="not_applicable", passed=None),
                _step(5, step_key="dev-step"),
            ],
            deviations=[{"step_key": "dev-step"}],
        )
        result = derive_step_statuses(candidate)
        assert result["total_steps"] == 5
        assert result["counts"]["documented_match"] == 1
        assert result["counts"]["documented_deviation"] == 2  # failed + explicit
        assert result["counts"]["not_recorded"] == 1
        assert result["counts"]["not_applicable"] == 1
        assert [row["status"] for row in result["rows"]] == [
            "documented_match",
            "documented_deviation",
            "not_recorded",
            "not_applicable",
            "documented_deviation",
        ]

    def test_prose_route_is_cannot_determine_heavy(self) -> None:
        candidate = _candidate(steps=[], route="verified_manual")
        result = derive_step_statuses(candidate)
        assert result["counts"]["cannot_determine"] == 1
        assert result["rows"] == []


def fake_comparison(user, store, *, scope, query, run):
    """Seam fake mirroring the real `_retrieve_comparison` store writes."""
    wo_fact = facts_from_work_order_row(
        store,
        {
            "work_order_id": 41,
            "reference": "WO-000041",
            "title": "Structured corrective",
            "board_status": "done",
            "lifecycle_status": "completed",
            "work_order_type": "corrective",
            "priority": "medium",
            "machine_id": 12,
            "machine": "Feed Pump",
            "due_date": None,
            "created_at": "2026-01-10T08:00:00+00:00",
            "updated_at": "2026-01-15T12:00:00+00:00",
            "actual_started_at": "2026-01-15T08:00:00+00:00",
            "actual_completed_at": "2026-01-15T12:00:00+00:00",
        },
        retrieval_id="ret_cmp",
        as_of=AS_OF,
        source_revision="snap_t",
    )
    fact_from_procedure_application(
        store,
        {
            "application_id": 7,
            "procedure_code": "PROC-9",
            "procedure_name": "Gate procedure",
            "revision": 3,
            "content_hash": "c" * 64,
            "drift_status": "current",
            "applied_at": "2026-01-15T08:00:00+00:00",
            "step_count": 3,
        },
        retrieval_id="ret_cmp",
        as_of=AS_OF,
    )
    step_facts = facts_from_group_rows(
        store,
        [
            {"key": "1", "status": "documented_match"},
            {"key": "2", "status": "documented_deviation"},
            {"key": "3", "status": "not_recorded"},
        ],
        retrieval_id="ret_cmp",
        as_of=AS_OF,
        source_class="step_execution",
        source_revision="c" * 64,
        grouping="comparison_step",
    )
    pending = store.open_evidence_set(
        source_class="work_order",
        filters={"rule": "most_recent_completed_corrective"},
        population_count=1,
        evaluated_count=1,
        complete_population=True,
        snapshot_hash="h" * 64,
        calculation={"operation": "comparison_statuses", "result": "3"},
    )
    pending.add_member("work_order", "41", "v1")
    pending.displayed_count = 1
    store.add_calculation(
        operation="comparison_statuses",
        input_refs=(wo_fact, *step_facts),
        values={
            "documented_match": FactValue("int", 1),
            "documented_deviation": FactValue("int", 1),
            "possible_documented_alignment": FactValue("int", 0),
            "not_recorded": FactValue("int", 1),
            "not_applicable": FactValue("int", 0),
            "cannot_determine": FactValue("int", 0),
            "total_steps": FactValue("int", 3),
            "drift": FactValue("bool", False),
        },
        evidence_set_handle=pending.handle,
        complete_population=True,
    )
    store.record_envelope({"retrieval_id": "ret_cmp", "source_class": "work_order"})
    store.set_primary_coverage({
        "population_count": 1,
        "returned_count": 1,
        "complete_population": True,
        "display_truncated": False,
        "date_field": "actual_completed_at",
        "timezone": "UTC",
        "filters": [],
        "as_of": AS_OF,
        "snapshot_label": None,
        "excluded_null_date_count": None,
        "incomplete_reason": None,
    })
    return {}


@pytest.fixture(autouse=True)
def _seams(monkeypatch):
    monkeypatch.setattr(executor_module, "_retrieve_comparison", fake_comparison)
    monkeypatch.setattr(executor_module, "synthesize_claims", lambda *_a, **_k: None)
    monkeypatch.setattr(executor_module, "_reauthorize", lambda _user, _store: True)


def _outcome(intent: str = "manual_wo_comparison"):
    return asyncio.run(run_analysis(_service(), _run(intent=intent, emitter=RecordingEmitter())))


class TestComparisonAnswer:
    def test_validated_comparison_answer(self) -> None:
        outcome = _outcome()
        assert outcome.turn_state == "complete"
        text = outcome.response.detailed_response
        assert "WO-000041 was compared against PROC-9 revision 3" in text
        assert "1 of 3 steps are documented as performed" in text
        assert "- step 2: documented_deviation" in text
        assert outcome.gate["verdict"] == "pass"

    def test_not_recorded_is_pinned_as_not_noncompliance(self) -> None:
        outcome = _outcome()
        assert "Absence of a record is not noncompliance" in outcome.response.detailed_response

    def test_compliance_boundary_is_always_rendered(self) -> None:
        outcome = _outcome()
        assert (
            "Compliance verdicts are not produced by this system"
            in outcome.response.detailed_response
        )

    def test_gate_unmet_names_the_missing_facets(self, monkeypatch) -> None:
        def _refuse(user, store, *, scope, query, run):
            raise AnalysisRetrievalIncomplete(
                "comparison_gate_unmet",
                facets=("manual_passage", "no_procedure_or_verified_manual"),
            )

        monkeypatch.setattr(executor_module, "_retrieve_comparison", _refuse)
        outcome = _outcome()
        assert outcome.turn_state == "incomplete"
        assert "comparison needs specific evidence" in outcome.response.detailed_response
        reasons = {(reason.code, reason.facet) for reason in outcome.response.incomplete_reasons}
        assert reasons == {
            ("comparison_gate_unmet", "manual_passage"),
            ("comparison_gate_unmet", "no_procedure_or_verified_manual"),
        }
        assert outcome.evidence_set_specs == []


class TestComparisonContradictionRule:
    def test_comparison_claim_without_the_tally_downgrades(self) -> None:
        # The claim schema already forces SOME calculation ref; the C09
        # rule catches the subtler poison — a comparison claim citing a
        # calculation that is NOT the deterministic status tally.
        store = EvidenceStore()
        wrong_basis = store.add_calculation(
            operation="count",
            input_refs=(),
            values={"count": FactValue("int", 3)},
            complete_population=True,
        )
        from ai.core.analysis.schemas import AnalysisClaim

        claims = [
            AnalysisClaim.model_validate_json(
                json.dumps({
                    "claim_id": "c1",
                    "claim_role": "answer",
                    "claim_type": "calculation",
                    "evidence_classification": "calculated",
                    "fact_refs": [],
                    "calculation_output_refs": [wrong_basis],
                    "evidence_refs": [],
                    "entity_refs": [],
                    "render_template": "analysis.comparison_summary",
                    "paraphrase": "",
                })
            )
        ]
        codes = [result.code for result in check_contradiction(claims, store)]
        assert "comparison_without_statuses" in codes

    def test_comparison_claim_with_the_tally_passes(self) -> None:
        store = EvidenceStore()
        calc = store.add_calculation(
            operation="comparison_statuses",
            input_refs=(),
            values={"total_steps": FactValue("int", 3)},
            complete_population=True,
        )
        from ai.core.analysis.schemas import AnalysisClaim

        claims = [
            AnalysisClaim.model_validate_json(
                json.dumps({
                    "claim_id": "c1",
                    "claim_role": "answer",
                    "claim_type": "calculation",
                    "evidence_classification": "calculated",
                    "fact_refs": [],
                    "calculation_output_refs": [calc],
                    "evidence_refs": [],
                    "entity_refs": [],
                    "render_template": "analysis.comparison_summary",
                    "paraphrase": "",
                })
            )
        ]
        results = check_contradiction(claims, store)
        assert all(result.outcome is CheckOutcome.PASS for result in results)
