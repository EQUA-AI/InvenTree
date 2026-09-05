"""
WF4: T4 Procurement Workflow

Procurement workflow with Human-in-the-Loop (HITL) approval:
- Vendor selection and quote gathering
- Purchase order generation
- Approval workflow for orders above threshold
- Order submission and tracking

Purchase-order actions use the centralized write tools in
ai.core.tools.inventree.write.purchase_orders (no workflow-local tool copies);
human-in-the-loop confirmation for writes is handled by the Tier-3 flow (Phase 4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from ai.core.agents.factory import AgentSpec, build_agent
from ai.core.config import get_settings
from ai.core.integrations.inventory_tools import INVENTORY_TOOLS
from ai.core.tools.inventree.write.purchase_orders import PURCHASE_ORDER_WRITE_TOOLS
from ai.core.tools.invocation_guard import CapabilityInvocationMiddleware
from ai.core.workflows.rbac_run import run_with_rbac

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from agent_framework import ChatAgent

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

    # No default (S13): a purchase order without a server-assigned reference is
    # a fabricated identifier. The old default_factory minted PO-{uuid4} for
    # any construction, so an invented reference could reach an approval
    # callback and be read as a real record.
    order_id: str
    status: ProcurementStatus = ProcurementStatus.DRAFT
    supplier_id: int | None = None
    supplier_name: str = ""
    line_items: list[LineItem] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
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


# Procurement uses the centralized PurchaseOrder write tools
# (ai.core.tools.inventree.write.purchase_orders) -- no workflow-local tool copies.


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

            # Tools-less: run_with_rbac supplies the per-user-filtered toolset.
            self._agent = build_agent(
                AgentSpec(
                    deployment=settings.azure_openai_deployment,
                    instructions=self.SYSTEM_PROMPT,
                    name="Procurement Agent",
                    description="Handles procurement and purchase orders",
                    middleware=CapabilityInvocationMiddleware(),
                    workflow="wf4",
                )
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

            # Run procurement agent (per-user RBAC-filtered tools; voice read-only)
            response = await run_with_rbac(
                agent,
                query,
                workflow="wf4",
                full_tools=[*INVENTORY_TOOLS, *PURCHASE_ORDER_WRITE_TOOLS],
                context=context,
                replay_history=True,  # M1 PR E: the first user-facing step
            )
            response_text = ""
            if response.messages:
                last_msg = response.messages[-1]
                response_text = last_msg.text if hasattr(last_msg, "text") else str(last_msg)

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
                formatted_response=f"Procurement failed: {e!s}",
                execution_time_ms=execution_time,
            )

    def _parse_purchase_order(self, response: str) -> PurchaseOrder | None:
        """Deleted (execution-plan S13): identifiers are never invented.

        This used to mint ``PO-{uuid4}`` whenever the words "purchase order"
        appeared in a model reply, then hand that fabricated identifier to the
        approval callback — an invented identifier presented as a real record,
        which is exactly the hazard the diagnosis rail exists to prevent. A
        real purchase order comes back from ``create_purchase_order`` with a
        server-assigned reference; until this rail reads that tool result, it
        reports no order at all.
        """
        del response
        return None

    async def stream_execute(
        self,
        query: str,
        thread_id: str = "",
        user_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Execute with streaming response."""
        yield "🛒 Processing procurement request...\n\n"

        try:
            agent = await self._get_agent()

            response = await run_with_rbac(
                agent,
                query,
                workflow="wf4",
                full_tools=[*INVENTORY_TOOLS, *PURCHASE_ORDER_WRITE_TOOLS],
                context=context,
                replay_history=True,  # M1 PR E: the first user-facing step
            )
            if response.messages:
                last_msg = response.messages[-1]
                yield last_msg.text if hasattr(last_msg, "text") else str(last_msg)

        except Exception as e:
            logger.error(f"T4 procurement stream failed: {e}")
            yield f"Error: {e!s}"


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
    ) -> T4ProcurementBuilder:
        """Set callback for approval requests."""
        self._approval_callback = callback
        return self

    def with_thresholds(
        self,
        thresholds: dict[ApprovalType, float],
    ) -> T4ProcurementBuilder:
        """Set custom approval thresholds."""
        self._custom_thresholds = thresholds
        return self

    def with_additional_tools(
        self,
        tools: list,
    ) -> T4ProcurementBuilder:
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

        return build_agent(
            AgentSpec(
                deployment=settings.azure_openai_deployment,
                instructions=T4ProcurementWorkflow.SYSTEM_PROMPT,
                name="AIMMS Procurement Agent",
                description="Procurement workflows with human-in-the-loop approval",
                # Tools-less by construction (S11): a constructor toolset is
                # unioned into every run, so the per-user RBAC filter in
                # run_with_rbac would never see it. Composed callers dispatch
                # through run_with_rbac like every other rail.
                middleware=CapabilityInvocationMiddleware(),
                workflow="wf4",
            )
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
