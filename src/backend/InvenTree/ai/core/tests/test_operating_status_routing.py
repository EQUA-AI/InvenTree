"""Equipment operating-state questions use authorized reads, not diagnosis."""

from unittest.mock import AsyncMock

import pytest
from ai.core.agents.routing import (
    UnifiedRouter,
    WorkflowType,
    is_operating_status_lookup,
)
from ai.core.tools import capabilities


@pytest.mark.parametrize(
    "question",
    [
        "Which pumps are running, and what needs attention in this pumphouse?",
        "How many pumps are running at the station?",
        "Is Pump 08 running?",
        "Show the idle equipment and its alarms.",
        "Which machines are faulted?",
    ],
)
async def test_operating_lookup_does_not_enter_diagnostic_workflow(question):
    """Route real state reads without a probabilistic classification call."""
    router = UnifiedRouter()
    router.fast_path.try_fast_path = AsyncMock(side_effect=AssertionError("not needed"))
    router.semantic.route = AsyncMock(side_effect=AssertionError("not needed"))
    router.classifier.classify = AsyncMock(side_effect=AssertionError("not needed"))
    decision = await router.route(question, "status-test")
    assert decision.workflow_type is WorkflowType.T1_LOOKUP
    assert decision.use_fast_path is False
    router.classifier.classify.assert_not_called()


@pytest.mark.parametrize(
    "question",
    [
        "Why is the pump running hot?",
        "Diagnose the stopped pump and repair it.",
        "Which pumps are running and what caused the fault?",
        "Stop the running pump.",
        "Which pumps are running? Turn off pump 8.",
        "List the running equipment and create a work order.",
        "Are pump seal kits running low?",
        "How many pump seals are in stock? Show a running total.",
        "How many purchase quotes do we have for running pumps?",
        "What were the operating conditions when the pump was repaired?",
        "Which pumps were running before the previous repair?",
        "What is the last recorded maintenance result and operating conditions?",
    ],
)
def test_operating_lookup_does_not_capture_diagnosis_writes_or_spares(question):
    """Preserve the actual diagnostic, approval and inventory boundaries."""
    assert not is_operating_status_lookup(question)


@pytest.mark.parametrize("task_intent", [None, "record_retrieval", "records_analysis"])
def test_operating_lookup_exposes_native_status_tools_not_inventory(monkeypatch, task_intent):
    """Keep an observation query on machine reads even after intent inference."""
    monkeypatch.setattr(capabilities, "category_lexicon", frozenset)
    monkeypatch.setattr(capabilities, "machine_lexicon", frozenset)
    selected = capabilities.select_capabilities(
        "Which pumps are running?",
        profile=frozenset({("work_order", "view")}),
        authenticated=True,
        task_intent=task_intent,
    )
    assert "get_machine_health" in selected.tool_ids
    assert "get_stock_quantity" not in selected.tool_ids
    assert "operating_state_lookup" in selected.signals


@pytest.mark.parametrize("authenticated", [False, True])
def test_operating_lookup_does_not_grant_missing_permission(monkeypatch, authenticated):
    """Routing a read never grants asset authority to a model or caller."""
    monkeypatch.setattr(capabilities, "category_lexicon", frozenset)
    monkeypatch.setattr(capabilities, "machine_lexicon", frozenset)
    selected = capabilities.select_capabilities(
        "Which pumps are running?",
        profile=frozenset(),
        authenticated=authenticated,
    )
    assert "get_machine_health" not in selected.tool_ids
