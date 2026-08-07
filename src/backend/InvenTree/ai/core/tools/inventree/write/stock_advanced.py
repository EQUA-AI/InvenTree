"""
Advanced Stock Write Tools

Advanced stock management operations including serialization,
installation tracking, assignment, and returns.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.integrations.data_provider import get_data_provider
from ai.core.maf_compat import ai_function
from ai.core.tools.inventree.base import require_hitl

logger = logging.getLogger(__name__)


@ai_function
@require_hitl(reason="Serializing stock items")
async def serialize_stock(
    stock_id: int,
    serial_numbers: list[str],
    destination_location_id: int | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Serialize a stock item into individually tracked items.

    Splits a stock item with quantity > 1 into individual items,
    each with a unique serial number. The original item is consumed.

    Args:
        stock_id: The stock item ID to serialize (required)
        serial_numbers: List of serial numbers to assign (required)
            Must match the quantity of the stock item
        destination_location_id: Location for serialized items (default: same location)
        notes: Notes about the serialization

    Returns:
        List of created serialized stock items

    Example:
        # Serialize 5 items with sequential serial numbers
        result = await serialize_stock(
            stock_id=123,
            serial_numbers=["SN-001", "SN-002", "SN-003", "SN-004", "SN-005"],
            notes="Serializing for customer shipment"
        )
    """
    if not serial_numbers:
        raise ValueError("At least one serial number is required")

    logger.info(f"Serializing stock item {stock_id} with {len(serial_numbers)} serial numbers")

    # Verify stock item exists
    provider = get_data_provider()
    stock_item = await provider.get_stock_item(stock_id)
    if not stock_item:
        raise ValueError(f"Stock item with ID {stock_id} not found")

    # Serialization is a per-item detail action: POST /stock/{pk}/serialize/
    # with quantity + serial_numbers + a REQUIRED destination
    destination = destination_location_id or stock_item.get("location")
    if not destination:
        raise ValueError(
            "destination_location_id is required (the stock item has no "
            "current location to fall back to)"
        )

    data: dict[str, Any] = {
        "quantity": len(serial_numbers),
        "serial_numbers": ",".join(serial_numbers),
        "destination": destination,
    }

    if notes:
        data["notes"] = notes

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", f"/stock/{stock_id}/serialize/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Serialized stock item {stock_id} into {len(serial_numbers)} items")
            return result

        return {"success": True, "serialized_count": len(serial_numbers)}

    except Exception as e:
        logger.error(f"Failed to serialize stock: {e}")
        raise


@ai_function
@require_hitl(reason="Installing stock into another item")
async def install_stock(
    stock_id: int,
    into_stock_id: int,
    quantity: float | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Install a stock item into another stock item.

    Used for tracking components installed into assemblies or equipment.
    The installed item becomes a child of the parent item.

    Args:
        stock_id: The stock item to install (required)
        into_stock_id: The stock item to install into (required)
        quantity: Quantity to install (None = all)
        notes: Notes about the installation

    Returns:
        Result of the installation operation

    Example:
        # Install a component into an assembly
        result = await install_stock(
            stock_id=456,  # Component to install
            into_stock_id=123,  # Assembly/parent item
            notes="Installed during final assembly"
        )
    """
    logger.info(f"Installing stock item {stock_id} into {into_stock_id}")

    # Installation is a per-item detail action on the PARENT item:
    # POST /stock/{parent_pk}/install/ with the child as 'stock_item'
    data: dict[str, Any] = {"stock_item": stock_id}
    if quantity is not None:
        data["quantity"] = int(quantity)

    if notes:
        data["note"] = notes

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", f"/stock/{into_stock_id}/install/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Installed stock item {stock_id}")
            return result

        return {"success": True, "installed_into": into_stock_id}

    except Exception as e:
        logger.error(f"Failed to install stock: {e}")
        raise


@ai_function
@require_hitl(reason="Uninstalling stock from an assembly")
async def uninstall_stock(
    stock_id: int,
    destination_location_id: int,
    quantity: float | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Uninstall a stock item from its parent assembly.

    Removes a previously installed component from an assembly
    and moves it to a specified location.

    Args:
        stock_id: The installed stock item to uninstall (required)
        destination_location_id: Where to put the uninstalled item (required)
        quantity: Quantity to uninstall (None = all)
        notes: Notes about the uninstallation

    Returns:
        Result of the uninstallation operation

    Example:
        # Uninstall a faulty component for replacement
        result = await uninstall_stock(
            stock_id=456,
            destination_location_id=10,  # Repair bench location
            notes="Removed for testing"
        )
    """
    logger.info(f"Uninstalling stock item {stock_id} to location {destination_location_id}")

    # Uninstallation is a per-item detail action on the INSTALLED item:
    # POST /stock/{pk}/uninstall/ - the whole item is always uninstalled
    if quantity is not None:
        logger.warning(
            "Partial uninstall is not supported by the API; "
            "the whole stock item will be uninstalled"
        )

    data: dict[str, Any] = {"location": destination_location_id}

    if notes:
        data["note"] = notes

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", f"/stock/{stock_id}/uninstall/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Uninstalled stock item {stock_id}")
            return result

        return {"success": True, "uninstalled_to": destination_location_id}

    except Exception as e:
        logger.error(f"Failed to uninstall stock: {e}")
        raise


@ai_function
@require_hitl(reason="Assigning stock to a customer")
async def assign_stock(
    stock_id: int,
    customer_id: int,
    quantity: float | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Assign stock to a customer.

    Marks stock as assigned to a specific customer. This is used
    for customer-owned inventory or consignment stock.

    Args:
        stock_id: The stock item to assign (required)
        customer_id: The customer company ID (required)
        quantity: Quantity to assign (None = all)
        notes: Notes about the assignment

    Returns:
        Updated stock item data

    Example:
        # Assign stock to customer for consignment
        result = await assign_stock(
            stock_id=123,
            customer_id=5,
            notes="Consignment inventory for Acme Corp"
        )
    """
    logger.info(f"Assigning stock item {stock_id} to customer {customer_id}")

    # StockAssignmentItemSerializer's field is 'item'; whole items are
    # assigned (no per-item quantity)
    if quantity is not None:
        logger.warning(
            "Partial assignment is not supported by the API; the whole stock item will be assigned"
        )

    data: dict[str, Any] = {
        "items": [{"item": stock_id}],
        "customer": customer_id,
    }

    if notes:
        data["notes"] = notes

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", "/stock/assign/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Assigned stock item {stock_id} to customer {customer_id}")
            return result

        return {"success": True, "assigned_to": customer_id}

    except Exception as e:
        logger.error(f"Failed to assign stock: {e}")
        raise


@ai_function
@require_hitl(reason="Returning stock from a customer")
async def return_stock(
    stock_id: int,
    destination_location_id: int,
    quantity: float | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Return stock from a customer.

    Removes customer assignment from stock and moves it to
    a specified location. Use for returned consignment stock.

    Args:
        stock_id: The stock item to return (required)
        destination_location_id: Location for returned stock (required)
        quantity: Quantity to return (None = all)
        notes: Notes about the return

    Returns:
        Updated stock item data

    Example:
        # Return consignment stock from customer
        result = await return_stock(
            stock_id=123,
            destination_location_id=5,  # Receiving dock
            notes="Consignment returned - excess inventory"
        )
    """
    logger.info(f"Returning stock item {stock_id} to location {destination_location_id}")

    item_data: dict[str, Any] = {"pk": stock_id}
    if quantity is not None:
        item_data["quantity"] = quantity

    data: dict[str, Any] = {
        "items": [item_data],
        "location": destination_location_id,
    }

    if notes:
        data["notes"] = notes

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        # Return uses the same endpoint as unassign
        result = await client._request("POST", "/stock/return/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Returned stock item {stock_id}")
            return result

        return {"success": True, "returned_to": destination_location_id}

    except Exception as e:
        logger.error(f"Failed to return stock: {e}")
        raise


# Export all advanced stock write tools
STOCK_ADVANCED_WRITE_TOOLS = [
    serialize_stock,
    install_stock,
    uninstall_stock,
    assign_stock,
    return_stock,
]

__all__ = [
    "STOCK_ADVANCED_WRITE_TOOLS",
    "assign_stock",
    "install_stock",
    "return_stock",
    "serialize_stock",
    "uninstall_stock",
]
