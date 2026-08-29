"""Unit tests for the S10 deterministic renderer (WP-A2)."""

from __future__ import annotations

import json
import re

import pytest
from ai.core.analysis.evidence import (
    EvidenceStore,
    FactValue,
    coverage_fact,
    facts_from_work_order_row,
)
from ai.core.analysis.renderer import (
    RENDER_TEMPLATES,
    RenderError,
    assign_ordinals,
    render_answer,
)
from ai.core.analysis.schemas import AnalysisClaim, IncompleteReason

AS_OF = "2026-08-27T12:00:00+00:00"

_DIGIT_RE = re.compile(r"\d")
_CODE_SHAPED_RE = re.compile(r"\b[A-Z]{2,}-\w+\b|\b\w+_\w+\b")


def _store_with_records() -> tuple[EvidenceStore, str, str, str]:
    store = EvidenceStore()
    fact_id = facts_from_work_order_row(
        store,
        {
            "work_order_id": 41,
            "reference": "WO-0041",
            "title": "Replace coolant filter",
            "board_status": "in_progress",
            "lifecycle_status": "released",
            "work_order_type": "corrective",
            "priority": "high",
            "machine_id": 12,
            "machine": "Feed Pump East",
            "due_date": "2026-09-01",
            "created_at": "2026-08-20T08:00:00+00:00",
            "updated_at": "2026-08-21T08:00:00+00:00",
            "actual_started_at": None,
            "actual_completed_at": None,
        },
        retrieval_id="ret_abc",
        as_of=AS_OF,
        source_revision="snap_1",
    )
    coverage_id = coverage_fact(
        store,
        {"population_count": 2, "returned_count": 2, "complete_population": True},
        retrieval_id="ret_abc",
        source_class="work_order",
        as_of=AS_OF,
    )
    pending = store.open_evidence_set(
        source_class="work_order",
        filters={"machine_ids": [12]},
        population_count=2,
        evaluated_count=2,
        complete_population=True,
        calculation={"operation": "count", "result": "2"},
    )
    pending.add_member("work_order", "41")
    pending.add_member("work_order", "42")
    calc_id = store.add_calculation(
        operation="count",
        input_refs=(fact_id, coverage_id),
        values={"count": FactValue("int", 2)},
        evidence_set_handle=pending.handle,
        complete_population=True,
    )
    return store, fact_id, calc_id, pending.handle


def _claim(**overrides) -> AnalysisClaim:
    base = {
        "claim_id": "c1",
        "claim_role": "answer",
        "claim_type": "calculation",
        "evidence_classification": "calculated",
        "fact_refs": [],
        "calculation_output_refs": [],
        "evidence_refs": [],
        "entity_refs": [],
        "render_template": "analysis.record_count",
        "paraphrase": "",
    }
    base.update(overrides)
    return AnalysisClaim.model_validate_json(json.dumps(base))


def test_template_literals_contain_no_digits_or_code_shaped_tokens() -> None:
    """The closure scan is sound only if templates author no values."""
    sentinel = "SLOTVALUE"
    for template in RENDER_TEMPLATES.values():
        slots = dict.fromkeys(template.required_slots, sentinel)
        text = template.build(slots, "", "", "en")
        stripped = text.replace(sentinel, "")
        assert not _DIGIT_RE.search(stripped), (template.key, stripped)
        assert not _CODE_SHAPED_RE.search(stripped), (template.key, stripped)


def test_ordinals_are_first_citation_order_and_deduped() -> None:
    store, fact_id, calc_id, handle = _store_with_records()
    claims = [
        _claim(claim_id="c1", calculation_output_refs=[calc_id]),
        _claim(
            claim_id="c2",
            claim_type="direct_source_fact",
            evidence_classification="documented",
            fact_refs=[fact_id],
            render_template="analysis.record_line",
        ),
        # c3 cites the SAME set as c1 -> same ordinal, no new entry.
        _claim(claim_id="c3", calculation_output_refs=[calc_id]),
    ]
    manifest = assign_ordinals(claims, store, default_as_of=AS_OF)
    assert [entry.ordinal for entry in manifest.entries] == [1, 2]
    assert manifest.by_claim["c1"] == (1,)
    assert manifest.by_claim["c2"] == (2,)
    assert manifest.by_claim["c3"] == (1,)
    set_entry = manifest.entries[0]
    assert set_entry.evidence_set_id == handle
    assert set_entry.calculation == "count: 2"
    assert set_entry.claim_ids == ("c1", "c3")
    fact_entry = manifest.entries[1]
    assert fact_entry.source_id == "41"
    assert fact_entry.controlled is False


def test_render_inserts_values_and_markers_and_indexes_them() -> None:
    store, fact_id, calc_id, _ = _store_with_records()
    claims = [
        _claim(claim_id="c1", calculation_output_refs=[calc_id]),
        _claim(
            claim_id="c2",
            claim_type="direct_source_fact",
            evidence_classification="documented",
            fact_refs=[fact_id],
            render_template="analysis.record_line",
        ),
    ]
    manifest = assign_ordinals(claims, store, default_as_of=AS_OF)
    rendered = render_answer(claims, store, ordinals=manifest, locale="en")

    assert "2 matching records" in rendered.detailed_response
    assert "[1]" in rendered.detailed_response
    assert "WO-0041" in rendered.detailed_response
    assert {"2", "WO-0041", "[1]", "[2]"} <= rendered.inserted_values
    assert [segment.claim_id for segment in rendered.segments] == ["c1", "c2"]
    # Spoken: answer-role segments only, markers stripped, plain text.
    assert rendered.spoken_summary
    assert "[1]" not in rendered.spoken_summary
    # The manifest wire shape matches the generated contract keys.
    entry = rendered.citation_manifest[0]
    assert set(entry) == {
        "ordinal",
        "source_type",
        "source_id",
        "source_title",
        "source_revision",
        "source_class",
        "controlled",
        "as_of",
        "available",
        "locator",
        "applicability",
        "evidence_set_id",
        "calculation",
    }


def test_partial_state_appends_notice_and_suppresses_spoken() -> None:
    store, fact_id, _, _ = _store_with_records()
    claims = [
        _claim(
            claim_id="c2",
            claim_type="direct_source_fact",
            evidence_classification="documented",
            fact_refs=[fact_id],
            render_template="analysis.record_line",
        ),
    ]
    manifest = assign_ordinals(claims, store, default_as_of=AS_OF)
    rendered = render_answer(
        claims,
        store,
        ordinals=manifest,
        locale="en",
        state="partial",
        incomplete_reasons=[IncompleteReason(code="retrieval_timeout", facet="limitations")],
    )
    assert "partial answer" in rendered.detailed_response
    assert "limitations" in rendered.detailed_response
    assert rendered.spoken_summary == ""


def test_missing_slot_and_unknown_template_raise_render_error() -> None:
    store, _, calc_id, _ = _store_with_records()
    unknown = _claim(
        claim_type="unknown",
        evidence_classification="insufficient",
        render_template="analysis.nonexistent",
    )
    with pytest.raises(RenderError, match="unknown render template"):
        render_answer([unknown], store, ordinals=assign_ordinals([], store))
    # record_line needs a fact with reference/status slots; a calc ref
    # cannot satisfy them.
    bad = _claim(
        claim_id="c9",
        calculation_output_refs=[calc_id],
        render_template="analysis.record_line",
    )
    with pytest.raises(RenderError, match="slot"):
        render_answer([bad], store, ordinals=assign_ordinals([bad], store))


def test_downgrade_and_abstention_templates_render_deterministic_text() -> None:
    store = EvidenceStore()
    downgrade = _claim(
        claim_id="c1",
        claim_role="limitation",
        claim_type="limitation",
        evidence_classification="insufficient",
        render_template="analysis.downgrade_limitation",
    )
    manifest = assign_ordinals([downgrade], store)
    rendered = render_answer([downgrade], store, ordinals=manifest, locale="en")
    assert "could not be verified" in rendered.detailed_response

    spanish = render_answer([downgrade], store, ordinals=manifest, locale="es")
    assert "verificarse" in spanish.detailed_response
