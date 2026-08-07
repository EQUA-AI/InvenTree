"""
Build Order Advanced Write Tools

Additional build order operations including holds, updates,
and unallocation.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function
from ai.core.tools.inventree.base import require_hitl

logger = logging.getLogger(__name__)


@ai_function
@require_hitl(reason="Placing build order on hold")
async def hold_build_order(
    build_id: int,
) -> dict[str, Any]:
    """
    Place a build order on hold.

    Temporarily suspends production of a build order.
    Allocated stock remains reserved.

    Args:
        build_id: The build order ID to hold (required)

    Returns:
        Updated build order data

    Example:
        # Hold production pending materials
        result = await hold_build_order(build_id=25)
    """
    logger.info(f"Placing build order {build_id} on hold")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", f"/build/{build_id}/hold/", json_data={})

        if isinstance(result, dict):
            logger.info(f"Placed build order {build_id} on hold")
            return result

        return {"success": True, "build_id": build_id, "status": "On Hold"}

    except Exception as e:
        logger.error(f"Failed to hold build order: {e}")
        raise


@ai_function
@require_hitl(reason="Updating build order")
async def update_build_order(
    build_id: int,
    quantity: float | None = None,
    reference: str | None = None,
    title: str | None = None,
    batch: str | None = None,
    target_date: str | None = None,
    destination_id: int | None = None,
    link: str | None = None,
    project_code: str | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    """
    Update a build order's properties.

    Modifies metadata of an existing build order.

    Args:
        build_id: The build order ID to update (required)
        quantity: New quantity to build
        reference: New reference number
        title: New title/description
        batch: New batch code
        target_date: New target date (ISO format)
        destination_id: New destination location
        link: New external link
        project_code: New project code
        priority: Priority level (0-50, higher = more urgent)

    Returns:
        Updated build order data

    Example:
        # Update quantity and target date
        result = await update_build_order(
            build_id=25,
            quantity=75,
            target_date="2024-03-15",
            priority=30
        )
    """
    logger.info(f"Updating build order {build_id}")

    data: dict[str, Any] = {}

    if quantity is not None:
        data["quantity"] = quantity
    if reference is not None:
        data["reference"] = reference
    if title is not None:
        data["title"] = title
    if batch is not None:
        data["batch"] = batch
    if target_date is not None:
        data["target_date"] = target_date
    if destination_id is not None:
        data["destination"] = destination_id
    if link is not None:
        data["link"] = link
    if project_code is not None:
        data["project_code"] = project_code
    if priority is not None:
        data["priority"] = priority

    if not data:
        raise ValueError("At least one field must be provided for update")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("PATCH", f"/build/{build_id}/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Updated build order {build_id}")
            return result

        return {"success": True, "build_id": build_id}

    except Exception as e:
        logger.error(f"Failed to update build order: {e}")
        raise


@ai_function
@require_hitl(reason="Unallocating stock from build order")
async def unallocate_build_stock(
    build_id: int,
    allocation_id: int | None = None,
    bom_item_id: int | None = None,
) -> dict[str, Any]:
    """
    Unallocate stock from a build order.

    Releases allocated stock back to available inventory.
    Can unallocate specific allocations or all for a BOM item.

    Args:
        build_id: The build order ID (required)
        allocation_id: Specific allocation ID to remove
        bom_item_id: Unallocate all for this BOM item

    Returns:
        Unallocation result

    Example:
        # Unallocate a specific allocation
        result = await unallocate_build_stock(
            build_id=25,
            allocation_id=100
        )

        # Unallocate all for a BOM line
        result = await unallocate_build_stock(
            build_id=25,
            bom_item_id=50
        )
    """
    logger.info(f"Unallocating stock from build order {build_id}")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        if allocation_id:
            # A single allocation is removed by deleting the BuildItem row -
            # the unallocate action only understands build lines
            await client._request("DELETE", f"/build/item/{allocation_id}/")
            logger.info(f"Removed build allocation {allocation_id}")
            return {"success": True, "removed_allocation": allocation_id}

        data: dict[str, Any] = {}

        if bom_item_id:
            # Resolve the build line for this BOM item; the serializer accepts
            # only 'build_line' (and 'output'), never 'bom_item'
            lines = await client._request(
                "GET", "/build/line/", params={"build": build_id, "limit": 500}
            )
            if isinstance(lines, dict) and "results" in lines:
                lines = lines["results"]

            line = next(
                (ln for ln in lines or [] if ln.get("bom_item") == bom_item_id),
                None,
            )
            if not line:
                raise ValueError(f"Build {build_id} has no line for BOM item {bom_item_id}")
            data["build_line"] = line.get("pk")

        result = await client._request("POST", f"/build/{build_id}/unallocate/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Unallocated stock from build order {build_id}")
            return result

        return {"success": True, "build_id": build_id}

    except Exception as e:
        logger.error(f"Failed to unallocate build stock: {e}")
        raise


@ai_function
@require_hitl(reason="Auto-allocating stock to build order")
async def auto_allocate_build(
    build_id: int,
    interchangeable: bool = False,
    substitutes: bool = True,
    optional: bool = False,
) -> dict[str, Any]:
    """
    Auto-allocate available stock to a build order.

    Automatically allocates stock to satisfy BOM requirements
    based on available inventory.

    Args:
        build_id: The build order ID (required)
        interchangeable: Allow interchangeable parts
        substitutes: Allow BOM substitutes
        optional: Include optional BOM items

    Returns:
        Allocation result with items allocated

    Example:
        # Auto-allocate with substitutes allowed
        result = await auto_allocate_build(
            build_id=25,
            substitutes=True
        )
    """
    logger.info(f"Auto-allocating stock for build order {build_id}")

    data: dict[str, Any] = {
        "interchangeable": interchangeable,
        "substitutes": substitutes,
        "optional_items": optional,
    }

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", f"/build/{build_id}/auto-allocate/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Auto-allocated stock for build order {build_id}")
            return result

        return {"success": True, "build_id": build_id}

    except Exception as e:
        logger.error(f"Failed to auto-allocate build stock: {e}")
        raise


@ai_function
@require_hitl(reason="Finishing build order")
async def finish_build_order(
    build_id: int,
    accept_incomplete: bool = False,
    accept_unallocated: bool = False,
) -> dict[str, Any]:
    """
    Finish (complete) a build order.

    Marks the build order as complete. All outputs should be
    completed before finishing.

    Args:
        build_id: The build order ID (required)
        accept_incomplete: Accept if not all quantity is built
        accept_unallocated: Accept if stock is not fully consumed

    Returns:
        Updated build order data

    Example:
        # Finish a fully completed build
        result = await finish_build_order(build_id=25)

        # Finish with partial completion
        result = await finish_build_order(
            build_id=25,
            accept_incomplete=True
        )
    """
    logger.info(f"Finishing build order {build_id}")

    data: dict[str, Any] = {}

    if accept_incomplete:
        data["accept_incomplete"] = True
    if accept_unallocated:
        data["accept_unallocated"] = True

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", f"/build/{build_id}/finish/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Finished build order {build_id}")
            return result

        return {"success": True, "build_id": build_id, "status": "Complete"}

    except Exception as e:
        logger.error(f"Failed to finish build order: {e}")
        raise


# Export all build order advanced write tools
BUILD_ORDER_ADVANCED_WRITE_TOOLS = [
    hold_build_order,
    update_build_order,
    unallocate_build_stock,
    auto_allocate_build,
    finish_build_order,
]

__all__ = [
    "BUILD_ORDER_ADVANCED_WRITE_TOOLS",
    "auto_allocate_build",
    "finish_build_order",
    "hold_build_order",
    "unallocate_build_stock",
    "update_build_order",
]
