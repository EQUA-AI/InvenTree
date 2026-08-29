"""S10 WP-A3: the deterministic validator + the §13.2 poisoned-answer matrix.

Every deliberately poisoned input must be downgraded, abstained, or failed
closed BEFORE rendering reaches any wire — no poison may survive the
downgrade re-render, and no check may be overridable by anything
model-shaped (there is no model anywhere in this module).
"""

from __future__ import annotations

import json
from dataclasses import replace

from ai.core.analysis.evidence import (
    EvidenceStore,
    FactValue,
    coverage_fact,
    fact_from_dataset_profile,
    fact_from_manual_citation,
    facts_from_work_order_row,
)
from ai.core.analysis.renderer import assign_ordinals, render_answer
from ai.core.analysis.schemas import AnalysisClaim, AnalysisFacet
from ai.core.analysis.scope_context import TurnScopeContext
from ai.core.analysis.validator import (
    CheckOutcome,
    check_population,
    shadow_scan_legacy,
    validate_analysis,
)

AS_OF = "2026-08-27T12:00:00+00:00"


def _scope(machine_ids=(12,)) -> TurnScopeContext:
    return TurnScopeContext(
        mode="explicit_assets",
        machine_ids=tuple(machine_ids),
        machine_serials=frozenset({"SN-12"}),
        date_from=None,
        date_to=None,
        source_classes=(),
        scope_hash="hash12",
        scope_version=3,
        snapshot_id="snap_test",
        thread_pk="thread_1",
        display_label="Feed pumps",
        shadow=True,
        enforce=True,
    )


def _row(machine_id: int = 12, work_order_id: int = 41, reference: str = "WO-0041") -> dict:
    return {
        "work_order_id": work_order_id,
        "reference": reference,
        "title": "Replace coolant filter",
        "board_status": "in_progress",
        "lifecycle_status": "released",
        "work_order_type": "corrective",
        "priority": "high",
        "machine_id": machine_id,
        "machine": "Feed Pump East",
        "due_date": "2026-09-01",
        "created_at": "2026-08-20T08:00:00+00:00",
        "updated_at": "2026-08-21T08:00:00+00:00",
        "actual_started_at": None,
        "actual_completed_at": None,
    }


def _claim(**overrides) -> AnalysisClaim:
    base = {
        "claim_id": "c1",
        "claim_role": "answer",
        "claim_type": "direct_source_fact",
        "evidence_classification": "documented",
        "fact_refs": [],
        "calculation_output_refs": [],
        "evidence_refs": [],
        "entity_refs": [],
        "render_template": "analysis.record_line",
        "paraphrase": "",
    }
    base.update(overrides)
    return AnalysisClaim.model_validate_json(json.dumps(base))


def _facet(name: str = "records", claim_ids: tuple[str, ...] = ("c1",)) -> AnalysisFacet:
    return AnalysisFacet.model_validate_json(
        json.dumps({"name": name, "status": "answered", "claim_ids": list(claim_ids)})
    )


def _validate(store, claims, rendered, **overrides):
    kwargs = {
        "claims": claims,
        "facets": [_facet(claim_ids=tuple(claim.claim_id for claim in claims))],
        "store": store,
        "rendered": rendered,
        "entities": [],
        "scope": _scope(),
        "ledger_retrieval_ids": frozenset({"ret_abc"}),
        "ledger_chunk_ids": None,
        "emitted_events": [],
        "reauthorize": lambda: True,
        "safety_audit": None,
    }
    kwargs.update(overrides)
    return validate_analysis(**kwargs)


def _grounded_case() -> tuple[EvidenceStore, list[AnalysisClaim], object]:
    """A fully valid single-record answer used as the baseline."""
    store = EvidenceStore()
    fact_id = facts_from_work_order_row(
        store, _row(), retrieval_id="ret_abc", as_of=AS_OF, source_revision="snap_1"
    )
    claims = [_claim(fact_refs=[fact_id], entity_refs=["machine:12"])]
    rendered = render_answer(
        claims, store, ordinals=assign_ordinals(claims, store, default_as_of=AS_OF)
    )
    return store, claims, rendered


def test_baseline_valid_answer_passes() -> None:
    store, claims, rendered = _grounded_case()
    verdict = _validate(store, claims, rendered)
    assert verdict.outcome is CheckOutcome.PASS
    assert verdict.dropped_claim_ids == ()


# --- the §13.2 matrix -----------------------------------------------------


def test_out_of_scope_work_order_is_downgraded() -> None:
    """Poison 1: a work-order fact for a machine outside the active scope."""
    store = EvidenceStore()
    out_fact = facts_from_work_order_row(
        store,
        _row(machine_id=99, work_order_id=77, reference="WO-0077"),
        retrieval_id="ret_abc",
        as_of=AS_OF,
        source_revision="snap_1",
    )
    claims = [_claim(fact_refs=[out_fact], entity_refs=["machine:99"])]
    rendered = render_answer(
        claims, store, ordinals=assign_ordinals(claims, store, default_as_of=AS_OF)
    )
    verdict = _validate(store, claims, rendered)
    # All answer claims dropped -> the downgrade escalates to abstention.
    assert verdict.outcome is CheckOutcome.ABSTAIN
    assert "out_of_scope_reference" in verdict.codes()
    assert verdict.dropped_claim_ids == ("c1",)


def test_wrong_machine_citation_is_downgraded() -> None:
    """Poison 6: a real citation that belongs to another machine."""
    store, claims, rendered = _grounded_case()
    stray = facts_from_work_order_row(
        store,
        _row(machine_id=99, work_order_id=78, reference="WO-0078"),
        retrieval_id="ret_abc",
        as_of=AS_OF,
        source_revision="snap_1",
    )
    poisoned = [
        *claims,
        _claim(claim_id="c2", fact_refs=[stray], render_template="analysis.record_line"),
    ]
    rendered = render_answer(
        poisoned, store, ordinals=assign_ordinals(poisoned, store, default_as_of=AS_OF)
    )
    verdict = _validate(store, poisoned, rendered)
    assert verdict.outcome is CheckOutcome.DOWNGRADE
    assert verdict.dropped_claim_ids == ("c2",)


def test_invented_manual_revision_fails_identifier_closure() -> None:
    """Poison 2: a code-shaped revision that no server surface inserted."""
    store, claims, rendered = _grounded_case()
    tampered = replace(
        rendered,
        detailed_response=rendered.detailed_response + " Per manual REV-X9 this is fine.",
    )
    verdict = _validate(store, claims, tampered)
    assert verdict.outcome in (CheckOutcome.DOWNGRADE, CheckOutcome.ABSTAIN)
    assert "unclosed_identifier" in verdict.codes()


def test_orphan_number_fails_value_closure() -> None:
    """Poison 3: a number absent from every referenced fact/calculation."""
    store, claims, rendered = _grounded_case()
    tampered = replace(
        rendered,
        detailed_response=rendered.detailed_response + " The reading was 37 bar.",
    )
    verdict = _validate(store, claims, tampered)
    assert "unclosed_value" in verdict.codes()


def test_model_count_differing_from_calculation_fails_closure() -> None:
    """Poison 12: the rendered count is not the referenced calculation's."""
    store, claims, rendered = _grounded_case()
    tampered = replace(
        rendered,
        detailed_response=rendered.detailed_response.replace("WO-0041", "WO-0041")
        + " 5 records matched.",
    )
    verdict = _validate(store, claims, tampered)
    assert "unclosed_value" in verdict.codes()


def test_incomplete_population_presented_as_complete_is_downgraded() -> None:
    """Poison 4: an aggregate over 25/403 rows presented as a total count."""
    store = EvidenceStore()
    calc = store.add_calculation(
        operation="count",
        input_refs=(),
        values={"count": FactValue("int", 25)},
        complete_population=False,
    )
    claims = [
        _claim(
            claim_type="calculation",
            evidence_classification="calculated",
            calculation_output_refs=[calc],
            render_template="analysis.record_count",
        )
    ]
    rendered = render_answer(
        claims, store, ordinals=assign_ordinals(claims, store, default_as_of=AS_OF)
    )
    verdict = _validate(store, claims, rendered)
    assert "incomplete_population" in verdict.codes()
    assert verdict.outcome is CheckOutcome.ABSTAIN  # only answer claim dropped


def test_dataset_profile_fact_vouches_for_completeness() -> None:
    """S7: a complete dataset profile satisfies C06 like a coverage fact."""
    store = EvidenceStore()
    fact_id = fact_from_dataset_profile(
        store,
        {
            "population_type": "work_orders",
            "population_count": 402,
            "complete_population": True,
            "date_field": "created_at",
            "timezone": "UTC",
        },
        retrieval_id="ret_prof",
        as_of=AS_OF,
    )
    claims = [
        _claim(
            fact_refs=[fact_id],
            render_template="analysis.record_count",
        )
    ]
    results = check_population(claims, store)
    assert all(result.outcome is CheckOutcome.PASS for result in results)

    incomplete = fact_from_dataset_profile(
        store,
        {
            "population_type": "work_orders",
            "population_count": 25,
            "complete_population": False,
            "date_field": "created_at",
            "timezone": "UTC",
        },
        retrieval_id="ret_prof2",
        as_of=AS_OF,
    )
    claims = [
        _claim(
            fact_refs=[incomplete],
            render_template="analysis.record_count",
        )
    ]
    codes = [result.code for result in check_population(claims, store)]
    assert "incomplete_population" in codes


def test_verified_applicability_requirement_gates_the_claim() -> None:
    """S8b C07 extension: verified relation, matched to the claim's machine."""
    from types import SimpleNamespace

    from ai.core.analysis.evidence import fact_from_applicability_claim
    from ai.core.analysis.renderer import RENDER_TEMPLATES, RenderTemplate
    from ai.core.analysis.validator import check_applicability

    template = RenderTemplate(
        key="analysis.test_verified_procedure",
        required_slots=(),
        build=lambda _slots, _paraphrase, marker, _locale: f"ok.{marker}",
        requires_verified_applicability=True,
    )
    RENDER_TEMPLATES[template.key] = template
    try:
        store = EvidenceStore()
        verified_row = SimpleNamespace(
            pk=7,
            kind="exact_machine",
            state="verified",
            document=SimpleNamespace(document_id="doc-1", revision="2.0"),
            effective_from=None,
            effective_to=None,
            target_machine_id=12,
            target_model="",
            document_content_sha256="b" * 64,
        )
        fact_id = fact_from_applicability_claim(
            store, verified_row, retrieval_id="ret_appl", as_of=AS_OF
        )

        matched = _claim(
            fact_refs=[fact_id],
            entity_refs=["machine:12"],
            render_template=template.key,
        )
        results = check_applicability([matched], store)
        assert all(result.outcome is CheckOutcome.PASS for result in results)

        # The verified relation covers ITS machine, never the neighbor's.
        mismatched = _claim(
            fact_refs=[fact_id],
            entity_refs=["machine:99"],
            render_template=template.key,
        )
        codes = [result.code for result in check_applicability([mismatched], store)]
        assert "unverified_applicability" in codes

        # A merely-proposed row never satisfies the requirement.
        store2 = EvidenceStore()
        proposed_row = SimpleNamespace(
            pk=8,
            kind="exact_machine",
            state="proposed",
            document=SimpleNamespace(document_id="doc-1", revision="2.0"),
            effective_from=None,
            effective_to=None,
            target_machine_id=12,
            target_model="",
            document_content_sha256="b" * 64,
        )
        proposed_fact = fact_from_applicability_claim(
            store2, proposed_row, retrieval_id="ret_appl", as_of=AS_OF
        )
        unproven = _claim(
            fact_refs=[proposed_fact],
            entity_refs=["machine:12"],
            render_template=template.key,
        )
        codes = [result.code for result in check_applicability([unproven], store2)]
        assert "unverified_applicability" in codes
    finally:
        RENDER_TEMPLATES.pop(template.key, None)


def test_uncited_chip_is_dropped() -> None:
    """Poison 5: a chip for an entity no surviving claim cites."""
    store, claims, rendered = _grounded_case()
    verdict = _validate(
        store,
        claims,
        rendered,
        entities=[
            {"model": "assetmachine", "pk": 12, "label": "Feed Pump East", "ref": "machine:12"},
            {"model": "workorder", "pk": 999, "label": "WO-0999", "ref": "workorder:999"},
        ],
    )
    assert "uncited_entity" in verdict.codes()
    assert [entity["pk"] for entity in verdict.allowed_entities] == [12]


def test_false_no_records_contradicts_nonzero_count() -> None:
    """Poison 7: "no records exist" while the inventory count is nonzero."""
    store = EvidenceStore()
    coverage = coverage_fact(
        store,
        {"population_count": 3, "returned_count": 3, "complete_population": True},
        retrieval_id="ret_abc",
        source_class="work_order",
        as_of=AS_OF,
    )
    calc = store.add_calculation(
        operation="count",
        input_refs=(coverage,),
        values={"count": FactValue("int", 3)},
        complete_population=True,
    )
    claims = [
        _claim(
            claim_type="calculation",
            evidence_classification="calculated",
            fact_refs=[coverage],
            calculation_output_refs=[calc],
            render_template="analysis.absence_of_records",
        )
    ]
    rendered = render_answer(
        claims, store, ordinals=assign_ordinals(claims, store, default_as_of=AS_OF)
    )
    verdict = _validate(store, claims, rendered)
    assert "absence_contradicts_counts" in verdict.codes()


def test_unrecorded_step_cannot_be_presented_as_noncompliance() -> None:
    """Poison 8: not-recorded rendered as an answer-grade finding."""
    store, _, _ = _grounded_case()
    fact_id = next(iter(store.facts))
    claims = [
        _claim(
            claim_type="unknown",
            evidence_classification="insufficient",
            fact_refs=[fact_id],
            render_template="analysis.record_line",
        )
    ]
    rendered = render_answer(
        claims, store, ordinals=assign_ordinals(claims, store, default_as_of=AS_OF)
    )
    verdict = _validate(store, claims, rendered)
    assert "insufficient_without_limitation" in verdict.codes()


def test_replacement_as_root_cause_needs_calibrated_template() -> None:
    """Poison 9: an inference rendered as a direct factual statement."""
    store, _, _ = _grounded_case()
    fact_id = next(iter(store.facts))
    claims = [
        _claim(
            claim_type="inference",
            evidence_classification="inferred",
            fact_refs=[fact_id],
            render_template="analysis.record_line",  # not calibrated
        )
    ]
    rendered = render_answer(
        claims, store, ordinals=assign_ordinals(claims, store, default_as_of=AS_OF)
    )
    verdict = _validate(store, claims, rendered)
    assert "uncalibrated_inference" in verdict.codes()


def test_conclusion_without_resolvable_evidence_is_dropped() -> None:
    """Poison 10: a documented conclusion whose evidence does not resolve."""
    store, claims, _ = _grounded_case()
    poisoned = [
        *claims,
        _claim(
            claim_id="c2",
            fact_refs=["fact_ghost"],
            render_template="analysis.inference_note",
            paraphrase="stable production going forward",
        ),
    ]
    rendered = render_answer(
        poisoned, store, ordinals=assign_ordinals(poisoned, store, default_as_of=AS_OF)
    )
    verdict = _validate(store, poisoned, rendered)
    assert {"unresolved_fact_ref", "documented_without_fact"} <= set(verdict.codes())
    assert "c2" in verdict.dropped_claim_ids


def test_prior_assistant_claim_never_acts_as_evidence() -> None:
    """Poison 13: prior-turn output is structurally absent from the store."""
    store, claims, _ = _grounded_case()
    poisoned = [
        *claims,
        _claim(
            claim_id="c2",
            fact_refs=["fact_from_last_turn"],
            render_template="analysis.inference_note",
            paraphrase="the earlier answer already confirmed this",
        ),
    ]
    rendered = render_answer(
        poisoned, store, ordinals=assign_ordinals(poisoned, store, default_as_of=AS_OF)
    )
    verdict = _validate(store, poisoned, rendered)
    assert "unresolved_fact_ref" in verdict.codes()
    assert "c2" in verdict.dropped_claim_ids


def test_refusal_followed_by_rca_fails_closed_without_safety_audit() -> None:
    """Poison 11: safety-adjacent prose with no audit available."""
    store, claims, rendered = _grounded_case()
    tampered = replace(
        rendered,
        detailed_response=(
            rendered.detailed_response
            + " After the lockout, the likely root cause is bearing wear."
        ),
    )
    verdict = _validate(store, claims, tampered, safety_audit=None)
    assert verdict.outcome is CheckOutcome.FAIL_CLOSED
    assert "safety_audit_unavailable" in verdict.codes()


def test_safety_audit_outage_fails_closed_even_when_flagged_safe() -> None:
    store, claims, rendered = _grounded_case()
    tampered = replace(rendered, detailed_response=rendered.detailed_response + " Interlock note.")

    def broken_audit() -> bool:
        raise RuntimeError("audit provider down")

    verdict = _validate(store, claims, tampered, safety_audit=broken_audit)
    assert verdict.outcome is CheckOutcome.FAIL_CLOSED


def test_revocation_between_retrieval_and_persistence_fails_closed() -> None:
    """Poison 14: the final authorization pass finds access revoked."""
    store, claims, rendered = _grounded_case()
    verdict = _validate(store, claims, rendered, reauthorize=lambda: False)
    assert verdict.outcome is CheckOutcome.FAIL_CLOSED
    assert "reauthorization_failed" in verdict.codes()


def test_provisional_prose_before_validation_fails_closed() -> None:
    """Poison 15: any content-bearing event emitted before validation."""
    store, claims, rendered = _grounded_case()
    verdict = _validate(
        store,
        claims,
        rendered,
        emitted_events=[
            {"type": "RUN_STARTED"},
            {"type": "TEXT_MESSAGE_CONTENT", "delta": "provisional answer text"},
        ],
    )
    assert verdict.outcome is CheckOutcome.FAIL_CLOSED
    assert "pre_validation_disclosure" in verdict.codes()

    sneaky = _validate(
        store,
        claims,
        rendered,
        emitted_events=[
            {
                "type": "STATE_DELTA",
                "kind": "analysis_progress",
                "stage": "reviewing_records",
                "excerpt": "leak",
            },
        ],
    )
    assert sneaky.outcome is CheckOutcome.FAIL_CLOSED


def test_progress_events_alone_are_allowed() -> None:
    store, claims, rendered = _grounded_case()
    verdict = _validate(
        store,
        claims,
        rendered,
        emitted_events=[
            {"type": "RUN_STARTED"},
            {"type": "WORKFLOW_STARTED"},
            {"type": "STATE_DELTA", "kind": "analysis_progress", "stage": "confirming_scope"},
        ],
    )
    assert verdict.outcome is CheckOutcome.PASS


def test_controlled_source_requirement_rejects_attachment_substitution() -> None:
    """§8.6 C07: uncontrolled attachments can't satisfy a procedural claim."""
    store = EvidenceStore()
    uncontrolled = fact_from_manual_citation(
        store,
        {"document": "Photo note", "document_id": "ATT-9", "revision": "", "chunk_id": "c-1"},
        retrieval_id="ret_abc",
        as_of=AS_OF,
    )
    object.__setattr__(store.facts[uncontrolled], "controlled", False)
    claims = [
        _claim(
            fact_refs=[uncontrolled],
            render_template="analysis.manual_passage_fact",
            paraphrase="check the torque values",
        )
    ]
    rendered = render_answer(
        claims, store, ordinals=assign_ordinals(claims, store, default_as_of=AS_OF)
    )
    verdict = _validate(store, claims, rendered)
    assert "uncontrolled_source" in verdict.codes()


# --- shadow prose scan ----------------------------------------------------


def test_shadow_scan_flags_unclosed_tokens_content_free() -> None:
    result = shadow_scan_legacy(
        message="Pump HX-200 failed 3 times; see WO-9999 for details.",
        known_values=frozenset({"HX-200"}),
        envelopes=[{"coverage": {"complete_population": False}}],
        intent="record_retrieval",
    )
    assert result is not None
    assert "unclosed_identifier" in result["would_fail"]
    assert "unclosed_value" in result["would_fail"]
    blob = json.dumps(result)
    assert "WO-9999" not in blob
    assert "HX-200" not in blob


def test_shadow_scan_absence_requires_complete_population() -> None:
    result = shadow_scan_legacy(
        message="No records exist for this machine.",
        known_values=frozenset(),
        envelopes=[{"coverage": {"complete_population": False}}],
        intent="record_retrieval",
    )
    assert "absence_without_complete_population" in result["would_fail"]

    grounded = shadow_scan_legacy(
        message="No records exist for this machine.",
        known_values=frozenset(),
        envelopes=[{"coverage": {"complete_population": True}}],
        intent="record_retrieval",
    )
    assert "absence_without_complete_population" not in grounded["would_fail"]


def test_shadow_scan_empty_message_is_none() -> None:
    assert shadow_scan_legacy(message="", known_values=frozenset()) is None
