"""Deterministic contract tests for the bounded Foundry reasoning adapter."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from ai.core.reasoning.luna_diagnostics import (
    InvalidReasoningEffort,
    LunaDiagnosticsAdapter,
    ReasoningProviderConfig,
    ToolLoopBudget,
    TrustedReasoningEnvelope,
)


def _envelope(*, tools: tuple[str, ...] = ()) -> TrustedReasoningEnvelope:
    return TrustedReasoningEnvelope(
        actor_id="user:7",
        scope={"customer_id": 9, "site_key": "plant-a"},
        thread_id="thread_reasoning",
        machine_id=44,
        repair_packet_id=123,
        user_message="The drive-end bearing is hot.",
        mode="voice",
        allowed_tool_names=tools,
        policy_version="1",
        correlation_id="00000000-0000-0000-0000-000000000007",
    )


def _canonical_json() -> str:
    return json.dumps({
        "kind": "repair_diagnosis",
        "response_version": 1,
        "response_state": "complete",
        "detailed_response": (
            "Check the authoritative safety status before proceeding. "
            "No authorized equipment evidence was available, so no equipment "
            "conclusion was produced."
        ),
        "spoken_summary": (
            "No authorized equipment evidence was available, so no equipment "
            "conclusion was produced. Check the authoritative safety status "
            "before proceeding."
        ),
        "reasoning_summary": "The response abstains without authorized evidence.",
        "confidence": "low",
        "evidence": [],
        "next_questions": [],
        "recommended_actions": [],
        "safety_boundary": ("Check the authoritative safety status before proceeding."),
        "speak": True,
    })


class _Responses:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.outputs.pop(0)


class _Client:
    def __init__(self, outputs):
        self.responses = _Responses(outputs)


def _response(*, response_id="resp_1", text="", calls=(), output_tokens=None):
    usage = SimpleNamespace(output_tokens=output_tokens) if output_tokens is not None else None
    return SimpleNamespace(
        id=response_id,
        output_text=text,
        output=list(calls),
        usage=usage,
    )


def _tool_call(name="get_machine_context", arguments='{"machine_id":44}', call_id="call_1"):
    return SimpleNamespace(type="function_call", name=name, arguments=arguments, call_id=call_id)


class _Registry:
    def __init__(self, result=None):
        self.result = result or {
            "data": {"machine_id": 44},
            "evidence": [{"source_id": "44", "source_revision": "2"}],
        }
        self.executions: list[dict] = []

    def provider_tools(self, *, context: object):
        del context
        return [
            {
                "type": "function",
                "name": "get_machine_context",
                "description": "Read current authorized machine context",
                "parameters": {
                    "type": "object",
                    "properties": {"machine_id": {"type": "integer"}},
                    "required": ["machine_id"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        ]

    async def execute(self, *, name, arguments, context):
        self.executions.append({"name": name, "arguments": arguments, "context": context})
        return self.result


def _config(mode="agent_reference") -> ReasoningProviderConfig:
    return ReasoningProviderConfig(
        invocation_mode=mode,
        project_endpoint=("https://aimms-foundry.services.ai.azure.com/api/projects/Epcon-AIMMS"),
        agent_name="voice-agent-test",
        agent_version="3",
        direct_endpoint="https://example.openai.azure.com",
        direct_deployment="gpt-5.6-luna",
        direct_api_version="2025-04-01-preview",
        default_effort="medium",
    )


def _adapter(client, *, registry=None, mode="agent_reference", budget=None):
    return LunaDiagnosticsAdapter(
        provider_config=_config(mode),
        budget=budget or ToolLoopBudget(),
        tool_registry=registry,
        client_factory=lambda: client,
    )


def test_rejects_invalid_effort_before_provider_dispatch() -> None:
    client = _Client([_response(text=_canonical_json())])
    adapter = _adapter(client)

    with pytest.raises(InvalidReasoningEffort):
        asyncio.run(adapter.reason(envelope=_envelope(), effort="xhigh"))

    assert client.responses.calls == []


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_agent_reference_dispatch_is_pinned_and_schema_strict(effort: str) -> None:
    client = _Client([_response(response_id="resp_agent", text=_canonical_json())])
    outcome = asyncio.run(_adapter(client).reason(envelope=_envelope(), effort=effort))

    request = client.responses.calls[0]
    assert request["extra_body"] == {
        "agent_reference": {
            "name": "voice-agent-test",
            "version": "3",
            "type": "agent_reference",
        }
    }
    assert request["reasoning"] == {"effort": effort}
    assert request["text"]["format"]["type"] == "json_schema"
    assert request["text"]["format"]["strict"] is True
    assert request["store"] is False
    assert outcome.response.response_state == "complete"
    assert outcome.provenance.provider_request_id == "resp_agent"
    assert outcome.provenance.agent_version == "3"
    assert "reasoning" not in outcome.provenance.to_dict()


def test_direct_deployment_is_supported_as_explicit_alternative() -> None:
    client = _Client([_response(text=_canonical_json())])
    outcome = asyncio.run(_adapter(client, mode="direct_deployment").reason(envelope=_envelope()))

    request = client.responses.calls[0]
    assert request["model"] == "gpt-5.6-luna"
    assert "extra_body" not in request
    assert outcome.provenance.invocation_mode == "direct_deployment"
    assert outcome.provenance.deployment == "gpt-5.6-luna"


def test_tool_calls_round_trip_through_local_registry_with_fresh_context() -> None:
    registry = _Registry()
    tool_context = object()
    client = _Client([
        _response(response_id="resp_tool", calls=[_tool_call()]),
        _response(response_id="resp_final", text=_canonical_json()),
    ])
    outcome = asyncio.run(
        _adapter(client, registry=registry).reason(
            envelope=_envelope(tools=("get_machine_context",)),
            tool_context=tool_context,
        )
    )

    assert registry.executions == [
        {
            "name": "get_machine_context",
            "arguments": {"machine_id": 44},
            "context": tool_context,
        }
    ]
    follow_up = client.responses.calls[1]
    assert follow_up["previous_response_id"] == "resp_tool"
    assert follow_up["input"][0]["type"] == "function_call_output"
    assert outcome.provenance.tool_names == ("get_machine_context",)
    assert outcome.provenance.tool_rounds == 1


def test_final_evidence_must_match_a_locally_authorized_tool_citation() -> None:
    citation = {
        "source_type": "asset_machine",
        "id": "44",
        "revision": "machine-r2",
        "locator": "/machines/44",
        "as_of": "2026-07-15T08:00:00+00:00",
        "authorization_class": "maintenance_scope",
        "claim": "Observed machine identity.",
    }
    registry = _Registry(result={"status": "ok", "evidence": [citation], "truncated": False})
    payload = json.loads(_canonical_json())
    payload["evidence"] = [
        {
            "source_type": citation["source_type"],
            "source_id": citation["id"],
            "source_revision": citation["revision"],
            "locator": {"field": citation["locator"]},
            "as_of": citation["as_of"],
            "authorization_class": citation["authorization_class"],
            "claim": "Observed machine identity.",
        }
    ]
    client = _Client([
        _response(response_id="tool", calls=[_tool_call()]),
        _response(response_id="final", text=json.dumps(payload)),
    ])

    accepted = asyncio.run(
        _adapter(client, registry=registry).reason(
            envelope=_envelope(tools=("get_machine_context",))
        )
    )

    payload["evidence"][0]["source_revision"] = "invented-revision"
    rejected_client = _Client([
        _response(response_id="tool", calls=[_tool_call()]),
        _response(response_id="final", text=json.dumps(payload)),
    ])
    rejected = asyncio.run(
        _adapter(rejected_client, registry=registry).reason(
            envelope=_envelope(tools=("get_machine_context",))
        )
    )

    assert accepted.response.response_state == "complete"
    assert accepted.response.evidence[0].source_revision == "machine-r2"
    assert rejected.response.response_state == "incomplete"
    assert rejected.provenance.outcome_code == "unauthorized_evidence"


def test_complete_diagnosis_recommending_action_without_evidence_is_incomplete() -> None:
    """The evidence gate must not pass vacuously on an empty citation list.

    A "complete" repair diagnosis that recommends action while citing nothing
    is exactly the uncited answer the adapter exists to prevent; it must land
    as the honest ``uncited_recommendation`` incomplete outcome. The evidence-
    free *abstention* shape (no recommendations) stays legal — that carve-out
    is pinned here too.
    """
    payload = json.loads(_canonical_json())
    payload["recommended_actions"] = [
        {
            "kind": "read_only",
            "title": "Inspect the bearing",
            "detail": "Check the drive-end bearing temperature against the manual limit.",
            "requires_approval": False,
        }
    ]
    client = _Client([_response(response_id="uncited", text=json.dumps(payload))])
    uncited = asyncio.run(_adapter(client).reason(envelope=_envelope()))

    assert uncited.response.response_state == "incomplete"
    assert uncited.provenance.outcome_code == "uncited_recommendation"
    assert uncited.response.recommended_actions == []
    assert uncited.response.speak is False

    abstention_client = _Client([_response(response_id="abstain", text=_canonical_json())])
    abstention = asyncio.run(_adapter(abstention_client).reason(envelope=_envelope()))

    assert abstention.response.response_state == "complete"
    assert abstention.response.evidence == []
    assert abstention.response.recommended_actions == []


@pytest.mark.parametrize(
    ("call", "expected_code"),
    [
        (_tool_call(name="create_diagnostic_draft"), "tool_denied"),
        (_tool_call(arguments="not-json"), "tool_arguments_invalid"),
    ],
)
def test_invalid_or_unavailable_tool_calls_are_incomplete(call, expected_code) -> None:
    client = _Client([_response(calls=[call])])
    outcome = asyncio.run(
        _adapter(client, registry=_Registry()).reason(
            envelope=_envelope(tools=("get_machine_context",))
        )
    )

    assert outcome.response.response_state == "incomplete"
    assert outcome.response.speak is False
    assert outcome.response.recommended_actions == []
    assert outcome.provenance.outcome_code == expected_code


def test_non_json_tool_result_is_incomplete() -> None:
    client = _Client([_response(calls=[_tool_call()])])
    outcome = asyncio.run(
        _adapter(client, registry=_Registry(result=object())).reason(
            envelope=_envelope(tools=("get_machine_context",))
        )
    )

    assert outcome.response.response_state == "incomplete"
    assert outcome.provenance.outcome_code == "tool_result_invalid"


def test_tool_round_and_data_budgets_end_in_exact_incomplete_state() -> None:
    round_client = _Client([
        _response(response_id="one", calls=[_tool_call(call_id="one")]),
        _response(response_id="two", calls=[_tool_call(call_id="two")]),
    ])
    round_outcome = asyncio.run(
        _adapter(
            round_client,
            registry=_Registry(),
            budget=ToolLoopBudget(max_tool_rounds=1),
        ).reason(
            envelope=_envelope(tools=("get_machine_context",)),
        )
    )

    large_registry = _Registry(result={"content": "x" * 2048})
    data_client = _Client([_response(calls=[_tool_call()])])
    data_outcome = asyncio.run(
        _adapter(
            data_client,
            registry=large_registry,
            budget=ToolLoopBudget(max_tool_data_bytes=1024),
        ).reason(envelope=_envelope(tools=("get_machine_context",)))
    )

    assert round_outcome.provenance.outcome_code == "tool_round_limit"
    assert data_outcome.provenance.outcome_code == "tool_data_limit"
    for outcome in (round_outcome, data_outcome):
        assert outcome.response.response_state == "incomplete"
        assert outcome.response.spoken_summary == ""


def test_multiple_calls_in_one_provider_round_fail_closed() -> None:
    client = _Client([
        _response(
            calls=[
                _tool_call(call_id="one"),
                _tool_call(call_id="two"),
            ]
        )
    ])
    outcome = asyncio.run(
        _adapter(client, registry=_Registry()).reason(
            envelope=_envelope(tools=("get_machine_context",))
        )
    )

    assert outcome.provenance.outcome_code == "tool_round_limit"
    assert outcome.response.response_state == "incomplete"


def test_output_token_budget_is_cumulative_across_tool_rounds() -> None:
    client = _Client([
        _response(
            response_id="token-heavy-tool-call",
            calls=[_tool_call()],
            output_tokens=5900,
        ),
        _response(text=_canonical_json()),
    ])
    outcome = asyncio.run(
        _adapter(client, registry=_Registry()).reason(
            envelope=_envelope(tools=("get_machine_context",))
        )
    )

    assert outcome.response.response_state == "incomplete"
    assert outcome.provenance.outcome_code == "output_token_limit"
    assert len(client.responses.calls) == 1


def test_cancel_and_timeout_cover_in_flight_provider_and_local_tool_work() -> None:
    class BlockingResponses:
        def __init__(self):
            self.started = asyncio.Event()

        async def create(self, **_kwargs):
            self.started.set()
            await asyncio.Event().wait()

    async def cancel_provider():
        responses = BlockingResponses()
        cancel = asyncio.Event()
        task = asyncio.create_task(
            _adapter(SimpleNamespace(responses=responses)).reason(
                envelope=_envelope(), cancel_event=cancel
            )
        )
        await responses.started.wait()
        cancel.set()
        return await task

    class SlowRegistry(_Registry):
        async def execute(self, *, name, arguments, context):
            del name, arguments, context
            await asyncio.sleep(1)

    canceled = asyncio.run(cancel_provider())
    timed_out = asyncio.run(
        _adapter(
            _Client([_response(calls=[_tool_call()])]),
            registry=SlowRegistry(),
            budget=ToolLoopBudget(timeout_seconds=0.01),
        ).reason(envelope=_envelope(tools=("get_machine_context",)))
    )

    assert canceled.provenance.outcome_code == "canceled"
    assert timed_out.provenance.outcome_code == "timeout"


def test_cancel_timeout_lost_response_and_invalid_schema_are_incomplete() -> None:
    cancel = asyncio.Event()
    cancel.set()
    canceled = asyncio.run(_adapter(_Client([])).reason(envelope=_envelope(), cancel_event=cancel))

    class SlowResponses:
        def __init__(self):
            self.calls = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            await asyncio.sleep(1)

    slow_client = SimpleNamespace(responses=SlowResponses())
    timed_out = asyncio.run(
        _adapter(
            slow_client,
            budget=ToolLoopBudget(timeout_seconds=0.01),
        ).reason(envelope=_envelope())
    )
    lost = asyncio.run(_adapter(_Client([_response(text="")])).reason(envelope=_envelope()))
    invalid = asyncio.run(
        _adapter(_Client([_response(text='{"unexpected":true}')])).reason(envelope=_envelope())
    )

    assert canceled.provenance.outcome_code == "canceled"
    assert timed_out.provenance.outcome_code == "timeout"
    assert lost.provenance.outcome_code == "lost_final_response"
    assert invalid.provenance.outcome_code == "invalid_final_schema"
    assert all(
        outcome.response.response_state == "incomplete"
        for outcome in (canceled, timed_out, lost, invalid)
    )
