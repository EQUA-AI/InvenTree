"""
Purchasing Read Tools

Read-only tools for retrieving purchasing and supplier information from InvenTree.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.integrations.data_provider import get_data_provider
from ai.core.maf_compat import ai_function

logger = logging.getLogger(__name__)


@ai_function
async def get_where_used(
    part_id: int,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    """
    Get all assemblies where a part is used as a component.

    This is the reverse lookup of a BOM - it shows which parent assemblies
    require this part. Useful for impact analysis when a part is discontinued
    or modified.

    Args:
        part_id: The part ID to find usages for
        include_inactive: Include inactive assemblies (default False)

    Returns:
        List of BOM entries where this part is used, each containing:
        - pk: BOM item ID
        - part: Parent assembly part ID
        - part_name: Parent assembly name
        - part_ipn: Parent assembly IPN
        - quantity: Quantity required per assembly
        - reference: Reference designators
        - optional: Whether the component is optional
        - note: Notes about usage
        - inherited: Whether inherited from template

    Example:
        # Find all products using a capacitor
        usages = await get_where_used(part_id=42)
        print(f"Part is used in {len(usages)} assemblies:")
        for u in usages:
            print(f"  {u['part_name']} requires {u['quantity']} units")
    """
    provider = get_data_provider()

    logger.info(f"Getting where-used for part {part_id}")

    # Get BOM items where this part is a sub-part
    bom_items = await provider.get_where_used(part_id)

    # Enrich with parent part info
    for item in bom_items:
        parent_id = item.get("part")
        if parent_id:
            parent_part = await provider.get_part(parent_id)
            if parent_part:
                item["part_name"] = parent_part.get("name")
                item["part_ipn"] = parent_part.get("IPN")
                item["part_active"] = parent_part.get("active", True)

    # Filter inactive if requested
    if not include_inactive:
        bom_items = [b for b in bom_items if b.get("part_active", True)]

    logger.info(f"Part {part_id} is used in {len(bom_items)} assemblies")

    return bom_items


@ai_function
async def get_categories(
    parent_id: int | None = None,
    include_children: bool = False,
    include_part_count: bool = True,
) -> list[dict[str, Any]]:
    """
    Get part categories for organizing inventory.

    Categories form a hierarchical structure for organizing parts
    (e.g., Electronics > Capacitors > Ceramic Capacitors).

    Args:
        parent_id: Filter by parent category ID. If None, returns top-level
                  categories (or all if include_children=True).
        include_children: If True, recursively include all child categories
        include_part_count: If True, include count of parts in each category
                          (default True)

    Returns:
        List of categories, each containing:
        - pk: Category ID
        - name: Category name
        - description: Category description
        - parent: Parent category ID (None for top-level)
        - pathstring: Full path (e.g., "Electronics/Capacitors/Ceramic")
        - level: Depth in hierarchy (0 for top-level)
        - structural: If True, category is structural only (no direct parts)
        - icon: Custom icon identifier
        - part_count: Number of parts in this category (if include_part_count)
        - subcategories_count: Number of direct child categories

    Example:
        # Get all top-level categories
        cats = await get_categories()

        # Get children of Electronics category
        cats = await get_categories(parent_id=5, include_children=True)
    """
    provider = get_data_provider()

    logger.info(f"Getting categories, parent_id={parent_id}")

    # Get all categories
    all_categories = await provider.get_categories()

    if parent_id is not None:
        if include_children:
            # Get parent and all descendants
            category_ids = _get_category_with_children(all_categories, parent_id)
            categories = [c for c in all_categories if c.get("pk") in category_ids]
        else:
            # Get direct children only
            categories = [c for c in all_categories if c.get("parent") == parent_id]
    else:
        if not include_children:
            # Top-level only
            categories = [c for c in all_categories if c.get("parent") is None]
        else:
            categories = all_categories

    if include_part_count:
        # Get parts to count
        all_parts = await provider.search_parts(limit=1000)
        parts_by_category: dict[int, int] = {}
        for part in all_parts:
            cat_id = part.get("category")
            if cat_id:
                parts_by_category[cat_id] = parts_by_category.get(cat_id, 0) + 1

        for cat in categories:
            cat["part_count"] = parts_by_category.get(cat.get("pk"), 0)

        # Count subcategories
        for cat in categories:
            cat["subcategories_count"] = sum(
                1 for c in all_categories if c.get("parent") == cat.get("pk")
            )

    logger.info(f"Found {len(categories)} categories")

    return categories


def _get_category_with_children(
    categories: list[dict[str, Any]],
    parent_id: int,
) -> set[int]:
    """Get a category ID and all its child category IDs."""
    result = {parent_id}

    # Repeat to catch nested children
    for _ in range(5):
        for cat in categories:
            if cat.get("parent") in result:
                result.add(cat.get("pk"))

    return result


@ai_function
async def get_suppliers(
    active_only: bool = True,
    has_parts: bool | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    Get a list of suppliers (companies that supply parts).

    Suppliers are companies from which parts can be purchased.
    Each supplier can have multiple supplier parts with pricing info.

    Args:
        active_only: Only return active suppliers (default True)
        has_parts: If True, only return suppliers with linked parts.
                  If False, only suppliers without parts. If None, all.
        limit: Maximum number of suppliers to return (default 100)

    Returns:
        List of suppliers, each containing:
        - pk: Supplier ID (company ID)
        - name: Company name
        - description: Company description
        - website: Company website URL
        - phone: Contact phone number
        - email: Contact email
        - address: Physical address
        - currency: Default currency for this supplier
        - is_active: Whether supplier is active
        - parts_count: Number of supplier parts linked
        - notes: Notes about the supplier

    Example:
        # Get all active suppliers
        suppliers = await get_suppliers()

        # Find suppliers with linked parts
        suppliers = await get_suppliers(has_parts=True)
    """
    provider = get_data_provider()

    logger.info(f"Getting suppliers, active_only={active_only}")

    suppliers = await provider.get_suppliers()

    if active_only:
        suppliers = [s for s in suppliers if s.get("active", True)]

    # Get parts count for each supplier
    for supplier in suppliers:
        supplier_id = supplier.get("pk")
        if supplier_id:
            try:
                parts = await provider.get_supplier_parts(supplier_id)
                supplier["parts_count"] = len(parts) if parts else 0
            except Exception:
                supplier["parts_count"] = 0

    if has_parts is not None:
        if has_parts:
            suppliers = [s for s in suppliers if s.get("parts_count", 0) > 0]
        else:
            suppliers = [s for s in suppliers if s.get("parts_count", 0) == 0]

    logger.info(f"Found {len(suppliers)} suppliers")

    return suppliers[:limit]


@ai_function
async def get_supplier_parts(
    part_id: int | None = None,
    supplier_id: int | None = None,
    include_pricing: bool = True,
) -> list[dict[str, Any]]:
    """
    Get supplier parts (parts available from suppliers with pricing).

    Supplier parts link internal parts to supplier SKUs with pricing,
    lead times, and ordering information.

    Args:
        part_id: Filter by internal part ID (get all suppliers for a part)
        supplier_id: Filter by supplier ID (get all parts from a supplier)
        include_pricing: Include detailed pricing info (default True)

    Returns:
        List of supplier parts, each containing:
        - pk: Supplier part ID
        - part: Internal part ID
        - part_name: Internal part name
        - part_ipn: Internal part IPN
        - supplier: Supplier company ID
        - supplier_name: Supplier company name
        - SKU: Supplier's part number/SKU
        - manufacturer: Manufacturer name
        - MPN: Manufacturer Part Number
        - description: Supplier's description
        - link: URL to supplier's product page
        - note: Notes
        - pack_quantity: Units per pack
        - available: Quantity available at supplier

        If include_pricing=True, also includes:
        - price: Unit price
        - price_currency: Currency code
        - effective_price: Price per unit (price / pack_quantity)
        - price_breaks: List of quantity price breaks
        - lead_time: Lead time in days

    Example:
        # Get all suppliers for a part
        suppliers = await get_supplier_parts(part_id=42)
        for sp in suppliers:
            print(f"{sp['supplier_name']}: {sp['SKU']} @ {sp['price']} {sp['price_currency']}")

        # Get all parts from a supplier
        parts = await get_supplier_parts(supplier_id=5)
    """
    provider = get_data_provider()

    logger.info(f"Getting supplier parts, part_id={part_id}, supplier_id={supplier_id}")

    if part_id is not None:
        supplier_parts = await provider.get_supplier_parts(part_id)
    elif supplier_id is not None:
        # Get all parts and filter by supplier
        # Note: This may need optimization for large datasets
        all_parts = await provider.search_parts(limit=500)
        supplier_parts = []
        for part in all_parts:
            pid = part.get("pk")
            if pid:
                sp_list = await provider.get_supplier_parts(pid)
                for sp in sp_list:
                    if sp.get("supplier") == supplier_id:
                        supplier_parts.append(sp)
    else:
        raise ValueError("Either part_id or supplier_id must be provided")

    # Enrich with part and supplier names
    for sp in supplier_parts:
        # Add part info
        pid = sp.get("part")
        if pid and not sp.get("part_name"):
            part = await provider.get_part(pid)
            if part:
                sp["part_name"] = part.get("name")
                sp["part_ipn"] = part.get("IPN")

        # Calculate effective price
        if include_pricing:
            pack_qty = sp.get("pack_quantity") or 1
            price = sp.get("price") or 0
            sp["effective_price"] = price / pack_qty if pack_qty > 0 else price

    logger.info(f"Found {len(supplier_parts)} supplier parts")

    return supplier_parts


@ai_function
async def get_purchase_orders(
    supplier_id: int | None = None,
    status: str | None = None,
    reference: str | None = None,
    outstanding: bool | None = None,
    overdue: bool | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Get purchase orders for tracking supplier orders.

    Purchase orders track orders placed with suppliers, including
    line items, receiving, and status.

    IMPORTANT: This returns order SUMMARIES. To get detailed Line Items (parts, prices),
    you must call `get_purchase_order(order_id)` with the order's ID.

    Args:
        supplier_id: Filter by supplier ID
        status: Filter by status. Options: "pending", "issued", "complete", "cancelled"
        reference: Filter by exact reference code (e.g. "PO0001")
        outstanding: If True, only orders with unreceived items
        overdue: If True, only orders past target date with outstanding items
        limit: Maximum orders to return (default 50)

    Returns:
        List of purchase order summaries.
    """
    logger.info(f"Getting purchase orders, supplier={supplier_id}, status={status}")

    # Map status string to code
    status_map = {
        "pending": 10,
        "placed": 20,
        "issued": 20,
        "complete": 30,
        "cancelled": 40,
    }

    status_code = status_map.get(status.lower()) if status else None

    # Get purchase orders from provider
    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()
        orders = await client.list_purchase_orders(
            supplier_id=supplier_id,
            status=status_code,
            limit=limit,
        )

        # Filter by reference if provided (client side)
        if reference:
            orders = [o for o in orders if o.get("reference") == reference]

    except Exception as e:
        logger.warning(f"Could not get purchase orders: {e}")
        orders = []

    # Add status text and filter
    status_text_map = {
        10: "Pending",
        20: "Issued",
        30: "Complete",
        40: "Cancelled",
    }

    from datetime import datetime

    today = datetime.now().date()

    for order in orders:
        order["status_text"] = status_text_map.get(order.get("status"), "Unknown")

        # Calculate overdue status
        target_date = order.get("target_date")
        if target_date and isinstance(target_date, str):
            try:
                target = datetime.fromisoformat(target_date.replace("Z", "+00:00")).date()
                order["is_overdue"] = target < today and order.get("status") == 20
            except ValueError:
                order["is_overdue"] = False
        else:
            order["is_overdue"] = False

    # Apply filters
    if outstanding is not None:
        if outstanding:
            orders = [o for o in orders if o.get("status") in (10, 20)]
        else:
            orders = [o for o in orders if o.get("status") == 30]

    if overdue:
        orders = [o for o in orders if o.get("is_overdue")]

    logger.info(f"Found {len(orders)} purchase orders")

    return orders[:limit]


@ai_function
async def get_purchase_order_lines(
    order_id: int,
) -> list[dict[str, Any]]:
    """
    Get line items for a purchase order.

    Line items specify the parts and quantities ordered.

    Args:
        order_id: The ID of the purchase order

    Returns:
        List of line items
    """
    logger.info(f"Getting lines for purchase order {order_id}")
    provider = get_data_provider()
    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()
        lines = await client.get_purchase_order_lines(order_id)

        # Enrich with part names
        for line in lines:
            part_id = line.get("part")
            if part_id:
                part = await provider.get_part(part_id)
                if part:
                    line["part_name"] = part.get("name")
                    line["part_ipn"] = part.get("IPN")

        return lines
    except Exception as e:
        logger.error(f"Error getting PO lines: {e}")
        return []


@ai_function
async def get_purchase_order(
    order_id: int,
) -> dict[str, Any]:
    """
    Get detailed information about a purchase order, including line items.

    Args:
        order_id: The ID of the purchase order

    Returns:
        Dictionary containing order details and a 'lines' key with the line items.
    """
    logger.info(f"Getting purchase order {order_id}")
    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        # Fetch header directly by primary key
        order = await client._request("GET", f"/order/po/{order_id}/")

        if not order:
            return {"error": "Order not found"}

        lines = await client.get_purchase_order_lines(order_id)
        order["lines"] = lines
        return order

    except Exception as e:
        logger.error(f"Error getting purchase order {order_id}: {e}")
        return {"error": str(e)}


# Export all read tools for purchasing
PURCHASING_READ_TOOLS = [
    get_where_used,
    get_categories,
    get_suppliers,
    get_supplier_parts,
    get_purchase_orders,
    get_purchase_order,
    get_purchase_order_lines,
]

__all__ = [
    "PURCHASING_READ_TOOLS",
    "get_categories",
    "get_purchase_order",
    "get_purchase_order_lines",
    "get_purchase_orders",
    "get_supplier_parts",
    "get_suppliers",
    "get_where_used",
]
