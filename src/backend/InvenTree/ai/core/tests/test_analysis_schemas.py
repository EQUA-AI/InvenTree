"""Contract tests for the canonical evidence-analysis response v2 (S10, WP-A1)."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from ai.core.analysis.schemas import (
    MAX_PARAPHRASE_CHARS,
    AnalysisClaim,
    CanonicalEvidenceAnalysisResponseV2,
    CanonicalTurnResponseV1,
    EvidenceAnalysisPayload,
    SynthesisClaimSet,
    SynthesisClaimSlot,
    parse_canonical_response,
)
from pydantic import ValidationError


def _spec_example() -> dict[str, Any]:
    """The §7.5 example shape, with placeholders swapped for plain prose.

    The spec's angle-bracket placeholders (``<server-rendered ...>``) would
    trip the plain-text spoken-summary check exactly as markup should; the
    structured parts are byte-faithful to the plan example.
    """
    return {
        "kind": "evidence_analysis",
        "response_version": 2,
        "response_state": "complete",
        "detailed_response": "Two work orders match the current scope. [1]",
        "spoken_summary": "Two work orders match the current scope.",
        "reasoning_summary": "Counted from the retrieved work-order population.",
        "evidence": [
            {
                "source_type": "work_order_population",
                "source_id": "set_1",
                "source_revision": "snap_abc",
                "locator": {"field": "population"},
                "as_of": "2026-08-26T00:00:00Z",
                "authorization_class": "maintenance_authorized",
                "claim": "c1",
            },
            {
                "source_type": "retrieval_coverage",
                "source_id": "ret_1",
                "source_revision": "snap_abc",
                "locator": {"field": "coverage"},
                "as_of": "2026-08-26T00:00:00Z",
                "authorization_class": "maintenance_authorized",
                "claim": "c2",
            },
        ],
        "next_questions": [],
        "recommended_actions": [],
        "safety_boundary": "Advisory; verify the cited source before action.",
        "speak": True,
        "payload": {
            "payload_type": "evidence_analysis_v2",
            "facets": [
                {"name": "record_count", "status": "answered", "claim_ids": ["c1"]},
                {"name": "limitations", "status": "answered", "claim_ids": ["c2"]},
            ],
            "claims": [
                {
                    "claim_id": "c1",
                    "claim_role": "answer",
                    "claim_type": "calculation",
                    "evidence_classification": "calculated",
                    "fact_refs": [],
                    "calculation_output_refs": ["calc_1"],
                    "evidence_refs": ["set_1"],
                    "entity_refs": ["machine:12", "machine:13"],
                    "render_template": "analysis.record_count",
                    "paraphrase": "",
                },
                {
                    "claim_id": "c2",
                    "claim_role": "limitation",
                    "claim_type": "limitation",
                    "evidence_classification": "insufficient",
                    "fact_refs": ["coverage_1"],
                    "calculation_output_refs": [],
                    "evidence_refs": ["ret_1"],
                    "entity_refs": [],
                    "render_template": "analysis.coverage_limitation",
                    "paraphrase": "",
                },
            ],
            "assumptions": [],
            "inclusion_rules": [],
            "exclusion_rules": [],
            "unknowns": [],
        },
        "incomplete_reasons": [],
    }


def _validate(payload: dict[str, Any]) -> CanonicalEvidenceAnalysisResponseV2:
    return CanonicalEvidenceAnalysisResponseV2.model_validate_json(json.dumps(payload))


def test_spec_example_validates_and_round_trips() -> None:
    response = _validate(_spec_example())
    assert response.response_version == 2
    assert response.payload.claims[0].evidence_classification == "calculated"
    dumped = response.model_dump(mode="json")
    assert _validate(dumped).model_dump(mode="json") == dumped


def test_v2_has_no_confidence_field_and_rejects_one() -> None:
    """Improvement 9: evidence classification REPLACES model confidence."""
    assert "confidence" not in CanonicalEvidenceAnalysisResponseV2.model_fields
    poisoned = _spec_example()
    poisoned["confidence"] = "high"
    with pytest.raises(ValidationError):
        _validate(poisoned)


def test_discriminated_union_routes_both_versions() -> None:
    v2 = parse_canonical_response(_spec_example())
    assert isinstance(v2, CanonicalEvidenceAnalysisResponseV2)

    v1_payload = {
        "kind": "repair_diagnosis",
        "response_version": 1,
        "response_state": "complete",
        "detailed_response": "Packet 123 records drive-end vibration for review.",
        "spoken_summary": "",
        "reasoning_summary": "Packet evidence only.",
        "confidence": "low",
        "evidence": [],
        "next_questions": [],
        "recommended_actions": [],
        "safety_boundary": "No safety boundary.",
        "speak": False,
    }
    v1 = parse_canonical_response(v1_payload)
    assert isinstance(v1, CanonicalTurnResponseV1)

    unknown = _spec_example()
    unknown["response_version"] = 3
    with pytest.raises(ValidationError):
        parse_canonical_response(unknown)


def test_partial_state_requires_typed_reasons() -> None:
    partial = _spec_example()
    partial["response_state"] = "partial"
    partial["speak"] = False
    partial["spoken_summary"] = ""
    with pytest.raises(ValidationError, match="typed reasons"):
        _validate(partial)

    partial["incomplete_reasons"] = [{"code": "retrieval_timeout", "facet": "limitations"}]
    response = _validate(partial)
    assert response.incomplete_reasons[0].code == "retrieval_timeout"


def test_complete_state_forbids_incomplete_reasons() -> None:
    payload = _spec_example()
    payload["incomplete_reasons"] = [{"code": "retrieval_timeout", "facet": "limitations"}]
    with pytest.raises(ValidationError, match="cannot carry incomplete reasons"):
        _validate(payload)


def test_non_complete_states_never_speak_or_recommend() -> None:
    for override in (
        {"speak": True},
        {"spoken_summary": "A summary."},
        {
            "recommended_actions": [
                {
                    "kind": "read_only",
                    "title": "Review",
                    "detail": "Open the record.",
                    "requires_approval": False,
                }
            ]
        },
    ):
        payload = _spec_example()
        payload["response_state"] = "partial"
        payload["speak"] = False
        payload["spoken_summary"] = ""
        payload["incomplete_reasons"] = [{"code": "retrieval_timeout", "facet": "limitations"}]
        payload.update(deepcopy(override))
        with pytest.raises(ValidationError):
            _validate(payload)


def test_unknown_incomplete_reason_code_rejects() -> None:
    payload = _spec_example()
    payload["response_state"] = "partial"
    payload["speak"] = False
    payload["spoken_summary"] = ""
    payload["incomplete_reasons"] = [{"code": "cosmic_rays", "facet": "records"}]
    with pytest.raises(ValidationError):
        _validate(payload)


def test_claim_classification_basis_is_structural() -> None:
    """§8.6 C03's structural half lives on the claim model itself."""
    base = {
        "claim_id": "c9",
        "claim_role": "answer",
        "claim_type": "direct_source_fact",
        "fact_refs": [],
        "calculation_output_refs": [],
        "evidence_refs": [],
        "entity_refs": [],
        "render_template": "analysis.record_line",
        "paraphrase": "",
    }

    def claim(**overrides: Any) -> dict[str, Any]:
        merged = dict(base)
        merged.update(overrides)
        return merged

    with pytest.raises(ValidationError, match="documented claim"):
        AnalysisClaim.model_validate_json(json.dumps(claim(evidence_classification="documented")))
    with pytest.raises(ValidationError, match="calculation output"):
        AnalysisClaim.model_validate_json(json.dumps(claim(evidence_classification="calculated")))
    with pytest.raises(ValidationError, match="premises"):
        AnalysisClaim.model_validate_json(json.dumps(claim(evidence_classification="inferred")))
    # Insufficient claims may carry no value refs at all.
    parsed = AnalysisClaim.model_validate_json(
        json.dumps(claim(evidence_classification="insufficient"))
    )
    assert parsed.evidence_classification == "insufficient"


def test_payload_claim_closure() -> None:
    payload = _spec_example()["payload"]
    payload["facets"][0]["claim_ids"] = ["missing"]
    with pytest.raises(ValidationError, match="unknown claims"):
        EvidenceAnalysisPayload.model_validate_json(json.dumps(payload))

    duplicated = _spec_example()["payload"]
    duplicated["claims"][1]["claim_id"] = "c1"
    with pytest.raises(ValidationError, match="unique"):
        EvidenceAnalysisPayload.model_validate_json(json.dumps(duplicated))


def test_paraphrase_bound() -> None:
    payload = _spec_example()
    payload["payload"]["claims"][0]["paraphrase"] = "x" * (MAX_PARAPHRASE_CHARS + 1)
    with pytest.raises(ValidationError, match="paraphrase"):
        _validate(payload)


def test_spoken_summary_rejects_markup() -> None:
    payload = _spec_example()
    payload["spoken_summary"] = "See the **manual** for details."
    with pytest.raises(ValidationError, match="plain text"):
        _validate(payload)


def test_synthesis_schema_has_no_value_fields() -> None:
    """The fence: the model's schema simply cannot carry rendered content."""
    slot_fields = set(SynthesisClaimSlot.model_fields)
    assert slot_fields == {
        "claim_id",
        "claim_role",
        "claim_type",
        "evidence_classification",
        "fact_refs",
        "calculation_output_refs",
        "evidence_refs",
        "entity_refs",
        "render_template",
        "paraphrase",
    }
    for forbidden in (
        "detailed_response",
        "spoken_summary",
        "value",
        "values",
        "count",
        "date",
        "identifier",
        "text",
    ):
        assert forbidden not in slot_fields

    set_fields = set(SynthesisClaimSet.model_fields)
    assert set_fields == {"facets", "claims", "assumptions", "unknowns"}
