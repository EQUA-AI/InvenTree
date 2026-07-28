"""A diagnosis with nothing to diagnose must say so, not format around it.

From the deployed transcripts: "Is there anything wrong with Influent Pump
Station No. 1?" produced a "# 🔍 Diagnostic Report" whose Problem Analysis and
Root Cause sections explained that insufficient information was available to
determine whether a problem exists. A report skeleton wrapped around an
admitted absence reads as authority it does not have.

These pin the degradation path: when the problem-analysis step admits it has
no evidence, the workflow answers with one honest sentence, runs no further
agent steps, and caches nothing.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.workflows.wf1_diagnostics import (  # noqa: E402
    ProblemAnalysisAgent,
    T6DiagnosticsWorkflow,
)


class _ScriptedAgent:
    """Stands in for one diagnostic agent step with a fixed reply."""

    def __init__(self, reply: str):
        self._reply = reply
        self.calls = 0

    async def get_agent(self):
        return self

    async def run(self, query):
        self.calls += 1

        class _Msg:
            text = self._reply

        class _Response:
            messages = (_Msg(),)

        return _Response()


def _workflow_with(analysis_reply: str) -> tuple[T6DiagnosticsWorkflow, _ScriptedAgent]:
    workflow = T6DiagnosticsWorkflow.__new__(T6DiagnosticsWorkflow)
    workflow.problem_agent = _ScriptedAgent(analysis_reply)
    workflow.technical_agent = _ScriptedAgent("**Root Cause Analysis:** 1. Bearing wear")
    workflow.solution_agent = _ScriptedAgent("**Solution 1: Replace bearing**")
    workflow.cache = None
    return workflow, workflow.technical_agent


async def test_admitted_absence_yields_a_sentence_not_a_report():
    """The sentinel path: structured admission, honest answer, no skeleton."""
    workflow, technical = _workflow_with(
        "INSUFFICIENT_EVIDENCE: no machine, symptom or data was named."
    )

    result = await workflow.execute("Is there anything wrong with it?")

    assert result.success is True
    assert "Diagnostic Report" not in result.formatted_response
    assert "Root Cause" not in result.formatted_response
    assert "don't have enough information" in result.formatted_response
    assert "no machine, symptom or data was named." in result.formatted_response
    # The two downstream agents never ran on the admitted absence.
    assert technical.calls == 0


async def test_prose_admission_is_caught_too():
    """Models admit insufficiency in prose; the backstop catches that."""
    workflow, technical = _workflow_with(
        "**Category:** Unknown\n**Initial Assessment:** Insufficient information "
        "is available to determine whether a problem exists."
    )

    result = await workflow.execute("Just overall in the whole system.")

    assert "Diagnostic Report" not in result.formatted_response
    assert technical.calls == 0


async def test_grounded_analysis_still_produces_the_report():
    """The degradation must not eat real diagnoses."""
    workflow, technical = _workflow_with(
        "**Category:** Equipment Failure\n**Symptoms:**\n- seal leakage\n"
        "- rising VFD current\n**Affected Components:**\n- Influent Pump 2"
    )

    result = await workflow.execute("Pump 2 seal leakage with rising VFD current")

    assert result.success is True
    assert "Diagnostic Report" in result.formatted_response
    assert technical.calls == 1


def test_analysis_prompt_offers_the_sentinel():
    """The detector only works if the agent was told how to admit absence."""
    prompt = ProblemAnalysisAgent.SYSTEM_PROMPT
    assert "INSUFFICIENT_EVIDENCE:" in prompt
    assert "Never invent symptoms" in prompt
