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
from typing import Any, AsyncIterator

from agent_framework import ChatAgent, ChatMessage, Role
from agent_framework.azure import AzureOpenAIChatClient

from ai.core.config import get_settings
from ai.core.integrations.inventory_tools import INVENTORY_TOOLS
from ai.core.integrations.email.tools import EMAIL_TOOLS
from ai.core.integrations.kanban_tools import KANBAN_TOOLS
from ai.core.integrations.document_search import DOCUMENT_SEARCH_TOOLS

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

    def __init__(self):
        """Initialize the T1 lookup workflow."""
        self._agent: ChatAgent | None = None
        self._inventree_client = None
        logger.info("T1LookupWorkflow initialized")

    async def _get_agent(self) -> ChatAgent:
        """Get or create the lookup agent."""
        if self._agent is None:
            settings = get_settings()

            # Create Azure OpenAI chat client
            chat_client = AzureOpenAIChatClient(
                deployment_name=settings.azure_openai_deployment,
                endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
            )

            # Build agent with InvenTree + email/PDF + kanban tools
            self._agent = ChatAgent(
                chat_client=chat_client,
                instructions=self.SYSTEM_PROMPT,
                name="T1 Lookup Agent",
                description="Fast inventory lookups, email, PDF generation, and kanban management",
                tools=INVENTORY_TOOLS + EMAIL_TOOLS + KANBAN_TOOLS + DOCUMENT_SEARCH_TOOLS,
            )

        return self._agent

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
            agent = await self._get_agent()

            # Run agent directly with the query (correct MAF API)
            response = await agent.run(query)
            
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

            logger.info(
                "T1 lookup complete",
                extra={
                    "thread_id": thread_id,
                    "execution_time_ms": execution_time,
                },
            )

            return LookupResult(
                lookup_type=lookup_type,
                success=True,
                data={"raw_response": response_text},
                formatted_response=response_text,
                execution_time_ms=execution_time,
            )

        except Exception as e:
            execution_time = (time.perf_counter() - start_time) * 1000

            logger.error(
                f"T1 lookup failed: {e}",
                extra={
                    "thread_id": thread_id,
                    "lookup_type": lookup_type.value,
                },
            )

            return LookupResult(
                lookup_type=lookup_type,
                success=False,
                data={},
                formatted_response=f"Unable to complete lookup: {str(e)}",
                execution_time_ms=execution_time,
                error=str(e),
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

            response = await agent.run(query)
            if response.messages:
                last_msg = response.messages[-1]
                content = last_msg.text if hasattr(last_msg, 'text') else str(last_msg)
                yield content

        except Exception as e:
            logger.error(f"T1 lookup stream failed: {e}")
            yield f"Error: {str(e)}"


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

    def with_system_prompt(self, prompt: str) -> "T1LookupWorkflowBuilder":
        """Set custom system prompt."""
        self._system_prompt = prompt
        return self

    def with_additional_tools(self, tools: list) -> "T1LookupWorkflowBuilder":
        """Add additional tools beyond InvenTree."""
        self._tools.extend(tools)
        return self

    def with_timeout(self, timeout_ms: int) -> "T1LookupWorkflowBuilder":
        """Set execution timeout."""
        self._timeout_ms = timeout_ms
        return self

    def with_model(self, deployment: str) -> "T1LookupWorkflowBuilder":
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
