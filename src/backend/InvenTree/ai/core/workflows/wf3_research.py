"""
WF3: T3 Research Workflow

Concurrent workflow for multi-source research:
- Parallel queries across multiple data sources
- Supplier information aggregation
- Specification gathering
- Pricing research

Uses ConcurrentBuilder to run multiple agents in parallel,
then aggregates results with a synthesis agent.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from ai.core.agents.factory import AgentSpec, build_agent
from ai.core.config import get_settings
from ai.core.integrations.email import EMAIL_TOOLS
from ai.core.integrations.inventory_tools import INVENTORY_TOOLS
from ai.core.tools.invocation_guard import CapabilityInvocationMiddleware
from ai.core.workflows.rbac_run import run_with_rbac

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agent_framework import ChatAgent

logger = logging.getLogger(__name__)


class ResearchType(Enum):
    """Types of research queries."""

    SUPPLIER_RESEARCH = "supplier_research"
    SPECIFICATION_RESEARCH = "specification_research"
    PRICING_RESEARCH = "pricing_research"
    AVAILABILITY_RESEARCH = "availability_research"
    ALTERNATIVE_RESEARCH = "alternative_research"
    COMPREHENSIVE_RESEARCH = "comprehensive_research"


@dataclass
class ResearchSource:
    """Result from a single research source."""

    source_name: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class ResearchResult:
    """Aggregated result from all research sources."""

    research_type: ResearchType
    success: bool
    sources: list[ResearchSource] = field(default_factory=list)
    synthesized_findings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    formatted_response: str = ""
    execution_time_ms: float = 0.0
    error: str | None = None


class InventoryResearchAgent:
    """
    Agent for researching inventory data.

    Queries InvenTree for:
    - Stock levels across locations
    - Part availability and lead times
    - Historical consumption data
    """

    SYSTEM_PROMPT = """You are an inventory research specialist.
Your job is to research inventory data including:
- Current stock levels and locations
- Part availability and status
- Stock movements and trends

Query the inventory system and provide:
- Current availability status
- Location breakdown
- Any stock concerns (low levels, expiring, etc.)

Be thorough but concise in your findings."""

    def __init__(self):
        self._agent: ChatAgent | None = None

    async def get_agent(self) -> ChatAgent:
        if self._agent is None:
            settings = get_settings()
            self._agent = build_agent(
                AgentSpec(
                    deployment=settings.azure_openai_deployment,
                    instructions=self.SYSTEM_PROMPT,
                    name="Inventory Research Agent",
                    middleware=CapabilityInvocationMiddleware(),
                    workflow="wf3",
                )
            )
        return self._agent


class SupplierResearchAgent:
    """
    Agent for researching supplier information.

    Queries for:
    - Supplier details and contacts
    - Part supplier relationships
    - Pricing and lead times
    """

    SYSTEM_PROMPT = """You are a supplier research specialist.
Your job is to research supplier information including:
- Supplier details and reliability
- Part-supplier relationships
- Pricing information if available
- Lead times and terms

Query the inventory system for supplier data and provide:
- List of suppliers for the requested parts
- Key supplier metrics
- Recommendations for preferred suppliers

Focus on actionable supplier intelligence."""

    def __init__(self):
        self._agent: ChatAgent | None = None

    async def get_agent(self) -> ChatAgent:
        if self._agent is None:
            settings = get_settings()
            self._agent = build_agent(
                AgentSpec(
                    deployment=settings.azure_openai_deployment,
                    instructions=self.SYSTEM_PROMPT,
                    name="Supplier Research Agent",
                    middleware=CapabilityInvocationMiddleware(),
                    workflow="wf3",
                )
            )
        return self._agent


class EmailResearchAgent:
    """
    Agent for researching email communications.

    Searches for:
    - Previous supplier communications
    - Quote history
    - Issue discussions
    """

    SYSTEM_PROMPT = """You are a communications research specialist.
Your job is to search email communications for relevant information:
- Previous quotes and pricing discussions
- Supplier communications
- Issue reports and resolutions
- Historical order information

Search the email system and provide:
- Relevant communication summaries
- Key dates and contacts
- Any pricing or terms mentioned

Focus on extracting actionable intelligence from communications."""

    def __init__(self):
        self._agent: ChatAgent | None = None

    async def get_agent(self) -> ChatAgent:
        if self._agent is None:
            settings = get_settings()
            self._agent = build_agent(
                AgentSpec(
                    deployment=settings.azure_openai_deployment,
                    instructions=self.SYSTEM_PROMPT,
                    name="Email Research Agent",
                    middleware=CapabilityInvocationMiddleware(),
                    workflow="wf3",
                )
            )
        return self._agent


class SynthesisAgent:
    """
    Agent for synthesizing research results.

    Combines findings from multiple sources into
    a coherent, actionable summary.
    """

    SYSTEM_PROMPT = """You are a research synthesis specialist.
Your job is to combine research findings from multiple sources into a coherent summary.

When synthesizing research:
1. Identify key themes across sources
2. Highlight consensus findings
3. Note any conflicting information
4. Prioritize actionable insights
5. Make clear recommendations

Format your synthesis with:
- Executive Summary (2-3 sentences)
- Key Findings (bulleted list)
- Recommendations (prioritized list)
- Any caveats or areas needing more research"""

    def __init__(self):
        self._agent: ChatAgent | None = None

    async def get_agent(self) -> ChatAgent:
        if self._agent is None:
            settings = get_settings()
            self._agent = build_agent(
                AgentSpec(
                    deployment=settings.azure_openai_deployment,
                    instructions=self.SYSTEM_PROMPT,
                    name="Synthesis Agent",
                    middleware=CapabilityInvocationMiddleware(),
                    workflow="wf3",
                )
            )
        return self._agent


class T3ResearchWorkflow:
    """
    T3 Research Workflow implementation.

    Runs multiple research agents in parallel using ConcurrentBuilder,
    then synthesizes results into a coherent response.

    The workflow:
    1. Spawn parallel research tasks to multiple sources
    2. Wait for all sources to complete (with timeout)
    3. Synthesize results with synthesis agent
    4. Return aggregated findings

    Usage:
        workflow = T3ResearchWorkflow()
        result = await workflow.execute(
            query="Research suppliers for component ABC",
            research_type=ResearchType.SUPPLIER_RESEARCH,
            thread_id="thread_123",
        )
    """

    # Timeout for parallel research tasks (seconds). Fallback only: the live
    # value comes from settings (RESEARCH_STAGE_TIMEOUT_S) — 30s left zero
    # agents complete under real load. An instance override (tests) wins.
    RESEARCH_TIMEOUT = 30.0

    def _research_timeout_s(self) -> float:
        """Configured fan-out budget; an explicit instance override wins."""
        if "RESEARCH_TIMEOUT" in self.__dict__:
            return float(self.RESEARCH_TIMEOUT)
        try:
            from ai.core.config import get_settings

            return float(get_settings().research_stage_timeout_s)
        except Exception:  # pragma: no cover - config absent in minimal envs
            return float(self.RESEARCH_TIMEOUT)

    def __init__(self):
        """Initialize workflow with research agents."""
        self.inventory_agent = InventoryResearchAgent()
        self.supplier_agent = SupplierResearchAgent()
        self.email_agent = EmailResearchAgent()
        self.synthesis_agent = SynthesisAgent()
        logger.info("T3ResearchWorkflow initialized")

    def _get_agents_for_type(self, research_type: ResearchType) -> list:
        """Get the research agents to use for the research type."""
        if research_type == ResearchType.SUPPLIER_RESEARCH:
            return [
                ("inventory", self.inventory_agent),
                ("supplier", self.supplier_agent),
                ("email", self.email_agent),
            ]
        elif research_type == ResearchType.PRICING_RESEARCH:
            return [
                ("supplier", self.supplier_agent),
                ("email", self.email_agent),
            ]
        elif research_type == ResearchType.AVAILABILITY_RESEARCH:
            return [
                ("inventory", self.inventory_agent),
                ("supplier", self.supplier_agent),
            ]
        else:
            # Comprehensive research uses all sources
            return [
                ("inventory", self.inventory_agent),
                ("supplier", self.supplier_agent),
                ("email", self.email_agent),
            ]

    async def _run_research_agent(
        self,
        source_name: str,
        agent_wrapper: Any,
        query: str,
        context: dict[str, Any] | None = None,
    ) -> ResearchSource:
        """Run a single research agent."""
        try:
            agent = await agent_wrapper.get_agent()

            # Research agents may use inventory or email tools; RBAC filters per
            # user and voice narrows to the read-only surface.
            response = await run_with_rbac(
                agent,
                query,
                workflow="wf3",
                full_tools=[*INVENTORY_TOOLS, *EMAIL_TOOLS],
                context=context,
            )
            response_text = ""
            if response.messages:
                last_msg = response.messages[-1]
                response_text = last_msg.text if hasattr(last_msg, "text") else str(last_msg)

            # Parse findings from response
            findings = [
                line.strip().lstrip("-*•– ")  # noqa: RUF001
                for line in response_text.split("\n")
                if line.strip() and line.strip().startswith(("-", "*", "•"))
            ]

            return ResearchSource(
                source_name=source_name,
                success=True,
                data={"raw_response": response_text},
                findings=findings or [response_text[:200] + "..."] if response_text else [],
            )

        except Exception as e:
            logger.error(f"Research agent {source_name} failed: {e}")
            return ResearchSource(
                source_name=source_name,
                success=False,
                error=str(e),
            )

    async def execute(
        self,
        query: str,
        research_type: ResearchType = ResearchType.COMPREHENSIVE_RESEARCH,
        thread_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> ResearchResult:
        """
        Execute parallel research workflow.

        Args:
            query: The research query
            research_type: Type of research to perform
            thread_id: Conversation thread ID
            context: Additional context

        Returns:
            ResearchResult with aggregated findings
        """
        import time

        start_time = time.perf_counter()

        logger.info(
            "Executing T3 research",
            extra={
                "thread_id": thread_id,
                "research_type": research_type.value,
            },
        )

        try:
            # Get agents for this research type
            agents = self._get_agents_for_type(research_type)

            # Create parallel research tasks
            tasks = [
                asyncio.ensure_future(self._run_research_agent(name, agent, query, context))
                for name, agent in agents
            ]

            # Run with timeout. wait_for(gather(...)) cancelled EVERY task on
            # expiry, so "returning partial results" was a lie — the empty
            # source list became success=False and the root turned a slow turn
            # into a 500. asyncio.wait keeps what actually finished.
            done, pending = await asyncio.wait(tasks, timeout=self._research_timeout_s())
            if pending:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                logger.warning(
                    "Research timeout; %d of %d sources completed",
                    len(done),
                    len(tasks),
                )
            sources = []
            for task in done:
                exc = task.exception()
                sources.append(exc if exc is not None else task.result())

            # Filter successful sources
            valid_sources = [s for s in sources if isinstance(s, ResearchSource) and s.success]

            # Synthesize results
            if valid_sources:
                synthesis = await self._synthesize_results(query, valid_sources)
            else:
                synthesis = "Unable to gather research from any source."

            execution_time = (time.perf_counter() - start_time) * 1000

            logger.info(
                "T3 research complete",
                extra={
                    "thread_id": thread_id,
                    "sources_count": len(valid_sources),
                    "execution_time_ms": execution_time,
                },
            )

            # Parse synthesis for findings and recommendations
            findings, recommendations = self._parse_synthesis(synthesis)

            return ResearchResult(
                research_type=research_type,
                success=len(valid_sources) > 0,
                sources=[s for s in sources if isinstance(s, ResearchSource)],
                synthesized_findings=findings,
                recommendations=recommendations,
                formatted_response=synthesis,
                execution_time_ms=execution_time,
            )

        except Exception as e:
            execution_time = (time.perf_counter() - start_time) * 1000

            logger.error(
                f"T3 research failed: {e}",
                extra={
                    "thread_id": thread_id,
                    "research_type": research_type.value,
                },
            )

            return ResearchResult(
                research_type=research_type,
                success=False,
                error=str(e),
                formatted_response=f"Research failed: {e!s}",
                execution_time_ms=execution_time,
            )

    async def _synthesize_results(
        self,
        original_query: str,
        sources: list[ResearchSource],
    ) -> str:
        """Synthesize results from multiple sources."""
        # Build synthesis prompt
        source_summaries = "\n\n".join([
            f"**{source.source_name.upper()} FINDINGS:**\n"
            + "\n".join(f"- {f}" for f in source.findings)
            for source in sources
        ])

        synthesis_query = f"""Original Research Query: {original_query}

Research Findings from Multiple Sources:
{source_summaries}

Please synthesize these findings into a coherent summary with recommendations."""

        try:
            agent = await self.synthesis_agent.get_agent()

            response = await agent.run(synthesis_query)
            response_text = ""
            if response.messages:
                last_msg = response.messages[-1]
                response_text = last_msg.text if hasattr(last_msg, "text") else str(last_msg)

            return response_text

        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            return source_summaries

    def _parse_synthesis(self, synthesis: str) -> tuple[list[str], list[str]]:
        """Parse synthesis into findings and recommendations."""
        findings = []
        recommendations = []

        lines = synthesis.split("\n")
        current_section = "findings"

        for line in lines:
            line = line.strip()
            if not line:
                continue

            lower = line.lower()

            if "recommendation" in lower or "suggest" in lower:
                current_section = "recommendations"
            elif "finding" in lower or "summary" in lower or "key" in lower:
                current_section = "findings"

            if line.startswith(("-", "*", "•", "–")):  # noqa: RUF001
                content = line.lstrip("-*•– ").strip()  # noqa: RUF001
                if current_section == "recommendations":
                    recommendations.append(content)
                else:
                    findings.append(content)

        return findings, recommendations

    async def stream_execute(
        self,
        query: str,
        research_type: ResearchType = ResearchType.COMPREHENSIVE_RESEARCH,
        thread_id: str = "",
    ) -> AsyncIterator[str]:
        """Execute with streaming response."""
        # For concurrent workflow, we run all research first, then stream synthesis
        yield "🔍 Researching from multiple sources...\n"

        agents = self._get_agents_for_type(research_type)

        for name, _ in agents:
            yield f"  • Querying {name}...\n"

        # Execute non-streaming
        result = await self.execute(query, research_type, thread_id)

        yield "\n📊 **Research Complete**\n\n"
        yield result.formatted_response


class T3ResearchBuilder:
    """
    Builder for T3 Research Workflow.

    Implements .as_agent() pattern for workflow composition.
    """

    def __init__(self):
        self._timeout: float = 30.0
        self._include_email: bool = True
        self._additional_sources: list = []

    def with_timeout(self, timeout: float) -> T3ResearchBuilder:
        """Set research timeout in seconds."""
        self._timeout = timeout
        return self

    def without_email(self) -> T3ResearchBuilder:
        """Disable email research source."""
        self._include_email = False
        return self

    def with_additional_source(
        self,
        name: str,
        agent: Any,
    ) -> T3ResearchBuilder:
        """Add a custom research source."""
        self._additional_sources.append((name, agent))
        return self

    def build(self) -> T3ResearchWorkflow:
        """Build configured workflow."""
        workflow = T3ResearchWorkflow()
        workflow.RESEARCH_TIMEOUT = self._timeout
        return workflow

    def as_agent(self) -> ChatAgent:
        """Convert workflow to a composable agent."""
        settings = get_settings()

        combined_prompt = """You are a comprehensive research specialist.

You have access to multiple data sources:
- Inventory system for stock and availability
- Supplier database for vendor information
- Email communications for historical context

When researching:
1. Query all relevant sources
2. Cross-reference findings
3. Synthesize into actionable insights
4. Provide clear recommendations

Format your response with:
- Executive Summary
- Key Findings (from all sources)
- Recommendations
- Any caveats or additional research needed"""

        return build_agent(
            AgentSpec(
                deployment=settings.azure_openai_deployment,
                instructions=combined_prompt,
                name="AIMMS Research Agent",
                description="Multi-source research: suppliers, specifications, pricing",
                # Tools-less by construction (S11): a constructor toolset is
                # unioned into every run, so the per-user RBAC filter in
                # run_with_rbac would never see it. Composed callers dispatch
                # through run_with_rbac like every other rail.
                middleware=CapabilityInvocationMiddleware(),
                workflow="wf3",
            )
        )


# Factory functions
def create_t3_research_workflow() -> T3ResearchWorkflow:
    """Create a T3 research workflow instance."""
    return T3ResearchWorkflow()


def t3_research_builder() -> T3ResearchBuilder:
    """Get a T3 research workflow builder."""
    return T3ResearchBuilder()
