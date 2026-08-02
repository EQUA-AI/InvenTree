"""
Workflow Registry

Centralized registry for workflow definitions with .as_agent() pattern.
Maps workflow IDs to workflow builders and provides uniform invocation.
"""

from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any, Protocol, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)


class WorkflowTier(StrEnum):
    """Workflow complexity tier classification."""

    T1_SINGLE_AGENT = "t1"
    """Single agent fast-path. Direct query/response."""

    T2_SEQUENTIAL = "t2"
    """Sequential multi-agent. Step-by-step processing."""

    T3_CONCURRENT = "t3"
    """Concurrent multi-agent. Parallel execution."""

    T4_HITL = "t4"
    """Human-in-the-loop. Requires approval."""

    T5_GROUP_CHAT = "t5"
    """Group chat. Collaborative agents."""

    T6_MAGENTIC = "t6"
    """Magentic orchestration. Complex multi-turn."""


@runtime_checkable
class WorkflowProtocol(Protocol):
    """Protocol for workflow implementations."""

    async def invoke(
        self,
        thread_id: str,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke the workflow with a user message."""
        ...

    def as_agent(self) -> Any:
        """Return the workflow as a composable agent for nested workflows."""
        ...


@dataclass
class WorkflowDefinition:
    """
    Workflow definition with metadata.

    Attributes:
        workflow_id: Unique identifier (e.g., "wf1", "wf8")
        name: Human-readable name
        description: Workflow description
        tier: Complexity tier
        builder: Workflow builder class or factory
        context_bundles: List of context provider names to attach
        requires_hitl: Whether workflow requires human approval
        cacheable: Whether responses can be cached
        tags: Additional tags for filtering
    """

    workflow_id: str
    name: str
    description: str
    tier: WorkflowTier
    builder: type | None = None
    context_bundles: list[str] = field(default_factory=list)
    requires_hitl: bool = False
    cacheable: bool = True
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate workflow definition."""
        if self.requires_hitl and self.tier != WorkflowTier.T4_HITL:
            logger.warning(
                "Workflow requires HITL but tier is not T4",
                workflow_id=self.workflow_id,
                tier=self.tier.value,
            )


# Context bundle mapping per workflow type
CONTEXT_BUNDLES_BY_WORKFLOW: dict[str, list[str]] = {
    "wf1": ["user_profile", "problem_solution", "thread_summary"],
    "wf2": ["user_profile", "parts_preference"],
    "wf3": ["user_profile", "parts_preference"],
    "wf4": ["user_profile", "parts_preference"],
    "wf5": ["user_profile", "parts_preference", "thread_summary"],
    "wf6": ["user_profile"],
    "wf7": ["user_profile", "problem_solution", "thread_summary"],
    "wf8": ["user_profile", "parts_preference"],
}

# ``WorkflowType.T6_DIAGNOSTICS`` intentionally retains its public value
# ``wf1_diagnostics`` while the registry's canonical diagnostics ID is ``wf1``.
# Resolve that historical/public spelling at lookup time without adding a
# duplicate registration or changing the canonical IDs returned by listings.
WORKFLOW_ID_ALIASES: dict[str, str] = {
    "wf1_diagnostics": "wf1",
}


def resolve_workflow_id(workflow_id: str | Enum) -> str:
    """Return the canonical registry ID for a public workflow identifier."""
    public_id = workflow_id.value if isinstance(workflow_id, Enum) else workflow_id
    return WORKFLOW_ID_ALIASES.get(public_id, public_id)


class WorkflowRegistry:
    """
    Registry for workflow definitions.

    Provides:
    - Registration of workflow builders
    - Lookup by workflow ID
    - Filtering by tier, tags, etc.
    - Factory method for workflow instantiation

    Example usage:
        ```python
        registry = get_workflow_registry()

        # Register a workflow
        registry.register(WorkflowDefinition(
            workflow_id="wf8",
            name="T1 Lookup",
            description="Fast single-agent lookup",
            tier=WorkflowTier.T1_SINGLE_AGENT,
            builder=LookupWorkflow,
        ))

        # Get workflow
        workflow = registry.get_workflow("wf8")
        result = await workflow.invoke(thread_id, message)

        # Get as composable agent
        agent = workflow.as_agent()
        ```
    """

    def __init__(self) -> None:
        """Initialize the workflow registry."""
        self._workflows: dict[str, WorkflowDefinition] = {}
        self._instances: dict[str, Any] = {}  # Cached workflow instances
        logger.info("WorkflowRegistry initialized")

    def register(self, definition: WorkflowDefinition) -> None:
        """
        Register a workflow definition.

        Args:
            definition: The workflow definition to register.
        """
        if definition.workflow_id in self._workflows:
            logger.warning(
                "Overwriting existing workflow registration",
                workflow_id=definition.workflow_id,
            )

        # Apply default context bundles if not specified
        if not definition.context_bundles:
            definition.context_bundles = CONTEXT_BUNDLES_BY_WORKFLOW.get(definition.workflow_id, [])

        self._workflows[definition.workflow_id] = definition

        logger.info(
            "Workflow registered",
            workflow_id=definition.workflow_id,
            name=definition.name,
            tier=definition.tier.value,
        )

    def get_definition(self, workflow_id: str) -> WorkflowDefinition | None:
        """
        Get a workflow definition by ID.

        Args:
            workflow_id: The workflow ID.

        Returns:
            WorkflowDefinition or None if not found.
        """
        return self._workflows.get(resolve_workflow_id(workflow_id))

    def get_workflow(self, workflow_id: str) -> Any | None:
        """
        Get a workflow instance by ID.

        Creates the instance if not already cached.

        Args:
            workflow_id: The workflow ID.

        Returns:
            Workflow instance or None if not found.
        """
        canonical_id = resolve_workflow_id(workflow_id)

        # Check cache
        if canonical_id in self._instances:
            return self._instances[canonical_id]

        definition = self._workflows.get(canonical_id)
        if definition is None or definition.builder is None:
            return None

        # Create instance
        instance = definition.builder()
        self._instances[canonical_id] = instance

        return instance

    def get_as_agent(self, workflow_id: str) -> Any | None:
        """
        Get a workflow as a composable agent.

        This follows the .as_agent() pattern for nested workflows.

        Args:
            workflow_id: The workflow ID.

        Returns:
            Agent wrapper or None if not found.
        """
        workflow = self.get_workflow(workflow_id)
        if workflow is None:
            return None

        if hasattr(workflow, "as_agent"):
            return workflow.as_agent()

        return workflow

    def list_workflows(
        self,
        tier: WorkflowTier | None = None,
        tag: str | None = None,
        requires_hitl: bool | None = None,
    ) -> list[WorkflowDefinition]:
        """
        List workflow definitions with optional filters.

        Args:
            tier: Filter by complexity tier.
            tag: Filter by tag.
            requires_hitl: Filter by HITL requirement.

        Returns:
            List of matching workflow definitions.
        """
        results = list(self._workflows.values())

        if tier is not None:
            results = [w for w in results if w.tier == tier]

        if tag is not None:
            results = [w for w in results if tag in w.tags]

        if requires_hitl is not None:
            results = [w for w in results if w.requires_hitl == requires_hitl]

        return results

    def list_workflow_ids(self) -> list[str]:
        """Get all registered workflow IDs."""
        return list(self._workflows.keys())

    def clear_cache(self) -> None:
        """Clear cached workflow instances."""
        self._instances.clear()


# Module-level singleton
_registry: WorkflowRegistry | None = None


def get_workflow_registry() -> WorkflowRegistry:
    """Get the singleton workflow registry instance."""
    global _registry
    if _registry is None:
        _registry = WorkflowRegistry()
        _register_default_workflows(_registry)
    return _registry


def _register_default_workflows(registry: WorkflowRegistry) -> None:
    """
    Register all default AIMMS workflows.

    Called automatically when the registry is first accessed.
    """
    # Import workflow classes lazily to avoid circular imports
    from ai.core.workflows.wf1_diagnostics import T6DiagnosticsWorkflow
    from ai.core.workflows.wf2_parts_analysis import T2PartsAnalysisWorkflow
    from ai.core.workflows.wf3_research import T3ResearchWorkflow
    from ai.core.workflows.wf4_procurement import T4ProcurementWorkflow
    from ai.core.workflows.wf5_cpq import T5CPQWorkflow
    from ai.core.workflows.wf6_documents import WF6DocumentWorkflow
    from ai.core.workflows.wf7_repair_packet import WF7RepairPacketWorkflow
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    # WF8: T1 Lookup (fast-path)
    registry.register(
        WorkflowDefinition(
            workflow_id="wf8",
            name="T1 Lookup",
            description="Fast single-agent lookup for stock, parts, BOM, locations",
            tier=WorkflowTier.T1_SINGLE_AGENT,
            builder=T1LookupWorkflow,
            cacheable=True,
            requires_hitl=False,
            tags=["read-only", "fast", "phase-1"],
        )
    )

    # WF2: T2 Parts Analysis
    registry.register(
        WorkflowDefinition(
            workflow_id="wf2",
            name="T2 Parts Analysis",
            description="Sequential BOM analysis and compatibility checking",
            tier=WorkflowTier.T2_SEQUENTIAL,
            builder=T2PartsAnalysisWorkflow,
            cacheable=True,
            requires_hitl=False,
            tags=["read-only", "analysis", "phase-1"],
        )
    )

    # WF3: T3 Research
    registry.register(
        WorkflowDefinition(
            workflow_id="wf3",
            name="T3 Research",
            description="Concurrent multi-source research and documentation",
            tier=WorkflowTier.T3_CONCURRENT,
            builder=T3ResearchWorkflow,
            cacheable=True,
            requires_hitl=False,
            tags=["read-only", "research", "phase-1"],
        )
    )

    # WF4: T4 Procurement
    registry.register(
        WorkflowDefinition(
            workflow_id="wf4",
            name="T4 Procurement",
            description="Purchase order creation with HITL approval",
            tier=WorkflowTier.T4_HITL,
            builder=T4ProcurementWorkflow,
            cacheable=False,
            requires_hitl=True,
            tags=["write", "procurement", "phase-2"],
        )
    )

    # WF5: T5 CPQ
    registry.register(
        WorkflowDefinition(
            workflow_id="wf5",
            name="T5 Configure-Price-Quote",
            description="Product configuration and quote generation",
            tier=WorkflowTier.T5_GROUP_CHAT,
            builder=T5CPQWorkflow,
            cacheable=False,
            requires_hitl=True,
            tags=["write", "cpq", "phase-2"],
        )
    )

    # WF1: T6 Diagnostics
    registry.register(
        WorkflowDefinition(
            workflow_id="wf1",
            name="T6 Diagnostics",
            description="Complex troubleshooting and root cause analysis",
            tier=WorkflowTier.T6_MAGENTIC,
            builder=T6DiagnosticsWorkflow,
            # Diagnoses must never be replayed across machines or faults;
            # HITLSafetyRules.NEVER_CACHE_WORKFLOWS lists wf1 for the same
            # reason. (No production code reads this flag today — it is kept
            # truthful so nothing can later wire caching in "because the
            # registry said it was safe".)
            cacheable=False,
            requires_hitl=False,
            tags=["analysis", "diagnostics", "phase-2"],
        )
    )

    # WF7: Repair Packet (spine) assembly
    registry.register(
        WorkflowDefinition(
            workflow_id="wf7",
            name="Repair Packet",
            description="Assemble an approval-ready repair packet from a fault",
            tier=WorkflowTier.T6_MAGENTIC,
            builder=WF7RepairPacketWorkflow,
            cacheable=False,
            requires_hitl=False,
            tags=["analysis", "packet", "spine"],
        )
    )

    # WF6: Document Processing
    registry.register(
        WorkflowDefinition(
            workflow_id="wf6",
            name="Document Processing",
            description="Incoming document processing with Azure Doc Intelligence",
            tier=WorkflowTier.T4_HITL,
            builder=WF6DocumentWorkflow,
            cacheable=False,
            requires_hitl=True,
            tags=["write", "documents", "phase-2"],
        )
    )

    # GENERAL: Fallback workflow using T1 Lookup (has all common tools)
    # This catches any requests that don't match a specific workflow type
    registry.register(
        WorkflowDefinition(
            workflow_id="general",
            name="General Assistant",
            description="General-purpose assistant with inventory, email, and PDF tools",
            tier=WorkflowTier.T1_SINGLE_AGENT,
            builder=T1LookupWorkflow,
            cacheable=True,
            requires_hitl=False,
            tags=["general", "fallback"],
        )
    )

    logger.info(
        "Default workflows registered",
        workflow_count=len(registry.list_workflow_ids()),
        workflow_ids=registry.list_workflow_ids(),
    )
