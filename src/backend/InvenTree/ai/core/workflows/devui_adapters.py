"""
MAF DevUI Workflow Adapters

Adapters to wrap existing AIMMS workflow classes (with execute() methods)
into proper MAF Workflow objects that can run in DevUI with run() and run_stream().

These patterns allow your existing workflow architecture to be compatible with
Microsoft Agent Framework's DevUI while preserving your current code structure.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import Any, Never, TYPE_CHECKING

from agent_framework import (
    Executor,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowOutputEvent,
    WorkflowEvent,
    handler,
    ChatMessage,
    Role,
)

if TYPE_CHECKING:
    from agent_framework import Workflow, WorkflowAgent
    from ai.core.workflows.wf1_diagnostics import DiagnosticsResult, T6DiagnosticsWorkflow
    from ai.core.workflows.wf2_parts_analysis import T2PartsAnalysisWorkflow
    from ai.core.workflows.wf3_research import T3ResearchWorkflow
    from ai.core.workflows.wf4_procurement import T4ProcurementWorkflow
    from ai.core.workflows.wf5_cpq import T5CPQWorkflow
    from ai.core.workflows.wf6_documents import WF6DocumentWorkflow
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

logger = logging.getLogger(__name__)


# =============================================================================
# Pattern 1: Single Executor Adapter
# Wraps a workflow class with execute() as a single MAF Executor
# =============================================================================

class WorkflowExecutorAdapter(Executor):
    """
    Adapter that wraps any workflow class with an execute() method.
    
    This is the simplest pattern - wraps your entire workflow as a single
    MAF Executor that can be used in a WorkflowBuilder.
    
    Usage:
        from ai.core.workflows.wf1_diagnostics import T6DiagnosticsWorkflow
        
        diagnostics = T6DiagnosticsWorkflow()
        executor = WorkflowExecutorAdapter(diagnostics, id="diagnostics")
        
        workflow = WorkflowBuilder().set_start_executor(executor).build()
    """
    
    def __init__(
        self,
        workflow_instance: Any,
        id: str,
        execute_method: str = "execute",
    ):
        super().__init__(id=id)
        self._workflow = workflow_instance
        self._execute_method = execute_method
    
    @handler
    async def handle_string(
        self, 
        message: str, 
        ctx: WorkflowContext
    ) -> None:
        """Handle string input."""
        result = await self._execute_workflow(message)
        await ctx.yield_output(result)
    
    @handler
    async def handle_messages(
        self, 
        messages: list[ChatMessage], 
        ctx: WorkflowContext
    ) -> None:
        """Handle list of ChatMessage input (for DevUI/agent compatibility)."""
        # Extract text from messages
        text_parts = []
        for msg in messages:
            if msg.text:
                text_parts.append(msg.text)
        message = " ".join(text_parts)
        
        result = await self._execute_workflow(message)
        await ctx.yield_output(result)
    
    @handler
    async def handle_dict(
        self, 
        data: dict, 
        ctx: WorkflowContext
    ) -> None:
        """Handle dict input with query and optional context."""
        query = data.get("query", data.get("message", str(data)))
        thread_id = data.get("thread_id", "default")
        context = data.get("context", {})
        
        result = await self._execute_workflow(query, thread_id, context)
        await ctx.yield_output(result)
    
    async def _execute_workflow(
        self,
        query: str,
        thread_id: str = "default",
        context: dict | None = None,
    ) -> Any:
        """Execute the wrapped workflow."""
        execute_fn = getattr(self._workflow, self._execute_method)
        
        # Try different signatures
        try:
            return await execute_fn(query, thread_id=thread_id, context=context or {})
        except TypeError:
            try:
                return await execute_fn(query, thread_id)
            except TypeError:
                return await execute_fn(query)


# =============================================================================
# Pattern 2: Streaming Executor Adapter
# For workflows that support streaming progress updates
# =============================================================================

class StreamingWorkflowAdapter(Executor):
    """
    Adapter for workflows that support streaming execution.
    
    Expects the workflow to have either:
    - execute_stream() that yields progress updates
    - execute() that returns steps/events that can be streamed
    
    Usage:
        class MyWorkflow:
            async def execute_stream(self, query: str) -> AsyncIterator[dict]:
                yield {"step": 1, "status": "analyzing..."}
                result = await self._analyze(query)
                yield {"step": 2, "status": "complete", "result": result}
        
        executor = StreamingWorkflowAdapter(MyWorkflow(), id="my_workflow")
    """
    
    def __init__(
        self,
        workflow_instance: Any,
        id: str,
        stream_method: str = "execute_stream",
        fallback_method: str = "execute",
    ):
        super().__init__(id=id)
        self._workflow = workflow_instance
        self._stream_method = stream_method
        self._fallback_method = fallback_method
    
    @handler
    async def handle_string(
        self, 
        message: str, 
        ctx: WorkflowContext[Never, Any]
    ) -> None:
        """Handle string input with streaming."""
        if hasattr(self._workflow, self._stream_method):
            stream_fn = getattr(self._workflow, self._stream_method)
            final_result = None
            async for event in stream_fn(message):
                # Emit intermediate events
                await ctx.add_event(WorkflowOutputEvent(data=event, source_executor_id=self.id))
                final_result = event
            if final_result:
                await ctx.yield_output(final_result)
        else:
            # Fallback to non-streaming
            execute_fn = getattr(self._workflow, self._fallback_method)
            result = await execute_fn(message)
            await ctx.yield_output(result)
    
    @handler
    async def handle_messages(
        self, 
        messages: list[ChatMessage], 
        ctx: WorkflowContext[Never, Any]
    ) -> None:
        """Handle ChatMessage list input."""
        text = " ".join(m.text for m in messages if m.text)
        
        if hasattr(self._workflow, self._stream_method):
            stream_fn = getattr(self._workflow, self._stream_method)
            final_result = None
            async for event in stream_fn(text):
                await ctx.add_event(WorkflowOutputEvent(data=event, source_executor_id=self.id))
                final_result = event
            if final_result:
                await ctx.yield_output(final_result)
        else:
            execute_fn = getattr(self._workflow, self._fallback_method)
            result = await execute_fn(text)
            await ctx.yield_output(result)


# =============================================================================
# Pattern 3: Direct Workflow Wrapper
# Creates a full Workflow object that delegates to your workflow class
# =============================================================================

def create_maf_workflow(
    workflow_instance: Any,
    workflow_id: str,
    name: str | None = None,
    description: str | None = None,
    execute_method: str = "execute",
) -> "Workflow":
    """
    Create a MAF Workflow from any workflow class with execute() method.
    
    This creates a proper MAF Workflow that can be:
    - Registered with DevUI directly
    - Converted to an agent with workflow.as_agent()
    - Used in nested workflows
    
    Usage:
        from ai.core.workflows.wf1_diagnostics import DiagnosticsWorkflow
        
        diagnostics = DiagnosticsWorkflow()
        workflow = create_maf_workflow(
            diagnostics, 
            workflow_id="diagnostics",
            name="T6 Diagnostics",
            description="Manufacturing diagnostics workflow"
        )
        
        # Use with DevUI
        from agent_framework.devui import serve
        serve(entities=[workflow])
    """
    from agent_framework import Workflow
    
    executor = WorkflowExecutorAdapter(
        workflow_instance,
        id=f"{workflow_id}_executor",
        execute_method=execute_method,
    )
    
    workflow = (
        WorkflowBuilder(name=name, description=description)
        .set_start_executor(executor)
        .build()
    )
    
    return workflow


# =============================================================================
# Pattern 4: Multi-Step Workflow with Progress
# For workflows that have distinct phases you want to expose
# =============================================================================

class DiagnosticsWorkflowExecutor(Executor):
    """
    Example: Diagnostics workflow with exposed phases.
    
    This pattern exposes workflow phases as separate executor steps,
    allowing the DevUI to show progress through each phase.
    """
    
    def __init__(self, diagnostics_workflow: Any, id: str = "diagnostics"):
        super().__init__(id=id)
        self._workflow = diagnostics_workflow
    
    @handler
    async def handle_input(
        self,
        message: str,
        ctx: WorkflowContext[dict, "DiagnosticsResult"],
    ) -> None:
        """Main handler that orchestrates the workflow."""
        # Phase 1: Problem Analysis
        await ctx.add_event(WorkflowOutputEvent(
            data={"phase": "analysis", "status": "starting"},
            source_executor_id=self.id
        ))
        
        analysis = await self._workflow._run_problem_analysis(message)
        
        await ctx.add_event(WorkflowOutputEvent(
            data={"phase": "analysis", "status": "complete", "result": analysis},
            source_executor_id=self.id
        ))
        
        # Phase 2: Technical Diagnosis
        await ctx.add_event(WorkflowOutputEvent(
            data={"phase": "diagnosis", "status": "starting"},
            source_executor_id=self.id
        ))
        
        diagnosis = await self._workflow._run_technical_diagnosis(analysis)
        
        await ctx.add_event(WorkflowOutputEvent(
            data={"phase": "diagnosis", "status": "complete", "result": diagnosis},
            source_executor_id=self.id
        ))
        
        # Phase 3: Solution Recommendation
        await ctx.add_event(WorkflowOutputEvent(
            data={"phase": "solutions", "status": "starting"},
            source_executor_id=self.id
        ))
        
        solutions = await self._workflow._run_solution_recommendation(diagnosis)
        
        # Final result
        result = await self._workflow._compile_result(analysis, diagnosis, solutions)
        await ctx.yield_output(result)


# =============================================================================
# Pattern 5: Workflow Wrapper Class with Agent Interface
# Provides both workflow and agent interfaces
# =============================================================================

class DevUICompatibleWorkflow:
    """
    Wrapper that makes any workflow class compatible with DevUI.
    
    Provides the interface that DevUI expects:
    - executors / get_executors_list() for workflow detection
    - run() and run_stream() for execution
    
    Usage:
        diagnostics = DiagnosticsWorkflow()
        devui_workflow = DevUICompatibleWorkflow(
            diagnostics,
            name="T6 Diagnostics",
            description="Manufacturing problem diagnostics"
        )
        
        from agent_framework.devui import serve
        serve(entities=[devui_workflow])
    """
    
    def __init__(
        self,
        workflow_instance: Any,
        name: str | None = None,
        description: str | None = None,
        execute_method: str = "execute",
    ):
        self._workflow_instance = workflow_instance
        self.name = name or workflow_instance.__class__.__name__
        self.description = description or ""
        self._execute_method = execute_method
        
        # Create the underlying MAF workflow
        self._maf_workflow = create_maf_workflow(
            workflow_instance,
            workflow_id=self.name.lower().replace(" ", "_"),
            name=name,
            description=description,
            execute_method=execute_method,
        )
    
    # Workflow detection properties (for DevUI)
    @property
    def executors(self):
        return self._maf_workflow.executors
    
    def get_executors_list(self):
        return self._maf_workflow.get_executors_list()
    
    # Execution methods
    async def run(self, message: Any, **kwargs) -> Any:
        """Non-streaming execution."""
        return await self._maf_workflow.run(message, **kwargs)
    
    async def run_stream(self, message: Any, **kwargs) -> AsyncIterable[WorkflowEvent]:
        """Streaming execution."""
        async for event in self._maf_workflow.run_stream(message, **kwargs):
            yield event


# =============================================================================
# Pattern 6: Agent Wrapper for Workflows
# Converts workflow to full Agent interface for DevUI
# =============================================================================

def create_workflow_agent(
    workflow_instance: Any,
    name: str,
    description: str | None = None,
    execute_method: str = "execute",
) -> "WorkflowAgent":
    """
    Create a WorkflowAgent from any workflow class.
    
    The resulting agent can be used anywhere an agent is expected,
    including DevUI agent view.
    
    Note: The workflow's executor must accept list[ChatMessage] as input.
    
    Usage:
        from ai.core.workflows.wf1_diagnostics import DiagnosticsWorkflow
        
        diagnostics = DiagnosticsWorkflow()
        agent = create_workflow_agent(
            diagnostics,
            name="Diagnostics Agent",
            description="Diagnose manufacturing problems"
        )
        
        # Now works as a standard agent
        response = await agent.run("My machine is making noise")
    """
    from agent_framework import WorkflowAgent
    
    workflow = create_maf_workflow(
        workflow_instance,
        workflow_id=name.lower().replace(" ", "_"),
        name=name,
        description=description,
        execute_method=execute_method,
    )
    
    return workflow.as_agent(name=name)


# =============================================================================
# Factory Functions for AIMMS Workflows
# =============================================================================

def create_diagnostics_workflow() -> DevUICompatibleWorkflow:
    """Create DevUI-compatible diagnostics workflow."""
    from ai.core.workflows.wf1_diagnostics import T6DiagnosticsWorkflow
    
    return DevUICompatibleWorkflow(
        T6DiagnosticsWorkflow(),
        name="T6 Diagnostics",
        description="Complex manufacturing diagnostics with root cause analysis",
    )


def create_parts_analysis_workflow() -> DevUICompatibleWorkflow:
    """Create DevUI-compatible parts analysis workflow."""
    from ai.core.workflows.wf2_parts_analysis import T2PartsAnalysisWorkflow
    
    return DevUICompatibleWorkflow(
        T2PartsAnalysisWorkflow(),
        name="T2 Parts Analysis",
        description="Analyze parts and BOM structures",
    )


def create_research_workflow() -> DevUICompatibleWorkflow:
    """Create DevUI-compatible research workflow."""
    from ai.core.workflows.wf3_research import T3ResearchWorkflow
    
    return DevUICompatibleWorkflow(
        T3ResearchWorkflow(),
        name="T3 Research",
        description="Multi-source PARALLEL research and information gathering",
    )


def create_procurement_workflow() -> DevUICompatibleWorkflow:
    """Create DevUI-compatible procurement workflow."""
    from ai.core.workflows.wf4_procurement import T4ProcurementWorkflow
    
    return DevUICompatibleWorkflow(
        T4ProcurementWorkflow(),
        name="T4 Procurement",
        description="Procurement with HITL human-in-the-loop approval",
    )


def create_cpq_workflow() -> DevUICompatibleWorkflow:
    """Create DevUI-compatible CPQ workflow."""
    from ai.core.workflows.wf5_cpq import T5CPQWorkflow
    
    return DevUICompatibleWorkflow(
        T5CPQWorkflow(),
        name="T5 CPQ",
        description="Configure-Price-Quote multi-agent workflow",
    )


def create_documents_workflow() -> DevUICompatibleWorkflow:
    """Create DevUI-compatible documents workflow."""
    from ai.core.workflows.wf6_documents import WF6DocumentWorkflow
    
    return DevUICompatibleWorkflow(
        WF6DocumentWorkflow(),
        name="T7 Documents",
        description="Document processing with Azure Document Intelligence",
    )


def create_lookup_workflow() -> DevUICompatibleWorkflow:
    """Create DevUI-compatible lookup workflow."""
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow
    
    return DevUICompatibleWorkflow(
        T1LookupWorkflow(),
        name="T1 Lookup",
        description="Fast inventory and parts lookup",
    )


def get_all_devui_workflows() -> list[DevUICompatibleWorkflow]:
    """Get all workflows configured for DevUI."""
    return [
        create_diagnostics_workflow(),
        create_parts_analysis_workflow(),
        create_research_workflow(),
        create_procurement_workflow(),
        create_cpq_workflow(),
        create_documents_workflow(),
        create_lookup_workflow(),
    ]


# =============================================================================
# DevUI Server Entry Point
# =============================================================================

def run_devui_with_workflows(port: int = 8080, auto_open: bool = True):
    """
    Launch DevUI server with all AIMMS workflows.
    
    Usage:
        python -m ai.core.workflows.devui_adapters
    """
    from agent_framework.devui import serve
    
    workflows = get_all_devui_workflows()
    
    logger.info(f"Starting DevUI with {len(workflows)} workflows on port {port}")
    for wf in workflows:
        logger.info(f"  - {wf.name}: {wf.description}")
    
    serve(entities=workflows, port=port, auto_open=auto_open)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_devui_with_workflows()
