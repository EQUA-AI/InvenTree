"""
WF2: T2 Parts Analysis Workflow

Sequential workflow for complex parts and BOM analysis:
- Component compatibility checks
- Alternative part suggestions
- BOM validation and optimization
- Assembly analysis

Uses ChatAgent with AzureOpenAIChatClient for specialized analysis tasks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from ai.core.agents.factory import AgentSpec, build_agent
from ai.core.config import get_settings
from ai.core.integrations.inventory_tools import INVENTORY_TOOLS
from ai.core.tools.invocation_guard import CapabilityInvocationMiddleware
from ai.core.workflows.rbac_run import run_with_rbac

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agent_framework import ChatAgent

logger = logging.getLogger(__name__)


class AnalysisType(Enum):
    """Types of parts analysis."""

    COMPATIBILITY_CHECK = "compatibility_check"
    ALTERNATIVE_PARTS = "alternative_parts"
    BOM_VALIDATION = "bom_validation"
    BOM_OPTIMIZATION = "bom_optimization"
    ASSEMBLY_ANALYSIS = "assembly_analysis"
    SPECIFICATION_ANALYSIS = "specification_analysis"
    GENERAL_ANALYSIS = "general_analysis"


@dataclass
class AnalysisResult:
    """Result of parts analysis."""

    analysis_type: AnalysisType
    success: bool
    findings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    formatted_response: str = ""
    execution_time_ms: float = 0.0
    error: str | None = None


class PartsAnalysisAgent:
    """
    Agent specialized in parts compatibility and alternatives.

    Analyzes parts data to find:
    - Compatible replacements
    - Alternative suppliers
    - Similar specifications
    """

    SYSTEM_PROMPT = """You are a parts analysis specialist for a manufacturing company.
Your expertise includes:
- Identifying compatible replacement parts
- Finding alternative suppliers for parts
- Analyzing part specifications and tolerances
- Recommending substitutes based on requirements

When analyzing parts:
1. Query the inventory system for part details
2. Compare specifications carefully
3. Consider supplier reliability and availability
4. Provide clear compatibility ratings (Compatible, Partially Compatible, Not Compatible)

Format your analysis clearly with:
- Part identification
- Key specifications comparison
- Compatibility assessment
- Recommendations with reasoning"""

    def __init__(self):
        self._agent: ChatAgent | None = None

    async def get_agent(self) -> ChatAgent:
        """Get or create the agent."""
        if self._agent is None:
            settings = get_settings()

            # Built tools-less; the per-user-filtered toolset is supplied per
            # run via run_with_rbac (MAF unions constructor + run-time tools).
            self._agent = build_agent(
                AgentSpec(
                    deployment=settings.azure_openai_deployment,
                    instructions=self.SYSTEM_PROMPT,
                    name="Parts Analysis Agent",
                    description="Analyzes part compatibility and alternatives",
                    middleware=CapabilityInvocationMiddleware(),
                    workflow="wf2",
                )
            )

        return self._agent


class BOMAnalysisAgent:
    """
    Agent specialized in BOM validation and optimization.

    Analyzes BOMs to:
    - Validate component availability
    - Identify bottlenecks
    - Suggest optimizations
    - Calculate costs
    """

    SYSTEM_PROMPT = """You are a Bill of Materials (BOM) analyst for a manufacturing company.
Your expertise includes:
- Validating BOM completeness and accuracy
- Identifying missing or deprecated components
- Analyzing component availability and lead times
- Optimizing BOMs for cost and efficiency

When analyzing BOMs:
1. Retrieve the full BOM for the assembly
2. Check stock availability for each component
3. Identify any components with low stock or long lead times
4. Calculate total material availability

Provide your analysis with:
- BOM overview (component count, total items needed)
- Availability status (Ready, Partial, Blocked)
- Component-by-component breakdown if issues found
- Recommendations for addressing any gaps"""

    def __init__(self):
        self._agent: ChatAgent | None = None

    async def get_agent(self) -> ChatAgent:
        """Get or create the agent."""
        if self._agent is None:
            settings = get_settings()

            self._agent = build_agent(
                AgentSpec(
                    deployment=settings.azure_openai_deployment,
                    instructions=self.SYSTEM_PROMPT,
                    name="BOM Analysis Agent",
                    description="Analyzes and validates Bills of Materials",
                    middleware=CapabilityInvocationMiddleware(),
                    workflow="wf2",
                )
            )

        return self._agent


class T2PartsAnalysisWorkflow:
    """
    T2 Parts Analysis Workflow implementation.

    Orchestrates specialized agents in sequence to perform
    comprehensive parts and BOM analysis.

    The workflow:
    1. Classify analysis type from query
    2. Route to appropriate specialized agent
    3. Aggregate results and format response

    Usage:
        workflow = T2PartsAnalysisWorkflow()
        result = await workflow.execute(
            query="Find alternatives for part XYZ-100",
            analysis_type=AnalysisType.ALTERNATIVE_PARTS,
            thread_id="thread_123",
        )
    """

    def __init__(self):
        """Initialize workflow with specialized agents."""
        self.parts_agent = PartsAnalysisAgent()
        self.bom_agent = BOMAnalysisAgent()
        logger.info("T2PartsAnalysisWorkflow initialized")

    def _get_agent_for_type(self, analysis_type: AnalysisType):
        """Get the appropriate agent for analysis type."""
        if analysis_type in [
            AnalysisType.COMPATIBILITY_CHECK,
            AnalysisType.ALTERNATIVE_PARTS,
            AnalysisType.SPECIFICATION_ANALYSIS,
        ]:
            return self.parts_agent
        elif analysis_type in [
            AnalysisType.BOM_VALIDATION,
            AnalysisType.BOM_OPTIMIZATION,
            AnalysisType.ASSEMBLY_ANALYSIS,
        ]:
            return self.bom_agent
        else:
            # Default to parts agent
            return self.parts_agent

    async def execute(
        self,
        query: str,
        analysis_type: AnalysisType = AnalysisType.GENERAL_ANALYSIS,
        thread_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> AnalysisResult:
        """
        Execute parts analysis workflow.

        Args:
            query: The analysis query
            analysis_type: The type of analysis to perform
            thread_id: Conversation thread ID
            context: Additional context

        Returns:
            AnalysisResult with findings and recommendations
        """
        import time

        start_time = time.perf_counter()

        logger.info(
            "Executing T2 parts analysis",
            extra={
                "thread_id": thread_id,
                "analysis_type": analysis_type.value,
            },
        )

        try:
            # Get appropriate agent
            agent_wrapper = self._get_agent_for_type(analysis_type)
            agent = await agent_wrapper.get_agent()

            # Run analysis with per-user RBAC-filtered tools (voice read-only).
            response = await run_with_rbac(
                agent,
                query,
                workflow="wf2",
                full_tools=INVENTORY_TOOLS,
                context=context,
                replay_history=True,  # M1 PR E: the first user-facing step
            )
            response_text = ""
            if response.messages:
                last_msg = response.messages[-1]
                response_text = last_msg.text if hasattr(last_msg, "text") else str(last_msg)

            execution_time = (time.perf_counter() - start_time) * 1000

            logger.info(
                "T2 analysis complete",
                extra={
                    "thread_id": thread_id,
                    "execution_time_ms": execution_time,
                },
            )

            # Parse response for findings and recommendations
            findings, recommendations = self._parse_response(response_text)

            return AnalysisResult(
                analysis_type=analysis_type,
                success=True,
                findings=findings,
                recommendations=recommendations,
                data={"raw_response": response_text},
                formatted_response=response_text,
                execution_time_ms=execution_time,
            )

        except Exception as e:
            execution_time = (time.perf_counter() - start_time) * 1000

            logger.error(
                f"T2 analysis failed: {e}",
                extra={
                    "thread_id": thread_id,
                    "analysis_type": analysis_type.value,
                },
            )

            return AnalysisResult(
                analysis_type=analysis_type,
                success=False,
                error=str(e),
                formatted_response=f"Analysis failed: {e!s}",
                execution_time_ms=execution_time,
            )

    def _parse_response(self, response: str) -> tuple[list[str], list[str]]:
        """
        Parse response text into findings and recommendations.

        Simple heuristic parser - looks for keywords to categorize content.
        """
        findings = []
        recommendations = []

        lines = response.split("\n")
        current_section = "findings"

        for line in lines:
            line = line.strip()
            if not line:
                continue

            lower = line.lower()

            # Detect section changes
            if any(kw in lower for kw in ["recommend", "suggest", "should", "consider"]):
                current_section = "recommendations"
            elif any(kw in lower for kw in ["found", "analysis", "result", "status"]):
                current_section = "findings"

            # Add to appropriate list
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
        analysis_type: AnalysisType = AnalysisType.GENERAL_ANALYSIS,
        thread_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Execute with streaming response."""
        try:
            agent_wrapper = self._get_agent_for_type(analysis_type)
            agent = await agent_wrapper.get_agent()

            response = await run_with_rbac(
                agent,
                query,
                workflow="wf2",
                full_tools=INVENTORY_TOOLS,
                context=context,
                replay_history=True,  # M1 PR E: the first user-facing step
            )
            if response.messages:
                last_msg = response.messages[-1]
                yield last_msg.text if hasattr(last_msg, "text") else str(last_msg)

        except Exception as e:
            logger.error(f"T2 analysis stream failed: {e}")
            yield f"Error: {e!s}"


class T2PartsAnalysisBuilder:
    """
    Builder for T2 Parts Analysis Workflow.

    Implements .as_agent() pattern for workflow composition.
    """

    def __init__(self):
        self._parts_prompt: str | None = None
        self._bom_prompt: str | None = None
        self._additional_tools: list = []

    def with_parts_prompt(self, prompt: str) -> T2PartsAnalysisBuilder:
        """Set custom parts analysis prompt."""
        self._parts_prompt = prompt
        return self

    def with_bom_prompt(self, prompt: str) -> T2PartsAnalysisBuilder:
        """Set custom BOM analysis prompt."""
        self._bom_prompt = prompt
        return self

    def with_additional_tools(self, tools: list) -> T2PartsAnalysisBuilder:
        """Add additional tools."""
        self._additional_tools.extend(tools)
        return self

    def build(self) -> T2PartsAnalysisWorkflow:
        """Build configured workflow."""
        workflow = T2PartsAnalysisWorkflow()

        if self._parts_prompt:
            workflow.parts_agent.SYSTEM_PROMPT = self._parts_prompt
        if self._bom_prompt:
            workflow.bom_agent.SYSTEM_PROMPT = self._bom_prompt

        return workflow

    def as_agent(self) -> ChatAgent:
        """Convert workflow to a composable agent."""
        settings = get_settings()

        combined_prompt = f"""You are a comprehensive parts analysis specialist.

PARTS ANALYSIS CAPABILITIES:
{PartsAnalysisAgent.SYSTEM_PROMPT}

BOM ANALYSIS CAPABILITIES:
{BOMAnalysisAgent.SYSTEM_PROMPT}

Analyze the user's request and provide thorough analysis with:
- Key findings
- Detailed recommendations
- Action items if applicable"""

        return build_agent(
            AgentSpec(
                deployment=settings.azure_openai_deployment,
                instructions=combined_prompt,
                name="AIMMS Parts Analyst",
                description="BOM analysis, compatibility checks, alternative parts",
                # Deliberately tools-less (S11). A constructor toolset bypasses
                # run_with_rbac entirely: MAF unions it into every run, so the
                # per-user filter never sees it. Composed callers must dispatch
                # through run_with_rbac like every other rail.
                middleware=CapabilityInvocationMiddleware(),
                workflow="wf2",
            )
        )


# Factory functions
def create_t2_parts_workflow() -> T2PartsAnalysisWorkflow:
    """Create a T2 parts analysis workflow instance."""
    return T2PartsAnalysisWorkflow()


def t2_parts_builder() -> T2PartsAnalysisBuilder:
    """Get a T2 parts analysis workflow builder."""
    return T2PartsAnalysisBuilder()
