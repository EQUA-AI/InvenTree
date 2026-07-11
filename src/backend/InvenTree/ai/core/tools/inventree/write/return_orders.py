"""
Return Order Write Tools

Write tools for managing return orders in InvenTree.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function

from ai.core.tools.inventree.base import require_hitl

logger = logging.getLogger(__name__)


@ai_function
@require_hitl(reason="Creating a return order")
async def create_return_order(
    customer_id: int,
    reference: str | None = None,
    description: str | None = None,
    target_date: str | None = None,
    link: str | None = None,
    contact_id: int | None = None,
    project_code: str | None = None,
) -> dict[str, Any]:
    """
    Create a new return order.
    
    Creates a return order for receiving goods back from a customer.
    Line items can be added after creation.
    
    Args:
        customer_id: The customer company ID (required)
        reference: Return order reference (auto-generated if not provided)
        description: Description of the return
        target_date: Expected return date (ISO format: YYYY-MM-DD)
        link: External link (e.g., RMA reference)
        contact_id: Customer contact person ID
        project_code: Project code for tracking
    
    Returns:
        Created return order data
    
    Example:
        # Create a return order for a customer
        ro = await create_return_order(
            customer_id=10,
            description="Warranty return - defective units",
            target_date="2024-02-01"
        )
    """
    logger.info(f"Creating return order for customer {customer_id}")
    
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
        
        result = await client._request("POST", "/order/ro/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Created return order pk={result.get('pk')}")
            return result
        
        return {"error": "Unexpected response format"}
        
    except Exception as e:
        logger.error(f"Failed to create return order: {e}")
        raise


@ai_function
@require_hitl(reason="Adding line item to return order")
async def add_ro_line_item(
    order_id: int,
    part_id: int,
    quantity: float,
    reference: str | None = None,
    notes: str | None = None,
    outcome: int | None = None,
) -> dict[str, Any]:
    """
    Add a line item to a return order.
    
    Adds a part expected to be returned to an existing return order.
    
    Args:
        order_id: The return order ID (required)
        part_id: The part being returned (required)
        quantity: Quantity expected (required)
        reference: Line item reference (e.g., original SO reference)
        notes: Notes for this line
        outcome: Expected outcome code (10=Return, 20=Repair, etc.)
    
    Returns:
        Created line item data
    
    Example:
        # Add 5 defective widgets to return order
        line = await add_ro_line_item(
            order_id=5,
            part_id=100,
            quantity=5,
            notes="Defective - screen not working",
            outcome=20  # Repair
        )
    """
    logger.info(f"Adding line item to RO {order_id}: part {part_id} x {quantity}")
    
    data: dict[str, Any] = {
        "order": order_id,
        "part": part_id,
        "quantity": quantity,
    }
    
    if reference:
        data["reference"] = reference
    if notes:
        data["notes"] = notes
    if outcome is not None:
        data["outcome"] = outcome
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/order/ro-line/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Added RO line item pk={result.get('pk')}")
            return result
        
        return {"error": "Unexpected response format"}
        
    except Exception as e:
        logger.error(f"Failed to add RO line item: {e}")
        raise


@ai_function
@require_hitl(reason="Issuing return order")
async def issue_return_order(
    order_id: int,
) -> dict[str, Any]:
    """
    Issue a return order.
    
    Changes the RO status from 'Pending' to 'In Progress'.
    This indicates the return is expected/authorized.
    
    Args:
        order_id: The return order ID to issue (required)
    
    Returns:
        Updated return order data
    
    Example:
        # Authorize a return order
        result = await issue_return_order(order_id=5)
    """
    logger.info(f"Issuing return order {order_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request(
            "POST", 
            f"/order/ro/{order_id}/issue/",
            json_data={}
        )
        
        if isinstance(result, dict):
            logger.info(f"Issued return order {order_id}")
            return result
        
        return {"success": True, "order_id": order_id, "status": "In Progress"}
        
    except Exception as e:
        logger.error(f"Failed to issue return order: {e}")
        raise


@ai_function
@require_hitl(reason="Receiving items from return order")
async def receive_ro_items(
    order_id: int,
    line_item_id: int,
    quantity: float,
    location_id: int,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Receive items from a return order.
    
    Records receipt of returned items, creating stock entries.
    
    Args:
        order_id: The return order ID (required)
        line_item_id: The RO line item ID (required)
        quantity: Quantity received (required)
        location_id: Stock location for received items (required)
        notes: Notes about the received items
    
    Returns:
        Receipt result with created stock items
    
    Example:
        # Receive 3 of 5 expected returns
        result = await receive_ro_items(
            order_id=5,
            line_item_id=10,
            quantity=3,
            location_id=15,  # Returns processing area
            notes="3 units received, awaiting inspection"
        )
    """
    logger.info(f"Receiving {quantity} items for RO {order_id} line {line_item_id}")
    
    item_data: dict[str, Any] = {
        "line_item": line_item_id,
        "quantity": quantity,
        "location": location_id,
    }
    
    data: dict[str, Any] = {
        "items": [item_data],
        "location": location_id,
    }
    
    if notes:
        data["notes"] = notes
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request(
            "POST", 
            f"/order/ro/{order_id}/receive/",
            json_data=data
        )
        
        if isinstance(result, dict):
            logger.info(f"Received items for RO {order_id}")
            return result
        
        return {"success": True, "received_quantity": quantity}
        
    except Exception as e:
        logger.error(f"Failed to receive RO items: {e}")
        raise


@ai_function
@require_hitl(reason="Completing return order")
async def complete_return_order(
    order_id: int,
    accept_incomplete: bool = False,
) -> dict[str, Any]:
    """
    Complete a return order.
    
    Marks the return order as complete. All expected items
    should be received before completing.
    
    Args:
        order_id: The return order ID to complete (required)
        accept_incomplete: If True, complete even with unreceived items
    
    Returns:
        Updated return order data
    
    Example:
        # Complete a fully received return
        result = await complete_return_order(order_id=5)
        
        # Complete with partial receipt
        result = await complete_return_order(
            order_id=5,
            accept_incomplete=True
        )
    """
    logger.info(f"Completing return order {order_id}")
    
    data: dict[str, Any] = {}
    if accept_incomplete:
        data["accept_incomplete"] = True
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request(
            "POST", 
            f"/order/ro/{order_id}/complete/",
            json_data=data
        )
        
        if isinstance(result, dict):
            logger.info(f"Completed return order {order_id}")
            return result
        
        return {"success": True, "order_id": order_id, "status": "Complete"}
        
    except Exception as e:
        logger.error(f"Failed to complete return order: {e}")
        raise


@ai_function
@require_hitl(reason="Updating return order")
async def update_return_order(
    return_order_id: int,
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
    Update a return order.
    
    Args:
        return_order_id: The return order ID to update (required)
        customer_id: Customer company ID
        reference: Order reference
        customer_reference: Customer's reference
        description: Description/notes
        target_date: Target return date
        link: External link
        contact_id: Contact person ID
        project_code: Project code ID
    
    Returns:
        Updated return order data
    """
    logger.info(f"Updating return order {return_order_id}")
    
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
        
        result = await client._request("PATCH", f"/order/ro/{return_order_id}/", json_data=data)
        logger.info(f"Updated return order {return_order_id}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to update return order: {e}")
        raise


@ai_function
@require_hitl(reason="Cancelling return order")
async def cancel_return_order(
    return_order_id: int,
) -> dict[str, Any]:
    """
    Cancel a return order.
    
    Args:
        return_order_id: The return order ID to cancel
        
    Returns:
        Cancellation confirmation
    """
    logger.info(f"Cancelling return order {return_order_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request(
            "POST", 
            f"/order/ro/{return_order_id}/cancel/",
            json_data={}
        )
        logger.info(f"Cancelled return order {return_order_id}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to cancel return order: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting return order")
async def delete_return_order(
    return_order_id: int,
) -> dict[str, Any]:
    """
    Delete a return order.
    
    Args:
        return_order_id: The return order ID to delete
        
    Returns:
        Success confirmation
    """
    logger.info(f"Deleting return order {return_order_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        await client._request("DELETE", f"/order/ro/{return_order_id}/")
        
        logger.info(f"Deleted return order {return_order_id}")
        return {"success": True, "return_order_id": return_order_id}
        
    except Exception as e:
        logger.error(f"Failed to delete return order: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting return order line item")
async def delete_ro_line_item(
    line_item_id: int,
) -> dict[str, Any]:
    """
    Delete a return order line item.
    
    Args:
        line_item_id: The line item ID to delete
        
    Returns:
        Success confirmation
    """
    logger.info(f"Deleting RO line item {line_item_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        await client._request("DELETE", f"/order/ro-line/{line_item_id}/")
        
        logger.info(f"Deleted RO line item {line_item_id}")
        return {"success": True, "line_item_id": line_item_id}
        
    except Exception as e:
        logger.error(f"Failed to delete RO line item: {e}")
        raise


# Export all return order write tools
RETURN_ORDER_WRITE_TOOLS = [
    create_return_order,
    add_ro_line_item,
    issue_return_order,
    receive_ro_items,
    complete_return_order,
    update_return_order,
    cancel_return_order,
    delete_return_order,
    delete_ro_line_item,
]

__all__ = [
    "create_return_order",
    "add_ro_line_item",
    "issue_return_order",
    "receive_ro_items",
    "complete_return_order",
    "update_return_order",
    "cancel_return_order",
    "delete_return_order",
    "delete_ro_line_item",
    "RETURN_ORDER_WRITE_TOOLS",
]
