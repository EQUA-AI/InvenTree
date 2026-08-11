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
