"""
WF8: T1 Lookup Workflow

Simple sequential workflow for fast database lookups:
- Stock availability checks
- Part details retrieval
- Location information
- Basic BOM queries

This is the lightest-weight workflow, optimized for < 500ms response time.
Uses ChatAgent with AzureOpenAIChatClient for minimal agent involvement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from agent_framework import ChatAgent, Role
from agent_framework.azure import AzureOpenAIChatClient
from ai.core.config import get_settings
from ai.core.integrations.document_search import DOCUMENT_SEARCH_TOOLS
from ai.core.integrations.email.tools import EMAIL_TOOLS
from ai.core.integrations.inventory_tools import INVENTORY_READ_TOOLS, INVENTORY_TOOLS
from ai.core.integrations.kanban_tools import KANBAN_READ_TOOLS, KANBAN_TOOLS
from ai.core.tools.invocation_guard import (
    CapabilityInvocationMiddleware,
    bind_capability_run,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


class LookupType(Enum):
    """Types of T1 lookup queries."""

    STOCK_CHECK = "stock_check"
    PART_DETAILS = "part_details"
    PART_LOCATION = "part_location"
    BOM_QUERY = "bom_query"
    CATEGORY_LIST = "category_list"
    SUPPLIER_LIST = "supplier_list"
    LOW_STOCK_ALERT = "low_stock_alert"
    GENERAL_LOOKUP = "general_lookup"


@dataclass
class LookupResult:
    """Result of a T1 lookup operation."""

    lookup_type: LookupType
    success: bool
    data: dict[str, Any]
    formatted_response: str
    execution_time_ms: float
    error: str | None = None


def _response_usage_metrics(response: Any) -> dict[str, int]:
    """Normalize provider usage without inferring unavailable token counts."""
    usage = getattr(response, "usage_details", None)
    if usage is None:
        return {}
    if isinstance(usage, dict):
        counts = dict(usage)
    elif hasattr(usage, "to_dict"):
        counts = usage.to_dict(exclude_none=True)
    else:
        counts = {
            key: getattr(usage, key, None)
            for key in (
                "input_token_count",
                "output_token_count",
                "total_token_count",
            )
        }

    metrics = {
        key: value
        for key, value in counts.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    cached = next(
        (
            metrics[key]
            for key in (
                "cache_read_input_token_count",
                "cached_input_token_count",
                "cached_tokens",
            )
            if key in metrics
        ),
        None,
    )
    input_tokens = metrics.get("input_token_count")
    if cached is not None:
        metrics["cached_input_token_count"] = cached
    if input_tokens is not None and cached is not None:
        metrics["uncached_input_token_count"] = max(input_tokens - cached, 0)
    return metrics


class T1LookupWorkflow:
    """
    T1 Lookup Workflow implementation.

    Uses a lightweight agent with InvenTree tools to answer
    simple database queries. Optimized for fast response times.

    The workflow:
    1. Receives classified lookup query from router
    2. Invokes single agent with InvenTree tools
    3. Returns structured result

    Usage:
        workflow = T1LookupWorkflow()
        result = await workflow.execute(
            query="What's the stock level for part ABC-123?",
            lookup_type=LookupType.STOCK_CHECK,
            thread_id="thread_123",
        )
    """

    SYSTEM_PROMPT = """You are an inventory lookup specialist for a manufacturing company.
Your job is to quickly answer questions about:
- Stock levels and availability
- Part details and specifications
- Part locations in the warehouse
- Bill of Materials (BOM) information
- Supplier information

Use the provided tools to query the InvenTree inventory system.
Be concise and factual in your responses.
Format numbers clearly and include units where applicable.

When reporting stock levels:
- Include the total quantity available
- Mention if stock is low (below 10 units)
- Note the location if available

When reporting part details:
- Include the part name and description
- Mention the category
- Note if it's an assembly (has BOM) or a component

You have FULL READ AND WRITE access to the inventory system:
- You CAN create new parts, categories, stock locations, companies, supplier parts, manufacturer parts
- You CAN create purchase orders, sales orders, BOM items, and stock adjustments
- You CAN update existing parts and set part parameters
- When a user asks you to add/create/put something into the database, USE the appropriate create_ tool
- When processing uploaded files (PDFs, spreadsheets), extract data and use write tools to create records
- NEVER say you cannot create or modify records — you have full write access

You also have email and PDF tools available:
- You can generate PDFs for the following document types:
  * sales_order  — "create a sales order", "generate SO PDF", "send sales order to customer"
  * purchase_order — "create a purchase order", "generate PO", "send PO to supplier"
  * bom — "generate a bill of materials", "create BOM PDF", "send BOM"
  * quote — "create a quote", "generate quotation", "send quote to customer"
  * rfq — "create an RFQ", "request for quote", "send RFQ to supplier", "ask for pricing"
  * work_order — "create a work order", "generate work order PDF", "send work order"
- You can send emails with PDF attachments
- When asked to email a document, use the generate_and_send_document tool with the matching document_type
- When asked to send a plain email, use the send_email tool

You also have Kanban board tools:
- list_kanban_cards: List/search/filter cards on the board
- get_kanban_card: Get full details of a specific card by ID
- create_kanban_card: Create a new card (title required, status defaults to 'backlog')
- update_kanban_card: Update any fields on an existing card
- move_kanban_card: Move a card to a different status column
- archive_kanban_card: Soft-delete (archive) a card
- restore_kanban_card: Restore an archived card
- delete_kanban_card: Permanently delete a card (prefer archive)
- get_kanban_summary: Get board overview with counts and overdue cards

Kanban statuses are: backlog, in-progress, review, done
Kanban priorities are: low, medium, high

You have access to indexed technical documentation (equipment manuals, datasheets, specs):
- Use search_part_documents to find relevant manual sections for diagnosis
- ALWAYS search documentation FIRST when asked about troubleshooting, error codes, maintenance, or operating procedures
- ALWAYS cite the source: include the document title and any page/section references from the results
- When diagnosing faults, search for the error code AND the symptom description

Always verify data from the tools before responding."""

    #: Voice-modality Tier-1 prompt: read-only, spoken, concise. Voice turns run
    #: under the read-only fence and are restricted to read tools, so this prompt
    #: must never imply the assistant can change anything.
    VOICE_SYSTEM_PROMPT = """You are the AIMMS voice assistant, answering inventory and
manufacturing questions out loud for a hands-free technician who often cannot look at a screen.

You can look up (read only): part details and specs, stock levels and availability, warehouse
locations, bills of materials (BOM), suppliers, and purchase/sales orders. Use the provided
read-only tools and answer only from what they return. Never invent part numbers, quantities,
locations, prices, or statuses; if a tool returns nothing or you are unsure, say so plainly.

This is a read-only conversation. You cannot create, update, delete, order, email, or change
anything by voice, and you must never say or imply that you did. If asked to make a change, say
it must be done on the normal authenticated screen, and offer to look up what they need.

Keep spoken answers to one or two short sentences: lead with the answer, then the key detail
(for example, "There are 42 in stock, in bin A-3 — below the reorder point."). Speak numbers and
units clearly; do not read out IDs, URLs, or long lists. If the answer is long or list-like, give
the headline and say the rest is in the chat. Preserve any uncertainty ("about", "as of the last
sync") rather than rounding it away."""

    #: Base toolset offered to lookups. Read-only inventory tools by design:
    #: a lookup agent never mutates, and the smaller schema cuts prompt size
    #: and time-to-first-token. The per-user RBAC filter subsets this list.
    BASE_TOOLS: tuple = ()

    #: Read-only subset offered to voice turns (Tier-1). Excludes EMAIL_TOOLS and
    #: KANBAN write tools, which mutate and do not route through the InvenTree
    #: read-only fence. Gated by feature_voice_readonly_tools.
    VOICE_BASE_TOOLS: tuple = ()

    #: A lookup needs at most a couple of tool rounds plus the answer; the
    #: framework default of 40 iterations only makes failures slow.
    MAX_TOOL_ITERATIONS = 6

    def __init__(self):
        """Initialize the T1 lookup workflow."""
        self._agent: ChatAgent | None = None
        self._voice_agent: ChatAgent | None = None
        self._inventree_client = None
        if not T1LookupWorkflow.BASE_TOOLS:
            T1LookupWorkflow.BASE_TOOLS = tuple(
                INVENTORY_READ_TOOLS + EMAIL_TOOLS + KANBAN_TOOLS + DOCUMENT_SEARCH_TOOLS
            )
        if not T1LookupWorkflow.VOICE_BASE_TOOLS:
            # Read-only surface for hands-free voice: inventory + build/work-order
            # reads (already in INVENTORY_READ_TOOLS), document search, and kanban
            # reads. Mutations (email, kanban/inventory writes) are excluded until
            # the Tier-3 confirmed-write flow (Phase 4).
            T1LookupWorkflow.VOICE_BASE_TOOLS = tuple(
                INVENTORY_READ_TOOLS + DOCUMENT_SEARCH_TOOLS + KANBAN_READ_TOOLS
            )
        logger.info("T1LookupWorkflow initialized")

    def _base_tools_for(self, *, is_voice: bool) -> tuple:
        """Tool set for this turn.

        Voice turns get the read-only subset when the safety flag is on; every
        other turn keeps the full text toolset. The per-user RBAC filter still
        subsets whichever list is returned here.
        """
        if is_voice and get_settings().feature_voice_readonly_tools:
            return self.VOICE_BASE_TOOLS
        return self.BASE_TOOLS

    async def _get_agent(self, *, voice: bool = False) -> ChatAgent:
        """Get or create the lookup agent.

        The agent is built WITHOUT tools: MAF unions constructor tools with
        run-time tools, so per-user RBAC filtering only works when the
        complete (filtered) list is supplied on each run. Voice turns use a
        separate cached agent carrying the read-only spoken prompt.
        """
        cached = self._voice_agent if voice else self._agent
        if cached is not None:
            return cached

        settings = get_settings()

        # Voice Tier-1 lookups use the fast deployment for lower latency; text
        # keeps the standard deployment.
        deployment = (
            settings.azure_openai_fast_deployment if voice else settings.azure_openai_deployment
        )

        # Create Azure OpenAI chat client
        chat_client = AzureOpenAIChatClient(
            deployment_name=deployment,
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
        )
        invocation_config = getattr(chat_client, "function_invocation_config", None)
        if invocation_config is not None:
            invocation_config.max_iterations = self.MAX_TOOL_ITERATIONS
            invocation_config.include_detailed_errors = False

        agent = ChatAgent(
            chat_client=chat_client,
            instructions=self.VOICE_SYSTEM_PROMPT if voice else self.SYSTEM_PROMPT,
            name="T1 Lookup Agent (Voice)" if voice else "T1 Lookup Agent",
            description=(
                "Fast read-only inventory lookups, spoken"
                if voice
                else "Fast inventory lookups, email, PDF generation, and kanban management"
            ),
            middleware=CapabilityInvocationMiddleware(),
        )

        if voice:
            self._voice_agent = agent
        else:
            self._agent = agent

        return agent

    def _capability_selection(
        self,
        *,
        query: str,
        lookup_type: LookupType,
        context: dict[str, Any] | None,
        current_tools: list[Any],
    ) -> Any | None:
        """Compute and observe a capability selection for this run."""
        settings = get_settings()
        enforce = settings.feature_capability_broker_enforce
        shadow = settings.feature_capability_broker_shadow
        if not shadow and not enforce:
            return

        import time

        from ai.core.auth import get_current_principal
        from ai.core.tools.capabilities import (
            CATALOG_VERSION,
            select_capabilities,
            serialized_contract_bytes,
        )
        from ai.core.tools.rbac import _permission_map_cached

        started = time.perf_counter()
        try:
            permission_map = _permission_map_cached()
            profile = frozenset(
                requirement
                for tool in current_tools
                if (requirement := permission_map.get(tool)) is not None
            )
            selection = select_capabilities(
                query,
                lookup_type=lookup_type.value,
                context=context,
                profile=profile,
                authenticated=get_current_principal() is not None,
            )
            current_bytes = serialized_contract_bytes(current_tools)
            selected_bytes = serialized_contract_bytes(selection.tools)
            reduction_pct = (
                round((1 - selected_bytes / current_bytes) * 100, 2) if current_bytes else 0.0
            )
            logger.info(
                "Capability broker selection",
                extra={
                    "workflow": "wf8",
                    "selection_mode": "enforce" if enforce else "shadow",
                    "catalog_version": CATALOG_VERSION,
                    "pack_ids": selection.pack_ids,
                    "selected_tool_ids": selection.tool_ids,
                    "selected_tool_count": len(selection.tools),
                    "current_tool_count": len(current_tools),
                    "selected_contract_bytes": selected_bytes,
                    "current_contract_bytes": current_bytes,
                    "contract_reduction_pct": reduction_pct,
                    "selection_latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "selection_reason": selection.reason,
                    "clarification_required": selection.clarification_required,
                    "requires_specialist": selection.requires_specialist,
                },
            )
            return selection
        except Exception as exc:
            logger.error(
                "Capability broker selection failed",
                extra={
                    "workflow": "wf8",
                    "lookup_type": lookup_type.value,
                    "error_type": type(exc).__name__,
                },
            )
            if enforce:
                raise
            return None

    async def execute(
        self,
        query: str,
        lookup_type: LookupType = LookupType.GENERAL_LOOKUP,
        thread_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> LookupResult:
        """
        Execute a T1 lookup query.

        Args:
            query: The user's lookup query
            lookup_type: The classified type of lookup
            thread_id: Conversation thread ID
            context: Additional context (e.g., extracted entities)

        Returns:
            LookupResult with query results
        """
        import time

        start_time = time.perf_counter()

        logger.info(
            "Executing T1 lookup",
            extra={
                "thread_id": thread_id,
                "lookup_type": lookup_type.value,
            },
        )

        try:
            is_voice = context is not None and context.get("modality") == "voice"
            voice_read_only = is_voice and get_settings().feature_voice_readonly_tools

            agent = await self._get_agent(voice=voice_read_only)

            # Offer only the tools this user's InvenTree roles permit; the
            # filtered list is memoized per permission profile and keeps a
            # stable order so provider prompt caching stays effective. Voice
            # turns start from the read-only subset (Tier-1 safety).
            from ai.core.tools.rbac import tools_for_current_user

            tools = await tools_for_current_user(self._base_tools_for(is_voice=is_voice))

            selection = self._capability_selection(
                query=query,
                lookup_type=lookup_type,
                context=context,
                current_tools=tools,
            )
            enforce = get_settings().feature_capability_broker_enforce
            runtime_tools = list(selection.tools) if enforce and selection is not None else tools

            # NOTE: no max_tokens here — reasoning deployments (gpt-5.6-luna)
            # reject it, demanding max_completion_tokens instead.
            modality = "voice" if is_voice else "text"
            with bind_capability_run(
                workflow="wf8", modality=modality, selected_tools=runtime_tools
            ):
                response = await agent.run(query, tools=runtime_tools)

            # Extract response text
            response_text = ""
            if hasattr(response, "text"):
                response_text = response.text
            elif hasattr(response, "content"):
                response_text = response.content
            elif hasattr(response, "messages") and response.messages:
                # Get last assistant message
                for msg in reversed(response.messages):
                    if hasattr(msg, "role") and msg.role == Role.ASSISTANT:
                        response_text = msg.text or ""
                        break

            execution_time = (time.perf_counter() - start_time) * 1000
            usage_metrics = _response_usage_metrics(response)

            logger.info(
                "T1 lookup complete",
                extra={
                    "thread_id": thread_id,
                    "execution_time_ms": execution_time,
                    "tool_count": len(runtime_tools),
                    "capability_broker_enforced": enforce,
                    **usage_metrics,
                },
            )

            return LookupResult(
                lookup_type=lookup_type,
                success=True,
                data={"raw_response": response_text, "usage": usage_metrics},
                formatted_response=response_text,
                execution_time_ms=execution_time,
            )

        except Exception as e:
            execution_time = (time.perf_counter() - start_time) * 1000

            logger.error(
                "T1 lookup failed",
                extra={
                    "thread_id": thread_id,
                    "lookup_type": lookup_type.value,
                    "error_type": type(e).__name__,
                },
            )

            return LookupResult(
                lookup_type=lookup_type,
                success=False,
                data={},
                formatted_response="Unable to complete lookup.",
                execution_time_ms=execution_time,
                error="lookup_failed",
            )

    async def stream_execute(
        self,
        query: str,
        lookup_type: LookupType = LookupType.GENERAL_LOOKUP,
        thread_id: str = "",
    ) -> AsyncIterator[str]:
        """
        Execute lookup with streaming response.

        Yields response chunks as they're generated.
        """
        try:
            agent = await self._get_agent()

            from ai.core.tools.rbac import tools_for_current_user

            tools = await tools_for_current_user(self.BASE_TOOLS)
            selection = self._capability_selection(
                query=query,
                lookup_type=lookup_type,
                context=None,
                current_tools=tools,
            )
            enforce = get_settings().feature_capability_broker_enforce
            runtime_tools = list(selection.tools) if enforce and selection is not None else tools
            with bind_capability_run(workflow="wf8", modality="text", selected_tools=runtime_tools):
                response = await agent.run(query, tools=runtime_tools)
            if response.messages:
                last_msg = response.messages[-1]
                yield last_msg.text if hasattr(last_msg, "text") else f"{last_msg!s}"

        except Exception as e:
            logger.error(
                "T1 lookup stream failed",
                extra={
                    "thread_id": thread_id,
                    "lookup_type": lookup_type.value,
                    "error_type": type(e).__name__,
                },
            )
            yield "Unable to complete lookup."


class T1LookupWorkflowBuilder:
    """
    Builder for T1 Lookup Workflow.

    Provides fluent API for workflow configuration.
    Implements .as_agent() pattern for workflow composition.
    """

    def __init__(self):
        """Initialize builder with defaults."""
        self._system_prompt: str | None = None
        self._tools: list = []
        self._timeout_ms: int = 5000
        self._model_deployment: str | None = None

    def with_system_prompt(self, prompt: str) -> T1LookupWorkflowBuilder:
        """Set custom system prompt."""
        self._system_prompt = prompt
        return self

    def with_additional_tools(self, tools: list) -> T1LookupWorkflowBuilder:
        """Add additional tools beyond InvenTree."""
        self._tools.extend(tools)
        return self

    def with_timeout(self, timeout_ms: int) -> T1LookupWorkflowBuilder:
        """Set execution timeout."""
        self._timeout_ms = timeout_ms
        return self

    def with_model(self, deployment: str) -> T1LookupWorkflowBuilder:
        """Set specific model deployment."""
        self._model_deployment = deployment
        return self

    def build(self) -> T1LookupWorkflow:
        """Build configured workflow."""
        workflow = T1LookupWorkflow()

        # Apply custom configurations
        if self._system_prompt:
            workflow.SYSTEM_PROMPT = self._system_prompt

        return workflow

    def as_agent(self) -> ChatAgent:
        """
        Convert workflow to a standalone agent.

        This allows the workflow to be composed into larger workflows
        using the MAF agent composition patterns.
        """
        settings = get_settings()

        all_tools = list(INVENTORY_TOOLS) + self._tools

        # Create Azure OpenAI chat client
        chat_client = AzureOpenAIChatClient(
            deployment_name=self._model_deployment or settings.azure_openai_deployment,
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
        )

        agent = ChatAgent(
            chat_client=chat_client,
            instructions=self._system_prompt or T1LookupWorkflow.SYSTEM_PROMPT,
            name="AIMMS Lookup Agent",
            description="Fast inventory lookups: stock levels, part details, BOM queries",
            tools=all_tools,
        )

        return agent


# Factory function
def create_t1_lookup_workflow() -> T1LookupWorkflow:
    """Create a T1 lookup workflow instance."""
    return T1LookupWorkflow()


# Workflow builder factory
def t1_lookup_builder() -> T1LookupWorkflowBuilder:
    """Get a T1 lookup workflow builder."""
    return T1LookupWorkflowBuilder()
