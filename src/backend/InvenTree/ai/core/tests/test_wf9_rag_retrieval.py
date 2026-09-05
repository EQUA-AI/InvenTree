"""Focused tests for the retrieval-only WF9 boundary."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ai.core.integrations.attachment_corpus import ATTACHMENT_CORPUS_TOOLS
from ai.core.integrations.controlled_document_corpus import CONTROLLED_CORPUS_TOOLS
from ai.core.integrations.media_corpus import EVIDENCE_MEDIA_TOOLS
from ai.core.workflows import wf9_rag_retrieval


def test_wf9_tool_boundary_is_exact_rag_union() -> None:
    assert (
        *CONTROLLED_CORPUS_TOOLS,
        *ATTACHMENT_CORPUS_TOOLS,
        *EVIDENCE_MEDIA_TOOLS,
    ) == wf9_rag_retrieval.RAG_RETRIEVAL_TOOLS


@pytest.mark.asyncio
async def test_wf9_rejects_empty_query_without_agent_run(monkeypatch) -> None:
    workflow = wf9_rag_retrieval.RagRetrievalWorkflow()
    get_agent = AsyncMock()
    monkeypatch.setattr(workflow, "_get_agent", get_agent)

    result = await workflow.execute("   ")

    assert not result.success
    assert result.error == "empty_query"
    get_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_wf9_runs_only_through_rbac_retrieval_boundary(monkeypatch) -> None:
    workflow = wf9_rag_retrieval.RagRetrievalWorkflow()
    agent = object()
    context = {"incident_id": "INC-1"}
    get_agent = AsyncMock(return_value=agent)
    run_with_rbac = AsyncMock(return_value=SimpleNamespace(text="Retrieved evidence."))
    monkeypatch.setattr(workflow, "_get_agent", get_agent)
    monkeypatch.setattr(wf9_rag_retrieval, "run_with_rbac", run_with_rbac)

    result = await workflow.execute(
        "Find the applicable manual section.",
        thread_id="thread-1",
        context=context,
    )

    assert result.success
    assert result.formatted_response == "Retrieved evidence."
    assert result.data == {"retrieval_only": True}
    run_with_rbac.assert_awaited_once_with(
        agent,
        "Find the applicable manual section.",
        workflow="wf9",
        full_tools=wf9_rag_retrieval.RAG_RETRIEVAL_TOOLS,
        context=context,
    )
