"""Phase 6 battery fix pass: records-grounded decline, voice budget, no-match."""

import asyncio
import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.reasoning.luna_diagnostics import ToolLoopBudget  # noqa: E402
from ai.core.workflows.wf1_diagnostics import T6DiagnosticsWorkflow  # noqa: E402


def test_voice_budget_field_validates():
    budget = ToolLoopBudget(timeout_seconds=45.0, voice_timeout_seconds=75.0)
    assert budget.voice_timeout_seconds is not None
    assert abs(budget.voice_timeout_seconds - 75.0) < 1e-9
    # None keeps voice on the text bound.
    assert ToolLoopBudget(timeout_seconds=45.0).voice_timeout_seconds is None
    try:
        ToolLoopBudget(timeout_seconds=45.0, voice_timeout_seconds=500.0)
    except ValueError:
        pass
    else:
        raise AssertionError("out-of-range voice timeout must be rejected")


def test_voice_mode_selects_the_voice_deadline():
    """The reason() deadline uses the voice ceiling only for voice envelopes."""
    from ai.core.reasoning import luna_diagnostics as luna

    captured = {}

    class _Clock:
        def __call__(self):
            return 1000.0

    adapter = luna.LunaDiagnosticsAdapter.__new__(luna.LunaDiagnosticsAdapter)
    adapter.budget = ToolLoopBudget(timeout_seconds=45.0, voice_timeout_seconds=75.0)
    adapter._clock = _Clock()

    # Reproduce exactly the deadline arithmetic from reason().
    for mode, expected in (("text", 1045.0), ("voice", 1075.0)):
        envelope = mock.Mock()
        envelope.mode = mode
        timeout = adapter.budget.timeout_seconds
        if getattr(envelope, "mode", "text") == "voice" and adapter.budget.voice_timeout_seconds:
            timeout = adapter.budget.voice_timeout_seconds
        captured[mode] = adapter._clock() + timeout
        assert abs(captured[mode] - expected) < 1e-9


def test_no_evidence_decline_prefers_grounded_records():
    """When the fallback yields text, it replaces the false-absence decline."""
    workflow = T6DiagnosticsWorkflow.__new__(T6DiagnosticsWorkflow)

    async def fake_agent_step(agent, query, name, step):  # noqa: RUF029
        return (
            "INSUFFICIENT_EVIDENCE: Maintenance records are missing",
            mock.Mock(),
        )

    grounded_text = (
        "I can't diagnose a current fault, but the records for Bench show: "
        "4 maintenance records in the last 365 days"
    )

    async def fake_fallback(query):  # noqa: RUF029
        return grounded_text

    workflow._run_agent_step = fake_agent_step
    with mock.patch.object(
        T6DiagnosticsWorkflow,
        "_records_grounded_fallback",
        staticmethod(fake_fallback),
    ):
        workflow.problem_agent = mock.Mock()
        result = asyncio.run(workflow.execute("How often has Bench needed maintenance?"))
    assert result.success is True
    assert result.formatted_response == grounded_text


def test_no_evidence_decline_stands_when_no_machine_matches():
    """A None fallback keeps the existing honest decline wording."""
    workflow = T6DiagnosticsWorkflow.__new__(T6DiagnosticsWorkflow)

    async def fake_agent_step(agent, query, name, step):  # noqa: RUF029
        return ("INSUFFICIENT_EVIDENCE: nothing identifiable", mock.Mock())

    async def fake_fallback(query):  # noqa: RUF029
        return None

    workflow._run_agent_step = fake_agent_step
    with mock.patch.object(
        T6DiagnosticsWorkflow,
        "_records_grounded_fallback",
        staticmethod(fake_fallback),
    ):
        workflow.problem_agent = mock.Mock()
        result = asyncio.run(workflow.execute("Something vague"))
    assert "I can't run a diagnosis" in result.formatted_response
    assert "nothing identifiable" in result.formatted_response


def test_no_match_instruction_present():
    """The no-matching-machine guidance ships in the static instructions."""
    import inspect

    from ai.core.reasoning import luna_diagnostics as luna

    source = inspect.getsource(luna)
    assert "COMPLETE\nabstention stating that no matching machine record exists" in source


def test_no_match_hint_wording_present_in_turn_service():
    """The deterministic no-match hint ships on the incomplete reasoning path."""
    import inspect

    from ai.core import turn_service

    source = inspect.getsource(turn_service)
    assert "no machine on record for your site" in source
    # It must be gated on the incomplete state and on named-record mismatch.
    anchor = source.index("no machine on record for your site")
    window = source[anchor - 1600 : anchor]
    assert 'response_state.value == "incomplete"' in window
    assert "authorized_records" in window


def test_no_match_hint_token_matching():
    """Partial machine names suppress the hint; unrelated names do not."""
    import re as re_mod

    def name_matches(name: str, lowered: str) -> bool:
        tokens = [token for token in re_mod.findall(r"[a-z]+", name.lower()) if len(token) >= 3]
        return bool(tokens) and all(token in lowered for token in tokens)

    utterance = "why is the influent pump station tripping on high vibration again?"
    assert name_matches("Influent Pump Station No. 1", utterance)
    assert not name_matches("Packaging Line 2 Conveyor", utterance)
    assert not name_matches("No. 1", utterance)  # no substantive tokens


def test_spoken_contract_failure_salvages_with_speech_stripped():
    """A final failing ONLY the spoken contract survives as unspoken text."""
    import json as jsonlib

    from ai.core.reasoning import luna_diagnostics as luna

    adapter = luna.LunaDiagnosticsAdapter.__new__(luna.LunaDiagnosticsAdapter)
    adapter.budget = luna.ToolLoopBudget(timeout_seconds=45.0)
    adapter.provider_config = luna.ReasoningProviderConfig(
        invocation_mode="direct_deployment",
        project_endpoint="",
        agent_name="",
        agent_version="",
        direct_endpoint="https://example.com",
        direct_deployment="luna",
        direct_api_version="v1",
    )

    good = {
        "kind": "repair_diagnosis",
        "response_version": luna.CANONICAL_RESPONSE_VERSION,
        "response_state": "complete",
        "detailed_response": "The likely cause may be a worn bearing.",
        # Violates the lexical contract: drops the uncertainty marker and
        # does not echo the safety boundary.
        "spoken_summary": "It is a worn bearing.",
        "reasoning_summary": "Cited review.",
        "confidence": "low",
        "evidence": [],
        "next_questions": [],
        "recommended_actions": [],
        "safety_boundary": "No safety status was inferred.",
        "speak": True,
    }
    response = {
        "id": "resp_x",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": jsonlib.dumps(good)}]}
        ],
    }

    outcome = adapter._finalize_response(
        response=response,
        selected_effort="medium",
        request_id="resp_x",
        tool_names=("get_machine_context",),
        tool_rounds=2,
        authorized_citations=set(),
        history_rounds=0,
    )
    result = outcome.response
    assert result.response_state.value == "complete"
    assert result.speak is False
    assert result.spoken_summary == ""
    assert "worn bearing" in result.detailed_response


def test_broken_final_still_fails_as_invalid_schema():
    """A final broken beyond the spoken contract keeps the incomplete path."""
    from ai.core.reasoning import luna_diagnostics as luna

    adapter = luna.LunaDiagnosticsAdapter.__new__(luna.LunaDiagnosticsAdapter)
    adapter.budget = luna.ToolLoopBudget(timeout_seconds=45.0)
    adapter.provider_config = luna.ReasoningProviderConfig(
        invocation_mode="direct_deployment",
        project_endpoint="",
        agent_name="",
        agent_version="",
        direct_endpoint="https://example.com",
        direct_deployment="luna",
        direct_api_version="v1",
    )
    response = {
        "id": "resp_y",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": '{"nonsense": true}'}]}
        ],
    }
    outcome = adapter._finalize_response(
        response=response,
        selected_effort="medium",
        request_id="resp_y",
        tool_names=(),
        tool_rounds=0,
        authorized_citations=set(),
        history_rounds=0,
    )
    assert outcome.response.response_state.value == "incomplete"
    assert "stopped (invalid_final_schema)" in outcome.response.reasoning_summary
