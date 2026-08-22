"""Routing must degrade, and failures must be locatable without being readable.

From the 2026-07-28 outage: revision 0000019 shipped without
``AZURE_OPENAI_API_KEY``, the semantic router's embedding client raised in its
constructor - one line above the guard whose comment promised fallback - and
every chat turn died in ~2ms. The error path logged only the exception class,
so the fault had to be found by matching that timing signature against source.

These tests pin both repairs:

* a broken embedding configuration degrades routing to LLM classification and
  never fails the turn;
* the redacted error path stays redacted (no messages, no args) while carrying
  enough code coordinates to locate the fault from the container logs alone.
"""

from __future__ import annotations

import logging
import os
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest  # noqa: E402
from ai.core.agents import routing as routing_module  # noqa: E402
from ai.core.agents.routing import (  # noqa: E402
    RoutingDecision,
    SemanticRouter,
    UnifiedRouter,
    WorkflowType,
)
from ai.core.faults import fault_location, log_fault  # noqa: E402

#: Stands in for a credential that must never reach a log line.
SECRET = "SECRET-K3Y-DO-NOT-LOG"


def _empty_azure_settings() -> SimpleNamespace:
    """The deployed failure: Azure config fields present but blank."""
    return SimpleNamespace(
        azure_openai_embedding_deployment="",
        azure_openai_endpoint="",
        azure_openai_api_key="",
        azure_openai_api_version="2024-02-01",
    )


# --------------------------------------------------------------------------- #
# Problem 1: a missing embedding config must not fail the turn                #
# --------------------------------------------------------------------------- #
async def test_semantic_router_survives_missing_azure_config(monkeypatch):
    """The exact outage, replayed: blank config, real client construction.

    ``AzureOpenAIEmbeddingClient`` raises in its constructor when endpoint and
    key are empty. That must yield "no semantic opinion", not an exception.
    """
    monkeypatch.setattr(routing_module, "get_settings", _empty_azure_settings)
    router = SemanticRouter()

    decision = await router.route("how much stock of M6 bolts")

    assert decision is None
    assert router._initialized is False


async def test_semantic_initialize_swallows_client_construction_failure():
    """initialize() keeps its own promise: 'we don't raise here'."""
    router = SemanticRouter()

    async def _boom():  # noqa: RUF029 - stands in for a coroutine
        raise RuntimeError(SECRET)

    router._get_client = _boom

    await router.initialize()

    assert router._initialized is False


async def test_unified_router_degrades_to_llm_classification():
    """Definition of done #1: embedding failure -> LLM routing, same turn."""
    router = UnifiedRouter()

    async def _semantic_dies(query, threshold=0.85):  # noqa: RUF029 - stands in for a coroutine
        raise RuntimeError(SECRET)

    expected = RoutingDecision(
        workflow_type=WorkflowType.T1_LOOKUP,
        confidence=0.7,
        reasoning="stub classification",
    )

    async def _classify(query, user_context="", conversation_summary=""):  # noqa: RUF029 - stands in for a coroutine
        return expected

    router.semantic.route = _semantic_dies
    router.classifier.classify = _classify

    decision = await router.route("tell me about the influent pump", "thread-1")

    assert decision is expected


async def test_explicit_uploaded_document_question_routes_to_lookup():
    """Document questions bypass probabilistic diagnostic classification."""
    router = UnifiedRouter()

    async def _unexpected(*args, **kwargs):  # noqa: RUF029
        pytest.fail("explicit document lookup reached a probabilistic router")

    router.fast_path.try_fast_path = _unexpected
    router.semantic.route = _unexpected
    router.classifier.classify = _unexpected

    decision = await router.route(
        "According to the uploaded HX-200 documents, how often should the plate pack be leak-inspected?",
        "thread-1",
    )

    assert decision.workflow_type is WorkflowType.T1_LOOKUP
    assert decision.get_workflow_id() == "wf8"
    assert decision.confidence == pytest.approx(1.0)


async def test_incoming_document_processing_is_not_forced_to_lookup():
    """An uploaded document processing command still reaches downstream routing."""
    router = UnifiedRouter()

    async def _no_fast(*args, **kwargs):  # noqa: RUF029
        return None

    expected = RoutingDecision(
        workflow_type=WorkflowType.T7_DOCUMENTS,
        confidence=0.9,
        reasoning="incoming document",
    )

    async def _document_route(*args, **kwargs):  # noqa: RUF029
        return expected

    router.fast_path.try_fast_path = _no_fast
    router.semantic.route = _document_route

    decision = await router.route("Process this uploaded invoice", "thread-1")

    assert decision is expected


async def test_unified_router_answers_general_when_every_strategy_dies():
    """Routing as a whole may never raise; the floor is a GENERAL decision."""
    router = UnifiedRouter()

    async def _dies(*args, **kwargs):  # noqa: RUF029 - stands in for a coroutine
        raise RuntimeError(SECRET)

    router.fast_path.try_fast_path = _dies
    router.semantic.route = _dies
    router.classifier.classify = _dies

    decision = await router.route("anything at all", "thread-1")

    assert decision.workflow_type is WorkflowType.GENERAL
    assert SECRET not in decision.reasoning


async def test_classifier_failure_reasoning_carries_no_message():
    """RoutingDecision.reasoning travels into telemetry; only the type may."""
    router = UnifiedRouter()

    async def _no_fast(*args, **kwargs):  # noqa: RUF029 - stands in for a coroutine
        return None

    async def _classifier_dies(query, user_context="", conversation_summary=""):  # noqa: RUF029 - stands in for a coroutine
        raise RuntimeError(SECRET)

    router.fast_path.try_fast_path = _no_fast
    router.semantic.route = _no_fast
    router.classifier.classify = _classifier_dies

    decision = await router.route("anything", "thread-1")

    assert decision.workflow_type is WorkflowType.GENERAL
    assert SECRET not in decision.reasoning


# --------------------------------------------------------------------------- #
# Problem 2: locatable without being readable                                 #
# --------------------------------------------------------------------------- #
def _raise_with_secret():
    raise ValueError(f"api_key={SECRET}; endpoint=https://internal.example")


def test_fault_location_reads_coordinates_never_content():
    """The location dict must place the raise and carry none of its words."""
    try:
        _raise_with_secret()
    except ValueError as exc:
        location = fault_location(exc)

    assert location["error_type"] == "ValueError"
    assert "_raise_with_secret" in location["raised_at"]
    assert "test_routing_degradation" in location["raised_at"]
    for value in location.values():
        assert SECRET not in value
        assert "endpoint=" not in value


def test_log_fault_pins_the_redaction_guarantee(caplog):
    """What reaches the log: event, stage, type, coordinates. Not the message."""
    logger = logging.getLogger("ai.core.tests.fault-pin")

    try:
        _raise_with_secret()
    except ValueError as exc:
        with caplog.at_level(logging.ERROR, logger=logger.name):
            log_fault(logger, "Semantic router initialization failed", exc, stage="routing")

    [record] = caplog.records
    rendered = record.getMessage()
    assert SECRET not in rendered
    assert "stage=routing" in rendered
    assert "error_type=ValueError" in rendered
    assert "_raise_with_secret" in rendered


async def test_root_workflow_error_path_is_locatable_and_redacted(caplog):
    """The container-log test from the outage review, made executable.

    A reader of this one log line must be able to say which pipeline stage
    failed and where the exception came from - without the exception's text
    ever appearing.
    """
    from ai.core.workflows.root import RootWorkflow

    class _RaisingRouter:
        async def route(self, message, thread_id, context=None):
            raise RuntimeError(f"credentials: {SECRET}")

    class _Conversations:
        def get_or_create_state(self, thread_id, user_id):
            return SimpleNamespace()

        async def gather_context(self, query, thread_id, user_id):
            return {}

    workflow = RootWorkflow(
        router=_RaisingRouter(),
        registry=SimpleNamespace(get_workflow=lambda _id: None),
        conversation_manager=_Conversations(),
    )

    with (
        caplog.at_level(logging.ERROR, logger="ai.core.workflows.root"),
        pytest.raises(RuntimeError),
    ):
        async for _chunk in workflow.run_stream(message="hi", emitter=None):
            pass

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert SECRET not in rendered
    assert "stage=routing" in rendered
    assert "error_type=RuntimeError" in rendered
    assert "raised_at=" in rendered and "raised_at=unknown" not in rendered
