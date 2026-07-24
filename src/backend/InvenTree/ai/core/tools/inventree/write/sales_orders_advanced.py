"""
Sales Order Advanced Write Tools

Additional sales order operations including shipment completion,
cancellation, and hold management.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function
from ai.core.tools.inventree.base import require_hitl

logger = logging.getLogger(__name__)


@ai_function
@require_hitl(reason="Shipping sales order items")
async def ship_so_shipment(
    shipment_id: int,
    tracking_number: str | None = None,
    invoice_number: str | None = None,
    link: str | None = None,
) -> dict[str, Any]:
    """
    Complete and ship a sales order shipment.

    Marks a shipment as shipped, consuming the allocated stock
    and recording the shipment date.

    Args:
        shipment_id: The shipment ID to ship (required)
        tracking_number: Carrier tracking number (updates if provided)
        invoice_number: Invoice number (updates if provided)
        link: External link (tracking URL, etc.)

    Returns:
        Updated shipment data

    Example:
        # Ship with tracking number
        result = await ship_so_shipment(
            shipment_id=5,
            tracking_number="1Z999AA10123456784",
            link="https://track.carrier.com/1Z999AA10123456784"
        )
    """
    logger.info(f"Shipping shipment {shipment_id}")

    data: dict[str, Any] = {}

    if tracking_number:
        data["tracking_number"] = tracking_number
    if invoice_number:
        data["invoice_number"] = invoice_number
    if link:
        data["link"] = link

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request(
            "POST", f"/order/so/shipment/{shipment_id}/ship/", json_data=data
        )

        if isinstance(result, dict):
            logger.info(f"Shipped shipment {shipment_id}")
            return result

        return {"success": True, "shipment_id": shipment_id, "status": "Shipped"}

    except Exception as e:
        logger.error(f"Failed to ship shipment: {e}")
        raise


@ai_function
@require_hitl(reason="Cancelling sales order")
async def cancel_sales_order(
    order_id: int,
) -> dict[str, Any]:
    """
    Cancel a sales order.

    Cancels an open sales order. Any allocated stock will be
    released. Cannot cancel orders with completed shipments.

    Args:
        order_id: The sales order ID to cancel (required)

    Returns:
        Cancellation confirmation

    Example:
        # Cancel an unwanted SO
        result = await cancel_sales_order(order_id=15)
    """
    logger.info(f"Cancelling sales order {order_id}")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", f"/order/so/{order_id}/cancel/", json_data={})

        if isinstance(result, dict):
            logger.info(f"Cancelled sales order {order_id}")
            return result

        return {"success": True, "order_id": order_id, "status": "Cancelled"}

    except Exception as e:
        logger.error(f"Failed to cancel sales order: {e}")
        raise


@ai_function
@require_hitl(reason="Placing sales order on hold")
async def hold_sales_order(
    order_id: int,
) -> dict[str, Any]:
    """
    Place a sales order on hold.

    Temporarily suspends processing of a sales order.
    Allocated stock remains reserved.

    Args:
        order_id: The sales order ID to hold (required)

    Returns:
        Updated order data

    Example:
        # Put order on hold pending customer confirmation
        result = await hold_sales_order(order_id=15)
    """
    logger.info(f"Placing sales order {order_id} on hold")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", f"/order/so/{order_id}/hold/", json_data={})

        if isinstance(result, dict):
            logger.info(f"Placed sales order {order_id} on hold")
            return result

        return {"success": True, "order_id": order_id, "status": "On Hold"}

    except Exception as e:
        logger.error(f"Failed to hold sales order: {e}")
        raise


@ai_function
@require_hitl(reason="Completing sales order")
async def complete_sales_order(
    order_id: int,
    accept_incomplete: bool = False,
) -> dict[str, Any]:
    """
    Complete a sales order.

    Marks the sales order as complete. All shipments should be
    shipped before completing.

    Args:
        order_id: The sales order ID to complete (required)
        accept_incomplete: If True, complete even with unshipped items

    Returns:
        Updated order data

    Example:
        # Complete a fully shipped order
        result = await complete_sales_order(order_id=15)

        # Complete with partial shipment
        result = await complete_sales_order(
            order_id=15,
            accept_incomplete=True
        )
    """
    logger.info(f"Completing sales order {order_id}")

    data: dict[str, Any] = {}
    if accept_incomplete:
        data["accept_incomplete"] = True

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", f"/order/so/{order_id}/complete/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Completed sales order {order_id}")
            return result

        return {"success": True, "order_id": order_id, "status": "Complete"}

    except Exception as e:
        logger.error(f"Failed to complete sales order: {e}")
        raise


@ai_function
@require_hitl(reason="Updating sales order")
async def update_sales_order(
    order_id: int,
    reference: str | None = None,
    description: str | None = None,
    target_date: str | None = None,
    link: str | None = None,
    contact_id: int | None = None,
    project_code: str | None = None,
) -> dict[str, Any]:
    """
    Update a sales order's properties.

    Modifies metadata of an existing sales order.

    Args:
        order_id: The sales order ID to update (required)
        reference: New reference number
        description: New description
        target_date: New target date (ISO format)
        link: New external link
        contact_id: New contact person ID
        project_code: New project code

    Returns:
        Updated sales order data

    Example:
        # Update delivery date and description
        result = await update_sales_order(
            order_id=15,
            target_date="2024-03-15",
            description="Updated Q1 order - priority shipping"
        )
    """
    logger.info(f"Updating sales order {order_id}")

    data: dict[str, Any] = {}

    if reference is not None:
        data["reference"] = reference
    if description is not None:
        data["description"] = description
    if target_date is not None:
        data["target_date"] = target_date
    if link is not None:
        data["link"] = link
    if contact_id is not None:
        data["contact"] = contact_id
    if project_code is not None:
        data["project_code"] = project_code

    if not data:
        raise ValueError("At least one field must be provided for update")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("PATCH", f"/order/so/{order_id}/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Updated sales order {order_id}")
            return result

        return {"success": True, "order_id": order_id}

    except Exception as e:
        logger.error(f"Failed to update sales order: {e}")
        raise


# Export all sales order advanced write tools
SALES_ORDER_ADVANCED_WRITE_TOOLS = [
    ship_so_shipment,
    cancel_sales_order,
    hold_sales_order,
    complete_sales_order,
    update_sales_order,
]

__all__ = [
    "SALES_ORDER_ADVANCED_WRITE_TOOLS",
    "cancel_sales_order",
    "complete_sales_order",
    "hold_sales_order",
    "ship_so_shipment",
    "update_sales_order",
]
