"""
Stock Write Tools

Write tools for managing stock items in InvenTree.
These tools require HITL approval for operations that modify inventory.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function

from ai.core.integrations.data_provider import get_data_provider
from ai.core.tools.inventree.base import require_hitl

logger = logging.getLogger(__name__)


@ai_function
@require_hitl(reason="Adding stock to inventory")
async def add_stock(
    part_id: int,
    quantity: float,
    location_id: int,
    serial: str | None = None,
    batch: str | None = None,
    purchase_order_id: int | None = None,
    supplier_part_id: int | None = None,
    purchase_price: float | None = None,
    expiry_date: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Add new stock to the inventory system.
    
    Creates a new stock item for the specified part at the given location.
    Use this when receiving new inventory that wasn't from a purchase order.
    
    Args:
        part_id: The part ID to add stock for (required)
        quantity: Quantity to add (required)
        location_id: Stock location ID where items will be stored (required)
        serial: Serial number (for trackable parts, one item per serial)
        batch: Batch/lot code for batch tracking
        purchase_order_id: Link to purchase order if from a PO
        supplier_part_id: Link to supplier part
        purchase_price: Purchase price per unit
        expiry_date: Expiry date in ISO format (YYYY-MM-DD)
        notes: Notes about this stock item
    
    Returns:
        Created stock item data including:
        - pk: Stock item ID
        - part: Part ID
        - quantity: Quantity added
        - location: Location ID
        - serial: Serial number
        - batch: Batch code
    
    Example:
        # Add 100 units of a part to warehouse
        stock = await add_stock(
            part_id=42,
            quantity=100,
            location_id=5,
            batch="LOT-2024-001",
            notes="Received from manual count"
        )
    """
    provider = get_data_provider()
    
    # Verify part exists
    part = await provider.get_part(part_id)
    if not part:
        raise ValueError(f"Part with ID {part_id} not found")
    
    logger.info(f"Adding {quantity} units of part {part_id} to location {location_id}")
    
    data: dict[str, Any] = {
        "part": part_id,
        "quantity": quantity,
        "location": location_id,
    }
    
    if serial:
        data["serial"] = serial
    if batch:
        data["batch"] = batch
    if purchase_order_id:
        data["purchase_order"] = purchase_order_id
    if supplier_part_id:
        data["supplier_part"] = supplier_part_id
    if purchase_price is not None:
        data["purchase_price"] = purchase_price
    if expiry_date:
        data["expiry_date"] = expiry_date
    if notes:
        data["notes"] = notes
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/stock/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Added stock item pk={result.get('pk')}")
            return result
        
        return {"error": "Unexpected response format"}
        
    except Exception as e:
        logger.error(f"Failed to add stock: {e}")
        raise


@ai_function
@require_hitl(reason="Removing stock from inventory")
async def remove_stock(
    stock_id: int,
    quantity: float,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Remove (consume) stock from inventory.
    
    Reduces the quantity of an existing stock item. Use this for
    consumption, scrap, or adjustments.
    
    Args:
        stock_id: The stock item ID to remove from (required)
        quantity: Quantity to remove (required, must be positive)
        notes: Reason for removal
    
    Returns:
        Result of the removal operation
    
    Example:
        # Remove 10 units for consumption
        result = await remove_stock(
            stock_id=123,
            quantity=10,
            notes="Used in production"
        )
    """
    if quantity <= 0:
        raise ValueError("Quantity must be positive")
    
    logger.info(f"Removing {quantity} units from stock item {stock_id}")
    
    data: dict[str, Any] = {
        "items": [{"pk": stock_id, "quantity": quantity}],
    }
    
    if notes:
        data["notes"] = notes
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/stock/remove/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Removed {quantity} from stock item {stock_id}")
            return result
        
        return {"success": True, "removed": quantity}
        
    except Exception as e:
        logger.error(f"Failed to remove stock: {e}")
        raise


@ai_function
@require_hitl(reason="Transferring stock to a different location")
async def transfer_stock(
    stock_id: int,
    destination_location_id: int,
    quantity: float | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Transfer stock from one location to another.
    
    Moves stock items between locations. Can transfer partial quantity
    or the entire stock item.
    
    Args:
        stock_id: The stock item ID to transfer (required)
        destination_location_id: Target location ID (required)
        quantity: Quantity to transfer (None = all)
        notes: Notes about the transfer
    
    Returns:
        Result of the transfer operation
    
    Example:
        # Transfer entire stock item to new location
        result = await transfer_stock(
            stock_id=123,
            destination_location_id=10,
            notes="Moving to production floor"
        )
        
        # Transfer partial quantity
        result = await transfer_stock(
            stock_id=123,
            destination_location_id=10,
            quantity=50,
        )
    """
    logger.info(f"Transferring stock item {stock_id} to location {destination_location_id}")
    
    item_data: dict[str, Any] = {"pk": stock_id}
    if quantity is not None:
        item_data["quantity"] = quantity
    
    data: dict[str, Any] = {
        "location": destination_location_id,
        "items": [item_data],
    }
    
    if notes:
        data["notes"] = notes
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/stock/transfer/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Transferred stock item {stock_id}")
            return result
        
        return {"success": True, "transferred_to": destination_location_id}
        
    except Exception as e:
        logger.error(f"Failed to transfer stock: {e}")
        raise


@ai_function
@require_hitl(reason="Counting/adjusting stock quantity")
async def count_stock(
    stock_id: int,
    quantity: float,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Perform a stock count (cycle count / stocktake).
    
    Adjusts the stock quantity to match the physical count.
    This will add or remove stock as needed to match the counted quantity.
    
    Args:
        stock_id: The stock item ID to count (required)
        quantity: The actual counted quantity (required)
        notes: Notes about the count (e.g., "Monthly cycle count")
    
    Returns:
        Updated stock item data
    
    Example:
        # Correct stock level after physical count
        result = await count_stock(
            stock_id=123,
            quantity=95,  # Physical count shows 95 units
            notes="Monthly cycle count - found 5 units short"
        )
    """
    logger.info(f"Counting stock item {stock_id}: new quantity = {quantity}")
    
    data: dict[str, Any] = {
        "items": [{"pk": stock_id, "quantity": quantity}],
    }
    
    if notes:
        data["notes"] = notes
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/stock/count/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Counted stock item {stock_id}")
            return result
        
        return {"success": True, "new_quantity": quantity}
        
    except Exception as e:
        logger.error(f"Failed to count stock: {e}")
        raise


@ai_function
@require_hitl(reason="Merging multiple stock items")
async def merge_stock(
    stock_ids: list[int],
    destination_location_id: int | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Merge multiple stock items into one.
    
    Combines multiple stock items of the same part into a single
    stock item. All items must be for the same part.
    
    Args:
        stock_ids: List of stock item IDs to merge (required, minimum 2)
        destination_location_id: Location for merged item (default: location of first item)
        notes: Notes about the merge
    
    Returns:
        The merged stock item data
    
    Example:
        # Merge three partial stock items into one
        result = await merge_stock(
            stock_ids=[123, 124, 125],
            destination_location_id=5,
            notes="Consolidating partial items"
        )
    """
    if len(stock_ids) < 2:
        raise ValueError("At least 2 stock items required for merge")
    
    logger.info(f"Merging stock items: {stock_ids}")
    
    data: dict[str, Any] = {
        "items": [{"pk": sid} for sid in stock_ids],
        "allow_mismatched_suppliers": False,
        "allow_mismatched_status": False,
    }
    
    if destination_location_id:
        data["location"] = destination_location_id
    if notes:
        data["notes"] = notes
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/stock/merge/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Merged {len(stock_ids)} stock items")
            return result
        
        return {"success": True, "merged_count": len(stock_ids)}
        
    except Exception as e:
        logger.error(f"Failed to merge stock: {e}")
        raise


@ai_function
@require_hitl(reason="Adding stock quantity to existing item")
async def add_stock_quantity(
    stock_id: int,
    quantity: float,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Add quantity to an existing stock item.
    
    Increases the quantity of a specific stock item. This is different from
    'add_stock' (which creates a new item). Use this for refilling a bin
    or correcting a count upwards.
    
    Args:
        stock_id: The stock item ID to add to
        quantity: The amount to add (must be positive)
        notes: Reason for the addition
    
    Returns:
        Updated stock item data
    
    Example:
        # Refill a bin with 50 more units
        result = await add_stock_quantity(
            stock_id=123,
            quantity=50,
            notes="Refilled from bulk storage"
        )
    """
    if quantity <= 0:
        raise ValueError("Quantity must be positive")

    logger.info(f"Adding {quantity} to stock item {stock_id}")
    
    data: dict[str, Any] = {
        "items": [{"pk": stock_id, "quantity": quantity}],
    }
    
    if notes:
        data["notes"] = notes
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/stock/add/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Added {quantity} to stock item {stock_id}")
            return result
        
        return {"success": True, "added": quantity}
        
    except Exception as e:
        logger.error(f"Failed to add stock quantity: {e}")
        raise


# Export all stock write tools
STOCK_WRITE_TOOLS = [
    add_stock,
    remove_stock,
    transfer_stock,
    count_stock,
    merge_stock,
    add_stock_quantity,
]

__all__ = [
    "add_stock",
    "remove_stock",
    "transfer_stock",
    "count_stock",
    "merge_stock",
    "add_stock_quantity",
    "STOCK_WRITE_TOOLS",
]
