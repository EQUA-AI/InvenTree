"""S22: option promotion, payload hygiene, and the machine-term matcher."""

# ruff: noqa: E402

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()


from ai.core.questions.promotion import (
    consume_question_proposal,
    promote_lexicon_options,
    promote_machine_candidates,
    set_question_proposal,
)
from ai.core.questions.schema import (
    FORBIDDEN_EVENT_KEYS,
    build_pending_record,
    render_question_text,
)
from ai.core.tools.capabilities import matched_machine_terms

CANDIDATES = [
    {"machine_id": 42, "name": "Influent Pump Station No. 1", "serial": "TC-INF-PS1-001"},
    {"machine_id": 43, "name": "Clarifier Drive 2", "serial": "TC-CLA-DR2-001"},
    {"machine_id": 44, "name": "UF/RO Skid 1", "serial": ""},
]


def test_machine_candidates_promote_with_refs_and_recommendation():
    options = promote_machine_candidates(CANDIDATES, modality="text")
    assert [option["id"] for option in options] == ["machine:42", "machine:43", "machine:44"]
    assert options[0]["recommended"] is True
    assert options[0]["ref"] == {
        "machine_id": 42,
        "serial": "TC-INF-PS1-001",
        "name": "Influent Pump Station No. 1",
    }
    assert options[0]["label"] == "Influent Pump Station No. 1 (TC-INF-PS1-001)"
    assert options[2]["label"] == "UF/RO Skid 1"


def test_voice_cap_is_three():
    four = [*CANDIDATES, {"machine_id": 45, "name": "Blower 4", "serial": "B4"}]
    assert len(promote_machine_candidates(four, modality="voice")) == 3
    assert len(promote_machine_candidates(four, modality="text")) == 4


def test_lexicon_promotion_machines_before_categories():
    options = promote_lexicon_options(
        machine_terms=[{"machine_id": 42, "name": "Influent Pump Station No. 1", "serial": "S"}],
        category_terms=["fasteners"],
        modality="text",
    )
    assert [option["kind"] for option in options] == ["machine", "lexicon_term"]
    assert options[1]["id"] == "term:fasteners"
    assert options[1]["ref"] == {"term": "fasteners"}


def test_fewer_than_two_options_is_not_a_question():
    assert (
        promote_lexicon_options(machine_terms=[], category_terms=["fasteners"], modality="text")
        == []
    )


def test_proposal_contextvar_is_consume_once():
    set_question_proposal({"source": "manual_search_ambiguity"})
    assert consume_question_proposal() == {"source": "manual_search_ambiguity"}
    assert consume_question_proposal() is None


def test_question_payload_carries_no_stale_client_keys():
    """Invariant 5: the wire payload must not trip the default SSE branch."""
    record, payload = build_pending_record(
        thread_id=1,
        turn_id="turn_x",
        source="manual_search_ambiguity",
        question_text="Which machine do you mean?",
        options=promote_machine_candidates(CANDIDATES, modality="text"),
        origin_content="what does the manual say about the pump",
        workflow="wf8",
        modality="text",
    )
    assert FORBIDDEN_EVENT_KEYS.isdisjoint(payload)
    assert all("ref" not in option for option in payload["options"])
    assert all("ref" in option for option in record["options"])
    assert record["interrupt_id"] == payload["interrupt_id"]
    assert record["expires_at"] == payload["expires_at"]


def test_render_voice_text_speaks_ordinals_and_literal_labels():
    options = promote_machine_candidates(CANDIDATES[:2], modality="voice")
    text = render_question_text("Which machine do you mean?", options, modality="voice")
    assert "Option one:" in text
    assert "Option two:" in text
    for option in options:
        assert option["label"] in text


def test_render_text_is_a_numbered_list():
    options = promote_machine_candidates(CANDIDATES[:2], modality="text")
    text = render_question_text("Which machine do you mean?", options, modality="text")
    assert text.splitlines()[0] == "Which machine do you mean?"
    assert "1. **Influent Pump Station No. 1 (TC-INF-PS1-001)** (recommended)" in text
    assert "2. **Clarifier Drive 2 (TC-CLA-DR2-001)**" in text


def test_matched_machine_terms_ranks_serial_over_name_over_tokens():
    machines = [
        {"machine_id": 1, "name": "Influent Pump Station No. 1", "serial": "TC-INF-PS1-001"},
        {"machine_id": 2, "name": "Effluent Pump Station", "serial": "TC-EFF-PS2-001"},
        {"machine_id": 3, "name": "Blower 4", "serial": "B4-001"},
    ]
    hits = matched_machine_terms("check tc-inf-ps1-001 and the effluent pump station", machines)
    assert [hit["machine_id"] for hit in hits] == [1, 2]

    token_hit = matched_machine_terms("problems with the influent pump", machines)
    assert token_hit and token_hit[0]["machine_id"] == 1


def test_matched_machine_terms_reads_history_conversational_rows_only():
    machines = [{"machine_id": 1, "name": "Clarifier Drive 2", "serial": "CD2"}]
    history = [
        {"role": "tool", "content": "clarifier drive 2 telemetry"},
        {"role": "user", "content": "and what about the clarifier drive 2?"},
    ]
    assert matched_machine_terms("show its open jobs", machines, history)

    tool_only = [{"role": "tool", "content": "clarifier drive 2 telemetry"}]
    assert matched_machine_terms("show its open jobs", machines, tool_only) == ()


def test_matched_machine_terms_caps_at_three():
    machines = [
        {"machine_id": index, "name": f"Pump Station {index}", "serial": f"S{index}"}
        for index in range(1, 6)
    ]
    hits = matched_machine_terms("pump station overview", machines)
    assert len(hits) <= 3


def test_wf8_voice_budget_drops_options_never_truncates():
    """Over the 700-char spoken ceiling: 3 options -> 2 -> give up (None)."""
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    helper = T1LookupWorkflow._question_text_for_proposal
    short = {
        "question_text": "Which machine do you mean?",
        "options": [
            {"id": "a", "label": "Pump 1"},
            {"id": "b", "label": "Pump 2"},
            {"id": "c", "label": "Pump 3"},
        ],
    }
    rendered = helper(dict(short), "voice")
    assert rendered is not None and "Option three" in rendered

    long_label = "Extremely Verbose Machine Designation " * 8  # ~300 chars
    crowded = {
        "question_text": "Which machine do you mean?",
        "options": [
            {"id": "a", "label": long_label + "A"},
            {"id": "b", "label": long_label + "B"},
            {"id": "c", "label": long_label + "C"},
        ],
    }
    proposal = dict(crowded)
    rendered = helper(proposal, "voice")
    if rendered is not None:
        # If it fits at width 2, the proposal was trimmed to match exactly.
        assert len(proposal["options"]) == 2
        assert "Option three" not in rendered
        for option in proposal["options"]:
            assert option["label"] in rendered

    impossible = {
        "question_text": "Which machine?",
        "options": [
            {"id": "a", "label": "X" * 500},
            {"id": "b", "label": "Y" * 500},
            {"id": "c", "label": "Z" * 500},
        ],
    }
    assert helper(dict(impossible), "voice") is None


def test_threads_projection_derives_provenance_from_persisted_events():
    """The reload channel reproduces what the live STATE_DELTA delivered."""
    from ai.core.app import _persisted_provenance

    metadata = {
        "events": [
            {"type": "RUN_STARTED"},
            {
                "type": "STATE_DELTA",
                "kind": "diagnosis_provenance",
                "confidence": "medium",
                "evidence": [{"source_type": "asset_machine", "source_id": "1"}],
            },
        ]
    }
    provenance = _persisted_provenance(metadata)
    assert provenance == {
        "confidence": "medium",
        "evidence": [{"source_type": "asset_machine", "source_id": "1"}],
    }
    assert _persisted_provenance({"events": [{"type": "RUN_STARTED"}]}) is None
    assert _persisted_provenance({}) is None
