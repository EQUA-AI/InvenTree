"""S18 turn-loop hygiene: wall-clock cap, provider budgets, wf3 salvage."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.config import Settings
from ai.core.memory.conversation import ConversationManager
from ai.core.workflows.root import RootWorkflow
from ai.core.workflows.wf3_research import (
    ResearchSource,
    ResearchType,
    T3ResearchWorkflow,
)


class _RecordingEmitter:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


class _State:
    def __init__(self):
        self.context_cache = {}

    def increment_turn(self):
        pass


class _Manager:
    """Conversation-manager stand-in that pins the turn to a fake workflow."""

    def get_or_create_state(self, thread_id, user_id):
        return _State()

    async def gather_context(self, query, thread_id, user_id):
        return {"pinned_workflow_id": "wf-test"}


class _HangingWorkflow:
    async def execute_streaming(self, **kwargs):
        yield "started"
        await asyncio.sleep(60)
        yield "never"


class _Registry:
    def __init__(self, workflow):
        self._workflow = workflow

    def get_workflow(self, workflow_id):
        return self._workflow


def _root(workflow) -> RootWorkflow:
    return RootWorkflow(
        router=None,
        registry=_Registry(workflow),
        conversation_manager=_Manager(),
    )


def _settings(**overrides) -> Settings:
    base = {"TURN_WALL_CLOCK_CAP_S": 0.4, "TURN_ROUTING_BUDGET_S": 0.2}
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.mark.asyncio
async def test_hung_workflow_terminates_at_the_cap_with_typed_event(monkeypatch):
    """A hung stream ends at the wall-clock cap, not at client disconnect."""
    monkeypatch.setattr("ai.core.workflows.root.get_settings", _settings)
    emitter = _RecordingEmitter()
    root = _root(_HangingWorkflow())

    chunks = []
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(5):
            async for chunk in root.run_stream("hello", emitter=emitter):
                chunks.append(chunk)

    assert chunks == ["started"]
    errors = [
        event for event in emitter.events if getattr(event.event_type, "value", "") == "RUN_ERROR"
    ]
    assert errors, "expected a typed RUN_ERROR timeout event"
    assert errors[-1].data.get("code") == "turn_timeout"
    assert errors[-1].data.get("stage") == "workflow_execution"


@pytest.mark.asyncio
async def test_cap_disabled_streams_to_completion(monkeypatch):
    monkeypatch.setattr(
        "ai.core.workflows.root.get_settings",
        lambda: _settings(TURN_WALL_CLOCK_CAP_S=0),
    )

    class _QuickWorkflow:
        async def execute_streaming(self, **kwargs):
            yield "one"
            yield "two"

    emitter = _RecordingEmitter()
    chunks = [chunk async for chunk in _root(_QuickWorkflow()).run_stream("hi", emitter=emitter)]
    assert chunks == ["one", "two"]


@pytest.mark.asyncio
async def test_one_hung_provider_costs_only_its_own_budget(monkeypatch, caplog):
    """gather_context returns the healthy providers' results within the budget."""
    manager = ConversationManager(enable_db_persistence=False)
    manager._providers_initialized = True

    class _Profile:
        async def get_profile(self, user_id):
            return {"role": "technician"}

    class _Hung:
        async def get_summary(self, thread_id):
            await asyncio.sleep(60)

    class _Prefs:
        async def get_preferences(self, user_id):
            raise RuntimeError("secret-laden provider message")

    manager._user_profile_provider = _Profile()
    manager._thread_summary_provider = _Hung()
    manager._parts_preference_provider = _Prefs()
    monkeypatch.setattr(ConversationManager, "_provider_timeout_s", staticmethod(lambda: 0.2))

    with caplog.at_level(logging.WARNING, logger="ai.core.memory.conversation"):
        async with asyncio.timeout(5):
            context = await manager.gather_context("q", "thread-1", "user-1")

    assert context == {"user_profile": {"role": "technician"}}
    assert "thread_summary" not in context
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "secret-laden" not in joined, "exception text must never reach the logs"
    assert "parts_preferences" in joined


@pytest.mark.asyncio
async def test_wf3_timeout_salvages_completed_sources(monkeypatch):
    """Partial research survives the deadline instead of becoming a 500."""
    workflow = T3ResearchWorkflow.__new__(T3ResearchWorkflow)
    workflow.RESEARCH_TIMEOUT = 0.3

    async def _agent(name, agent, query, context):
        if name == "hung":
            await asyncio.sleep(60)
        return ResearchSource(source_name=name, success=True, findings=[f"{name}-fact"])

    async def _synthesize(query, sources):
        await asyncio.sleep(0)
        return "FINDINGS:\n- combined\nRECOMMENDATIONS:\n- act"

    monkeypatch.setattr(
        T3ResearchWorkflow,
        "_get_agents_for_type",
        lambda _self, _rt: [("fast-a", None), ("fast-b", None), ("hung", None)],
    )
    monkeypatch.setattr(T3ResearchWorkflow, "_run_research_agent", staticmethod(_agent))
    monkeypatch.setattr(T3ResearchWorkflow, "_synthesize_results", staticmethod(_synthesize))

    async with asyncio.timeout(5):
        result = await workflow.execute(
            query="q", research_type=ResearchType.COMPREHENSIVE_RESEARCH
        )

    assert result.success is True
    assert {source.source_name for source in result.sources} == {"fast-a", "fast-b"}


@pytest.mark.asyncio
async def test_wf3_timeout_with_no_sources_fails_honestly(monkeypatch):
    workflow = T3ResearchWorkflow.__new__(T3ResearchWorkflow)
    workflow.RESEARCH_TIMEOUT = 0.2

    async def _agent(name, agent, query, context):
        await asyncio.sleep(60)

    monkeypatch.setattr(
        T3ResearchWorkflow,
        "_get_agents_for_type",
        lambda _self, _rt: [("hung-a", None), ("hung-b", None)],
    )
    monkeypatch.setattr(T3ResearchWorkflow, "_run_research_agent", staticmethod(_agent))

    async with asyncio.timeout(5):
        result = await workflow.execute(
            query="q", research_type=ResearchType.COMPREHENSIVE_RESEARCH
        )

    assert result.success is False
    assert result.sources == []
