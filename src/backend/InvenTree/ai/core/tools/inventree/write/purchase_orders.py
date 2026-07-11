"""
Purchase Order Write Tools

Write tools for managing purchase orders in InvenTree.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function

from ai.core.integrations.data_provider import get_data_provider
from ai.core.tools.inventree.base import require_hitl

logger = logging.getLogger(__name__)


@ai_function
@require_hitl(reason="Creating a purchase order")
async def create_purchase_order(
    supplier_id: int,
    reference: str | None = None,
    description: str | None = None,
    target_date: str | None = None,
    link: str | None = None,
    contact_id: int | None = None,
    project_code: str | None = None,
) -> dict[str, Any]:
    """
    Create a new purchase order.
    
    Creates a purchase order for a supplier. Line items can be
    added after creation using add_po_line_item.
    
    Args:
        supplier_id: The supplier company ID (required)
        reference: PO reference number (auto-generated if not provided)
        description: Description of the order
        target_date: Expected delivery date (ISO format: YYYY-MM-DD)
        link: External link (e.g., supplier portal URL)
        contact_id: Supplier contact person ID
        project_code: Project code for cost tracking
    
    Returns:
        Created purchase order data
    
    Example:
        # Create a PO for a supplier
        po = await create_purchase_order(
            supplier_id=5,
            description="Monthly resistor order",
            target_date="2024-02-15"
        )
    """
    # Verify supplier exists
    provider = get_data_provider()
    suppliers = await provider.get_suppliers()
    supplier = next((s for s in suppliers if s.get("pk") == supplier_id), None)
    if not supplier:
        raise ValueError(f"Supplier with ID {supplier_id} not found")
    
    logger.info(f"Creating purchase order for supplier {supplier_id}")
    
    data: dict[str, Any] = {
        "supplier": supplier_id,
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
        
        result = await client._request("POST", "/order/po/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Created purchase order pk={result.get('pk')}")
            return result
        
        return {"error": "Unexpected response format"}
        
    except Exception as e:
        logger.error(f"Failed to create purchase order: {e}")
        raise


@ai_function
@require_hitl(reason="Adding line item to purchase order")
async def add_po_line_item(
    order_id: int,
    part_id: int,
    quantity: float,
    supplier_part_id: int | None = None,
    purchase_price: float | None = None,
    reference: str | None = None,
    notes: str | None = None,
    destination_id: int | None = None,
) -> dict[str, Any]:
    """
    Add a line item to a purchase order.
    
    Adds a part to an existing purchase order with specified quantity.
    
    Args:
        order_id: The purchase order ID (required)
        part_id: The part to order (required)
        quantity: Quantity to order (required)
        supplier_part_id: Specific supplier part link
        purchase_price: Unit price
        reference: Line item reference
        notes: Notes for this line
        destination_id: Stock location for received items
    
    Returns:
        Created line item data
    
    Example:
        # Add 100 resistors to the PO
        line = await add_po_line_item(
            order_id=10,
            part_id=42,
            quantity=100,
            purchase_price=0.05,
            destination_id=5  # Electronics shelf
        )
    """
    logger.info(f"Adding line item to PO {order_id}: part {part_id} x {quantity}")
    
    data: dict[str, Any] = {
        "order": order_id,
        "part": part_id,
        "quantity": quantity,
    }
    
    if supplier_part_id:
        data["supplier_part"] = supplier_part_id
    if purchase_price is not None:
        data["purchase_price"] = purchase_price
    if reference:
        data["reference"] = reference
    if notes:
        data["notes"] = notes
    if destination_id:
        data["destination"] = destination_id
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/order/po-line/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Added PO line item pk={result.get('pk')}")
            return result
        
        return {"error": "Unexpected response format"}
        
    except Exception as e:
        logger.error(f"Failed to add PO line item: {e}")
        raise


@ai_function
@require_hitl(reason="Issuing purchase order to supplier")
async def issue_purchase_order(
    order_id: int,
) -> dict[str, Any]:
    """
    Issue a purchase order to the supplier.
    
    Changes the PO status from 'Pending' to 'Placed'. This indicates
    the order has been sent to the supplier.
    
    Args:
        order_id: The purchase order ID to issue (required)
    
    Returns:
        Updated purchase order data
    
    Example:
        # Issue a PO to the supplier
        result = await issue_purchase_order(order_id=10)
    """
    logger.info(f"Issuing purchase order {order_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request(
            "POST", 
            f"/order/po/{order_id}/issue/",
            json_data={}
        )
        
        if isinstance(result, dict):
            logger.info(f"Issued purchase order {order_id}")
            return result
        
        return {"success": True, "order_id": order_id, "status": "Placed"}
        
    except Exception as e:
        logger.error(f"Failed to issue purchase order: {e}")
        raise


@ai_function
@require_hitl(reason="Receiving items from purchase order")
async def receive_po_items(
    order_id: int,
    line_item_id: int,
    quantity: float,
    location_id: int,
    serial_numbers: str | None = None,
    batch_code: str | None = None,
    barcode: str | None = None,
) -> dict[str, Any]:
    """
    Receive items from a purchase order line.
    
    Records receipt of items against a PO line, creating stock entries.
    
    Args:
        order_id: The purchase order ID (required)
        line_item_id: The PO line item ID (required)
        quantity: Quantity received (required)
        location_id: Stock location for received items (required)
        serial_numbers: Comma-separated serial numbers for serialized parts
        batch_code: Batch/lot code
        barcode: Barcode for the received items
    
    Returns:
        Receipt confirmation with stock item IDs
    
    Example:
        # Receive 50 of 100 ordered items
        result = await receive_po_items(
            order_id=10,
            line_item_id=25,
            quantity=50,
            location_id=5,
            batch_code="LOT-2024-001"
        )
    """
    logger.info(f"Receiving {quantity} items for PO {order_id} line {line_item_id}")
    
    item_data: dict[str, Any] = {
        "line_item": line_item_id,
        "quantity": quantity,
        "location": location_id,
    }
    
    if serial_numbers:
        item_data["serial_numbers"] = serial_numbers
    if batch_code:
        item_data["batch_code"] = batch_code
    if barcode:
        item_data["barcode"] = barcode
    
    data: dict[str, Any] = {
        "items": [item_data],
        "location": location_id,
    }
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request(
            "POST", 
            f"/order/po/{order_id}/receive/",
            json_data=data
        )
        
        if isinstance(result, dict):
            logger.info(f"Received items for PO {order_id}")
            return result
        
        return {"success": True, "received_quantity": quantity}
        
    except Exception as e:
        logger.error(f"Failed to receive PO items: {e}")
        raise


@ai_function
@require_hitl(reason="Cancelling purchase order")
async def cancel_purchase_order(
    order_id: int,
) -> dict[str, Any]:
    """
    Cancel a purchase order.
    
    Cancels an open purchase order. Cannot cancel orders that
    have already been fully received.
    
    Args:
        order_id: The purchase order ID to cancel (required)
    
    Returns:
        Cancellation confirmation
    
    Example:
        # Cancel an unwanted PO
        result = await cancel_purchase_order(order_id=10)
    """
    logger.info(f"Cancelling purchase order {order_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request(
            "POST", 
            f"/order/po/{order_id}/cancel/",
            json_data={}
        )
        
        if isinstance(result, dict):
            logger.info(f"Cancelled purchase order {order_id}")
            return result
        
        return {"success": True, "order_id": order_id, "status": "Cancelled"}
        
    except Exception as e:
        logger.error(f"Failed to cancel purchase order: {e}")
        raise


@ai_function
@require_hitl(reason="Updating purchase order")
async def update_purchase_order(
    purchase_order_id: int,
    supplier_id: int | None = None,
    sales_order_id: int | None = None,
    order_number: str | None = None,
    reference: str | None = None,
    description: str | None = None,
    target_date: str | None = None,
    link: str | None = None,
    contact_id: int | None = None,
    project_code: int | None = None,
) -> dict[str, Any]:
    """
    Update a purchase order.
    
    Args:
        purchase_order_id: The purchase order ID to update (required)
        supplier_id: Supplier company ID
        sales_order_id: Linked sales order ID
        order_number: Order number
        reference: External reference
        description: Description/notes
        target_date: Expected date
        link: External link
        contact_id: Contact person ID
        project_code: Project code ID
    
    Returns:
        Updated purchase order data
    """
    logger.info(f"Updating purchase order {purchase_order_id}")
    
    data: dict[str, Any] = {}
    if supplier_id:
        data["supplier"] = supplier_id
    if sales_order_id:
        data["sales_order"] = sales_order_id
    if order_number:
        data["reference"] = order_number
    if reference:
        data["supplier_reference"] = reference
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
        
        result = await client._request(
            "PATCH", 
            f"/order/po/{purchase_order_id}/", 
            json_data=data
        )
        logger.info(f"Updated purchase order {purchase_order_id}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to update purchase order: {e}")
        raise


@ai_function
@require_hitl(reason="Completing purchase order")
async def complete_purchase_order(
    order_id: int,
    accept_incomplete: bool = False,
) -> dict[str, Any]:
    """
    Complete a purchase order.
    
    Args:
        order_id: The purchase order ID
        accept_incomplete: Accept incomplete order
        
    Returns:
        Completion result
    """
    logger.info(f"Completing purchase order {order_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request(
            "POST", 
            f"/order/po/{order_id}/complete/",
            json_data={"accept_incomplete": accept_incomplete}
        )
        logger.info(f"Completed purchase order {order_id}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to complete purchase order: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting purchase order")
async def delete_purchase_order(
    order_id: int,
) -> dict[str, Any]:
    """
    Delete a purchase order.
    
    Args:
        order_id: The purchase order ID to delete
        
    Returns:
        Success confirmation
    """
    logger.info(f"Deleting purchase order {order_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        await client._request("DELETE", f"/order/po/{order_id}/")
        
        logger.info(f"Deleted purchase order {order_id}")
        return {"success": True, "order_id": order_id}
        
    except Exception as e:
        logger.error(f"Failed to delete purchase order: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting purchase order line item")
async def delete_po_line_item(
    line_item_id: int,
) -> dict[str, Any]:
    """
    Delete a purchase order line item.
    
    Args:
        line_item_id: The line item ID to delete
        
    Returns:
        Success confirmation
    """
    logger.info(f"Deleting PO line item {line_item_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        await client._request("DELETE", f"/order/po-line/{line_item_id}/")
        
        logger.info(f"Deleted PO line item {line_item_id}")
        return {"success": True, "line_item_id": line_item_id}
        
    except Exception as e:
        logger.error(f"Failed to delete PO line item: {e}")
        raise


# Export all purchase order write tools
PURCHASE_ORDER_WRITE_TOOLS = [
    create_purchase_order,
    add_po_line_item,
    issue_purchase_order,
    receive_po_items,
    cancel_purchase_order,
    update_purchase_order,
    complete_purchase_order,
    delete_purchase_order,
    delete_po_line_item,
]

__all__ = [
    "create_purchase_order",
    "add_po_line_item",
    "issue_purchase_order",
    "receive_po_items",
    "cancel_purchase_order",
    "update_purchase_order",
    "complete_purchase_order",
    "delete_purchase_order",
    "delete_po_line_item",
    "PURCHASE_ORDER_WRITE_TOOLS",
]
