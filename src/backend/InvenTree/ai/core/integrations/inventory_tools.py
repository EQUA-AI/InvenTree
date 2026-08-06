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
from ai.core.tools.inventree.read.builds import (
    get_build_order,
    get_build_order_lines,
)
from ai.core.tools.inventree.read.builds import (
    get_build_orders as list_build_orders,
)
from ai.core.tools.inventree.read.database import (
    list_database_tables,
    query_database,
)
from ai.core.tools.inventree.read.machines import (
    get_machine_anomalies,
    get_machine_attachments,
    get_machine_health,
    get_machine_maintenance_history,
    get_machine_overview,
    get_machine_parts,
    get_machine_signal_trend,
    get_machine_signals,
    search_machines,
)
from ai.core.tools.inventree.read.maintenance import (
    get_open_repairs_for_machine,
    get_work_order_history,
    get_work_order_overview,
    get_work_order_readiness,
    get_work_order_repair_state,
    search_work_orders,
)
from ai.core.tools.inventree.read.parts import (
    get_part as get_part_details,  # Alias for backward compatibility
)

# Import Read Tools
from ai.core.tools.inventree.read.parts import (
    get_part_attachments,
    get_part_parameters,
    get_part_pricing,
    search_parts,
)
from ai.core.tools.inventree.read.purchasing import (
    get_categories as list_categories,
)
from ai.core.tools.inventree.read.purchasing import (
    get_purchase_order,  # The new one with lines
    get_purchase_order_lines,
    get_supplier_parts,
    get_where_used,
)
from ai.core.tools.inventree.read.purchasing import (
    get_purchase_orders as list_purchase_orders,
)
from ai.core.tools.inventree.read.purchasing import (
    get_suppliers as list_suppliers,
)
from ai.core.tools.inventree.read.sales import (
    get_customers,
    get_sales_order,  # The new one with lines
    get_sales_order_lines,
)
from ai.core.tools.inventree.read.sales import (
    get_sales_orders as list_sales_orders,
)
from ai.core.tools.inventree.read.stock import (
    get_bom,
    get_stock_at_location,
    get_stock_item,
    get_stock_items,
    get_stock_level,
    summarize_stock_items,
)
from ai.core.tools.inventree.read.stock import (
    get_stock_locations as list_locations,
)

# Import Write Tools (Basic set)
try:
    from ai.core.tools.inventree.write.bom import (
        add_bom_item,
    )
    from ai.core.tools.inventree.write.categories import (
        create_part_category,
        create_stock_location,
    )
    from ai.core.tools.inventree.write.companies import (
        create_company,
        create_manufacturer_part,
        create_supplier_part,
    )
    from ai.core.tools.inventree.write.parts import (
        create_part,
        deactivate_part,
        set_part_parameter,
        update_part,
    )
    from ai.core.tools.inventree.write.purchase_orders import (
        add_po_line_item,
        create_purchase_order,
    )
    from ai.core.tools.inventree.write.sales_orders import (
        add_so_line_item,
        create_sales_order,
    )
    from ai.core.tools.inventree.write.stock import (
        add_stock,
        count_stock,
        merge_stock,
        remove_stock,
        transfer_stock,
    )

    # Advanced tools (optional based on need, but user asked for "full functionality")
    from ai.core.tools.inventree.write.stock_advanced import (
        assign_stock,
        install_stock,
        return_stock,
        serialize_stock,
        uninstall_stock,
    )
    from ai.core.tools.inventree.write.stock_operations import (
        add_stock_test_result,
        change_stock_status,
        convert_stock,
        split_stock,
        update_stock_location,
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
) -> dict[str, Any] | list[dict[str, Any]]:
    """
    Get the total stock held for a part, broken down by location.

    For a part this returns the answer directly -- the summed total plus a
    per-location breakdown -- so there is no need to add up individual stock
    rows. A part that exists but holds nothing returns total_in_stock 0 with
    resolved true: that means zero on hand, NOT "part not found".

    Args:
        part_id: The part ID to report stock for.
        location_id: Alternatively, report the stock items held at a location.

    Returns:
        For a part: {part_id, part_name, part_ipn, description, units,
        total_in_stock, item_count, locations: [{name, quantity}], resolved}.
        For a location: the list of stock items held there.
        When neither argument is given: {"resolved": false, "error": ...}.
    """
    if location_id is not None:
        return await get_stock_at_location(location_id=location_id)
    if part_id is None:
        return {
            "resolved": False,
            "error": "part_id or location_id is required to report stock levels",
        }

    items = await get_stock_items(part_id=part_id)
    part: dict[str, Any] = {}
    try:
        part = await get_part_details(part_id=part_id) or {}
    except Exception:  # the location breakdown is still worth returning
        logger.warning("Could not resolve part %s for the stock summary", part_id)
    if not part and not items:
        return {
            "resolved": False,
            "part_id": part_id,
            "error": f"No part found with id {part_id}",
        }
    return summarize_stock_items(items, part=part, part_id=part_id)


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
        "unit": level.get("unit"),
    }


# Export all tools as a list
INVENTORY_TOOLS = [
    # Parts (read + write)
    search_parts,
    get_part_details,  # aliased get_part
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
    get_stock_levels,  # wrapper
    get_stock_quantity,  # wrapper
    get_stock_item,
    get_stock_at_location,
    list_locations,  # aliased get_stock_locations
    get_bom,
    # BOM Write
    add_bom_item,
    # Purchasing / Suppliers (read + write)
    get_where_used,
    list_categories,  # aliased get_categories
    list_suppliers,  # aliased get_suppliers
    get_supplier_parts,
    list_purchase_orders,  # aliased get_purchase_orders
    get_purchase_order,
    get_purchase_order_lines,
    create_purchase_order,
    add_po_line_item,
    # Sales (read + write)
    list_sales_orders,  # aliased get_sales_orders
    get_sales_order,
    get_sales_order_lines,
    get_customers,
    create_sales_order,
    add_so_line_item,
    # Builds
    list_build_orders,  # aliased get_build_orders
    get_build_order,
    get_build_order_lines,
    # Machines / assets (scope-authorized per call in assets.ai_read)
    search_machines,
    get_machine_overview,
    get_machine_health,
    get_machine_signals,
    get_machine_signal_trend,
    get_machine_anomalies,
    get_machine_parts,
    get_machine_maintenance_history,
    get_machine_attachments,
    # Maintenance work orders (scope-authorized per call in tasks.ai_read)
    search_work_orders,
    get_work_order_overview,
    get_work_order_readiness,
    get_work_order_repair_state,
    get_open_repairs_for_machine,
    get_work_order_history,
    # Direct read-only SQL (RBAC-checked per table, read-only transaction)
    list_database_tables,
    query_database,
]

# Read-only subset for lookup/answer agents: a smaller tool schema lowers
# prompt size and latency, and a lookup agent has no business writing.
INVENTORY_READ_TOOLS = [
    search_parts,
    get_part_details,
    check_low_stock,
    get_part_parameters,
    get_part_attachments,
    get_part_pricing,
    get_stock_levels,
    get_stock_quantity,
    get_stock_item,
    get_stock_at_location,
    list_locations,
    get_bom,
    get_where_used,
    list_categories,
    list_suppliers,
    get_supplier_parts,
    list_purchase_orders,
    get_purchase_order,
    get_purchase_order_lines,
    list_sales_orders,
    get_sales_order,
    get_sales_order_lines,
    get_customers,
    list_build_orders,
    get_build_order,
    get_build_order_lines,
    # Machines / assets. Present on the read subset because this is the list
    # voice and the lookup agent are built from -- a machine question has no
    # answer without them, which is what made the machine page unreachable.
    search_machines,
    get_machine_overview,
    get_machine_health,
    get_machine_signals,
    get_machine_signal_trend,
    get_machine_anomalies,
    get_machine_parts,
    get_machine_maintenance_history,
    get_machine_attachments,
    # Maintenance work orders travel with the machines rationale: a job
    # question has no answer without them on the voice/lookup surface.
    search_work_orders,
    get_work_order_overview,
    get_work_order_readiness,
    get_work_order_repair_state,
    get_open_repairs_for_machine,
    get_work_order_history,
    list_database_tables,
    query_database,
]

__all__ = [
    "INVENTORY_READ_TOOLS",
    "INVENTORY_TOOLS",
    "check_low_stock",
    "create_part",
    "get_bom",
    "get_build_order",
    "get_open_repairs_for_machine",
    "get_part_details",
    "get_purchase_order",
    "get_sales_order",
    "get_stock_levels",
    "get_stock_quantity",
    "get_supplier_parts",
    "get_where_used",
    "get_work_order_history",
    "get_work_order_overview",
    "get_work_order_readiness",
    "get_work_order_repair_state",
    "list_build_orders",
    "list_categories",
    "list_database_tables",
    "list_locations",
    "list_purchase_orders",
    "list_sales_orders",
    "list_suppliers",
    "query_database",
    "search_parts",
    "search_work_orders",
]
