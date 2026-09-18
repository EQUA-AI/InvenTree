"""Strict extraction response and request-boundary regression cases."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from ai.core.integrations import memory_extractor as service


def _row():
    return {
        "source_message_id": "message-1",
        "text": "I prefer a maintenance checklist.",
        "slot_key": "checklist",
        "memory_type": "user_preference",
        "topics": [],
        "classification": "preference",
        "prohibited": False,
        "entity_kind": "user",
        "entity_id": "1",
        "source_model": None,
        "source_id": None,
        "source_field": None,
    }


@pytest.mark.parametrize("change", ["unknown_source", "extra_key", "missing_key", "too_many"])
def test_strict_source_bound_response(change):
    """No foreign source, invented authority key or unbounded list survives."""
    row = _row()
    rows = [row]
    if change == "unknown_source":
        row["source_message_id"] = "foreign"
    elif change == "extra_key":
        row["verification_class"] = "tool_verified"
    elif change == "missing_key":
        del row["prohibited"]
    else:
        rows *= 6
    with pytest.raises(ValueError):
        service.parse_candidates(json.dumps({"candidates": rows}), {"message-1"})


def test_redaction_and_reservation_bound():
    """Provider input is redacted; the budget includes framing and output."""
    payload = service.request_payload(
        [{"source_message_id": "message-1", "content": "private@example.test"}],
        owner_id=1,
        locale="en",
    )
    assert "private@example.test" not in payload
    assert service.reservation_bound(payload) > len(payload.encode()) + service.MAX_OUTPUT_TOKENS


def test_refusal_keeps_usage_without_output(monkeypatch):
    """Refused or truncated output is never interpreted as an empty success."""
    client = MagicMock()
    client.with_options.return_value.__enter__.return_value = client
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(refusal="fixture", content="sensitive-provider-text"),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
    )
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(service, "build_openai_client", factory)
    result = service.extract(
        [{"source_message_id": "message-1", "content": "Ordinary checklist preference."}],
        owner_id=1,
        locale="en",
        settings=SimpleNamespace(memory_extraction_deployment="fixture"),
    )
    assert result.error_code == "extraction_unavailable"
    assert result.usage_known
    assert result.candidates == ()
    assert result.input_tokens == 5
    assert factory.call_args.kwargs["require_keyless"] is True
    assert client.with_options.call_args.kwargs["max_retries"] == 0
