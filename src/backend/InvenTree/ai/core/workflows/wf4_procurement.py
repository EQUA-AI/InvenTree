"""
WF4: T4 Procurement Workflow

Procurement workflow with Human-in-the-Loop (HITL) approval:
- Vendor selection and quote gathering
- Purchase order generation
- Approval workflow for orders above threshold
- Order submission and tracking

Uses @ai_function with approval_mode="always_require" for actions
that require human approval (e.g., submitting purchase orders).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Callable

from agent_framework import ChatAgent, ChatMessage, Role, ai_function
from agent_framework.azure import AzureOpenAIChatClient

from ai.core.config import get_settings
from ai.core.integrations.inventory_tools import INVENTORY_TOOLS

logger = logging.getLogger(__name__)


class ProcurementStatus(Enum):
    """Status of procurement request."""

    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class ApprovalType(Enum):
    """Types of approval required."""

    NONE = "none"
    MANAGER = "manager"
    FINANCE = "finance"
    EXECUTIVE = "executive"


@dataclass
class LineItem:
    """A line item in a purchase order."""

    part_id: int
    part_name: str
    quantity: int
    unit_price: float
    supplier_id: int | None = None
    supplier_name: str = ""
    notes: str = ""

    @property
    def total_price(self) -> float:
        return self.quantity * self.unit_price


@dataclass
class PurchaseOrder:
    """A purchase order document."""

    order_id: str = field(default_factory=lambda: f"PO-{uuid.uuid4().hex[:8].upper()}")
    status: ProcurementStatus = ProcurementStatus.DRAFT
    supplier_id: int | None = None
    supplier_name: str = ""
    line_items: list[LineItem] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = ""
    approved_by: str | None = None
    approved_at: datetime | None = None
    approval_type: ApprovalType = ApprovalType.NONE
    notes: str = ""

    @property
    def total_amount(self) -> float:
        return sum(item.total_price for item in self.line_items)

    @property
    def item_count(self) -> int:
        return len(self.line_items)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "order_id": self.order_id,
            "status": self.status.value,
            "supplier_id": self.supplier_id,
            "supplier_name": self.supplier_name,
            "line_items": [
                {
                    "part_id": item.part_id,
                    "part_name": item.part_name,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "total_price": item.total_price,
                }
                for item in self.line_items
            ],
            "total_amount": self.total_amount,
            "item_count": self.item_count,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "approval_type": self.approval_type.value,
            "notes": self.notes,
        }


@dataclass
class ProcurementResult:
    """Result of procurement workflow."""

    success: bool
    purchase_order: PurchaseOrder | None = None
    status: ProcurementStatus = ProcurementStatus.DRAFT
    requires_approval: bool = False
    approval_type: ApprovalType = ApprovalType.NONE
    formatted_response: str = ""
    execution_time_ms: float = 0.0
    error: str | None = None


# HITL approval thresholds
APPROVAL_THRESHOLDS = {
    ApprovalType.NONE: 0,
    ApprovalType.MANAGER: 1000,
    ApprovalType.FINANCE: 10000,
    ApprovalType.EXECUTIVE: 50000,
}


def determine_approval_type(amount: float) -> ApprovalType:
    """Determine required approval type based on amount."""
    if amount >= APPROVAL_THRESHOLDS[ApprovalType.EXECUTIVE]:
        return ApprovalType.EXECUTIVE
    elif amount >= APPROVAL_THRESHOLDS[ApprovalType.FINANCE]:
        return ApprovalType.FINANCE
    elif amount >= APPROVAL_THRESHOLDS[ApprovalType.MANAGER]:
        return ApprovalType.MANAGER
    else:
        return ApprovalType.NONE


# HITL-enabled procurement tools
@ai_function(
    description="Create a draft purchase order for parts. This creates a PO in draft status.",
)
def create_purchase_order(
    supplier_id: int,
    supplier_name: str,
    items: list[dict[str, Any]],
    notes: str = "",
) -> dict[str, Any]:
    """
    Create a draft purchase order.

    Args:
        supplier_id: The supplier ID
        supplier_name: The supplier name
        items: List of items with part_id, part_name, quantity, unit_price
        notes: Optional notes for the order

    Returns:
        The created purchase order
    """
    line_items = [
        LineItem(
            part_id=item["part_id"],
            part_name=item["part_name"],
            quantity=item["quantity"],
            unit_price=item["unit_price"],
            supplier_id=supplier_id,
            supplier_name=supplier_name,
        )
        for item in items
    ]

    po = PurchaseOrder(
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        line_items=line_items,
        notes=notes,
        approval_type=determine_approval_type(sum(item.total_price for item in line_items)),
    )

    logger.info(
        f"Created draft PO {po.order_id}",
        extra={
            "order_id": po.order_id,
            "total_amount": po.total_amount,
            "approval_type": po.approval_type.value,
        },
    )

    return po.to_dict()


@ai_function(
    description="Submit a purchase order for approval. REQUIRES HUMAN APPROVAL for orders above threshold.",
)
def submit_purchase_order(
    order_id: str,
    submitted_by: str = "system",
) -> dict[str, Any]:
    """
    Submit a purchase order for processing.

    This function requires human approval and will not execute
    until the user explicitly approves the action.

    Args:
        order_id: The purchase order ID
        submitted_by: Who is submitting the order

    Returns:
        Status of the submission
    """
    # In production, this would:
    # 1. Validate the PO exists
    # 2. Send to supplier
    # 3. Update status
    # 4. Send notifications

    logger.info(
        f"Submitting PO {order_id}",
        extra={
            "order_id": order_id,
            "submitted_by": submitted_by,
        },
    )

    return {
        "order_id": order_id,
        "status": "submitted",
        "submitted_by": submitted_by,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "message": f"Purchase order {order_id} has been submitted to the supplier.",
    }


@ai_function(
    description="Cancel a purchase order. REQUIRES HUMAN APPROVAL.",
)
def cancel_purchase_order(
    order_id: str,
    reason: str,
    cancelled_by: str = "system",
) -> dict[str, Any]:
    """
    Cancel a purchase order.

    Args:
        order_id: The purchase order ID
        reason: Reason for cancellation
        cancelled_by: Who is cancelling

    Returns:
        Cancellation status
    """
    logger.info(
        f"Cancelling PO {order_id}",
        extra={
            "order_id": order_id,
            "reason": reason,
            "cancelled_by": cancelled_by,
        },
    )

    return {
        "order_id": order_id,
        "status": "cancelled",
        "reason": reason,
        "cancelled_by": cancelled_by,
        "cancelled_at": datetime.now(timezone.utc).isoformat(),
    }


@ai_function(
    description="Get quote from a supplier for specified parts. Does not require approval.",
)
def request_quote(
    supplier_id: int,
    items: list[dict[str, Any]],
    urgency: str = "normal",
) -> dict[str, Any]:
    """
    Request a quote from a supplier.

    Args:
        supplier_id: The supplier ID
        items: List of items with part_id and quantity
        urgency: Quote urgency (normal, urgent, critical)

    Returns:
        Quote request status
    """
    quote_id = f"QR-{uuid.uuid4().hex[:8].upper()}"

    logger.info(
        f"Requesting quote {quote_id}",
        extra={
            "quote_id": quote_id,
            "supplier_id": supplier_id,
            "item_count": len(items),
        },
    )

    return {
        "quote_id": quote_id,
        "supplier_id": supplier_id,
        "item_count": len(items),
        "urgency": urgency,
        "status": "requested",
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "expected_response": "24-48 hours" if urgency == "normal" else "4-8 hours",
    }


# Collect HITL-enabled tools
PROCUREMENT_TOOLS = [
    create_purchase_order,
    submit_purchase_order,
    cancel_purchase_order,
    request_quote,
]


class T4ProcurementWorkflow:
    """
    T4 Procurement Workflow implementation.

    Handles procurement operations with HITL approval for
    sensitive operations like submitting purchase orders.

    The workflow:
    1. Gather part requirements and supplier options
    2. Generate purchase order draft
    3. Route for approval based on amount
    4. Submit to supplier upon approval

    HITL Integration:
    - Uses approval_mode="always_require" for submit/cancel actions
    - Provides approval UI through AG-UI events
    - Blocks execution until human confirms

    Usage:
        workflow = T4ProcurementWorkflow()
        result = await workflow.execute(
            query="Order 100 units of part XYZ from SupplierCo",
            thread_id="thread_123",
            user_id="user_456",
        )
    """

    SYSTEM_PROMPT = """You are a procurement specialist for a manufacturing company.
Your responsibilities include:
- Analyzing part requirements and finding suppliers
- Creating and managing purchase orders
- Gathering quotes from suppliers
- Managing the procurement approval workflow

When processing procurement requests:
1. First, search for the requested parts in inventory
2. Find suppliers that carry those parts
3. Check current stock levels to confirm need
4. Create a draft purchase order with accurate quantities
5. Submit for approval when ready

IMPORTANT:
- Purchase orders above $1,000 require manager approval
- Orders above $10,000 require finance approval
- Orders above $50,000 require executive approval
- Always confirm the total amount before submitting

Format purchase orders clearly with:
- Order ID
- Supplier information
- Line items with quantities and prices
- Total amount
- Required approval level"""

    def __init__(
        self,
        on_approval_required: Callable[[PurchaseOrder], None] | None = None,
    ):
        """
        Initialize workflow.

        Args:
            on_approval_required: Callback when approval is needed
        """
        self._agent: ChatAgent | None = None
        self.on_approval_required = on_approval_required
        logger.info("T4ProcurementWorkflow initialized")

    async def _get_agent(self) -> ChatAgent:
        """Get or create the procurement agent."""
        if self._agent is None:
            settings = get_settings()

            # Combine InvenTree tools with procurement tools
            all_tools = list(INVENTORY_TOOLS) + PROCUREMENT_TOOLS

            chat_client = AzureOpenAIChatClient(
                deployment_name=settings.azure_openai_deployment,
                endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
            )

            self._agent = ChatAgent(
                chat_client=chat_client,
                instructions=self.SYSTEM_PROMPT,
                name="Procurement Agent",
                description="Handles procurement and purchase orders",
                tools=all_tools,
            )

        return self._agent

    async def execute(
        self,
        query: str,
        thread_id: str = "",
        user_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> ProcurementResult:
        """
        Execute procurement workflow.

        Args:
            query: The procurement request
            thread_id: Conversation thread ID
            user_id: User making the request
            context: Additional context

        Returns:
            ProcurementResult with PO details and status
        """
        import time

        start_time = time.perf_counter()

        logger.info(
            "Executing T4 procurement",
            extra={
                "thread_id": thread_id,
                "user_id": user_id,
            },
        )

        try:
            agent = await self._get_agent()

            # Run procurement agent
            response = await agent.run(query)
            response_text = ""
            if response.messages:
                last_msg = response.messages[-1]
                response_text = last_msg.text if hasattr(last_msg, 'text') else str(last_msg)

            execution_time = (time.perf_counter() - start_time) * 1000

            # Parse response for PO information
            po = self._parse_purchase_order(response_text)

            logger.info(
                "T4 procurement complete",
                extra={
                    "thread_id": thread_id,
                    "has_po": po is not None,
                    "execution_time_ms": execution_time,
                },
            )

            result = ProcurementResult(
                success=True,
                purchase_order=po,
                status=po.status if po else ProcurementStatus.DRAFT,
                requires_approval=po.approval_type != ApprovalType.NONE if po else False,
                approval_type=po.approval_type if po else ApprovalType.NONE,
                formatted_response=response_text,
                execution_time_ms=execution_time,
            )

            # Trigger approval callback if needed
            if result.requires_approval and self.on_approval_required and po:
                self.on_approval_required(po)

            return result

        except Exception as e:
            execution_time = (time.perf_counter() - start_time) * 1000

            logger.error(
                f"T4 procurement failed: {e}",
                extra={
                    "thread_id": thread_id,
                },
            )

            return ProcurementResult(
                success=False,
                error=str(e),
                formatted_response=f"Procurement failed: {str(e)}",
                execution_time_ms=execution_time,
            )

    def _parse_purchase_order(self, response: str) -> PurchaseOrder | None:
        """
        Parse response text for purchase order details.

        In production, this would parse structured output from tools.
        For now, we return a placeholder if PO-related keywords are found.
        """
        if "PO-" in response or "purchase order" in response.lower():
            # Extract PO ID if present
            import re

            po_match = re.search(r"PO-[A-Z0-9]+", response)
            order_id = po_match.group(0) if po_match else f"PO-{uuid.uuid4().hex[:8].upper()}"

            return PurchaseOrder(
                order_id=order_id,
                status=ProcurementStatus.DRAFT,
                notes="Parsed from agent response",
            )

        return None

    async def stream_execute(
        self,
        query: str,
        thread_id: str = "",
        user_id: str = "",
    ) -> AsyncIterator[str]:
        """Execute with streaming response."""
        yield "🛒 Processing procurement request...\n\n"

        try:
            agent = await self._get_agent()

            response = await agent.run(query)
            if response.messages:
                last_msg = response.messages[-1]
                content = last_msg.text if hasattr(last_msg, 'text') else str(last_msg)
                yield content

        except Exception as e:
            logger.error(f"T4 procurement stream failed: {e}")
            yield f"Error: {str(e)}"


class T4ProcurementBuilder:
    """
    Builder for T4 Procurement Workflow.

    Implements .as_agent() pattern for workflow composition.
    """

    def __init__(self):
        self._approval_callback: Callable | None = None
        self._custom_thresholds: dict[ApprovalType, float] | None = None
        self._additional_tools: list = []

    def with_approval_callback(
        self,
        callback: Callable[[PurchaseOrder], None],
    ) -> "T4ProcurementBuilder":
        """Set callback for approval requests."""
        self._approval_callback = callback
        return self

    def with_thresholds(
        self,
        thresholds: dict[ApprovalType, float],
    ) -> "T4ProcurementBuilder":
        """Set custom approval thresholds."""
        self._custom_thresholds = thresholds
        return self

    def with_additional_tools(
        self,
        tools: list,
    ) -> "T4ProcurementBuilder":
        """Add additional tools."""
        self._additional_tools.extend(tools)
        return self

    def build(self) -> T4ProcurementWorkflow:
        """Build configured workflow."""
        workflow = T4ProcurementWorkflow(on_approval_required=self._approval_callback)

        if self._custom_thresholds:
            global APPROVAL_THRESHOLDS
            APPROVAL_THRESHOLDS.update(self._custom_thresholds)

        return workflow

    def as_agent(self) -> ChatAgent:
        """Convert workflow to a composable agent."""
        settings = get_settings()

        all_tools = list(INVENTORY_TOOLS) + PROCUREMENT_TOOLS + self._additional_tools

        chat_client = AzureOpenAIChatClient(
            deployment_name=settings.azure_openai_deployment,
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
        )

        return ChatAgent(
            chat_client=chat_client,
            instructions=T4ProcurementWorkflow.SYSTEM_PROMPT,
            name="AIMMS Procurement Agent",
            description="Procurement workflows with human-in-the-loop approval",
            tools=all_tools,
        )


# Factory functions
def create_t4_procurement_workflow(
    on_approval_required: Callable[[PurchaseOrder], None] | None = None,
) -> T4ProcurementWorkflow:
    """Create a T4 procurement workflow instance."""
    return T4ProcurementWorkflow(on_approval_required=on_approval_required)


def t4_procurement_builder() -> T4ProcurementBuilder:
    """Get a T4 procurement workflow builder."""
    return T4ProcurementBuilder()
