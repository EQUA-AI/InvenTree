"""
WF1: T6 Diagnostics Workflow

Complex diagnostics workflow using MagneticBuilder for dynamic agent selection:
- Problem analysis and root cause identification
- Cross-functional troubleshooting
- Solution recommendation with confidence scoring
- Integration with semantic problem-solution cache

MagneticBuilder enables:
- Dynamic agent selection based on problem type
- Automatic handoff between specialists
- Parallel analysis when beneficial
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from agent_framework import ChatAgent
from agent_framework.azure import AzureOpenAIChatClient
from ai.core.config import get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

# WS3 intentionally leaves this legacy workflow with no model-callable tools.
# Complex diagnosis is intercepted by NormalizedTurnService and sent through
# the Foundry adapter plus ``ai.core.tools.diagnostics``. Reattaching the
# deployment-wide INVENTORY_TOOLS collection here would reopen write access.
LEGACY_DIAGNOSTIC_TOOLS: tuple[Any, ...] = ()


class ProblemCategory(Enum):
    """Categories of manufacturing problems."""

    EQUIPMENT_FAILURE = "equipment_failure"
    QUALITY_ISSUE = "quality_issue"
    SUPPLY_CHAIN = "supply_chain"
    PROCESS_DEVIATION = "process_deviation"
    MAINTENANCE = "maintenance"
    INVENTORY = "inventory"
    SAFETY = "safety"
    UNKNOWN = "unknown"


class DiagnosisConfidence(Enum):
    """Confidence levels for diagnoses."""

    HIGH = "high"  # > 80% confidence
    MEDIUM = "medium"  # 50-80% confidence
    LOW = "low"  # < 50% confidence


@dataclass
class DiagnosisStep:
    """A step in the diagnostic process."""

    step_number: int
    agent_name: str
    analysis: str
    findings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


@dataclass
class RootCause:
    """An identified root cause."""

    description: str
    confidence: DiagnosisConfidence
    evidence: list[str] = field(default_factory=list)
    category: ProblemCategory = ProblemCategory.UNKNOWN


@dataclass
class Solution:
    """A recommended solution."""

    description: str
    steps: list[str] = field(default_factory=list)
    estimated_time: str = ""
    estimated_cost: str = ""
    priority: str = "medium"
    requires_parts: list[str] = field(default_factory=list)


@dataclass
class DiagnosticsResult:
    """Result of diagnostics workflow."""

    success: bool
    problem_category: ProblemCategory = ProblemCategory.UNKNOWN
    root_causes: list[RootCause] = field(default_factory=list)
    solutions: list[Solution] = field(default_factory=list)
    diagnosis_steps: list[DiagnosisStep] = field(default_factory=list)
    formatted_response: str = ""
    cache_hit: bool = False
    execution_time_ms: float = 0.0
    error: str | None = None


class ProblemAnalysisAgent:
    """
    Initial problem analysis agent.

    Analyzes the problem description to:
    - Categorize the problem type
    - Extract key symptoms
    - Identify relevant systems/components
    """

    SYSTEM_PROMPT = """You are a problem analysis specialist for a manufacturing facility.
Your job is to analyze problem descriptions and extract key information:

1. PROBLEM CATEGORIZATION: Classify into one of:
   - Equipment Failure: Machinery breakdowns, sensor issues
   - Quality Issue: Defects, out-of-spec products
   - Supply Chain: Material shortages, delivery issues
   - Process Deviation: Unexpected process changes
   - Maintenance: Preventive or corrective maintenance needed
   - Inventory: Stock discrepancies, location issues
   - Safety: Safety hazards or violations

2. SYMPTOM EXTRACTION: List all mentioned symptoms

3. COMPONENT IDENTIFICATION: Identify affected systems, machines, parts

4. URGENCY ASSESSMENT: Rate urgency (critical/high/medium/low)

Format your analysis as:
**Category:** [category]
**Urgency:** [level]
**Symptoms:**
- [symptom 1]
- [symptom 2]
**Affected Components:**
- [component 1]
- [component 2]
**Initial Assessment:** [brief summary]"""

    def __init__(self):
        self._agent: ChatAgent | None = None

    async def get_agent(self) -> ChatAgent:
        if self._agent is None:
            settings = get_settings()
            chat_client = AzureOpenAIChatClient(
                deployment_name=settings.azure_openai_deployment,
                endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
            )
            self._agent = ChatAgent(
                chat_client=chat_client,
                instructions=self.SYSTEM_PROMPT,
                name="Problem Analysis Agent",
            )
        return self._agent


class TechnicalDiagnosticsAgent:
    """
    Technical root cause analysis agent.

    Performs deep technical analysis to identify root causes.
    """

    SYSTEM_PROMPT = """You are a technical diagnostics expert for manufacturing systems.
Your specialty is identifying root causes of technical problems.

When diagnosing issues:
1. Review the symptoms and affected components
2. Query inventory/parts data for relevant information
3. Apply troubleshooting logic systematically
4. Consider multiple potential causes
5. Rank causes by likelihood

Use the 5 Whys technique:
- Start with the symptom
- Ask "Why?" repeatedly to drill down
- Stop when you reach the root cause

For each potential root cause:
- Describe the cause clearly
- List supporting evidence
- Rate confidence (high/medium/low)
- Explain how to verify

Format your diagnosis:
**Root Cause Analysis:**
1. [Primary cause] - Confidence: [level]
   Evidence: [supporting facts]
   Verification: [how to confirm]

2. [Secondary cause] - Confidence: [level]
   ..."""

    def __init__(self):
        self._agent: ChatAgent | None = None

    async def get_agent(self) -> ChatAgent:
        if self._agent is None:
            settings = get_settings()
            chat_client = AzureOpenAIChatClient(
                deployment_name=settings.azure_openai_deployment,
                endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
            )
            self._agent = ChatAgent(
                chat_client=chat_client,
                instructions=self.SYSTEM_PROMPT,
                name="Technical Diagnostics Agent",
                tools=list(LEGACY_DIAGNOSTIC_TOOLS),
            )
        return self._agent


class SolutionRecommendationAgent:
    """
    Solution recommendation agent.

    Recommends solutions based on identified root causes.
    """

    SYSTEM_PROMPT = """You are a solutions specialist for manufacturing problems.
Your job is to recommend practical solutions based on diagnosed root causes.

When recommending solutions:
1. Address each identified root cause
2. Prioritize by impact and urgency
3. Consider resource availability
4. Include immediate actions and long-term fixes

For each solution:
- Clear description of what needs to be done
- Step-by-step implementation plan
- Required parts/materials (check inventory)
- Estimated time and resources
- Expected outcome

Format your recommendations:
**Recommended Solutions:**

**Solution 1: [Title]** - Priority: [High/Medium/Low]
*Addresses:* [which root cause]
*Steps:*
1. [Step 1]
2. [Step 2]
*Required Parts:* [list parts, check availability]
*Estimated Time:* [duration]
*Expected Outcome:* [result]

**Solution 2: ...**"""

    def __init__(self):
        self._agent: ChatAgent | None = None

    async def get_agent(self) -> ChatAgent:
        if self._agent is None:
            settings = get_settings()
            chat_client = AzureOpenAIChatClient(
                deployment_name=settings.azure_openai_deployment,
                endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
            )
            self._agent = ChatAgent(
                chat_client=chat_client,
                instructions=self.SYSTEM_PROMPT,
                name="Solution Recommendation Agent",
                tools=list(LEGACY_DIAGNOSTIC_TOOLS),
            )
        return self._agent


class T6DiagnosticsWorkflow:
    """
    T6 Diagnostics Workflow implementation.

    Uses a magnetic pattern to dynamically select and chain
    diagnostic agents based on the problem type and findings.

    The workflow:
    1. Problem Analysis: Categorize and extract symptoms
    2. Technical Diagnosis: Identify root causes
    3. Solution Recommendation: Propose fixes
    4. Cache result for future similar problems

    Integrates with ProblemSolutionProvider for semantic caching.

    Usage:
        workflow = T6DiagnosticsWorkflow()
        result = await workflow.execute(
            query="Machine X is producing defective parts...",
            thread_id="thread_123",
        )
    """

    def __init__(
        self,
        problem_solution_cache: Any | None = None,
    ):
        """
        Initialize workflow.

        Args:
            problem_solution_cache: Optional cache for problem-solution pairs
        """
        self.problem_agent = ProblemAnalysisAgent()
        self.technical_agent = TechnicalDiagnosticsAgent()
        self.solution_agent = SolutionRecommendationAgent()
        self.cache = problem_solution_cache
        logger.info("T6DiagnosticsWorkflow initialized")

    async def _check_cache(self, query: str) -> DiagnosticsResult | None:
        """Check semantic cache for similar problems."""
        if self.cache is None:
            return None

        try:
            # Query cache for similar problems
            cached = await self.cache.find_similar(query, threshold=0.85)
            if cached:
                logger.info("Cache hit for diagnostics query")
                return DiagnosticsResult(
                    success=True,
                    formatted_response=cached.get("solution", ""),
                    cache_hit=True,
                )
        except Exception as e:
            logger.warning(f"Cache lookup failed: {e}")

        return None

    async def _run_agent_step(
        self,
        agent_wrapper: Any,
        query: str,
        agent_name: str,
        step_number: int,
    ) -> tuple[str, DiagnosisStep]:
        """Run a single diagnostic agent step."""
        agent = await agent_wrapper.get_agent()

        response = await agent.run(query)
        response_text = ""
        if response.messages:
            last_msg = response.messages[-1]
            response_text = last_msg.text if hasattr(last_msg, "text") else str(last_msg)

        # Parse findings from response
        findings = self._extract_list_items(response_text, ["finding", "cause", "symptom"])
        recommendations = self._extract_list_items(
            response_text, ["recommend", "solution", "action"]
        )

        step = DiagnosisStep(
            step_number=step_number,
            agent_name=agent_name,
            analysis=response_text,
            findings=findings,
            recommendations=recommendations,
        )

        return response_text, step

    def _extract_list_items(self, text: str, keywords: list[str]) -> list[str]:
        """Extract list items from text near given keywords."""
        items = []
        lines = text.split("\n")

        in_relevant_section = False
        for line in lines:
            lower = line.lower()

            # Check if we're entering a relevant section
            if any(kw in lower for kw in keywords):
                in_relevant_section = True

            # Extract list items
            if (
                line.strip().startswith(("-", "*", "•", "–"))  # noqa: RUF001
                or line.strip()[:2].replace(".", "").isdigit()
            ):
                content = line.strip().lstrip("-*•–0123456789. ").strip()  # noqa: RUF001
                if content and in_relevant_section:
                    items.append(content)

            # Reset on empty line
            if not line.strip():
                in_relevant_section = False

        return items[:10]  # Limit to 10 items

    async def execute(
        self,
        query: str,
        thread_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> DiagnosticsResult:
        """
        Execute diagnostics workflow.

        Args:
            query: The problem description
            thread_id: Conversation thread ID
            context: Additional context

        Returns:
            DiagnosticsResult with diagnosis and solutions
        """
        import time

        start_time = time.perf_counter()

        logger.info("Executing T6 diagnostics", extra={"thread_id": thread_id})

        try:
            # Check cache first
            cached_result = await self._check_cache(query)
            if cached_result:
                cached_result.execution_time_ms = (time.perf_counter() - start_time) * 1000
                return cached_result

            steps = []

            # Step 1: Problem Analysis
            analysis_query = f"Analyze this manufacturing problem:\n\n{query}"
            analysis_response, analysis_step = await self._run_agent_step(
                self.problem_agent,
                analysis_query,
                "ProblemAnalysis",
                1,
            )
            steps.append(analysis_step)

            # Determine problem category from analysis
            category = self._determine_category(analysis_response)

            # Step 2: Technical Diagnosis
            diagnosis_query = f"""Based on this problem analysis:
{analysis_response}

Original problem:
{query}

Perform root cause analysis and identify the underlying causes."""

            diagnosis_response, diagnosis_step = await self._run_agent_step(
                self.technical_agent,
                diagnosis_query,
                "TechnicalDiagnosis",
                2,
            )
            steps.append(diagnosis_step)

            # Parse root causes
            root_causes = self._parse_root_causes(diagnosis_response)

            # Step 3: Solution Recommendation
            solution_query = f"""Based on this diagnosis:
{diagnosis_response}

Problem analysis:
{analysis_response}

Recommend practical solutions to address the identified root causes."""

            solution_response, solution_step = await self._run_agent_step(
                self.solution_agent,
                solution_query,
                "SolutionRecommendation",
                3,
            )
            steps.append(solution_step)

            # Parse solutions
            solutions = self._parse_solutions(solution_response)

            # Compile final response
            final_response = self._compile_response(
                analysis_response,
                diagnosis_response,
                solution_response,
            )

            execution_time = (time.perf_counter() - start_time) * 1000

            logger.info(
                "T6 diagnostics complete",
                extra={
                    "thread_id": thread_id,
                    "category": category.value,
                    "root_causes_count": len(root_causes),
                    "solutions_count": len(solutions),
                    "execution_time_ms": execution_time,
                },
            )

            result = DiagnosticsResult(
                success=True,
                problem_category=category,
                root_causes=root_causes,
                solutions=solutions,
                diagnosis_steps=steps,
                formatted_response=final_response,
                cache_hit=False,
                execution_time_ms=execution_time,
            )

            # Cache the result for future use
            await self._cache_result(query, result)

            return result

        except Exception as e:
            execution_time = (time.perf_counter() - start_time) * 1000

            logger.error(f"T6 diagnostics failed: {e}", extra={"thread_id": thread_id})

            return DiagnosticsResult(
                success=False,
                error=str(e),
                formatted_response=f"Diagnostics failed: {e!s}",
                execution_time_ms=execution_time,
            )

    def _determine_category(self, analysis: str) -> ProblemCategory:
        """Determine problem category from analysis text."""
        lower = analysis.lower()

        if any(kw in lower for kw in ["equipment", "machine", "sensor", "motor"]):
            return ProblemCategory.EQUIPMENT_FAILURE
        elif any(kw in lower for kw in ["quality", "defect", "out-of-spec", "tolerance"]):
            return ProblemCategory.QUALITY_ISSUE
        elif any(kw in lower for kw in ["supply", "supplier", "delivery", "shortage"]):
            return ProblemCategory.SUPPLY_CHAIN
        elif any(kw in lower for kw in ["process", "procedure", "deviation"]):
            return ProblemCategory.PROCESS_DEVIATION
        elif any(kw in lower for kw in ["maintenance", "preventive", "wear"]):
            return ProblemCategory.MAINTENANCE
        elif any(kw in lower for kw in ["inventory", "stock", "location"]):
            return ProblemCategory.INVENTORY
        elif any(kw in lower for kw in ["safety", "hazard", "violation"]):
            return ProblemCategory.SAFETY
        else:
            return ProblemCategory.UNKNOWN

    def _parse_root_causes(self, diagnosis: str) -> list[RootCause]:
        """Parse root causes from diagnosis text."""
        causes = []

        # Simple parsing - look for numbered items with confidence
        import re

        # Pattern for root causes
        cause_pattern = r"(?:cause|root cause|problem)\s*:?\s*(.+?)(?:confidence|evidence|$)"

        for match in re.finditer(cause_pattern, diagnosis, re.IGNORECASE):
            description = match.group(1).strip()
            if description:
                # Determine confidence
                confidence = DiagnosisConfidence.MEDIUM
                if "high" in diagnosis.lower():
                    confidence = DiagnosisConfidence.HIGH
                elif "low" in diagnosis.lower():
                    confidence = DiagnosisConfidence.LOW

                causes.append(
                    RootCause(
                        description=description[:200],
                        confidence=confidence,
                    )
                )

        # If no causes found, extract from bullet points
        if not causes:
            for line in diagnosis.split("\n"):
                if line.strip().startswith(("1.", "2.", "3.", "-", "*")):
                    text = line.strip().lstrip("123.-* ").strip()
                    if text and len(text) > 20:
                        causes.append(
                            RootCause(
                                description=text[:200],
                                confidence=DiagnosisConfidence.MEDIUM,
                            )
                        )
                        if len(causes) >= 3:
                            break

        return causes

    def _parse_solutions(self, solution_text: str) -> list[Solution]:
        """Parse solutions from recommendation text."""
        solutions = []

        # Simple parsing - look for solution sections
        import re

        # Split by "Solution" headers
        solution_sections = re.split(r"\*\*Solution\s*\d*:?\s*", solution_text, flags=re.IGNORECASE)

        for section in solution_sections[1:]:  # Skip first split (before first solution)
            lines = section.strip().split("\n")
            if lines:
                title = lines[0].strip().rstrip("*").strip()

                # Extract steps
                steps = []
                for line in lines[1:]:
                    if line.strip().startswith(("1.", "2.", "3.", "4.", "5.")):
                        steps.append(line.strip().lstrip("12345. ").strip())

                solutions.append(
                    Solution(
                        description=title,
                        steps=steps,
                    )
                )

                if len(solutions) >= 3:
                    break

        return solutions

    def _compile_response(
        self,
        analysis: str,
        diagnosis: str,
        solution: str,
    ) -> str:
        """Compile all diagnostic outputs into a final response."""
        return f"""# 🔍 Diagnostic Report

## Problem Analysis
{analysis}

---

## Root Cause Analysis
{diagnosis}

---

## Recommended Solutions
{solution}

---
*This diagnostic report was generated by the AIMMS Diagnostics Workflow.*
"""

    async def _cache_result(self, query: str, result: DiagnosticsResult) -> None:
        """Cache the result for future similar queries."""
        if self.cache is None:
            return

        try:
            await self.cache.store(
                problem=query,
                solution=result.formatted_response,
                category=result.problem_category.value,
            )
            logger.info("Cached diagnostics result")
        except Exception as e:
            logger.warning(f"Failed to cache result: {e}")

    async def stream_execute(
        self,
        query: str,
        thread_id: str = "",
    ) -> AsyncIterator[str]:
        """Execute with streaming response."""
        yield "🔍 **Starting Diagnostic Analysis**\n\n"
        yield "📋 Step 1: Analyzing problem...\n"

        # Run problem analysis
        analysis_query = f"Analyze this manufacturing problem:\n\n{query}"
        agent = await self.problem_agent.get_agent()
        response = await agent.run(analysis_query)
        if response.messages:
            last_msg = response.messages[-1]
            content = last_msg.text if hasattr(last_msg, "text") else str(last_msg)
            yield f"\n{content}\n"

        yield "\n---\n🔧 Step 2: Identifying root causes...\n"

        # Run technical diagnosis
        agent = await self.technical_agent.get_agent()
        response = await agent.run(f"Diagnose: {query}")
        if response.messages:
            last_msg = response.messages[-1]
            content = last_msg.text if hasattr(last_msg, "text") else str(last_msg)
            yield f"\n{content}\n"

        yield "\n---\n💡 Step 3: Recommending solutions...\n"

        # Run solution recommendation
        agent = await self.solution_agent.get_agent()
        response = await agent.run(f"Recommend solutions for: {query}")
        if response.messages:
            last_msg = response.messages[-1]
            content = last_msg.text if hasattr(last_msg, "text") else str(last_msg)
            yield f"\n{content}\n"

        yield "\n---\n✅ Diagnostic analysis complete.\n"


class T6DiagnosticsBuilder:
    """
    Builder for T6 Diagnostics Workflow.

    Implements .as_agent() pattern for workflow composition.
    """

    def __init__(self):
        self._cache: Any | None = None
        self._additional_agents: list = []

    def with_cache(self, cache: Any) -> T6DiagnosticsBuilder:
        """Set the problem-solution cache."""
        self._cache = cache
        return self

    def with_additional_agent(
        self,
        name: str,
        agent: Any,
    ) -> T6DiagnosticsBuilder:
        """Add an additional diagnostic agent."""
        self._additional_agents.append((name, agent))
        return self

    def build(self) -> T6DiagnosticsWorkflow:
        """Build configured workflow."""
        return T6DiagnosticsWorkflow(problem_solution_cache=self._cache)

    def as_agent(self) -> ChatAgent:
        """Convert workflow to a composable agent."""
        settings = get_settings()

        combined_prompt = """You are a comprehensive diagnostics specialist for manufacturing.

You combine expertise in:
- Problem analysis and categorization
- Technical root cause analysis
- Solution recommendation

When diagnosing problems:
1. First analyze and categorize the problem
2. Identify potential root causes systematically
3. Recommend practical solutions
4. Consider parts availability and resources

Provide thorough analysis with:
- Problem category and urgency
- Root causes ranked by likelihood
- Solutions prioritized by impact
- Required resources and estimated time"""

        chat_client = AzureOpenAIChatClient(
            deployment_name=settings.azure_openai_deployment,
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
        )

        return ChatAgent(
            chat_client=chat_client,
            instructions=combined_prompt,
            name="AIMMS Diagnostics Agent",
            description="Equipment diagnostics and troubleshooting",
            tools=list(LEGACY_DIAGNOSTIC_TOOLS),
        )


# Factory functions
def create_t6_diagnostics_workflow(
    cache: Any | None = None,
) -> T6DiagnosticsWorkflow:
    """Create a T6 diagnostics workflow instance."""
    return T6DiagnosticsWorkflow(problem_solution_cache=cache)


def t6_diagnostics_builder() -> T6DiagnosticsBuilder:
    """Get a T6 diagnostics workflow builder."""
    return T6DiagnosticsBuilder()
