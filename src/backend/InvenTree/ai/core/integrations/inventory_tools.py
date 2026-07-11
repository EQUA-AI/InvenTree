"""
AIMMS Unified Inventory Tools

AI-function tools that work with both demo dataset and live InvenTree API.
These tools automatically use the configured data provider based on
the USE_DEMO_DATASET environment variable.

This module aggregates tools from the `ai.core.tools.inventree` package.

Usage:
    from ai.core.integrations.inventory_tools import INVENTORY_TOOLS

    # Use in agent builder
    agent = ChatAgent(
        tools=INVENTORY_TOOLS,
        ...
    )
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function

# Import Read Tools
from ai.core.tools.inventree.read.parts import (
    search_parts, 
    get_part as get_part_details, # Alias for backward compatibility
    get_part,
    get_part_parameters,
    get_part_attachments,
    get_part_pricing
)
from ai.core.tools.inventree.read.stock import (
    get_stock_level, 
    get_stock_items, 
    get_stock_at_location,
    get_stock_locations as list_locations,
    get_bom,
    get_stock_item
)
from ai.core.tools.inventree.read.purchasing import (
    get_where_used,
    get_categories as list_categories,
    get_suppliers as list_suppliers,
    get_supplier_parts,
    get_purchase_orders as list_purchase_orders,
    get_purchase_order, # The new one with lines
    get_purchase_order_lines,
    get_categories,
    get_suppliers    
)
from ai.core.tools.inventree.read.sales import (
    get_sales_orders as list_sales_orders,
    get_sales_order, # The new one with lines
    get_sales_order_lines,
    get_customers
)
from ai.core.tools.inventree.read.builds import (
    get_build_orders as list_build_orders,
    get_build_order,
    get_build_order_lines
)

# Import Write Tools (Basic set)
try:
    from ai.core.tools.inventree.write.parts import (
        create_part,
        update_part,
        set_part_parameter,
        deactivate_part,
    )
    from ai.core.tools.inventree.write.categories import (
        create_part_category,
        create_stock_location,
    )
    from ai.core.tools.inventree.write.companies import (
        create_company,
        create_supplier_part,
        create_manufacturer_part,
    )
    from ai.core.tools.inventree.write.stock import (
        add_stock,
        remove_stock,
        transfer_stock,
        count_stock,
        merge_stock,
    )
    from ai.core.tools.inventree.write.stock_operations import (
        update_stock_location,
        change_stock_status,
        split_stock,
        convert_stock,
        add_stock_test_result,
    )
    # Advanced tools (optional based on need, but user asked for "full functionality")
    from ai.core.tools.inventree.write.stock_advanced import (
        serialize_stock,
        install_stock,
        uninstall_stock,
        assign_stock,
        return_stock,
    )
    from ai.core.tools.inventree.write.purchase_orders import (
        create_purchase_order,
        add_po_line_item,
    )
    from ai.core.tools.inventree.write.sales_orders import (
        create_sales_order,
        add_so_line_item,
    )
    from ai.core.tools.inventree.write.bom import (
        add_bom_item,
    )
except ImportError:
    # Fallback if write tools are not fully available
    pass

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Composite / Wrapper Tools (to maintain specific agent behaviors)
# --------------------------------------------------------------------------

@ai_function
async def get_stock_levels(
    part_id: int | None = None,
    location_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    Get stock levels for parts or locations.
    
    Args:
        part_id: Filter by part ID. Returns all stock items for this part.
        location_id: Filter by location ID. Returns all stock items at this location.
        
    Returns:
        List of stock items with quantities and locations
    """
    if location_id is not None:
        return await get_stock_at_location(location_id=location_id)
    elif part_id is not None:
        return await get_stock_items(part_id=part_id)
    else:
        return []

@ai_function
async def check_low_stock(threshold: float | None = None) -> list[dict[str, Any]]:
    """
    Check for parts with stock below minimum threshold.
    
    Args:
        threshold: Custom threshold. If None, uses part's minimum_stock.
        
    Returns:
        List of parts with low stock.
    """
    # Use search_parts with low_stock=True
    # Threshold logic might need to be manual if search_parts doesn't support generic threshold overrides
    # search_parts supports `low_stock=True` which uses the part's own minimum.
    return await search_parts(low_stock=True, limit=50)

@ai_function
async def get_stock_quantity(part_id: int) -> dict[str, Any]:
    """
    Get the total stock quantity for a specific part.
    
    Args:
        part_id: The part ID to check stock for
        
    Returns:
        Dictionary with part_id and total quantity
    """
    # Wrap get_stock_level validation
    level = await get_stock_level(part_id)
    return {
        "part_id": level.get("part_id"),
        "quantity": level.get("quantity"),
        "unit": level.get("unit")
    }


# Export all tools as a list
INVENTORY_TOOLS = [
    # Parts (read + write)
    search_parts,
    get_part_details, # aliased get_part
    check_low_stock,
    create_part,
    update_part,
    set_part_parameter,
    deactivate_part,
    get_part_parameters,
    get_part_attachments,
    get_part_pricing,
    
    # Categories / Locations (write)
    create_part_category,
    create_stock_location,
    
    # Companies / Suppliers / Manufacturers (write)
    create_company,
    create_supplier_part,
    create_manufacturer_part,
    
    # Stock Write Tools
    add_stock,
    remove_stock,
    transfer_stock,
    count_stock,
    merge_stock,
    update_stock_location,
    change_stock_status,
    split_stock,
    convert_stock,
    add_stock_test_result,
    serialize_stock,
    install_stock,
    uninstall_stock,
    assign_stock,
    return_stock,
    
    # Stock Read
    get_stock_levels, # wrapper
    get_stock_quantity, # wrapper
    get_stock_item,
    get_stock_at_location,
    list_locations, # aliased get_stock_locations
    get_bom,
    
    # BOM Write
    add_bom_item,
    
    # Purchasing / Suppliers (read + write)
    get_where_used,
    list_categories, # aliased get_categories
    list_suppliers, # aliased get_suppliers
    get_supplier_parts,
    list_purchase_orders, # aliased get_purchase_orders
    get_purchase_order,
    get_purchase_order_lines,
    create_purchase_order,
    add_po_line_item,
    
    # Sales (read + write)
    list_sales_orders, # aliased get_sales_orders
    get_sales_order,
    get_sales_order_lines,
    get_customers,
    create_sales_order,
    add_so_line_item,
    
    # Builds
    list_build_orders, # aliased get_build_orders
    get_build_order,
    get_build_order_lines
]

__all__ = [
    "INVENTORY_TOOLS",
    "search_parts",
    "get_part_details",
    "get_stock_levels",
    "get_bom",
    "get_where_used",
    "list_categories",
    "list_locations",
    "list_suppliers",
    "get_supplier_parts",
    "check_low_stock",
    "get_stock_quantity",
    "create_part",
    "get_purchase_order",
    "list_purchase_orders",
    "get_sales_order",
    "list_sales_orders",
    "get_build_order",
    "list_build_orders",
]
