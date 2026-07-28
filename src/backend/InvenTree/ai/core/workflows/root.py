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

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any, Protocol

from ai.core.agents.routing import RoutingDecision, UnifiedRouter
from ai.core.config import get_settings
from ai.core.faults import fault_location
from ai.core.memory.conversation import ConversationManager
from ai.core.streaming import EventEmitter, RunContext
from ai.core.workflows.registry import WorkflowRegistry, get_workflow_registry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


class RouterProtocol(Protocol):
    """Protocol for the router component."""

    async def route(
        self, message: str, thread_id: str, context: dict[str, Any] | None = None
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

    async def _maybe_fast_path_answer(self, result: dict[str, Any]) -> str | None:
        """Format a permitted fast-path result into a spoken answer, else None.

        Returns None (falling through to the RBAC-enforced workflow) when there
        is no authenticated principal or the principal lacks the required view
        permission for this result type.
        """
        from ai.core.auth import get_current_principal
        from ai.core.tools.invocation_guard import fresh_permission_profile
        from ai.core.workflows.fast_path import (
            fast_path_permitted,
            format_fast_path_answer,
        )

        result_type = str(result.get("type") or "")
        principal = get_current_principal()
        if principal is None or getattr(principal, "user_pk", None) is None:
            return None
        profile = await fresh_permission_profile(principal.user_pk)
        if not fast_path_permitted(result_type, profile):
            return None
        return format_fast_path_answer(result)

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

        # Which pipeline phase was active when a failure surfaced. Logged on
        # the redacted error path, where it is often the only clue that places
        # an outage without correlating timestamps against the source.
        stage = "start"

        try:
            # Emit run started
            await run_ctx.emit_run_started()

            # Step 1: Gather context
            # We gather context before routing to help the router make better decisions
            stage = "context"
            await run_ctx.emit_thinking("Gathering context...")

            aggregated_context = await self.conversation_manager.gather_context(
                query=message,
                thread_id=thread_id,
                user_id=user_id,
            )

            if context:
                aggregated_context.update(context)

            # Step 2: Route
            stage = "routing"
            await run_ctx.emit_thinking("Routing request...")

            decision = await self.router.route(
                message=message, thread_id=thread_id, context=aggregated_context
            )

            # Deterministic fast-path answer for permitted voice lookups: skip
            # the LLM tool loop entirely. Permission-gated because the fast path
            # bypasses the per-tool RBAC filter (routing executed the reads).
            if (
                decision.use_fast_path
                and decision.fast_path_result
                and aggregated_context.get("modality") == "voice"
                and get_settings().feature_voice_fast_path
            ):
                bypass = await self._maybe_fast_path_answer(decision.fast_path_result)
                if bypass is not None:
                    await run_ctx.emit_workflow_started(
                        workflow_id="wf8-fastpath",
                        workflow_name=decision.workflow_type.name,
                    )
                    await run_ctx.emit_text_start()
                    await run_ctx.emit_text_delta(bypass)
                    await run_ctx.emit_text_end()
                    yield bypass
                    await run_ctx.emit_run_finished()
                    return

            # Step 3: Execute Workflow
            stage = "workflow_execution"
            workflow_id = decision.get_workflow_id()

            # A server-owned pin wins over this router's own choice. Voice turns
            # are classified upstream by the deterministic VoiceComplexityRouter;
            # re-deriving the workflow here let an injection-prefixed imperative
            # land on wf4 procurement (a write tier) even though the voice router
            # had selected wf8. The pin is set only in trusted server context and
            # is never readable from client-supplied fields.
            pinned = aggregated_context.get("pinned_workflow_id")
            if pinned:
                if pinned != workflow_id:
                    logger.info(
                        "Honouring server workflow pin",
                        extra={"pinned": pinned, "router_choice": workflow_id},
                    )
                workflow_id = pinned

            if not workflow_id:
                # Fallback to general conversation if no specific workflow
                workflow_id = "general"  # Or handle gracefully

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
                    message=message, thread_id=thread_id, run_id=run_id, decision=decision
                ):
                    yield chunk
            else:
                # Fallback to sync/async execute
                result = await workflow.execute(
                    query=message,
                    thread_id=thread_id,
                    context=aggregated_context,
                )

                # A workflow that caught its own exception still failed. Reading
                # only formatted_response laundered that into a successful turn,
                # so "Unable to complete lookup." was spoken as if it were an
                # answer and the audited failure phrase was never reached.
                if getattr(result, "success", True) is False:
                    error = getattr(result, "error", None) or "workflow_failed"
                    logger.error(
                        "Workflow reported failure",
                        extra={"workflow_id": workflow_id, "error": str(error)},
                    )
                    raise RuntimeError(str(error))

                response = str(result)
                if hasattr(result, "formatted_response"):
                    response = result.formatted_response

                # Stream the response as a single chunk (or split it)
                await run_ctx.emit_text_start()
                await run_ctx.emit_text_delta(response)
                await run_ctx.emit_text_end()
                yield response

            await run_ctx.emit_run_finished()

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Provider failures may contain credentials or customer content.
            # Let NormalizedTurnService persist the honest failed lifecycle;
            # do not turn the exception into a successful assistant answer.
            #
            # The class name alone once left an outage diagnosable only by its
            # timing signature; the pipeline stage and the code coordinates of
            # the raise are logged too. fault_location() never reads the
            # exception's message or args, so the redaction holds.
            location = fault_location(exc)
            logger.error(
                "Root workflow failed (stage=%s error_type=%s raised_at=%s via=%s)",
                stage,
                location["error_type"],
                location["raised_at"],
                location["via"],
            )
            await run_ctx.emit_error("AI turn failed")
            raise


#: Process-wide instance: the semantic router's embedding index (~70 example
#: embeddings, one batch call) and the conversation context cache are only
#: useful when they survive across turns; per-turn construction re-embedded
#: the whole index on every request (~1s of pure latency).
_root_workflow: RootWorkflow | None = None


def get_root_workflow() -> RootWorkflow:
    """Return the shared RootWorkflow instance."""
    global _root_workflow
    if _root_workflow is None:
        _root_workflow = RootWorkflow(
            router=UnifiedRouter(),
            registry=get_workflow_registry(),
            # ConversationManager handles its own persistence/caching
            conversation_manager=ConversationManager(),
        )
    return _root_workflow
