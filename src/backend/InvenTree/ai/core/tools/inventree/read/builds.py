"""
Build Order Read Tools

Read-only tools for retrieving build order information from InvenTree.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function
from ai.core.integrations.data_provider import get_data_provider

logger = logging.getLogger(__name__)


@ai_function
async def get_build_orders(
    part_id: int | None = None,
    status: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Get build orders.
    
    Args:
        part_id: Filter by part ID being built
        status: Filter by status code (10: Pending, 20: In Production, 40: Complete, 50: Cancelled)
        limit: Maximum orders to return
        
    Returns:
        List of build orders
    """
    logger.info(f"Getting build orders, part={part_id}, status={status}")
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        orders = await client.list_build_orders(part_id=part_id, status=status, limit=limit)
        
        # Enrich with status text if not present
        status_map = {
            10: "Pending",
            20: "In Production",
            30: "On Hold",
            40: "Complete",
            50: "Cancelled",
        }
        for order in orders:
            if "status_text" not in order:
                order["status_text"] = status_map.get(order.get("status"), "Unknown")
        
        return orders
    except Exception as e:
        logger.error(f"Error getting build orders: {e}")
        return []


@ai_function
async def get_build_order_lines(
    order_id: int,
) -> list[dict[str, Any]]:
    """
    Get the line items (allocations) for a build order.
    These are the parts consumed to build the order.
    
    Args:
        order_id: The build order ID
        
    Returns:
        List of build order allocations
    """
    logger.info(f"Getting lines for build order {order_id}")
    provider = get_data_provider()
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        lines = await client.get_build_order_allocations(order_id)
        
        # Enrich with part names if needed
        for line in lines:
            part_id = line.get("part") or line.get("sub_part") # Allocation might have 'part' as the stock item's part
            # Actually allocations usually point to a stock item or a bom item.
            # Let's try to find part ID.
            if not part_id and "stock_item_detail" in line:
                 part_id = line["stock_item_detail"].get("part")
            
            if part_id and "part_name" not in line:
                part = await provider.get_part(part_id)
                if part:
                    line["part_name"] = part.get("name")
                    line["part_ipn"] = part.get("IPN")
        
        return lines
    except Exception as e:
        logger.error(f"Error getting BO lines: {e}")
        return []


@ai_function
async def get_build_order(
    order_id: int,
) -> dict[str, Any]:
    """
    Get detailed information about a build order, including line items (allocations).
    
    Args:
        order_id: The build order ID
        
    Returns:
        Dictionary containing build order details and 'lines' (allocations).
    """
    logger.info(f"Getting build order {order_id}")
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        order = await client.get_build_order(order_id)
        if not order:
            return {"error": "Build Order not found"}
            
        lines = await get_build_order_lines(order_id)
        order["lines"] = lines
        return order
        
    except Exception as e:
        logger.error(f"Error getting build order {order_id}: {e}")
        return {"error": str(e)}


BUILD_READ_TOOLS = [
    get_build_orders,
    get_build_order,
    get_build_order_lines,
]

__all__ = [
    "get_build_orders",
    "get_build_order",
    "get_build_order_lines",
    "BUILD_READ_TOOLS",
]
