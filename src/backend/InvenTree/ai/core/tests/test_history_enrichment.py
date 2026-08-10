"""S26: confidence-gated server-side history enrichment.

A COMPLETE medium/high-confidence diagnosis that cites no history and never
consulted a history tool gets ONE server-initiated continuation: the server
executes the history reads itself, replays the full transcript statelessly
with the results appended, and re-validates through the same finalizer. Any
failure keeps the original outcome — enrichment may only ever improve an
answer, never lose one.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from ai.core.reasoning.luna_diagnostics import (
    AuthorizedRecord,
    LunaDiagnosticsAdapter,
    ToolLoopBudget,
    TrustedReasoningEnvelope,
)
from ai.core.tests.test_luna_diagnostics import (
    _canonical_json,
    _Client,
    _config,
    _response,
)

_HISTORY_TOOLS = ("get_recent_maintenance_history", "find_similar_past_repairs")


def _envelope(*, tools=_HISTORY_TOOLS, records=True) -> TrustedReasoningEnvelope:
    return TrustedReasoningEnvelope(
        actor_id="user:7",
        scope={"customer_id": 9, "site_key": "plant-a"},
        thread_id="thread_enrichment",
        machine_id=44,
        repair_packet_id=123,
        user_message="The drive-end bearing is hot.",
        mode="voice",
        allowed_tool_names=tuple(tools),
        authorized_records=(
            (
                AuthorizedRecord(
                    entity_type="machine",
                    entity_id=44,
                    expected_revision="rev-44",
                    display_name="Influent Pump Station No. 1",
                ),
            )
            if records
            else ()
        ),
        policy_version="1",
        correlation_id="00000000-0000-0000-0000-000000000026",
    )


def _diagnosis_json(*, confidence="medium") -> str:
    payload = json.loads(_canonical_json())
    payload["confidence"] = confidence
    return json.dumps(payload)


class _HistoryRegistry:
    """Registry fake: records aexecute calls, returns citeable history."""

    def __init__(self):
        self.executions: list[dict] = []

    def provider_tools(self, *, context: object):
        del context
        return []

    async def aexecute(self, *, name, arguments, context):
        del context
        self.executions.append({"name": name, "arguments": dict(arguments)})
        return {
            "tool": name,
            "status": "ok",
            "evidence": [
                {
                    "source_type": "work_order_closeout",
                    "id": "9",
                    "revision": "hash-9",
                    "authorization_class": "maintenance_scope",
                    "locator": "/maintenance/work-orders/9/closeout",
                    "as_of": "2026-08-01T00:00:00+00:00",
                    "claim": '{"action":"replaced seal"}',
                }
            ],
        }


def _adapter(client, *, registry=None, budget=None):
    return LunaDiagnosticsAdapter(
        provider_config=_config("direct_deployment"),
        budget=budget or ToolLoopBudget(),
        tool_registry=registry if registry is not None else _HistoryRegistry(),
        client_factory=lambda: client,
    )


def _settings(enabled=True):
    return SimpleNamespace(feature_history_enrichment=enabled)


def _run(adapter, envelope):
    return asyncio.run(adapter.reason(envelope=envelope, tool_context=object()))


def test_enrichment_fires_once_and_is_recorded() -> None:
    """The trigger case: server reads history, one continuation, counted."""
    registry = _HistoryRegistry()
    client = _Client([
        _response(response_id="resp_1", text=_diagnosis_json(confidence="medium")),
        _response(response_id="resp_2", text=_diagnosis_json(confidence="high")),
    ])
    adapter = _adapter(client, registry=registry)
    with patch("ai.core.config.get_settings", return_value=_settings(True)):
        outcome = _run(adapter, _envelope())

    assert outcome.response.confidence == "high"
    assert outcome.provenance.history_enrichment_rounds == 1
    assert [call["name"] for call in registry.executions] == list(_HISTORY_TOOLS)
    # The similar-repairs read scores against the current packet.
    similar = registry.executions[1]["arguments"]
    assert similar == {
        "machine_id": 44,
        "expected_revision": "rev-44",
        "repair_packet_id": 123,
    }
    assert set(_HISTORY_TOOLS) <= set(outcome.provenance.tool_names)


def test_continuation_transcript_is_a_stateless_full_replay() -> None:
    """Input two: user turn, replayed answer, synthetic calls, note. No refs."""
    client = _Client([
        _response(text=_diagnosis_json()),
        _response(text=_diagnosis_json(confidence="high")),
    ])
    adapter = _adapter(client)
    with patch("ai.core.config.get_settings", return_value=_settings(True)):
        _run(adapter, _envelope())

    assert len(client.responses.calls) == 2
    continuation = client.responses.calls[1]
    assert "previous_response_id" not in continuation
    kinds = [item.get("type", item.get("role")) for item in continuation["input"]]
    assert kinds == [
        "user",
        "function_call",
        "function_call_output",
        "function_call",
        "function_call_output",
        "user",
    ]
    assert "[SERVER-HISTORY-ENRICHMENT]" in continuation["input"][-1]["content"]


def test_provider_rejecting_synthetic_calls_falls_back_to_fenced_note() -> None:
    """A rejected synthetic transcript retries once with inlined results."""

    class _PickyResponses:
        def __init__(self, outputs):
            self.outputs = list(outputs)
            self.calls: list[dict] = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            if any(
                str(item.get("call_id", "")).startswith("srv_history")
                for item in kwargs["input"]
                if isinstance(item, dict)
            ):
                raise RuntimeError("Unknown call_id in input")
            return self.outputs.pop(0)

    client = SimpleNamespace(
        responses=_PickyResponses([
            _response(text=_diagnosis_json()),
            _response(text=_diagnosis_json(confidence="high")),
        ])
    )
    # First dispatch has no synthetic ids; second (primary enrichment) is
    # rejected; third (fallback) succeeds.
    adapter = _adapter(client)
    with patch("ai.core.config.get_settings", return_value=_settings(True)):
        outcome = _run(adapter, _envelope())

    assert outcome.provenance.history_enrichment_rounds == 1
    fallback = client.responses.calls[-1]
    kinds = [item.get("type", item.get("role")) for item in fallback["input"]]
    assert kinds == ["user", "user"]
    assert "Result of get_recent_maintenance_history" in fallback["input"][-1]["content"]


def test_no_enrichment_on_low_confidence() -> None:
    """A low-confidence answer is not worth a continuation."""
    registry = _HistoryRegistry()
    client = _Client([_response(text=_diagnosis_json(confidence="low"))])
    adapter = _adapter(client, registry=registry)
    with patch("ai.core.config.get_settings", return_value=_settings(True)):
        outcome = _run(adapter, _envelope())
    assert outcome.provenance.history_enrichment_rounds == 0
    assert registry.executions == []
    assert len(client.responses.calls) == 1


def test_no_enrichment_when_flag_dark() -> None:
    """The feature flag gates the whole path."""
    registry = _HistoryRegistry()
    client = _Client([_response(text=_diagnosis_json())])
    adapter = _adapter(client, registry=registry)
    with patch("ai.core.config.get_settings", return_value=_settings(False)):
        outcome = _run(adapter, _envelope())
    assert outcome.provenance.history_enrichment_rounds == 0
    assert registry.executions == []


def test_no_enrichment_when_budget_rounds_zero() -> None:
    """AZURE_LUNA_HISTORY_ENRICHMENT_ROUNDS=0 disables even with the flag on."""
    client = _Client([_response(text=_diagnosis_json())])
    adapter = _adapter(client, budget=ToolLoopBudget(history_enrichment_rounds=0))
    with patch("ai.core.config.get_settings", return_value=_settings(True)):
        outcome = _run(adapter, _envelope())
    assert outcome.provenance.history_enrichment_rounds == 0
    assert len(client.responses.calls) == 1


def test_no_enrichment_when_history_tool_was_already_called() -> None:
    """A diagnosis that already consulted history is left alone."""
    call = SimpleNamespace(
        type="function_call",
        name="get_recent_maintenance_history",
        arguments='{"machine_id":44,"expected_revision":"rev-44"}',
        call_id="call_1",
    )
    registry = _HistoryRegistry()
    client = _Client([
        _response(calls=[call]),
        _response(text=_diagnosis_json()),
    ])
    adapter = _adapter(client, registry=registry)
    with patch("ai.core.config.get_settings", return_value=_settings(True)):
        outcome = _run(adapter, _envelope(tools=("get_recent_maintenance_history",)))
    assert outcome.provenance.history_enrichment_rounds == 0
    # Exactly the model-initiated execution, no server-initiated ones.
    assert len(registry.executions) == 1


def test_no_enrichment_without_a_machine_root() -> None:
    """No authorized machine root means nothing can be read server-side."""
    registry = _HistoryRegistry()
    client = _Client([_response(text=_diagnosis_json())])
    adapter = _adapter(client, registry=registry)
    with patch("ai.core.config.get_settings", return_value=_settings(True)):
        outcome = _run(adapter, _envelope(records=False))
    assert outcome.provenance.history_enrichment_rounds == 0
    assert registry.executions == []


def test_short_deadline_skips_enrichment_honestly() -> None:
    """Below the 8s floor the original answer ships instead of a timeout."""
    registry = _HistoryRegistry()
    client = _Client([_response(text=_diagnosis_json())])
    adapter = _adapter(client, registry=registry, budget=ToolLoopBudget(timeout_seconds=5.0))
    with patch("ai.core.config.get_settings", return_value=_settings(True)):
        outcome = _run(adapter, _envelope())
    assert outcome.provenance.history_enrichment_rounds == 0
    assert registry.executions == []


def test_failed_continuation_keeps_the_original_outcome() -> None:
    """An invalid continuation degrades silently to the first answer."""
    client = _Client([
        _response(response_id="resp_1", text=_diagnosis_json()),
        _response(response_id="resp_2", text="{not json"),
    ])
    adapter = _adapter(client)
    with patch("ai.core.config.get_settings", return_value=_settings(True)):
        outcome = _run(adapter, _envelope())
    assert outcome.response.confidence == "medium"
    assert outcome.provenance.outcome_code == "complete"
    assert outcome.provenance.history_enrichment_rounds == 0
    assert outcome.provenance.provider_request_id == "resp_1"


def test_continuation_requesting_more_tools_forfeits_enrichment() -> None:
    """The continuation gets no further tool rounds."""
    call = SimpleNamespace(
        type="function_call",
        name="get_machine_context",
        arguments='{"machine_id":44,"expected_revision":"rev-44"}',
        call_id="call_9",
    )
    client = _Client([
        _response(text=_diagnosis_json()),
        _response(calls=[call]),
    ])
    adapter = _adapter(client)
    with patch("ai.core.config.get_settings", return_value=_settings(True)):
        outcome = _run(adapter, _envelope())
    assert outcome.provenance.history_enrichment_rounds == 0
    assert outcome.response.confidence == "medium"


def test_history_evidence_already_cited_skips_enrichment() -> None:
    """History evidence in the answer means the gate has nothing to add.

    Unit-level on the gate itself: building a full turn whose history
    citation is AUTHORIZED needs a real tool round, and the previous
    fixture quietly failed schema validation instead of testing this.
    """
    from ai.core.reasoning.luna_diagnostics import ReasoningOutcome
    from ai.core.reasoning.schemas import CanonicalTurnResponse

    registry = _HistoryRegistry()
    adapter = _adapter(_Client([]), registry=registry)
    payload = json.loads(_diagnosis_json())
    payload["evidence"] = [
        {
            "source_type": "asset_maintenance_record",
            "source_id": "5",
            "source_revision": "2026-08-01T00:00:00+00:00",
            "authorization_class": "maintenance_scope",
            "as_of": datetime(2026, 8, 1, tzinfo=UTC),
            "locator": {"field": "/machines/44/maintenance/5"},
            "claim": "Seal replaced two weeks ago.",
        }
    ]
    canonical = CanonicalTurnResponse.model_validate(payload)
    outcome = ReasoningOutcome(
        response=canonical,
        provenance=adapter._provenance(
            effort="medium",
            request_id="resp_1",
            tool_names=(),
            tool_rounds=0,
            outcome_code="complete",
        ),
    )
    with patch("ai.core.config.get_settings", return_value=_settings(True)):
        result = asyncio.run(
            adapter._maybe_enrich_with_history(
                outcome=outcome,
                envelope=_envelope(),
                tool_context=object(),
                transcript=[],
                response=_response(text=_diagnosis_json()),
                selected_effort="medium",
                request_id="resp_1",
                tool_names=[],
                tool_rounds=0,
                tool_data_bytes=0,
                authorized_citations=set(),
                deadline=1e12,
                output_tokens_used=0,
                cancel_event=None,
            )
        )
    assert result is None
    assert registry.executions == []
