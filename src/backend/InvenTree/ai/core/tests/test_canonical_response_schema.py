"""Contract tests for the strict versioned canonical turn response."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from ai.core.reasoning import (
    CANONICAL_RESPONSE_VERSION,
    CanonicalTurnResponse,
    ConfidenceLevel,
    ResponseState,
)
from pydantic import ValidationError


def _valid_complete() -> dict[str, Any]:
    return {
        "kind": "repair_diagnosis",
        "response_version": 1,
        "response_state": "complete",
        "detailed_response": (
            "**Evidence:** Packet 123 records drive-end vibration. The bearing may need inspection."
        ),
        "spoken_summary": (
            "The bearing may need inspection; current vibration is unconfirmed. "
            "Do not operate the pump until lockout is confirmed."
        ),
        "reasoning_summary": (
            "Packet evidence supports a possible bearing issue, but current vibration "
            "is unconfirmed."
        ),
        "confidence": "low",
        "evidence": [
            {
                "source_type": "repair_packet",
                "source_id": "123",
                "source_revision": "7",
                "locator": {"field": "fault_summary"},
                "as_of": datetime(2026, 7, 14, 20, 0, tzinfo=UTC),
                "authorization_class": "repair_packet.read",
                "claim": "Drive-end vibration was recorded.",
            }
        ],
        "next_questions": ["What is the current vibration reading?"],
        "recommended_actions": [
            {
                "kind": "proposed_action",
                "title": "Prepare an inspection",
                "detail": "Open the normal work-order proposal surface.",
                "requires_approval": True,
            },
            {
                "kind": "read_only",
                "title": "Review the packet",
                "detail": "Open repair packet 123.",
                "requires_approval": False,
            },
        ],
        "safety_boundary": "Do not operate the pump until lockout is confirmed.",
        "speak": True,
    }


def _valid_terminal(state: str) -> dict[str, Any]:
    return {
        "kind": "repair_diagnosis",
        "response_version": 1,
        "response_state": state,
        "detailed_response": f"The diagnostic response is {state} and has no recommendation.",
        "spoken_summary": "",
        "reasoning_summary": "No diagnostic conclusion was produced.",
        "confidence": "low",
        "evidence": [],
        "next_questions": ["Would you like to retry?"],
        "recommended_actions": [],
        "safety_boundary": "No safety boundary.",
        "speak": False,
    }


def test_valid_complete_response_and_json_round_trip() -> None:
    response = CanonicalTurnResponse.model_validate(_valid_complete())

    assert response.response_version == 1
    assert response.evidence[0].as_of.utcoffset() is not None
    assert response.recommended_actions[0].requires_approval is True
    assert response.recommended_actions[1].requires_approval is False

    from_json = CanonicalTurnResponse.model_validate_json(response.model_dump_json())
    assert from_json == response


def test_public_version_and_type_exports_are_stable() -> None:
    confidence = ConfidenceLevel.HIGH
    state = ResponseState.COMPLETE

    assert CANONICAL_RESPONSE_VERSION == 1
    assert confidence == "high"
    assert state == "complete"


@pytest.mark.parametrize("state", ["incomplete", "canceled", "failed"])
def test_valid_non_complete_responses(state: str) -> None:
    response = CanonicalTurnResponse.model_validate(_valid_terminal(state))

    assert response.response_state == state
    assert response.spoken_summary == ""
    assert response.recommended_actions == []
    assert response.speak is False


@pytest.mark.parametrize("missing_field", list(_valid_complete()))
def test_every_top_level_field_is_required(missing_field: str) -> None:
    payload = _valid_complete()
    payload.pop(missing_field)

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


@pytest.mark.parametrize(
    "missing_field",
    [
        "source_type",
        "source_id",
        "source_revision",
        "locator",
        "as_of",
        "authorization_class",
        "claim",
    ],
)
def test_every_evidence_field_is_required(missing_field: str) -> None:
    payload = _valid_complete()
    payload["evidence"][0].pop(missing_field)

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


@pytest.mark.parametrize("missing_field", ["kind", "title", "detail", "requires_approval"])
def test_every_action_field_is_required(missing_field: str) -> None:
    payload = _valid_complete()
    payload["recommended_actions"][0].pop(missing_field)

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


def test_locator_requires_a_typed_coordinate() -> None:
    payload = _valid_complete()
    payload["evidence"][0]["locator"] = {}

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


@pytest.mark.parametrize(
    ("level", "extra_field"),
    [
        ("response", "chain_of_thought"),
        ("evidence", "raw_record"),
        ("locator", "query"),
        ("action", "command_payload"),
    ],
)
def test_extra_fields_are_forbidden_at_every_level(level: str, extra_field: str) -> None:
    payload = _valid_complete()
    if level == "response":
        payload[extra_field] = "must not be accepted"
    elif level == "evidence":
        payload["evidence"][0][extra_field] = "must not be accepted"
    elif level == "locator":
        payload["evidence"][0]["locator"][extra_field] = "must not be accepted"
    else:
        payload["recommended_actions"][0][extra_field] = "must not be accepted"

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


@pytest.mark.parametrize("state", ["incomplete", "canceled", "failed"])
def test_non_complete_response_cannot_recommend_actions(state: str) -> None:
    payload = _valid_terminal(state)
    payload["recommended_actions"] = [deepcopy(_valid_complete()["recommended_actions"][0])]

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


@pytest.mark.parametrize("state", ["incomplete", "canceled", "failed"])
def test_non_complete_response_cannot_enable_answer_speech(state: str) -> None:
    payload = _valid_terminal(state)
    payload["speak"] = True

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


@pytest.mark.parametrize("state", ["incomplete", "canceled", "failed"])
def test_non_complete_response_cannot_carry_answer_summary(state: str) -> None:
    payload = _valid_terminal(state)
    payload["spoken_summary"] = "The bearing may need inspection."

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


def test_complete_spoken_response_requires_nonempty_summary() -> None:
    payload = _valid_complete()
    payload["spoken_summary"] = ""

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


@pytest.mark.parametrize(
    ("kind", "requires_approval"),
    [
        ("proposed_action", False),
        ("read_only", True),
        ("write_immediately", True),
    ],
)
def test_action_kind_and_approval_semantics_are_strict(kind: str, requires_approval: bool) -> None:
    payload = _valid_complete()
    payload["recommended_actions"][0]["kind"] = kind
    payload["recommended_actions"][0]["requires_approval"] = requires_approval

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


@pytest.mark.parametrize("state", ["done", "timeout", "cancelled", "error"])
def test_unknown_response_states_are_rejected(state: str) -> None:
    payload = _valid_complete()
    payload["response_state"] = state

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


def test_response_version_is_exactly_one() -> None:
    payload = _valid_complete()
    payload["response_version"] = 2

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


def test_spoken_summary_cannot_add_substantive_detail() -> None:
    payload = _valid_complete()
    payload["spoken_summary"] = (
        "The bearing may need inspection at 900 RPM; current vibration is unconfirmed. "
        "Do not operate the pump until lockout is confirmed."
    )

    with pytest.raises(ValidationError, match="adds substantive tokens"):
        CanonicalTurnResponse.model_validate(payload)


def test_spoken_summary_cannot_drop_uncertainty() -> None:
    payload = _valid_complete()
    payload["detailed_response"] = "Inspection may be needed for the bearing."
    payload["spoken_summary"] = (
        "Inspection is needed for the bearing; current vibration is unconfirmed. "
        "Do not operate the pump until lockout is confirmed."
    )

    with pytest.raises(ValidationError, match="uncertainty marker"):
        CanonicalTurnResponse.model_validate(payload)


def test_spoken_summary_cannot_drop_uncertainty_negation() -> None:
    payload = _valid_complete()
    payload["reasoning_summary"] = "Current vibration is not confirmed."
    payload["detailed_response"] = "The bearing needs inspection."
    payload["spoken_summary"] = (
        "The bearing needs inspection; current vibration is confirmed. "
        "Do not operate the pump until lockout is confirmed."
    )

    with pytest.raises(ValidationError, match="uncertainty negation"):
        CanonicalTurnResponse.model_validate(payload)


def test_spoken_summary_cannot_drop_safety_caveat() -> None:
    payload = _valid_complete()
    payload["spoken_summary"] = "The bearing may need inspection; current vibration is unconfirmed."

    with pytest.raises(ValidationError, match="safety boundary"):
        CanonicalTurnResponse.model_validate(payload)


@pytest.mark.parametrize(
    "spoken_summary",
    [
        "**The bearing may need inspection.**",
        "[Inspect the bearing](https://example.invalid)",
        "# The bearing may need inspection.",
        "The bearing may need inspection.\nDo not operate the pump.",
        "The bearing may need inspection.\x00",
    ],
)
def test_spoken_summary_must_be_plain_text_without_controls(spoken_summary: str) -> None:
    payload = _valid_complete()
    payload["spoken_summary"] = spoken_summary

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


def test_spoken_summary_cannot_reverse_safety_negation_by_reordering_words() -> None:
    payload = _valid_complete()
    payload["safety_boundary"] = "The pump is not safe to operate."
    payload["spoken_summary"] = "The pump is safe to operate. Not."
    payload["detailed_response"] += " The pump is not safe to operate."

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


def test_evidence_timestamp_must_be_timezone_aware() -> None:
    payload = _valid_complete()
    payload["evidence"][0]["as_of"] = datetime(2026, 7, 14, 20, 0)

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "bad_value"),
    [
        (("response_version",), "1"),
        (("response_state",), b"complete"),
        (("confidence",), b"low"),
        (("speak",), 1),
        (("evidence", 0, "source_id"), 123),
        (("evidence", 0, "locator", "page"), "1"),
        (("recommended_actions", 0, "requires_approval"), 1),
    ],
)
def test_scalar_coercion_is_rejected(path: tuple[str | int, ...], bad_value: Any) -> None:
    payload = _valid_complete()
    target: Any = payload
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = bad_value

    with pytest.raises(ValidationError):
        CanonicalTurnResponse.model_validate(payload)


def test_json_schema_forbids_additional_properties_recursively() -> None:
    schema = CanonicalTurnResponse.model_json_schema()

    assert schema["additionalProperties"] is False
    assert schema["$defs"]["EvidenceEntry"]["additionalProperties"] is False
    assert schema["$defs"]["EvidenceLocator"]["additionalProperties"] is False
    assert schema["$defs"]["RecommendedAction"]["additionalProperties"] is False
    assert json.loads(CanonicalTurnResponse.model_validate(_valid_complete()).model_dump_json())
