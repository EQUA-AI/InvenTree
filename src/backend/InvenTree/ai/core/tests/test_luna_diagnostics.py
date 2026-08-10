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
    # Stateless chaining: ``previous_response_id`` is rejected by the provider
    # when the parent was sent with store:False (live-verified 2026-08-03), so
    # the follow-up must replay the full transcript instead — original user
    # message, the model's function_call item, then the tool output.
    assert "previous_response_id" not in follow_up
    types = [item.get("type", item.get("role")) for item in follow_up["input"]]
    assert types == ["user", "function_call", "function_call_output"]
    assert follow_up["input"][0] == client.responses.calls[0]["input"][0]
    assert follow_up["input"][-1]["call_id"] == follow_up["input"][1]["call_id"]
    assert outcome.provenance.tool_names == ("get_machine_context",)
    assert outcome.provenance.tool_rounds == 1


def test_second_tool_round_replays_the_accumulated_transcript() -> None:
    """Round N's request carries every prior round — nothing is stored remotely.

    Two tool rounds: the third request must contain the user message, both
    function_call items, and both function_call_output items in order. A
    reasoning item without encrypted content must NOT be replayed (the
    provider rejects it and tolerates its absence).
    """
    registry = _Registry()
    first = _response(response_id="resp_a", calls=[_tool_call(call_id="call_a")])
    first.output.insert(0, SimpleNamespace(type="reasoning", encrypted_content=None))
    client = _Client([
        first,
        _response(response_id="resp_b", calls=[_tool_call(call_id="call_b")]),
        _response(response_id="resp_final", text=_canonical_json()),
    ])
    outcome = asyncio.run(
        _adapter(client, registry=registry).reason(
            envelope=_envelope(tools=("get_machine_context",)),
            tool_context=object(),
        )
    )

    assert outcome.provenance.tool_rounds == 2
    third = client.responses.calls[2]
    assert "previous_response_id" not in third
    types = [item.get("type", item.get("role")) for item in third["input"]]
    assert types == [
        "user",
        "function_call",
        "function_call_output",
        "function_call",
        "function_call_output",
    ]
    call_ids = [item.get("call_id") for item in third["input"][1:]]
    assert call_ids == ["call_a", "call_a", "call_b", "call_b"]


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
    # Three responses: the corrective retry consumes one and the forged
    # citation is repeated, so the turn still ends unauthorized_evidence.
    rejected_client = _Client([
        _response(response_id="tool", calls=[_tool_call()]),
        _response(response_id="final", text=json.dumps(payload)),
        _response(response_id="final_retry", text=json.dumps(payload)),
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


def test_uncited_gate_cannot_be_dodged_by_kind_drift() -> None:
    """``kind`` is a model-chosen free string, so the gate must ignore it.

    Observed live (2026-08-03): the model labelled a grounded answer
    "diagnostic_response" rather than "repair_diagnosis". A gate keyed to one
    kind value would let an uncited recommendation through under any other
    label the model invents.
    """
    payload = json.loads(_canonical_json())
    payload["kind"] = "diagnostic_response"
    payload["recommended_actions"] = [
        {
            "kind": "read_only",
            "title": "Inspect the bearing",
            "detail": "Check the drive-end bearing temperature against the manual limit.",
            "requires_approval": False,
        }
    ]
    client = _Client([_response(response_id="drift", text=json.dumps(payload))])
    outcome = asyncio.run(_adapter(client).reason(envelope=_envelope()))

    assert outcome.response.response_state == "incomplete"
    assert outcome.provenance.outcome_code == "uncited_recommendation"


def test_invalid_tool_arguments_are_incomplete() -> None:
    client = _Client([_response(calls=[_tool_call(arguments="not-json")])])
    outcome = asyncio.run(
        _adapter(client, registry=_Registry()).reason(
            envelope=_envelope(tools=("get_machine_context",))
        )
    )

    assert outcome.response.response_state == "incomplete"
    assert outcome.response.speak is False
    assert outcome.response.recommended_actions == []
    assert outcome.provenance.outcome_code == "tool_arguments_invalid"


def test_denied_tool_call_gets_a_refusal_and_the_turn_recovers() -> None:
    """An invented tool name no longer kills the turn (live 2026-08-10).

    The model receives one constant refusal output and its corrected next
    round completes normally; the refusal text must reveal nothing about
    whether the record or tool exists.
    """
    client = _Client([
        _response(calls=[_tool_call(name="create_diagnostic_draft", call_id="bad_1")]),
        _response(text=_canonical_json()),
    ])
    outcome = asyncio.run(
        _adapter(client, registry=_Registry()).reason(
            envelope=_envelope(tools=("get_machine_context",))
        )
    )

    assert outcome.response.response_state == "complete"
    # The second request replays the refusal output for the bad call.
    second = client.responses.calls[1]
    refusals = [
        item
        for item in second["input"]
        if isinstance(item, dict)
        and item.get("call_id") == "bad_1"
        and item.get("type") == "function_call_output"
    ]
    assert len(refusals) == 1
    assert "tool_unavailable" in refusals[0]["output"]
    assert "create_diagnostic_draft" not in refusals[0]["output"]
    # Denied calls never reach provenance as executed tools.
    assert outcome.provenance.tool_names == ()


def test_persistent_denied_calls_still_end_the_turn() -> None:
    """The denial budget is a cap, not forgiveness: three bad calls die."""
    bad = [
        _response(calls=[_tool_call(name="create_diagnostic_draft", call_id=f"bad_{i}")])
        for i in range(3)
    ]
    client = _Client(bad)
    outcome = asyncio.run(
        _adapter(client, registry=_Registry()).reason(
            envelope=_envelope(tools=("get_machine_context",))
        )
    )
    assert outcome.provenance.outcome_code == "tool_denied"
    assert outcome.response.response_state == "incomplete"


def test_failed_tool_execution_gets_the_same_refusal_and_recovers() -> None:
    """An authorization failure is indistinguishable from an unknown name."""

    class _FailingRegistry(_Registry):
        async def execute(self, *, name, arguments, context):
            raise RuntimeError("authorization failed for record 44")

    client = _Client([
        _response(calls=[_tool_call(call_id="auth_1")]),
        _response(text=_canonical_json()),
    ])
    outcome = asyncio.run(
        _adapter(client, registry=_FailingRegistry()).reason(
            envelope=_envelope(tools=("get_machine_context",))
        )
    )
    assert outcome.response.response_state == "complete"
    second = client.responses.calls[1]
    refusals = [
        item
        for item in second["input"]
        if isinstance(item, dict)
        and item.get("call_id") == "auth_1"
        and item.get("type") == "function_call_output"
    ]
    assert len(refusals) == 1
    assert "tool_unavailable" in refusals[0]["output"]
    # The failure detail must never leak into what the model sees.
    assert "authorization failed" not in refusals[0]["output"]
    assert "44" not in refusals[0]["output"]


def test_invalid_final_schema_gets_one_corrective_retry() -> None:
    """Model JSON drift (live 08-03/08-10) is repaired once, then honest."""
    client = _Client([
        _response(response_id="resp_bad", text="{not valid json"),
        _response(response_id="resp_good", text=_canonical_json()),
    ])
    outcome = asyncio.run(_adapter(client).reason(envelope=_envelope()))

    assert outcome.response.response_state == "complete"
    assert outcome.provenance.provider_request_id == "resp_good"
    retry = client.responses.calls[1]
    note = retry["input"][-1]
    assert note["role"] == "user"
    assert "not a valid CanonicalTurnResponse" in note["content"]


def test_schema_retry_is_single_shot() -> None:
    """Two invalid finals stay invalid_final_schema — no retry loops."""
    client = _Client([
        _response(text="{not valid json"),
        _response(text="{still not valid"),
    ])
    outcome = asyncio.run(_adapter(client).reason(envelope=_envelope()))
    assert outcome.provenance.outcome_code == "invalid_final_schema"
    assert len(client.responses.calls) == 2


def test_history_directive_only_when_tools_offered() -> None:
    """The instructions never tempt tools the envelope cannot honour."""
    with_history = _Client([_response(text=_canonical_json())])
    asyncio.run(
        _adapter(with_history).reason(envelope=_envelope(tools=("get_recent_maintenance_history",)))
    )
    assert "find_similar_past_repairs" in with_history.responses.calls[0]["instructions"]

    without = _Client([_response(text=_canonical_json())])
    asyncio.run(_adapter(without).reason(envelope=_envelope(tools=("get_machine_context",))))
    instructions = without.responses.calls[0]["instructions"]
    assert "find_similar_past_repairs" not in instructions
    assert "never invent or guess a tool name" in instructions


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
    # Two invalid finals: the schema retry (single-shot) runs and the turn
    # still reports honestly.
    invalid = asyncio.run(
        _adapter(
            _Client([
                _response(text='{"unexpected":true}'),
                _response(text='{"unexpected":true}'),
            ])
        ).reason(envelope=_envelope())
    )

    assert canceled.provenance.outcome_code == "canceled"
    assert timed_out.provenance.outcome_code == "timeout"
    assert lost.provenance.outcome_code == "lost_final_response"
    assert invalid.provenance.outcome_code == "invalid_final_schema"
    assert all(
        outcome.response.response_state == "incomplete"
        for outcome in (canceled, timed_out, lost, invalid)
    )


def test_unauthorized_evidence_gets_one_corrective_retry() -> None:
    """A citation-mangled final is corrected once, then honest (2026-08-10)."""
    cited = json.loads(_canonical_json())
    cited["evidence"] = [
        {
            "source_type": "asset_machine",
            "source_id": "44",
            "source_revision": "2026-08-01T00:00:00+00:00",
            "authorization_class": "maintenance_scope",
            "as_of": "2026-08-01T00:00:00+00:00",
            "locator": {"field": "/machines/44"},
            "claim": "The machine context supports this.",
        }
    ]
    client = _Client([
        _response(response_id="resp_cited", text=json.dumps(cited)),
        _response(response_id="resp_clean", text=_canonical_json()),
    ])
    outcome = asyncio.run(_adapter(client).reason(envelope=_envelope()))

    assert outcome.response.response_state == "complete"
    assert outcome.provenance.provider_request_id == "resp_clean"
    note = client.responses.calls[1]["input"][-1]
    assert note["role"] == "user"
    assert "did not reproduce a local tool citation exactly" in note["content"]
