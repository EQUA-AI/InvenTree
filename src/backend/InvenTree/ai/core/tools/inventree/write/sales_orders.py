"""
Sales Order Write Tools

Write tools for managing sales orders in InvenTree.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function
from ai.core.tools.inventree.base import require_hitl

logger = logging.getLogger(__name__)


@ai_function
@require_hitl(reason="Creating a sales order")
async def create_sales_order(
    customer_id: int,
    reference: str | None = None,
    description: str | None = None,
    target_date: str | None = None,
    link: str | None = None,
    contact_id: int | None = None,
    project_code: str | None = None,
) -> dict[str, Any]:
    """
    Create a new sales order.

    Creates a sales order for a customer. Line items can be
    added after creation using add_so_line_item.

    Args:
        customer_id: The customer company ID (required)
        reference: SO reference number (auto-generated if not provided)
        description: Description of the order
        target_date: Requested delivery date (ISO format: YYYY-MM-DD)
        link: External link (e.g., customer PO reference)
        contact_id: Customer contact person ID
        project_code: Project code for tracking

    Returns:
        Created sales order data

    Example:
        # Create a SO for a customer
        so = await create_sales_order(
            customer_id=10,
            description="Q1 widget order",
            target_date="2024-03-01"
        )
    """
    logger.info(f"Creating sales order for customer {customer_id}")

    data: dict[str, Any] = {
        "customer": customer_id,
    }

    if reference:
        data["reference"] = reference
    if description:
        data["description"] = description
    if target_date:
        data["target_date"] = target_date
    if link:
        data["link"] = link
    if contact_id:
        data["contact"] = contact_id
    if project_code:
        data["project_code"] = project_code

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", "/order/so/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Created sales order pk={result.get('pk')}")
            return result

        return {"error": "Unexpected response format"}

    except Exception as e:
        logger.error(f"Failed to create sales order: {e}")
        raise


@ai_function
@require_hitl(reason="Adding line item to sales order")
async def add_so_line_item(
    order_id: int,
    part_id: int,
    quantity: float,
    sale_price: float | None = None,
    reference: str | None = None,
    notes: str | None = None,
    target_date: str | None = None,
) -> dict[str, Any]:
    """
    Add a line item to a sales order.

    Adds a part to an existing sales order with specified quantity.

    Args:
        order_id: The sales order ID (required)
        part_id: The part to sell (required)
        quantity: Quantity to sell (required)
        sale_price: Unit sale price
        reference: Line item reference
        notes: Notes for this line
        target_date: Delivery date for this line

    Returns:
        Created line item data

    Example:
        # Add 50 widgets to the SO
        line = await add_so_line_item(
            order_id=15,
            part_id=100,
            quantity=50,
            sale_price=25.00
        )
    """
    logger.info(f"Adding line item to SO {order_id}: part {part_id} x {quantity}")

    data: dict[str, Any] = {
        "order": order_id,
        "part": part_id,
        "quantity": quantity,
    }

    if sale_price is not None:
        data["sale_price"] = sale_price
    if reference:
        data["reference"] = reference
    if notes:
        data["notes"] = notes
    if target_date:
        data["target_date"] = target_date

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", "/order/so-line/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Added SO line item pk={result.get('pk')}")
            return result

        return {"error": "Unexpected response format"}

    except Exception as e:
        logger.error(f"Failed to add SO line item: {e}")
        raise


@ai_function
@require_hitl(reason="Issuing sales order")
async def issue_sales_order(
    order_id: int,
) -> dict[str, Any]:
    """
    Issue a sales order.

    Changes the SO status from 'Pending' to 'In Progress'.
    This indicates the order is being processed.

    Args:
        order_id: The sales order ID to issue (required)

    Returns:
        Updated sales order data

    Example:
        # Start processing a sales order
        result = await issue_sales_order(order_id=15)
    """
    logger.info(f"Issuing sales order {order_id}")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", f"/order/so/{order_id}/issue/", json_data={})

        if isinstance(result, dict):
            logger.info(f"Issued sales order {order_id}")
            return result

        return {"success": True, "order_id": order_id, "status": "In Progress"}

    except Exception as e:
        logger.error(f"Failed to issue sales order: {e}")
        raise


@ai_function
@require_hitl(reason="Creating shipment for sales order")
async def create_so_shipment(
    order_id: int,
    reference: str | None = None,
    tracking_number: str | None = None,
    invoice_number: str | None = None,
    link: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Create a shipment for a sales order.

    Creates a new shipment record that can have stock items
    allocated to it for shipping.

    Args:
        order_id: The sales order ID (required)
        reference: Shipment reference number
        tracking_number: Carrier tracking number
        invoice_number: Invoice number
        link: External link (tracking URL, etc.)
        notes: Shipment notes

    Returns:
        Created shipment data

    Example:
        # Create a shipment for partial order
        shipment = await create_so_shipment(
            order_id=15,
            reference="SHIP-001",
            tracking_number="1Z999AA10123456784"
        )
    """
    logger.info(f"Creating shipment for sales order {order_id}")

    data: dict[str, Any] = {
        "order": order_id,
    }

    if reference:
        data["reference"] = reference
    if tracking_number:
        data["tracking_number"] = tracking_number
    if invoice_number:
        data["invoice_number"] = invoice_number
    if link:
        data["link"] = link
    if notes:
        data["notes"] = notes

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", "/order/so/shipment/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Created shipment pk={result.get('pk')}")
            return result

        return {"error": "Unexpected response format"}

    except Exception as e:
        logger.error(f"Failed to create shipment: {e}")
        raise


@ai_function
@require_hitl(reason="Allocating stock to sales order")
async def allocate_so_stock(
    line_item_id: int,
    stock_item_id: int,
    quantity: float,
    shipment_id: int | None = None,
) -> dict[str, Any]:
    """
    Allocate stock to a sales order line item.

    Reserves specific stock items for a sales order line.
    Allocated stock is held for this order.

    Args:
        line_item_id: The SO line item ID (required)
        stock_item_id: The stock item to allocate (required)
        quantity: Quantity to allocate (required)
        shipment_id: Shipment to associate allocation with

    Returns:
        Created allocation data

    Example:
        # Allocate 25 items from stock to an order line
        allocation = await allocate_so_stock(
            line_item_id=30,
            stock_item_id=123,
            quantity=25,
            shipment_id=5
        )
    """
    logger.info(f"Allocating {quantity} from stock {stock_item_id} to SO line {line_item_id}")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        # Allocations are created through the order-level action
        # POST /order/so/{order}/allocate/ (the so-allocation list endpoint
        # is read/bulk-edit only) - resolve the order from the line first
        line = await client._request("GET", f"/order/so-line/{line_item_id}/")
        if not isinstance(line, dict) or not line.get("order"):
            raise ValueError(f"Sales order line {line_item_id} not found")

        data: dict[str, Any] = {
            "items": [
                {
                    "line_item": line_item_id,
                    "stock_item": stock_item_id,
                    "quantity": quantity,
                }
            ],
        }

        if shipment_id:
            data["shipment"] = shipment_id

        result = await client._request(
            "POST", f"/order/so/{line['order']}/allocate/", json_data=data
        )

        if isinstance(result, dict):
            logger.info(f"Allocated stock to SO line {line_item_id}")
            return result

        return {"success": True, "line_item": line_item_id, "quantity": quantity}

    except Exception as e:
        logger.error(f"Failed to allocate stock: {e}")
        raise


@ai_function
@require_hitl(reason="Updating sales order")
async def update_sales_order(
    sales_order_id: int,
    customer_id: int | None = None,
    reference: str | None = None,
    customer_reference: str | None = None,
    description: str | None = None,
    target_date: str | None = None,
    link: str | None = None,
    contact_id: int | None = None,
    project_code: int | None = None,
) -> dict[str, Any]:
    """
    Update a sales order.

    Args:
        sales_order_id: The sales order ID to update (required)
        customer_id: Customer company ID
        reference: Order reference number
        customer_reference: Customer's PO number
        description: Description/notes
        target_date: Target delivery date
        link: External link
        contact_id: Contact person ID
        project_code: Project code ID

    Returns:
        Updated sales order data
    """
    logger.info(f"Updating sales order {sales_order_id}")

    data: dict[str, Any] = {}
    if customer_id:
        data["customer"] = customer_id
    if reference:
        data["reference"] = reference
    if customer_reference:
        data["customer_reference"] = customer_reference
    if description:
        data["description"] = description
    if target_date:
        data["target_date"] = target_date
    if link:
        data["link"] = link
    if contact_id:
        data["contact"] = contact_id
    if project_code:
        data["project_code"] = project_code

    if not data:
        raise ValueError("No fields to update provided")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("PATCH", f"/order/so/{sales_order_id}/", json_data=data)
        logger.info(f"Updated sales order {sales_order_id}")
        return result

    except Exception as e:
        logger.error(f"Failed to update sales order: {e}")
        raise


@ai_function
@require_hitl(reason="Cancelling sales order")
async def cancel_sales_order(
    sales_order_id: int,
) -> dict[str, Any]:
    """
    Cancel a sales order.

    Args:
        sales_order_id: The sales order ID to cancel

    Returns:
        Cancellation confirmation
    """
    logger.info(f"Cancelling sales order {sales_order_id}")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", f"/order/so/{sales_order_id}/cancel/", json_data={})
        logger.info(f"Cancelled sales order {sales_order_id}")
        return result

    except Exception as e:
        logger.error(f"Failed to cancel sales order: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting sales order")
async def delete_sales_order(
    sales_order_id: int,
) -> dict[str, Any]:
    """
    Delete a sales order.

    Args:
        sales_order_id: The sales order ID to delete

    Returns:
        Success confirmation
    """
    logger.info(f"Deleting sales order {sales_order_id}")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        await client._request("DELETE", f"/order/so/{sales_order_id}/")

        logger.info(f"Deleted sales order {sales_order_id}")
        return {"success": True, "sales_order_id": sales_order_id}

    except Exception as e:
        logger.error(f"Failed to delete sales order: {e}")
        raise


@ai_function
@require_hitl(reason="Completing sales order")
async def complete_sales_order(
    sales_order_id: int,
    accept_incomplete: bool = False,
) -> dict[str, Any]:
    """
    Complete a sales order.

    Args:
        sales_order_id: The sales order ID to complete
        accept_incomplete: Accept incomplete order (allow un-shipped items)

    Returns:
        Completion result
    """
    logger.info(f"Completing sales order {sales_order_id}")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request(
            "POST",
            f"/order/so/{sales_order_id}/complete/",
            json_data={"accept_incomplete": accept_incomplete},
        )
        logger.info(f"Completed sales order {sales_order_id}")
        return result

    except Exception as e:
        logger.error(f"Failed to complete sales order: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting sales order line item")
async def delete_so_line_item(
    line_item_id: int,
) -> dict[str, Any]:
    """
    Delete a sales order line item.

    Args:
        line_item_id: The line item ID to delete

    Returns:
        Success confirmation
    """
    logger.info(f"Deleting SO line item {line_item_id}")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        await client._request("DELETE", f"/order/so-line/{line_item_id}/")

        logger.info(f"Deleted SO line item {line_item_id}")
        return {"success": True, "line_item_id": line_item_id}

    except Exception as e:
        logger.error(f"Failed to delete SO line item: {e}")
        raise


# Export all sales order write tools
SALES_ORDER_WRITE_TOOLS = [
    create_sales_order,
    add_so_line_item,
    issue_sales_order,
    create_so_shipment,
    allocate_so_stock,
    update_sales_order,
    cancel_sales_order,
    delete_sales_order,
    complete_sales_order,
    delete_so_line_item,
]

__all__ = [
    "SALES_ORDER_WRITE_TOOLS",
    "add_so_line_item",
    "allocate_so_stock",
    "cancel_sales_order",
    "complete_sales_order",
    "create_sales_order",
    "create_so_shipment",
    "delete_sales_order",
    "delete_so_line_item",
    "issue_sales_order",
    "update_sales_order",
]
