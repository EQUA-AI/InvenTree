"""
AIMMS Root Workflow

The top-level workflow that orchestrates all user interactions.
Replaces the monolithic OrchestratorAgent with a workflow-first approach.

Responsibilities:
1. Manage conversation state (thread/run)
2. Route to specific workflows (using injected Router)
3. Handle event streaming (AG-UI)
4. Manage global error handling
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncIterator, Protocol

from ai.core.streaming import EventType, RunContext, EventEmitter, create_run_context
from ai.core.agents.routing import RoutingDecision, WorkflowType, UnifiedRouter
from ai.core.workflows.registry import WorkflowRegistry, get_workflow_registry
from ai.core.memory.conversation import ConversationManager

logger = logging.getLogger(__name__)


class RouterProtocol(Protocol):
    """Protocol for the router component."""
    
    async def route(
        self, 
        message: str, 
        thread_id: str, 
        context: dict[str, Any] | None = None
    ) -> RoutingDecision:
        """Route a message to a workflow."""
        ...


class ConversationManagerProtocol(Protocol):
    """Protocol for conversation manager."""
    
    def get_or_create_state(self, thread_id: str, user_id: str) -> Any: ...
    async def gather_context(self, query: str, thread_id: str, user_id: str) -> dict[str, Any]: ...


class RootWorkflow:
    """
    The root workflow that serves as the entry point for all chat interactions.
    """
    
    def __init__(
        self,
        router: RouterProtocol,
        registry: WorkflowRegistry,
        conversation_manager: ConversationManagerProtocol,
    ):
        self.router = router
        self.registry = registry
        self.conversation_manager = conversation_manager

    async def run_stream(
        self,
        message: str,
        thread_id: str | None = None,
        user_id: str = "anonymous",
        emitter: EventEmitter | None = None,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """
        Process a user message with AG-UI event streaming.
        
        Args:
            message: User's input message
            thread_id: Optional thread ID (generated if None)
            user_id: User identifier
            emitter: Event emitter for AG-UI events
            context: Additional context dictionary
            
        Yields:
            Response text chunks
        """
        # Generate IDs
        thread_id = thread_id or str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        
        # Get conversation state
        state = self.conversation_manager.get_or_create_state(thread_id, user_id)
        if hasattr(state, "increment_turn"):
            state.increment_turn()
        
        # Create run context for event emission
        run_ctx = RunContext(
            emitter=emitter,
            thread_id=thread_id,
            run_id=run_id,
            agent_name="root_workflow",
        )
        
        try:
            # Emit run started
            await run_ctx.emit_run_started()
            
            # Step 1: Gather context
            # We gather context before routing to help the router make better decisions
            await run_ctx.emit_thinking("Gathering context...")
            
            aggregated_context = await self.conversation_manager.gather_context(
                query=message,
                thread_id=thread_id,
                user_id=user_id,
            )
            
            if context:
                aggregated_context.update(context)
            
            # Step 2: Route
            await run_ctx.emit_thinking("Routing request...")
            
            decision = await self.router.route(
                message=message,
                thread_id=thread_id,
                context=aggregated_context
            )
            
            # Handle fast path if the router executed it and returned a result
            if decision.use_fast_path and decision.fast_path_result:
                # If the router already handled it (e.g. fast path), we just stream the result
                # Note: This depends on how the Router is implemented. 
                # If it returns a result in the decision, we use it.
                # For now, we assume the router might return a result directly.
                # But typically routing just picks a workflow.
                # If fast path is a "workflow" (T1_LOOKUP), we proceed to execute it.
                pass

            # Step 3: Execute Workflow
            workflow_id = decision.get_workflow_id()
            
            if not workflow_id:
                # Fallback to general conversation if no specific workflow
                workflow_id = "general" # Or handle gracefully
                
            await run_ctx.emit_workflow_started(
                workflow_id=workflow_id or "unknown",
                workflow_name=decision.workflow_type.name,
            )
            
            workflow = self.registry.get_workflow(workflow_id)
            
            if not workflow:
                # If workflow not found, try general fallback or error
                error_msg = f"Workflow '{workflow_id}' not found."
                logger.error(error_msg)
                await run_ctx.emit_error(error_msg)
                yield f"I apologize, but I couldn't find the appropriate workflow ({workflow_id}) to handle your request."
                return

            await run_ctx.emit_executing(f"Running {decision.workflow_type.name}...")
            
            # Execute the workflow
            # We support both streaming and non-streaming workflows
            if hasattr(workflow, "execute_streaming"):
                async for chunk in workflow.execute_streaming(
                    query=message,
                    thread_id=thread_id,
                    context=aggregated_context,
                ):
                    yield chunk
            elif hasattr(workflow, "run_stream"):
                 # Agent Framework style
                 async for chunk in workflow.run_stream(
                    message=message,
                    thread_id=thread_id,
                    run_id=run_id,
                    decision=decision
                 ):
                     yield chunk
            else:
                # Fallback to sync/async execute
                result = await workflow.execute(
                    query=message,
                    thread_id=thread_id,
                    context=aggregated_context,
                )
                
                response = str(result)
                if hasattr(result, "formatted_response"):
                    response = result.formatted_response
                
                # Stream the response as a single chunk (or split it)
                await run_ctx.emit_text_start()
                await run_ctx.emit_text_delta(response)
                await run_ctx.emit_text_end()
                yield response

            await run_ctx.emit_run_finished()

        except Exception as e:
            logger.error(f"RootWorkflow error: {e}", exc_info=True)
            await run_ctx.emit_error(str(e))
            yield f"I apologize, but I encountered an error while processing your request: {str(e)}"


def get_root_workflow() -> RootWorkflow:
    """Factory to create a fresh RootWorkflow instance."""
    router = UnifiedRouter()
    registry = get_workflow_registry()
    # ConversationManager handles its own persistence/caching
    conversation_manager = ConversationManager()
    
    return RootWorkflow(
        router=router,
        registry=registry,
        conversation_manager=conversation_manager,
    )
