"""S7 WP-C5: aggregate/trend synthesis, iterating tables, typed messages.

End-to-end ``run_analysis`` over deterministic seam fakes (the real ORM
bodies are pinned by ``test_analysis_snapshot`` and the tasks suite), plus
direct renderer pins for the one-claim table contract: every cell is a
server-inserted value (C05 closes over the whole table by construction)
and fenced operator labels never enter rendered text.
"""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.analysis import executor as executor_module
from ai.core.analysis.evidence import (
    EvidenceStore,
    FactValue,
    fact_from_dataset_profile,
    facts_from_group_rows,
)
from ai.core.analysis.executor import run_analysis
from ai.core.analysis.renderer import assign_ordinals, render_answer
from ai.core.analysis.snapshot import AnalysisRetrievalIncomplete
from ai.core.analysis.synthesis import deterministic_claims
from ai.core.analysis.validator import check_value_closure
from ai.core.tests.test_analysis_executor import RecordingEmitter, _run, _service

AS_OF = "2026-08-28T00:00:00+00:00"

FENCED_PUMP = "[UNTRUSTED-CONTENT-BEGIN]\nFeed Pump 7\n[UNTRUSTED-CONTENT-END]"


def fake_aggregate(user, store, *, scope, query, run):
    """Deterministic stand-in mirroring the real ``_retrieve_aggregate``."""
    profile_fact = fact_from_dataset_profile(
        store,
        {
            "population_type": "work_orders",
            "population_count": 3,
            "null_date_count": 0,
            "unassigned_machine_count": 1,
            "distinct_machine_count": 2,
            "date_field": "created_at",
            "timezone": "UTC",
            "complete_population": True,
            "high_watermark": "2026-08-27T00:00:00+00:00",
        },
        retrieval_id="ret_agg",
        as_of=AS_OF,
    )
    group_facts = facts_from_group_rows(
        store,
        [
            {"key": 12, "label": FENCED_PUMP, "group_count": 2},
            {"key": 15, "label": "Inverter Hall", "group_count": 1},
        ],
        retrieval_id="ret_agg",
        as_of=AS_OF,
        source_class="work_order",
        source_revision="h" * 64,
        grouping="machine",
    )
    pending = store.open_evidence_set(
        source_class="work_order",
        filters={"date_field": "created_at"},
        population_count=3,
        evaluated_count=3,
        complete_population=True,
        snapshot_hash="h" * 64,
        calculation={"operation": "group_count", "result": "2"},
    )
    for pk in ("41", "42", "43"):
        pending.add_member("work_order", pk, "v1")
    pending.displayed_count = 2
    store.add_calculation(
        operation="group_count",
        input_refs=(profile_fact, *group_facts),
        values={
            "grouping": FactValue("enum", "machine"),
            "population_count": FactValue("int", 3),
            "total_group_count": FactValue("int", 2),
            "shown_group_count": FactValue("int", 2),
            "remainder_group_count": FactValue("int", 0),
            "remainder_count": FactValue("int", 0),
            "unassigned_machine_count": FactValue("int", 1),
        },
        evidence_set_handle=pending.handle,
        complete_population=True,
    )
    store.record_envelope({"retrieval_id": "ret_agg", "source_class": "work_order"})
    store.set_primary_coverage({
        "population_count": 3,
        "returned_count": 2,
        "complete_population": True,
        "display_truncated": False,
        "date_field": "created_at",
        "timezone": "UTC",
        "filters": [],
        "as_of": AS_OF,
        "snapshot_label": None,
        "excluded_null_date_count": None,
        "incomplete_reason": None,
    })
    return {}


def fake_trend(user, store, *, scope, query, run):
    """Deterministic stand-in mirroring the real ``_retrieve_trend``."""
    bucket_facts = facts_from_group_rows(
        store,
        [
            {"bucket": "2026-01-01", "group_count": 1},
            {"bucket": "2026-02-01", "group_count": 0},
            {"bucket": "2026-03-01", "group_count": 1},
        ],
        retrieval_id="ret_tl",
        as_of=AS_OF,
        source_class="work_order",
        source_revision="h" * 64,
        grouping="bucket",
    )
    pending = store.open_evidence_set(
        source_class="work_order",
        filters={"date_field": "created_at"},
        population_count=2,
        evaluated_count=2,
        complete_population=True,
        snapshot_hash="h" * 64,
        calculation={"operation": "bucket_count", "result": "3"},
    )
    for pk in ("41", "43"):
        pending.add_member("work_order", pk, "v1")
    pending.displayed_count = 3
    store.add_calculation(
        operation="bucket_count",
        input_refs=tuple(bucket_facts),
        values={
            "bucket": FactValue("enum", "month"),
            "population_count": FactValue("int", 2),
            "bucket_count": FactValue("int", 3),
            "null_date_count": FactValue("int", 1),
        },
        evidence_set_handle=pending.handle,
        complete_population=True,
    )
    store.record_envelope({"retrieval_id": "ret_tl", "source_class": "work_order"})
    store.set_primary_coverage({
        "population_count": 2,
        "returned_count": 3,
        "complete_population": True,
        "display_truncated": False,
        "date_field": "created_at",
        "timezone": "UTC",
        "filters": [],
        "as_of": AS_OF,
        "snapshot_label": None,
        "excluded_null_date_count": 1,
        "incomplete_reason": None,
    })
    return {}


@pytest.fixture(autouse=True)
def _seams(monkeypatch):
    monkeypatch.setattr(executor_module, "_retrieve_aggregate", fake_aggregate)
    monkeypatch.setattr(executor_module, "_retrieve_trend", fake_trend)
    monkeypatch.setattr(executor_module, "synthesize_claims", lambda *_a, **_k: None)
    monkeypatch.setattr(executor_module, "_reauthorize", lambda _user, _store: True)


def _outcome(intent: str):
    return asyncio.run(run_analysis(_service(), _run(intent=intent, emitter=RecordingEmitter())))


class TestAggregateAnswer:
    def test_validated_table_answer(self) -> None:
        outcome = _outcome("fleet_aggregate")
        assert outcome.turn_state == "complete"
        text = outcome.response.detailed_response
        assert "The complete population is 3 records by created_at (UTC)" in text
        assert "Breakdown by machine across 2 groups, 3 records" in text
        assert "- 12: 2" in text
        assert "- 15: 1" in text
        assert outcome.gate["verdict"] == "pass"
        assert len(outcome.evidence_set_specs) == 1
        assert len(outcome.evidence_set_specs[0]["members"]) == 3

    def test_fenced_labels_never_enter_rendered_text(self) -> None:
        # An operator-authored machine name (digits included) stays out of
        # the answer body; chips and expansion carry names.
        outcome = _outcome("fleet_aggregate")
        assert "Feed Pump" not in outcome.response.detailed_response
        assert "UNTRUSTED-CONTENT" not in outcome.response.detailed_response

    def test_unassigned_note_renders(self) -> None:
        outcome = _outcome("fleet_aggregate")
        assert "1 work orders have no machine assigned" in outcome.response.detailed_response

    def test_table_stays_out_of_spoken_summary(self) -> None:
        outcome = _outcome("fleet_aggregate")
        assert "- 12:" not in outcome.response.spoken_summary
        assert "complete population is 3 records" in outcome.response.spoken_summary

    def test_machine_chips_come_from_group_rows(self) -> None:
        outcome = _outcome("fleet_aggregate")
        pks = {entity["pk"] for entity in outcome.entities if entity["model"] == "assetmachine"}
        assert pks == {12, 15}


class TestTrendAnswer:
    def test_validated_series_answer_zero_filled(self) -> None:
        outcome = _outcome("trend_analysis")
        text = outcome.response.detailed_response
        assert outcome.turn_state == "complete"
        assert "Series by month over 3 buckets, 2 records" in text
        assert "- 2026-02-01: 0" in text
        assert "1 records have no value for the selected date field" in text
        assert outcome.gate["verdict"] == "pass"

    def test_membership_backs_the_series(self) -> None:
        outcome = _outcome("trend_analysis")
        spec = outcome.evidence_set_specs[0]
        assert spec["complete_population"] is True
        assert len(spec["members"]) == 2


class TestTypedUnavailability:
    def test_each_code_renders_its_named_message(self, monkeypatch) -> None:
        expectations = {
            "grouping_unavailable": "defensible grouping",
            "bucket_range_exceeded": "more time buckets",
            "population_cap_exceeded": "exact-analysis envelope",
            "snapshot_changed": "single consistent snapshot",
        }
        for code, phrase in expectations.items():

            def _refuse(user, store, *, scope, query, run, code=code):
                raise AnalysisRetrievalIncomplete(code)

            monkeypatch.setattr(executor_module, "_retrieve_aggregate", _refuse)
            outcome = _outcome("fleet_aggregate")
            assert outcome.turn_state == "incomplete", code
            assert phrase in outcome.response.detailed_response, code
            codes = {reason.code for reason in outcome.response.incomplete_reasons}
            assert codes == {code}
            assert outcome.evidence_set_specs == []


class TestRendererTableContract:
    def _store_with_table(self, *, remainder: int = 0):
        store = EvidenceStore()
        facts = facts_from_group_rows(
            store,
            [
                {"key": "high", "group_count": 3},
                {"key": "INV-7", "group_count": 2},
            ],
            retrieval_id="ret_x",
            as_of=AS_OF,
            source_class="work_order",
            source_revision="r",
            grouping="priority",
        )
        calc = store.add_calculation(
            operation="group_count",
            input_refs=tuple(facts),
            values={
                "grouping": FactValue("enum", "priority"),
                "population_count": FactValue("int", 5 + remainder),
                "total_group_count": FactValue("int", 2 + (1 if remainder else 0)),
                "shown_group_count": FactValue("int", 2),
                "remainder_group_count": FactValue("int", 1 if remainder else 0),
                "remainder_count": FactValue("int", remainder),
            },
            complete_population=True,
        )
        return store, facts, calc

    def _table_claim(self, facts, calc):
        from ai.core.analysis.schemas import AnalysisClaim

        return AnalysisClaim.model_validate_json(
            json.dumps({
                "claim_id": "c1",
                "claim_role": "answer",
                "claim_type": "calculation",
                "evidence_classification": "calculated",
                "fact_refs": list(facts),
                "calculation_output_refs": [calc],
                "evidence_refs": [],
                "entity_refs": [],
                "render_template": "analysis.group_breakdown",
                "paraphrase": "",
            })
        )

    def test_every_table_cell_is_value_closed(self) -> None:
        store, facts, calc = self._store_with_table()
        claims = [self._table_claim(facts, calc)]
        rendered = render_answer(
            claims, store, ordinals=assign_ordinals(claims, store, default_as_of=AS_OF)
        )
        assert "- high: 3" in rendered.detailed_response
        assert "- INV-7: 2" in rendered.detailed_response
        results = check_value_closure(claims, rendered)
        assert all(result.code == "value_closure" for result in results)

    def test_remainder_row_is_server_counted(self) -> None:
        store, facts, calc = self._store_with_table(remainder=4)
        claims = [self._table_claim(facts, calc)]
        rendered = render_answer(
            claims, store, ordinals=assign_ordinals(claims, store, default_as_of=AS_OF)
        )
        assert "plus 4 records across 1 further groups" in rendered.detailed_response
        results = check_value_closure(claims, rendered)
        assert all(result.code == "value_closure" for result in results)


class TestDeterministicBranches:
    def test_fleet_aggregate_claim_shapes(self) -> None:
        store = EvidenceStore()
        fake_aggregate(None, store, scope=None, query="", run=SimpleNamespace())
        facets, claims = deterministic_claims("fleet_aggregate", store)
        by_facet = {facet.name: facet for facet in facets}
        assert str(by_facet["profile"].status) == "answered"
        assert str(by_facet["aggregate"].status) == "answered"
        assert str(by_facet["limitations"].status) == "answered"  # unassigned=1
        templates = [claim.render_template for claim in claims]
        assert templates.count("analysis.group_breakdown") == 1
        table_claim = next(
            claim for claim in claims if claim.render_template == "analysis.group_breakdown"
        )
        assert len(table_claim.fact_refs) == 2
        assert table_claim.entity_refs == ["machine:12", "machine:15"]

    def test_trend_claim_shapes(self) -> None:
        store = EvidenceStore()
        fake_trend(None, store, scope=None, query="", run=SimpleNamespace())
        facets, claims = deterministic_claims("trend_analysis", store)
        by_facet = {facet.name: facet for facet in facets}
        assert str(by_facet["timeline"].status) == "answered"
        assert str(by_facet["limitations"].status) == "answered"  # null_date_count=1
        templates = [claim.render_template for claim in claims]
        assert templates == ["analysis.timeline_breakdown", "analysis.null_date_note"]
