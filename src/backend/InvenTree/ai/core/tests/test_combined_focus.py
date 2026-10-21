"""The thread and per-turn Work targets coexist without replacing any fence."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from unittest.mock import AsyncMock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest
from ai.core.agui.models import RunAgentInput
from ai.core.analysis.scope import normalize_scope_request, scope_to_payload
from ai.core.analysis.scope_context import current_turn_scope, scope_miss_for_machine
from ai.core.app import ChatRequest
from ai.core.focus import commands
from ai.core.focus.contract import FocusTarget, focus_payload, focus_to_stored
from ai.core.tests.test_work_focus_wire import (
    PUBLIC,
    _context,
    _principal,
    _Repository,
    _service,
    _with_principal,
    _Workflow,
)


def _snapshot(kind="machine", identity=12, *, machine_id=12, work_order_id=None):
    return {
        "focus": focus_to_stored(
            FocusTarget(kind, identity, "Recorded target", machine_id, work_order_id)
        ),
        "version": 6,
    }


class _CombinedRepository(_Repository):
    def __init__(self, focus_snapshot, scope_snapshot=None):
        super().__init__()
        self.focus_snapshot = focus_snapshot
        self.scope_snapshot = scope_snapshot

    def begin_turn(self, thread_id, **kwargs):
        return replace(
            super().begin_turn(thread_id, **kwargs),
            focus_snapshot=self.focus_snapshot,
            scope_snapshot=self.scope_snapshot,
        )


def _run(repository, workflow, *, content="What is its status?"):
    return asyncio.run(
        _with_principal(
            _service(workflow, repository).process(
                actor=_principal(),
                thread_id="thread-focus",
                content=content,
                modality="text",
                trusted_context=_context(),
                modality_metadata=None,
                idempotency_key="combined-focus-turn",
                correlation_id="combined-focus-correlation",
                expected_focus_version=6,
                work_focus_receipt="receipt-ok",
            )
        )
    )


@pytest.fixture
def authorized_focus(monkeypatch):
    monkeypatch.setattr(commands, "_focus_snapshot_available", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "ai.core.turn_service.NormalizedTurnService._recorded_focus_readable_now",
        staticmethod(lambda _actor, _focus: True),
    )


def test_both_request_wires_accept_both_focus_coordinates():
    request = ChatRequest(
        message="status", expected_focus_version=6, work_focus_receipt="receipt-ok"
    )
    assert request.expected_focus_version == 6
    assert request.work_focus_receipt == "receipt-ok"
    props = RunAgentInput.model_validate({
        "runId": "r",
        "forwardedProps": {
            "expectedFocusVersion": 6,
            "workFocusReceipt": "receipt-ok",
        },
    }).forwarded_props
    assert props.expected_focus_version == 6
    assert props.work_focus_receipt == "receipt-ok"


def test_work_target_answers_anaphor_without_persistent_conversation_target():
    workflow = _Workflow()
    result = _run(_CombinedRepository(None), workflow)
    assert result.message == "Work 73 is open."
    assert workflow.call["message"] == "What is its status? — work order 73"
    assert workflow.call["context"]["work_focus"] == PUBLIC
    assert "conversation_focus" not in workflow.call["context"]


def test_both_recorded_targets_reach_model_and_result(authorized_focus):
    snapshot = _snapshot()
    repository = _CombinedRepository(snapshot)
    workflow = _Workflow()
    result = _run(repository, workflow)
    assert repository.begin_calls[0]["expected_focus_version"] == 6
    assert repository.begin_calls[0]["work_focus_receipt"] == "receipt-ok"
    context = workflow.call["context"]
    assert context["work_focus"] == PUBLIC
    assert context["conversation_focus"] == {
        **focus_payload(FocusTarget("machine", 12, "Recorded target", 12, None)),
        "focus_version": 6,
    }
    assert result.focus == focus_payload(FocusTarget("machine", 12, "Recorded target", 12, None))
    assert result.focus_version == 6
    assert repository.terminal_kwargs["canonical_result"]["work_focus"] == PUBLIC
    assert snapshot == _snapshot(), "the turn must not rewrite the persistent focus snapshot"


@pytest.mark.parametrize(
    "snapshot",
    [
        _snapshot(identity=13, machine_id=13),
        _snapshot("work_order", 74, work_order_id=74),
        _snapshot("kanban_card", 90, work_order_id=74),
        _snapshot("kanban_card", 90, machine_id=None),
    ],
)
def test_incompatible_targets_clarify_before_model_or_tools(snapshot, authorized_focus):
    repository = _CombinedRepository(snapshot)
    workflow = _Workflow()
    result = _run(repository, workflow)
    assert result.workflow_used == "focus_clarify"
    assert "Work target differ" in result.message
    assert workflow.call is None


def test_work_receipt_cannot_rescue_revoked_conversation_focus(monkeypatch):
    monkeypatch.setattr(commands, "_focus_snapshot_available", AsyncMock(return_value=False))
    workflow = _Workflow()
    result = _run(_CombinedRepository(_snapshot()), workflow)
    assert result.workflow_used == "focus_clarify"
    assert "no longer available" in result.message
    assert workflow.call is None
    assert result.focus is None


def test_both_focuses_preserve_analysis_scope_intersection(monkeypatch, authorized_focus):
    from ai.core.config import get_settings

    monkeypatch.setattr(get_settings(), "feature_ai_thread_scope_enforce", True)
    scope_snapshot = {
        "scope": scope_to_payload(
            normalize_scope_request({"mode": "explicit_assets", "machine_ids": [13]})
        ),
        "version": 4,
        "hash": "s" * 64,
    }

    class ScopedWorkflow(_Workflow):
        async def run_stream(self, **kwargs):
            # This is the same predicate the native tool wrappers apply after
            # their O24/native authorization. Neither target adds machine 12.
            self.scope = current_turn_scope()
            self.focused_machine_miss = scope_miss_for_machine(12)
            self.in_scope_machine_miss = scope_miss_for_machine(13)
            async for item in super().run_stream(**kwargs):
                yield item

    workflow = ScopedWorkflow()
    _run(_CombinedRepository(_snapshot(), scope_snapshot), workflow)
    assert workflow.scope.machine_ids == frozenset({13})
    assert workflow.scope.scope_version == 4
    assert workflow.focused_machine_miss["code"] == "out_of_analysis_scope"
    assert workflow.in_scope_machine_miss is None
    assert scope_snapshot["scope"]["machine_ids"] == [13]
