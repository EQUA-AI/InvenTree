"""
Build Order Write Tools

Write tools for managing build orders (manufacturing) in InvenTree.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function

from ai.core.integrations.data_provider import get_data_provider
from ai.core.tools.inventree.base import require_hitl

logger = logging.getLogger(__name__)


@ai_function
@require_hitl(reason="Creating a build order")
async def create_build_order(
    part_id: int,
    quantity: float,
    reference: str | None = None,
    title: str | None = None,
    batch: str | None = None,
    target_date: str | None = None,
    parent_build_id: int | None = None,
    sales_order_id: int | None = None,
    destination_id: int | None = None,
    link: str | None = None,
    project_code: str | None = None,
) -> dict[str, Any]:
    """
    Create a new build order for manufacturing.
    
    Creates a build order to manufacture a specified quantity of a part.
    The part must be an assembly with a valid BOM.
    
    Args:
        part_id: The part to build (must be an assembly) (required)
        quantity: Quantity to build (required)
        reference: Build order reference (auto-generated if not provided)
        title: Title/description of the build
        batch: Batch code for produced items
        target_date: Target completion date (ISO format: YYYY-MM-DD)
        parent_build_id: Parent build order ID (for sub-assemblies)
        sales_order_id: Sales order this build fulfills
        destination_id: Stock location for completed items
        link: External link
        project_code: Project code for tracking
    
    Returns:
        Created build order data
    
    Example:
        # Create a build order for 50 assemblies
        build = await create_build_order(
            part_id=100,
            quantity=50,
            title="Q1 Production Run",
            target_date="2024-02-28",
            destination_id=10  # Finished goods warehouse
        )
    """
    # Verify part exists and is an assembly
    provider = get_data_provider()
    part = await provider.get_part(part_id)
    if not part:
        raise ValueError(f"Part with ID {part_id} not found")
    if not part.get("assembly"):
        raise ValueError(f"Part {part_id} is not an assembly (cannot be built)")
    
    logger.info(f"Creating build order for part {part_id} x {quantity}")
    
    data: dict[str, Any] = {
        "part": part_id,
        "quantity": quantity,
    }
    
    if reference:
        data["reference"] = reference
    if title:
        data["title"] = title
    if batch:
        data["batch"] = batch
    if target_date:
        data["target_date"] = target_date
    if parent_build_id:
        data["parent"] = parent_build_id
    if sales_order_id:
        data["sales_order"] = sales_order_id
    if destination_id:
        data["destination"] = destination_id
    if link:
        data["link"] = link
    if project_code:
        data["project_code"] = project_code
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/build/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Created build order pk={result.get('pk')}")
            return result
        
        return {"error": "Unexpected response format"}
        
    except Exception as e:
        logger.error(f"Failed to create build order: {e}")
        raise


@ai_function
@require_hitl(reason="Issuing build order")
async def issue_build_order(
    build_id: int,
) -> dict[str, Any]:
    """
    Issue a build order to start production.
    
    Changes build status from 'Pending' to 'Production'.
    Stock can be allocated and consumed after issuing.
    
    Args:
        build_id: The build order ID to issue (required)
    
    Returns:
        Updated build order data
    
    Example:
        # Start production on a build order
        result = await issue_build_order(build_id=25)
    """
    logger.info(f"Issuing build order {build_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request(
            "POST", 
            f"/build/{build_id}/issue/",
            json_data={}
        )
        
        if isinstance(result, dict):
            logger.info(f"Issued build order {build_id}")
            return result
        
        return {"success": True, "build_id": build_id, "status": "Production"}
        
    except Exception as e:
        logger.error(f"Failed to issue build order: {e}")
        raise


@ai_function
@require_hitl(reason="Allocating stock to build order")
async def allocate_build_stock(
    build_id: int,
    bom_item_id: int,
    stock_item_id: int,
    quantity: float,
) -> dict[str, Any]:
    """
    Allocate stock to a build order line.
    
    Reserves stock items for consumption in a build order.
    
    Args:
        build_id: The build order ID (required)
        bom_item_id: The BOM item to allocate for (required)
        stock_item_id: The stock item to allocate (required)
        quantity: Quantity to allocate (required)
    
    Returns:
        Created allocation data
    
    Example:
        # Allocate 50 resistors to a build
        allocation = await allocate_build_stock(
            build_id=25,
            bom_item_id=100,  # BOM line for resistors
            stock_item_id=500,  # Stock of resistors
            quantity=50
        )
    """
    logger.info(f"Allocating {quantity} from stock {stock_item_id} to build {build_id}")
    
    data: dict[str, Any] = {
        "build": build_id,
        "bom_item": bom_item_id,
        "stock_item": stock_item_id,
        "quantity": quantity,
    }
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/build/item/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Created build allocation pk={result.get('pk')}")
            return result
        
        return {"error": "Unexpected response format"}
        
    except Exception as e:
        logger.error(f"Failed to allocate build stock: {e}")
        raise


@ai_function
@require_hitl(reason="Completing build order output")
async def complete_build_output(
    build_id: int,
    quantity: float,
    location_id: int,
    serial_numbers: str | None = None,
    batch_code: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Complete build output (create finished goods).
    
    Records completion of built items, creating new stock entries
    for the finished assemblies.
    
    Args:
        build_id: The build order ID (required)
        quantity: Quantity completed (required)
        location_id: Stock location for completed items (required)
        serial_numbers: Comma-separated serial numbers (for serialized parts)
        batch_code: Batch code for completed items
        notes: Notes about completion
    
    Returns:
        Completion result with created stock items
    
    Example:
        # Complete 25 units from a build order
        result = await complete_build_output(
            build_id=25,
            quantity=25,
            location_id=10,  # Finished goods
            batch_code="BUILD-2024-001"
        )
    """
    logger.info(f"Completing {quantity} outputs for build {build_id}")
    
    output_data: dict[str, Any] = {
        "quantity": quantity,
    }
    
    if serial_numbers:
        output_data["serial_numbers"] = serial_numbers
    if batch_code:
        output_data["batch_code"] = batch_code
    
    data: dict[str, Any] = {
        "outputs": [output_data],
        "location": location_id,
        "status": 10,  # OK status
    }
    
    if notes:
        data["notes"] = notes
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request(
            "POST", 
            f"/build/{build_id}/complete/",
            json_data=data
        )
        
        if isinstance(result, dict):
            logger.info(f"Completed build output for {build_id}")
            return result
        
        return {"success": True, "completed_quantity": quantity}
        
    except Exception as e:
        logger.error(f"Failed to complete build output: {e}")
        raise


@ai_function
@require_hitl(reason="Cancelling build order")
async def cancel_build_order(
    build_id: int,
) -> dict[str, Any]:
    """
    Cancel a build order.
    
    Cancels an active build order. Any allocated stock will be
    released. Cannot cancel builds with completed outputs.
    
    Args:
        build_id: The build order ID to cancel (required)
    
    Returns:
        Cancellation confirmation
    
    Example:
        # Cancel an unwanted build order
        result = await cancel_build_order(build_id=25)
    """
    logger.info(f"Cancelling build order {build_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request(
            "POST", 
            f"/build/{build_id}/cancel/",
            json_data={}
        )
        
        if isinstance(result, dict):
            logger.info(f"Cancelled build order {build_id}")
            return result
        
        return {"success": True, "build_id": build_id, "status": "Cancelled"}
        
    except Exception as e:
        logger.error(f"Failed to cancel build order: {e}")
        raise


@ai_function
@require_hitl(reason="Updating build order")
async def update_build_order(
    build_id: int,
    title: str | None = None,
    description: str | None = None,
    target_date: str | None = None,
    priority: int | None = None,
    project_code: int | None = None,
    reference: str | None = None,
) -> dict[str, Any]:
    """
    Update an existing build order.
    
    Args:
        build_id: The build order ID to update (required)
        title: New title
        description: New description/notes
        target_date: New target date
        priority: New priority level
        project_code: New project code ID
        reference: New reference string
    
    Returns:
        Updated build order data
    """
    logger.info(f"Updating build order {build_id}")
    
    data: dict[str, Any] = {}
    if title:
        data["title"] = title
    if description:
        data["notes"] = description
    if target_date:
        data["target_date"] = target_date
    if priority is not None:
        data["priority"] = priority
    if project_code is not None:
        data["project_code"] = project_code
    if reference:
        data["reference"] = reference
        
    if not data:
        raise ValueError("No fields to update provided")
        
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("PATCH", f"/build/{build_id}/", json_data=data)
        logger.info(f"Updated build order {build_id}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to update build order: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting build order")
async def delete_build_order(
    build_id: int,
) -> dict[str, Any]:
    """
    Delete a build order.
    
    Args:
        build_id: The build order ID to delete
    
    Returns:
        Success confirmation
    """
    logger.info(f"Deleting build order {build_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        await client._request("DELETE", f"/build/{build_id}/")
        
        logger.info(f"Deleted build order {build_id}")
        return {"success": True, "build_id": build_id}
        
    except Exception as e:
        logger.error(f"Failed to delete build order: {e}")
        raise


@ai_function
@require_hitl(reason="Finishing build order")
async def finish_build_order(
    build_id: int,
    accept_incomplete: bool = False,
    accept_unallocated: bool = False,
) -> dict[str, Any]:
    """
    Finish a build order.
    
    Marks the build order as complete.
    
    Args:
        build_id: The build order ID to finish
        accept_incomplete: Accept incomplete allocations
        accept_unallocated: Accept unallocated items
        
    Returns:
        Result of the finish operation
    """
    logger.info(f"Finishing build order {build_id}")
    
    data = {
        "accept_incomplete": accept_incomplete,
        "accept_unallocated": accept_unallocated,
    }
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", f"/build/{build_id}/finish/", json_data=data)
        logger.info(f"Finished build order {build_id}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to finish build order: {e}")
        raise


@ai_function
@require_hitl(reason="Auto-allocating build stock")
async def auto_allocate_build(
    build_id: int,
    location_id: int | None = None,
    exclude_location_id: int | None = None,
    interchangeable: bool = False,
    substitutes: bool = False,
) -> dict[str, Any]:
    """
    Auto-allocate stock to a build order.
    
    Args:
        build_id: The build order ID
        location_id: Source location ID
        exclude_location_id: Location ID to exclude
        interchangeable: Allow interchangeable parts
        substitutes: Allow substitute parts
        
    Returns:
        Result of allocation
    """
    logger.info(f"Auto-allocating build {build_id}")
    
    data: dict[str, Any] = {
        "interchangeable": interchangeable,
        "substitutes": substitutes,
    }
    
    if location_id:
        data["location"] = location_id
    if exclude_location_id:
        data["exclude_location"] = exclude_location_id
        
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", f"/build/{build_id}/auto-allocate/", json_data=data)
        logger.info(f"Auto-allocated build {build_id}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to auto-allocate build: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting build allocation")
async def delete_build_allocation(
    allocation_id: int,
) -> dict[str, Any]:
    """
    Delete a build allocation (unallocate stock).
    
    Args:
        allocation_id: The allocation ID (from allocate_build_stock)
        
    Returns:
        Success confirmation
    """
    logger.info(f"Deleting build allocation {allocation_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        await client._request("DELETE", f"/build/item/{allocation_id}/")
        
        logger.info(f"Deleted build allocation {allocation_id}")
        return {"success": True, "allocation_id": allocation_id}
        
    except Exception as e:
        logger.error(f"Failed to delete build allocation: {e}")
        raise


# Export all build order write tools
BUILD_ORDER_WRITE_TOOLS = [
    create_build_order,
    issue_build_order,
    allocate_build_stock,
    complete_build_output,
    cancel_build_order,
    update_build_order,
    delete_build_order,
    finish_build_order,
    auto_allocate_build,
    delete_build_allocation,
]

__all__ = [
    "create_build_order",
    "issue_build_order",
    "allocate_build_stock",
    "complete_build_output",
    "cancel_build_order",
    "update_build_order",
    "delete_build_order",
    "finish_build_order",
    "auto_allocate_build",
    "delete_build_allocation",
    "BUILD_ORDER_WRITE_TOOLS",
]
