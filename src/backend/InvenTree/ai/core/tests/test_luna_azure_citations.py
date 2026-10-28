"""Azure schema choices contain only complete, server-returned source tuples."""

import copy
import json

import pytest
from ai.core.reasoning.citation_references import (
    bind_citation_references,
    materialize_citation_references,
)
from ai.core.reasoning.luna_diagnostics import _local_citation_candidates
from ai.core.reasoning.schemas import CanonicalTurnResponse
from ai.core.tools.provider_schema import strict_provider_schema
from jsonschema import Draft202012Validator, ValidationError


def _citation(identity, source_type="machine_anomaly"):
    return {
        "source_type": source_type,
        "id": str(identity),
        "revision": "a" * 64,
        "authorization_class": "maintenance_scope",
        "locator": f"/sources/{identity}",
        "as_of": "2026-10-27T21:56:33.553724+00:00",
    }


def _request(groups):
    return {
        "text": {"format": {"schema": strict_provider_schema(CanonicalTurnResponse)}},
        "input": [
            {
                "type": "function_call_output",
                "output": json.dumps({"status": "ok", "evidence": group}),
            }
            for group in groups
        ],
    }


def _entry(citation):
    return {
        "source_type": citation["source_type"],
        "source_id": citation["id"],
        "source_revision": citation["revision"],
        "authorization_class": citation["authorization_class"],
        "locator": {"field": citation["locator"], "page": None, "chunk": None},
        "as_of": citation["as_of"],
        "claim": "Actual returned evidence.",
    }


def test_complete_coordinates_are_coupled_and_immutable():
    first, second = _citation(17), _citation(204, "related_attached_manual")
    request = _request([[first], [second]])
    original = copy.deepcopy(request)
    bounded, references = bind_citation_references(request, _local_citation_candidates)
    schema = bounded["text"]["format"]["schema"]["properties"]["evidence"]["items"]
    validator = Draft202012Validator(schema)
    keys = list(references)
    for key, original_source in zip(keys, (first, second), strict=True):
        selected = {"citation_id": key, "claim": "Actual returned evidence."}
        validator.validate(selected)
        expanded = json.loads(
            materialize_citation_references(json.dumps({"evidence": [selected]}), references)
        )
        assert expanded["evidence"] == [_entry(original_source)]
    bad = {"citation_id": "not-returned", "claim": "Invented reference"}
    with pytest.raises(ValidationError):
        validator.validate(bad)
    bad = {"citation_id": keys[0], "claim": "Mixed", "source_id": second["id"]}
    with pytest.raises(ValidationError):
        validator.validate(bad)
    raw = json.dumps({"evidence": [bad]})
    assert (
        json.loads(materialize_citation_references(raw, references))[
            "unresolved_citation_reference"
        ]
        is True
    )
    assert request == original


def test_each_observed_reader_gets_a_choice_before_extra_signals():
    groups = [[_citation(index) for index in range(20)]]
    groups.extend([
        [_citation(204, "related_attached_manual")],
        [_citation(2886, "asset_maintenance_record")],
    ])
    bounded, references = bind_citation_references(_request(groups), _local_citation_candidates)
    assert [entry["source_id"] for entry in list(references.values())[:3]] == [
        "0",
        "204",
        "2886",
    ]

    def count(value):
        if isinstance(value, dict):
            return len(value.get("properties", {})) + sum(count(v) for v in value.values())
        if isinstance(value, list):
            return sum(count(v) for v in value)
        return 0

    assert count(bounded["text"]["format"]["schema"]) <= 100


def test_missing_or_denied_sources_never_create_choices():
    request = _request([])
    assert bind_citation_references(request, _local_citation_candidates) == (request, {})
    request = _request([[_citation(17)]])
    request["input"][0]["output"] = json.dumps({"status": "denied", "evidence": [_citation(17)]})
    bounded, references = bind_citation_references(request, _local_citation_candidates)
    assert references == {}
    assert json.loads(bounded["input"][0]["output"])["evidence"] == []
    assert json.loads(request["input"][0]["output"])["evidence"] == [_citation(17)]


def test_another_request_cannot_supply_a_citation_reference():
    _, first = bind_citation_references(_request([[_citation(17)]]), _local_citation_candidates)
    _, other = bind_citation_references(
        _request([[_citation(999), _citation(1000)]]), _local_citation_candidates
    )
    raw = json.dumps({"evidence": [{"citation_id": "SRC2", "claim": "Other scope"}]})
    assert (
        json.loads(materialize_citation_references(raw, first))["unresolved_citation_reference"]
        is True
    )
    selected = json.dumps({"evidence": [{"citation_id": "SRC1", "claim": "Observed"}]})
    current = json.loads(materialize_citation_references(selected, first))["evidence"][0]
    assert current["source_id"] == "17"
    assert current["source_id"] != other["SRC1"]["source_id"]


def test_inline_references_resolve_identity_only_and_unknown_references_fail():
    _, references = bind_citation_references(
        _request([[_citation(17)]]), _local_citation_candidates
    )
    raw = json.dumps({
        "evidence": [{"citation_id": "SRC1", "claim": "Observed"}],
        "detailed_response": "Actual source [SRC1].",
    })
    expanded = json.loads(materialize_citation_references(raw, references))
    assert expanded["detailed_response"] == "Actual source [machine anomaly #17]."
    invalid = raw.replace("Actual source [SRC1]", "Actual source [SRC999]")
    assert (
        json.loads(materialize_citation_references(invalid, references))[
            "unresolved_citation_reference"
        ]
        is True
    )


def test_reference_identity_is_stable_when_new_readers_are_appended():
    _, first = bind_citation_references(
        _request([[_citation(17), _citation(18)]]), _local_citation_candidates
    )
    _, later = bind_citation_references(
        _request([[_citation(17), _citation(18)], [_citation(204)]]), _local_citation_candidates
    )
    assert first["SRC1"] == later["SRC1"]
    assert first["SRC2"] == later["SRC2"]


def test_visible_action_question_and_claim_aliases_use_exact_existing_identity():
    _, references = bind_citation_references(
        _request([[_citation(17)]]), _local_citation_candidates
    )
    payload = {
        "evidence": [{"citation_id": "SRC1", "claim": "Observed [SRC1]"}],
        "recommended_actions": [{"title": "Read [SRC1]", "detail": "Compare [SRC1]"}],
        "next_questions": ["Review [SRC1]?"],
    }
    expanded = json.loads(materialize_citation_references(json.dumps(payload), references))
    assert expanded["recommended_actions"] == [
        {"title": "Read [machine anomaly #17]", "detail": "Compare [machine anomaly #17]"}
    ]
    assert expanded["next_questions"] == ["Review [machine anomaly #17]?"]
    assert expanded["evidence"][0]["claim"] == "Observed [machine anomaly #17]"
    assert expanded["evidence"][0]["source_revision"] == "a" * 64
    payload["recommended_actions"][0]["detail"] = "Read [SRC999]"
    assert (
        json.loads(materialize_citation_references(json.dumps(payload), references))[
            "unresolved_citation_reference"
        ]
        is True
    )


def test_same_hash_manual_copies_do_not_crowd_out_real_sensor_page():
    def manual(identity, page):
        row = _citation(identity, "related_attached_manual")
        row["locator"] = f"/attachments/{identity}.pdf#page={page}"
        row["claim"] = f"Real page {page}" if page != 999 else "UNCITABLE_BODY"
        return row

    groups = [
        [_citation(index, "machine_signal_state") for index in range(20)],
        [_citation(index, "asset_maintenance_record") for index in range(10)],
        [_citation(index, "work_order_closeout") for index in range(5)],
        [_citation(35, "repair_packet_redacted")],
        [
            manual(68, 50),
            manual(204, 50),
            manual(68, 38),
            manual(204, 38),
            manual(68, 23),
            *[manual(68, page) for page in range(60, 80)],
            manual(68, 999),
        ],
    ]
    request = _request(groups)
    original = copy.deepcopy(request)
    bounded, references = bind_citation_references(request, _local_citation_candidates)
    selected = [
        row for row in references.values() if row["source_type"] == "related_attached_manual"
    ]
    assert {row["locator"]["field"] for row in selected} >= {
        "/attachments/68.pdf#page=50",
        "/attachments/68.pdf#page=38",
        "/attachments/68.pdf#page=23",
    }
    assert all(row["source_id"] == "68" for row in selected)
    assert len(references) <= 20
    assert "UNCITABLE_BODY" not in json.dumps(bounded)
    assert "UNCITABLE_BODY" in json.dumps(request)
    assert request == original
    for item in bounded["input"]:
        for row in json.loads(item["output"])["evidence"]:
            assert row["citation_id"] in references
    invalid = json.dumps({"evidence": [{"citation_id": "SRC999", "claim": "UNCITABLE_BODY"}]})
    assert (
        json.loads(materialize_citation_references(invalid, references))[
            "unresolved_citation_reference"
        ]
        is True
    )
