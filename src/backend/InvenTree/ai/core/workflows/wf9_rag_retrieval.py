"""WF9: retrieval-only RAG workflow."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from agent_framework import ChatAgent, Role
from ai.core.agents.factory import AgentSpec, build_agent
from ai.core.integrations.attachment_corpus import ATTACHMENT_CORPUS_TOOLS
from ai.core.integrations.controlled_document_corpus import CONTROLLED_CORPUS_TOOLS
from ai.core.integrations.media_corpus import EVIDENCE_MEDIA_TOOLS
from ai.core.model_policy import ModelPurpose, select_deployment
from ai.core.tools.invocation_guard import CapabilityInvocationMiddleware
from ai.core.workflows.rbac_run import run_with_rbac

logger = logging.getLogger(__name__)

RAG_RETRIEVAL_TOOLS: tuple[Any, ...] = (
    *CONTROLLED_CORPUS_TOOLS,
    *ATTACHMENT_CORPUS_TOOLS,
    *EVIDENCE_MEDIA_TOOLS,
)


@dataclass
class RagRetrievalResult:
    """Read-only retrieval result consumable by the root workflow."""

    success: bool
    formatted_response: str
    data: dict[str, Any] = field(default_factory=dict)
    execution_time_ms: float = 0.0
    error: str | None = None
    failure_class: str | None = None

    def __str__(self) -> str:
        return self.formatted_response


class RagRetrievalWorkflow:
    """Retrieve cited evidence without operational, repair, or write tools."""

    SYSTEM_PROMPT = """You are the AIMMS evidence retrieval agent.

Your only task is to retrieve relevant, cited source material. For every
non-empty request, use one or more retrieval tools that are available to you
before responding. Do not diagnose faults, recommend a repair, select parts,
approve work, summarize a conclusion as fact, or invoke a non-retrieval tool.

Prefer controlled manuals first. Treat uploaded documents as uncontrolled and
label them that way. Treat photos and videos as evidence recordings, not
authoritative technical instructions. Do not treat prior conversation text as
current evidence.

Return only these sections:

RETRIEVED EVIDENCE
- Short factual descriptions tied to the citations returned by tools.

RETRIEVAL GAPS
- Missing identity, source, revision, or observation needed to retrieve more.

If no retrieval tool is available or no source is found, say so plainly. Keep
identifiers exactly as returned by the tools."""

    MAX_TOOL_ITERATIONS = 3
    BASE_TOOLS = RAG_RETRIEVAL_TOOLS

    def __init__(self) -> None:
        self._agent: ChatAgent | None = None

    async def _get_agent(self) -> ChatAgent:
        if self._agent is None:
            deployment = select_deployment(ModelPurpose.WF8_PRIMARY, modality="text")
            self._agent = build_agent(
                AgentSpec(
                    deployment=deployment,
                    instructions=self.SYSTEM_PROMPT,
                    name="AIMMS RAG Retrieval Agent",
                    description="Read-only retrieval of cited technical and evidence material",
                    middleware=CapabilityInvocationMiddleware(),
                    max_tool_iterations=self.MAX_TOOL_ITERATIONS,
                    include_detailed_errors=False,
                    workflow="wf9",
                )
            )

        return self._agent

    @staticmethod
    def _response_text(response: Any) -> str:
        text = getattr(response, "text", None)
        if isinstance(text, str) and text:
            return text

        messages = getattr(response, "messages", ()) or ()
        for message in reversed(messages):
            role = getattr(message, "role", None)
            message_text = getattr(message, "text", None)
            if role in (Role.ASSISTANT, "assistant") and isinstance(message_text, str):
                return message_text

        return "No retrieval summary was returned."

    async def execute(
        self,
        query: str,
        *,
        thread_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> RagRetrievalResult:
        """Run the retrieval-only agent with the caller's authorized tools."""
        start_time = time.perf_counter()
        query = query.strip()
        if not query:
            return RagRetrievalResult(
                success=False,
                formatted_response="Please provide a retrieval question.",
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
                error="empty_query",
            )

        try:
            response = await run_with_rbac(
                await self._get_agent(),
                query,
                workflow="wf9",
                full_tools=self.BASE_TOOLS,
                context=context,
            )
            execution_time_ms = (time.perf_counter() - start_time) * 1000
            logger.info(
                "RAG retrieval complete",
                extra={
                    "thread_id": thread_id,
                    "execution_time_ms": execution_time_ms,
                    "tool_count": len(self.BASE_TOOLS),
                },
            )
            return RagRetrievalResult(
                success=True,
                formatted_response=self._response_text(response),
                data={"retrieval_only": True},
                execution_time_ms=execution_time_ms,
            )
        except Exception as exc:
            from ai.core.failure_taxonomy import classify_turn_failure

            execution_time_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "RAG retrieval failed",
                extra={"thread_id": thread_id, "error_type": type(exc).__name__},
            )
            return RagRetrievalResult(
                success=False,
                formatted_response="Unable to retrieve supporting evidence.",
                execution_time_ms=execution_time_ms,
                error="rag_retrieval_failed",
                failure_class=classify_turn_failure(exc).value,
            )


def create_rag_retrieval_workflow() -> RagRetrievalWorkflow:
    """Create the internal retrieval-only workflow."""
    return RagRetrievalWorkflow()


__all__ = [
    "RAG_RETRIEVAL_TOOLS",
    "RagRetrievalResult",
    "RagRetrievalWorkflow",
    "create_rag_retrieval_workflow",
]
