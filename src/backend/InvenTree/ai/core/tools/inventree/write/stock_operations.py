"""
Stock Operations Write Tools

Additional stock operations including status changes, conversions,
test results, and splitting.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function

from ai.core.integrations.data_provider import get_data_provider
from ai.core.tools.inventree.base import require_hitl

logger = logging.getLogger(__name__)


@ai_function
@require_hitl(reason="Changing stock status")
async def change_stock_status(
    stock_id: int,
    status: int,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Change the status of a stock item.
    
    Updates the status code for a stock item. Common statuses include:
    - 10: OK (available for use)
    - 50: Attention needed
    - 55: Damaged
    - 60: Destroyed
    - 65: Rejected
    - 70: Lost
    - 85: Returned
    
    Args:
        stock_id: The stock item ID (required)
        status: New status code (required)
        notes: Notes about the status change
    
    Returns:
        Updated stock item data
    
    Example:
        # Mark stock as damaged
        result = await change_stock_status(
            stock_id=123,
            status=55,  # Damaged
            notes="Found water damage during inspection"
        )
    """
    logger.info(f"Changing stock item {stock_id} status to {status}")
    
    data: dict[str, Any] = {
        "items": [{"pk": stock_id}],
        "status": status,
    }
    
    if notes:
        data["notes"] = notes
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/stock/change_status/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Changed status of stock item {stock_id} to {status}")
            return result
        
        return {"success": True, "new_status": status}
        
    except Exception as e:
        logger.error(f"Failed to change stock status: {e}")
        raise


@ai_function
@require_hitl(reason="Converting stock to a different part")
async def convert_stock(
    stock_id: int,
    target_part_id: int,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Convert stock from one part to another.
    
    Changes the part association of a stock item. The target part
    must be a valid variant or related part. Use for rework or
    variant conversions.
    
    Args:
        stock_id: The stock item ID to convert (required)
        target_part_id: The part ID to convert to (required)
        notes: Notes about the conversion
    
    Returns:
        Updated stock item data
    
    Example:
        # Convert stock to a different variant
        result = await convert_stock(
            stock_id=123,
            target_part_id=456,  # Target variant part
            notes="Converted to EU voltage variant"
        )
    """
    # Verify parts exist
    provider = get_data_provider()
    stock_item = await provider.get_stock_item(stock_id)
    if not stock_item:
        raise ValueError(f"Stock item with ID {stock_id} not found")
    
    target_part = await provider.get_part(target_part_id)
    if not target_part:
        raise ValueError(f"Target part with ID {target_part_id} not found")
    
    logger.info(f"Converting stock item {stock_id} to part {target_part_id}")
    
    data: dict[str, Any] = {
        "items": [{"pk": stock_id}],
        "part": target_part_id,
    }
    
    if notes:
        data["notes"] = notes
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/stock/convert/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Converted stock item {stock_id} to part {target_part_id}")
            return result
        
        return {"success": True, "converted_to": target_part_id}
        
    except Exception as e:
        logger.error(f"Failed to convert stock: {e}")
        raise


@ai_function
@require_hitl(reason="Recording test result for stock")
async def add_stock_test_result(
    stock_id: int,
    test_name: str,
    result: bool,
    value: str | None = None,
    notes: str | None = None,
    attachment: str | None = None,
) -> dict[str, Any]:
    """
    Add a test result for a stock item.
    
    Records a test result against a stock item. The test must be
    defined in the part's test templates.
    
    Args:
        stock_id: The stock item ID (required)
        test_name: Name of the test (required)
        result: True for pass, False for fail (required)
        value: Measured value or result text
        notes: Additional notes about the test
        attachment: URL or path to test evidence
    
    Returns:
        Created test result data
    
    Example:
        # Record a passed voltage test
        result = await add_stock_test_result(
            stock_id=123,
            test_name="Voltage Test",
            result=True,
            value="12.1V",
            notes="Within tolerance ±0.5V"
        )
    """
    logger.info(f"Adding test result '{test_name}' to stock item {stock_id}")
    
    data: dict[str, Any] = {
        "stock_item": stock_id,
        "test": test_name,
        "result": result,
    }
    
    if value:
        data["value"] = value
    if notes:
        data["notes"] = notes
    if attachment:
        data["attachment"] = attachment
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result_data = await client._request("POST", "/stock/test/", json_data=data)
        
        if isinstance(result_data, dict):
            logger.info(f"Added test result to stock item {stock_id}")
            return result_data
        
        return {"success": True, "test": test_name, "passed": result}
        
    except Exception as e:
        logger.error(f"Failed to add test result: {e}")
        raise


@ai_function
@require_hitl(reason="Splitting stock into multiple items")
async def split_stock(
    stock_id: int,
    quantities: list[float],
    destination_location_id: int | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Split a stock item into multiple items.
    
    Divides a single stock item into multiple items with specified
    quantities. The sum of quantities must equal the original quantity.
    
    Args:
        stock_id: The stock item ID to split (required)
        quantities: List of quantities for new items (required)
        destination_location_id: Location for split items (default: same location)
        notes: Notes about the split
    
    Returns:
        List of created stock items
    
    Example:
        # Split 100 units into 3 items: 50, 30, and 20
        result = await split_stock(
            stock_id=123,
            quantities=[50, 30, 20],
            notes="Split for different orders"
        )
    """
    if not quantities or len(quantities) < 2:
        raise ValueError("At least 2 quantities required for split")
    
    # Verify stock item exists
    provider = get_data_provider()
    stock_item = await provider.get_stock_item(stock_id)
    if not stock_item:
        raise ValueError(f"Stock item with ID {stock_id} not found")
    
    logger.info(f"Splitting stock item {stock_id} into {len(quantities)} items")
    
    # Build split data - InvenTree expects individual transfer operations
    # We'll do this by transferring portions to create new items
    created_items = []
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        # For splits, we keep the first quantity in the original and transfer the rest
        remaining_quantities = quantities[1:]  # Skip first, it stays in original
        
        for qty in remaining_quantities:
            transfer_data: dict[str, Any] = {
                "items": [{"pk": stock_id, "quantity": qty}],
            }
            
            if destination_location_id:
                transfer_data["location"] = destination_location_id
            else:
                # Use same location as original
                if stock_item.get("location"):
                    transfer_data["location"] = stock_item["location"]
            
            if notes:
                transfer_data["notes"] = notes
            
            result = await client._request("POST", "/stock/transfer/", json_data=transfer_data)
            
            if isinstance(result, dict):
                created_items.append(result)
        
        logger.info(f"Split stock item {stock_id} into {len(quantities)} items")
        return {
            "success": True,
            "original_id": stock_id,
            "split_count": len(quantities),
            "quantities": quantities,
            "created_items": created_items,
        }
        
    except Exception as e:
        logger.error(f"Failed to split stock: {e}")
        raise


@ai_function
@require_hitl(reason="Adjusting stock location")
async def update_stock_location(
    location_id: int,
    name: str | None = None,
    description: str | None = None,
    parent_id: int | None = None,
    structural: bool | None = None,
) -> dict[str, Any]:
    """
    Update a stock location's properties.
    
    Modifies the properties of an existing stock location including
    its name, description, parent location, or structural flag.
    
    Args:
        location_id: The location ID to update (required)
        name: New name for the location
        description: New description
        parent_id: New parent location ID (None to make root)
        structural: If True, location is organizational only (no stock)
    
    Returns:
        Updated location data
    
    Example:
        # Rename a location and move under new parent
        result = await update_stock_location(
            location_id=5,
            name="Warehouse A - Shelf 1",
            parent_id=10,
            description="Primary storage for electronics"
        )
    """
    logger.info(f"Updating stock location {location_id}")
    
    data: dict[str, Any] = {}
    
    if name is not None:
        data["name"] = name
    if description is not None:
        data["description"] = description
    if parent_id is not None:
        data["parent"] = parent_id
    if structural is not None:
        data["structural"] = structural
    
    if not data:
        raise ValueError("At least one field must be provided for update")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("PATCH", f"/stock/location/{location_id}/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Updated stock location {location_id}")
            return result
        
        return {"success": True, "location_id": location_id}
        
    except Exception as e:
        logger.error(f"Failed to update stock location: {e}")
        raise


@ai_function
@require_hitl(reason="Creating new stock location")
async def create_stock_location(
    name: str,
    description: str | None = None,
    parent_id: int | None = None,
    structural: bool = False,
) -> dict[str, Any]:
    """
    Create a new stock location.
    
    Args:
        name: Name of the location (required)
        description: Description of the location
        parent_id: Parent location ID (None for root)
        structural: If True, location is organizational only (no stock)
    
    Returns:
        Created location data
    """
    logger.info(f"Creating stock location: {name}")
    
    data: dict[str, Any] = {"name": name, "structural": structural}
    
    if description:
        data["description"] = description
    if parent_id:
        data["parent"] = parent_id
        
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/stock/location/", json_data=data)
        logger.info(f"Created stock location: {name}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to create stock location: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting stock location")
async def delete_stock_location(
    location_id: int,
) -> dict[str, Any]:
    """
    Delete a stock location.
    
    Args:
        location_id: The location ID to delete
    
    Returns:
        Success confirmation
    """
    logger.info(f"Deleting stock location {location_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        await client._request("DELETE", f"/stock/location/{location_id}/")
        
        logger.info(f"Deleted stock location {location_id}")
        return {"success": True, "location_id": location_id}
        
    except Exception as e:
        logger.error(f"Failed to delete stock location: {e}")
        raise


@ai_function
@require_hitl(reason="Updating stock item")
async def update_stock_item(
    stock_id: int,
    quantity: float | None = None,
    status: int | None = None,
    batch: str | None = None,
    serial: str | None = None,
    location_id: int | None = None,
) -> dict[str, Any]:
    """
    Update a stock item's properties.
    
    Args:
        stock_id: ID of the stock item to update
        quantity: New quantity
        status: New status code
        batch: New batch code
        serial: New serial number
        location_id: New location ID
        
    Returns:
        Updated stock item data
    """
    logger.info(f"Updating stock item {stock_id}")
    
    data: dict[str, Any] = {}
    if quantity is not None:
        data["quantity"] = quantity
    if status is not None:
        data["status"] = status
    if batch is not None:
        data["batch"] = batch
    if serial is not None:
        data["serial"] = serial
    if location_id is not None:
        data["location"] = location_id
        
    if not data:
        raise ValueError("At least one field must be provided for update")
        
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("PATCH", f"/stock/{stock_id}/", json_data=data)
        logger.info(f"Updated stock item {stock_id}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to update stock item: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting stock item")
async def delete_stock_item(
    stock_id: int,
) -> dict[str, Any]:
    """
    Delete a stock item.
    
    Args:
        stock_id: ID of the stock item to delete
        
    Returns:
        Success confirmation
    """
    logger.info(f"Deleting stock item {stock_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        await client._request("DELETE", f"/stock/{stock_id}/")
        
        logger.info(f"Deleted stock item {stock_id}")
        return {"success": True, "stock_id": stock_id}
        
    except Exception as e:
        logger.error(f"Failed to delete stock item: {e}")
        raise


# Export all stock operation write tools
STOCK_OPERATIONS_WRITE_TOOLS = [
    change_stock_status,
    convert_stock,
    add_stock_test_result,
    split_stock,
    update_stock_location,
    create_stock_location,
    delete_stock_location,
    update_stock_item,
    delete_stock_item,
]

__all__ = [
    "change_stock_status",
    "convert_stock",
    "add_stock_test_result",
    "split_stock",
    "update_stock_location",
    "create_stock_location",
    "delete_stock_location",
    "update_stock_item",
    "delete_stock_item",
    "STOCK_OPERATIONS_WRITE_TOOLS",
]
