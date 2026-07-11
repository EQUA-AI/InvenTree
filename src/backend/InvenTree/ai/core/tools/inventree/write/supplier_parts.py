"""
Supplier and Manufacturer Part Write Tools

Tools for managing supplier parts and manufacturer parts in InvenTree.
"""

import logging
from typing import Any

from ai.core.tools.inventree.base import (
    WriteTool,
    ai_function,
    require_hitl,
)

logger = logging.getLogger(__name__)


@ai_function(
    name="update_supplier_part",
    description="Update a supplier part record. Modify pricing, SKU, lead time, or other supplier-specific information.",
)
@require_hitl(reason="Updating supplier parts requires approval")
async def update_supplier_part(
    supplier_part_id: int,
    sku: str | None = None,
    description: str | None = None,
    link: str | None = None,
    note: str | None = None,
    pack_quantity: int | None = None,
    pack_quantity_native: float | None = None,
) -> dict[str, Any]:
    """
    Update a supplier part.

    Args:
        supplier_part_id: ID of the supplier part to update
        sku: New supplier SKU/part number
        description: Supplier's description of the part
        link: URL to supplier's product page
        note: Internal notes about this supplier part
        pack_quantity: Quantity per pack/unit
        pack_quantity_native: Native pack quantity value

    Returns:
        Updated supplier part details
    """
    tool = WriteTool("update_supplier_part")

    try:
        client = await tool.get_client()

        data = {}
        if sku is not None:
            data["SKU"] = sku
        if description is not None:
            data["description"] = description
        if link is not None:
            data["link"] = link
        if note is not None:
            data["note"] = note
        if pack_quantity is not None:
            data["pack_quantity"] = pack_quantity
        if pack_quantity_native is not None:
            data["pack_quantity_native"] = pack_quantity_native

        if not data:
            return tool.error_response("No fields provided to update.")

        result = await client.patch(
            f"company/part/{supplier_part_id}/",
            json=data,
        )

        logger.info(f"Updated supplier part {supplier_part_id}")
        return tool.success_response(
            data=result,
            message=f"Successfully updated supplier part {supplier_part_id}",
        )

    except Exception as e:
        logger.error(f"Failed to update supplier part {supplier_part_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="delete_supplier_part",
    description="Delete a supplier part record. This removes the link between a part and a supplier.",
)
@require_hitl(reason="Deleting supplier parts requires approval")
async def delete_supplier_part(
    supplier_part_id: int,
) -> dict[str, Any]:
    """
    Delete a supplier part.

    Args:
        supplier_part_id: ID of the supplier part to delete

    Returns:
        Deletion confirmation
    """
    tool = WriteTool("delete_supplier_part")

    try:
        client = await tool.get_client()

        # Get supplier part details first
        supplier_part = await client.get(f"company/part/{supplier_part_id}/")

        await client.delete(f"company/part/{supplier_part_id}/")

        logger.info(f"Deleted supplier part {supplier_part_id}")
        return tool.success_response(
            data={
                "supplier_part_id": supplier_part_id,
                "sku": supplier_part.get("SKU"),
                "deleted": True,
            },
            message=f"Successfully deleted supplier part {supplier_part_id}",
        )

    except Exception as e:
        logger.error(f"Failed to delete supplier part {supplier_part_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="update_manufacturer_part",
    description="Update a manufacturer part record. Modify MPN or other manufacturer-specific information.",
)
@require_hitl(reason="Updating manufacturer parts requires approval")
async def update_manufacturer_part(
    manufacturer_part_id: int,
    mpn: str | None = None,
    description: str | None = None,
    link: str | None = None,
) -> dict[str, Any]:
    """
    Update a manufacturer part.

    Args:
        manufacturer_part_id: ID of the manufacturer part to update
        mpn: New manufacturer part number
        description: Manufacturer's description
        link: URL to manufacturer's product page

    Returns:
        Updated manufacturer part details
    """
    tool = WriteTool("update_manufacturer_part")

    try:
        client = await tool.get_client()

        data = {}
        if mpn is not None:
            data["MPN"] = mpn
        if description is not None:
            data["description"] = description
        if link is not None:
            data["link"] = link

        if not data:
            return tool.error_response("No fields provided to update.")

        result = await client.patch(
            f"company/part/manufacturer/{manufacturer_part_id}/",
            json=data,
        )

        logger.info(f"Updated manufacturer part {manufacturer_part_id}")
        return tool.success_response(
            data=result,
            message=f"Successfully updated manufacturer part {manufacturer_part_id}",
        )

    except Exception as e:
        logger.error(f"Failed to update manufacturer part {manufacturer_part_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="delete_manufacturer_part",
    description="Delete a manufacturer part record. This removes the link between a part and a manufacturer.",
)
@require_hitl(reason="Deleting manufacturer parts requires approval")
async def delete_manufacturer_part(
    manufacturer_part_id: int,
) -> dict[str, Any]:
    """
    Delete a manufacturer part.

    Args:
        manufacturer_part_id: ID of the manufacturer part to delete

    Returns:
        Deletion confirmation
    """
    tool = WriteTool("delete_manufacturer_part")

    try:
        client = await tool.get_client()

        # Get manufacturer part details first
        mfr_part = await client.get(
            f"company/part/manufacturer/{manufacturer_part_id}/"
        )

        await client.delete(f"company/part/manufacturer/{manufacturer_part_id}/")

        logger.info(f"Deleted manufacturer part {manufacturer_part_id}")
        return tool.success_response(
            data={
                "manufacturer_part_id": manufacturer_part_id,
                "mpn": mfr_part.get("MPN"),
                "deleted": True,
            },
            message=f"Successfully deleted manufacturer part {manufacturer_part_id}",
        )

    except Exception as e:
        logger.error(
            f"Failed to delete manufacturer part {manufacturer_part_id}: {e}"
        )
        return tool.error_response(str(e))


@ai_function(
    name="add_supplier_price_break",
    description="Add a price break for a supplier part. Price breaks define quantity-based pricing tiers.",
)
@require_hitl(reason="Adding price breaks requires approval")
async def add_supplier_price_break(
    supplier_part_id: int,
    quantity: int,
    price: float,
    price_currency: str = "USD",
) -> dict[str, Any]:
    """
    Add a price break for a supplier part.

    Args:
        supplier_part_id: ID of the supplier part
        quantity: Minimum quantity for this price tier
        price: Price per unit at this quantity
        price_currency: Currency code (e.g., 'USD', 'EUR', 'GBP')

    Returns:
        Created price break details
    """
    tool = WriteTool("add_supplier_price_break")

    if quantity < 1:
        return tool.error_response("Quantity must be at least 1.")

    if price < 0:
        return tool.error_response("Price cannot be negative.")

    try:
        client = await tool.get_client()

        data = {
            "part": supplier_part_id,
            "quantity": quantity,
            "price": price,
            "price_currency": price_currency,
        }

        result = await client.post("company/price-break/", json=data)

        logger.info(
            f"Added price break for supplier part {supplier_part_id}: "
            f"{quantity}+ @ {price} {price_currency}"
        )
        return tool.success_response(
            data=result,
            message=f"Successfully added price break: {quantity}+ units @ {price} {price_currency}",
        )

    except Exception as e:
        logger.error(
            f"Failed to add price break for supplier part {supplier_part_id}: {e}"
        )
        return tool.error_response(str(e))


# Export all supplier part write tools
SUPPLIER_PART_WRITE_TOOLS = [
    update_supplier_part,
    delete_supplier_part,
    update_manufacturer_part,
    delete_manufacturer_part,
    add_supplier_price_break,
]
