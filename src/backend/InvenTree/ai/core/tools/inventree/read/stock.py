"""
Stock Read Tools

Read-only tools for retrieving stock information from InvenTree.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.integrations.data_provider import get_data_provider
from ai.core.maf_compat import ai_function

logger = logging.getLogger(__name__)


@ai_function
async def get_stock_level(
    part_id: int,
    location_id: int | None = None,
    include_sublocation: bool = True,
) -> dict[str, Any]:
    """
    Get the current stock level for a specific part.

    Returns aggregated stock quantity across all locations or for a specific
    location. Use this to quickly check if a part is in stock without
    retrieving all individual stock items.

    Args:
        part_id: The part ID to check stock for
        location_id: Optional location ID to check stock at a specific location.
                    If None, returns total stock across all locations.
        include_sublocation: If True, include stock from sub-locations when
                           location_id is specified (default True)

    Returns:
        Stock level information including:
        - part_id: The part ID
        - part_name: Part name
        - quantity: Total stock quantity
        - location_id: Location ID if specified, None for all locations
        - location_name: Location name if specified
        - minimum_stock: Minimum stock threshold
        - is_low_stock: True if quantity < minimum_stock
        - stock_items_count: Number of individual stock items
        - unit: Unit of measure

    Example:
        # Check total stock for a part
        stock = await get_stock_level(part_id=42)
        print(f"Total in stock: {stock['quantity']} {stock['unit']}")

        # Check stock at a specific location
        stock = await get_stock_level(part_id=42, location_id=5)
        print(f"Stock at {stock['location_name']}: {stock['quantity']}")
    """
    provider = get_data_provider()

    logger.info(f"Getting stock level for part_id={part_id}, location_id={location_id}")

    # Get part info
    part = await provider.get_part(part_id)
    if not part:
        raise ValueError(f"Part with ID {part_id} not found")

    # Get stock items for this part
    stock_items = await provider.get_stock_items(part_id=part_id)

    # Filter by location if specified
    if location_id is not None:
        if include_sublocation:
            # Get location hierarchy to include sub-locations
            locations = await provider.get_locations()
            location_ids = _get_location_with_children(locations, location_id)
            stock_items = [s for s in stock_items if s.get("location") in location_ids]
        else:
            stock_items = [s for s in stock_items if s.get("location") == location_id]

    # Calculate total quantity
    total_quantity = sum(item.get("quantity", 0) for item in stock_items)
    minimum_stock = part.get("minimum_stock") or 0

    # Get location name if specified
    location_name = None
    if location_id is not None:
        locations = await provider.get_locations()
        for loc in locations:
            if loc.get("pk") == location_id:
                location_name = loc.get("name")
                break

    result = {
        "part_id": part_id,
        "part_name": part.get("name"),
        "quantity": total_quantity,
        "location_id": location_id,
        "location_name": location_name,
        "minimum_stock": minimum_stock,
        "is_low_stock": total_quantity < minimum_stock,
        "stock_items_count": len(stock_items),
        "unit": part.get("units") or "units",
    }

    logger.info(f"Stock level for part {part_id}: {total_quantity}")

    return result


def _get_location_with_children(
    locations: list[dict[str, Any]],
    parent_id: int,
) -> set[int]:
    """Get a location ID and all its child location IDs."""
    result = {parent_id}

    for loc in locations:
        if loc.get("parent") in result:
            result.add(loc.get("pk"))

    # Repeat to catch nested children (simple approach for shallow hierarchies)
    for _ in range(3):
        for loc in locations:
            if loc.get("parent") in result:
                result.add(loc.get("pk"))

    return result


@ai_function
async def get_stock_item(
    stock_id: int,
    include_history: bool = False,
) -> dict[str, Any] | None:
    """
    Get detailed information about a specific stock item.

    A stock item represents a quantity of a specific part at a specific location,
    with optional serial number or batch tracking.

    Args:
        stock_id: The stock item ID to retrieve
        include_history: If True, include stock movement history (transfers,
                        adjustments, etc.)

    Returns:
        Stock item details including:
        - pk: Stock item ID
        - part: Part ID
        - part_name: Part name
        - quantity: Current quantity
        - location: Location ID
        - location_name: Location name
        - serial: Serial number (if trackable)
        - batch: Batch code (if tracked)
        - status: Stock status (in stock, attention needed, etc.)
        - status_text: Human-readable status
        - purchase_order: Associated purchase order if received
        - supplier_part: Supplier part info if from purchase
        - expiry_date: Expiry date if applicable
        - notes: Notes about the stock item
        - link: External reference URL
        - history: List of stock movements (if include_history=True)

        Returns None if stock item not found.

    Example:
        stock = await get_stock_item(stock_id=123)
        print(f"Part: {stock['part_name']}")
        print(f"Quantity: {stock['quantity']} at {stock['location_name']}")
        if stock['serial']:
            print(f"Serial: {stock['serial']}")
    """
    provider = get_data_provider()

    logger.info(f"Getting stock item {stock_id}")

    # Get all stock items and find the one we need
    # Note: The provider may not support direct stock item lookup
    all_stock = await provider.get_stock_items()
    stock_item = next((s for s in all_stock if s.get("pk") == stock_id), None)

    if not stock_item:
        logger.info(f"Stock item {stock_id} not found")
        return None

    # Enrich with part name
    part_id = stock_item.get("part")
    if part_id:
        part = await provider.get_part(part_id)
        if part:
            stock_item["part_name"] = part.get("name")

    # Enrich with location name
    location_id = stock_item.get("location")
    if location_id:
        locations = await provider.get_locations()
        for loc in locations:
            if loc.get("pk") == location_id:
                stock_item["location_name"] = loc.get("name")
                break

    # Add stock status text
    status = stock_item.get("status")
    status_map = {
        10: "OK",
        50: "Attention needed",
        55: "Damaged",
        60: "Destroyed",
        65: "Rejected",
        70: "Lost",
        85: "Returned",
    }
    stock_item["status_text"] = status_map.get(status, "Unknown")

    if include_history:
        # Stock tracking history would require additional API calls
        # For now, return empty list - can be enhanced
        stock_item["history"] = []
        logger.info("Stock history not yet implemented in data provider")

    logger.info(f"Found stock item {stock_id}: {stock_item.get('quantity')} units")

    return stock_item


@ai_function
async def get_stock_locations(
    parent_id: int | None = None,
    include_children: bool = False,
    include_stock_count: bool = True,
) -> list[dict[str, Any]]:
    """
    Get a list of stock locations.

    Stock locations organize where physical inventory is stored. Locations
    can be nested in a hierarchy (warehouse -> aisle -> shelf -> bin).

    Args:
        parent_id: Filter by parent location ID. If None, returns top-level
                  locations (or all locations if include_children=True).
        include_children: If True, recursively include all child locations
        include_stock_count: If True, include count of stock items at each
                           location (default True)

    Returns:
        List of locations, each containing:
        - pk: Location ID
        - name: Location name
        - description: Location description
        - parent: Parent location ID (None for top-level)
        - pathstring: Full path (e.g., "Warehouse A / Shelf 1 / Bin 3")
        - structural: If True, location is structural only (no stock)
        - external: If True, location is external (customer/supplier site)
        - stock_items_count: Number of stock items at this location
        - sublocations_count: Number of child locations
        - icon: Custom icon identifier

    Example:
        # Get all top-level locations
        locations = await get_stock_locations()

        # Get children of a warehouse
        locations = await get_stock_locations(parent_id=1, include_children=True)
    """
    provider = get_data_provider()

    logger.info(f"Getting stock locations, parent_id={parent_id}")

    # Get all locations
    all_locations = await provider.get_locations()

    if parent_id is not None:
        if include_children:
            # Get parent and all descendants
            location_ids = _get_location_with_children(all_locations, parent_id)
            locations = [loc for loc in all_locations if loc.get("pk") in location_ids]
        else:
            # Get direct children only
            locations = [loc for loc in all_locations if loc.get("parent") == parent_id]
    else:
        if not include_children:
            # Top-level only
            locations = [loc for loc in all_locations if loc.get("parent") is None]
        else:
            locations = all_locations

    if include_stock_count:
        # Get stock counts per location
        all_stock = await provider.get_stock_items()
        stock_by_location: dict[int, int] = {}
        for stock in all_stock:
            loc_id = stock.get("location")
            if loc_id:
                stock_by_location[loc_id] = stock_by_location.get(loc_id, 0) + 1

        for loc in locations:
            loc["stock_items_count"] = stock_by_location.get(loc.get("pk"), 0)

        # Count sublocations
        for loc in locations:
            loc["sublocations_count"] = sum(
                1 for sub in all_locations if sub.get("parent") == loc.get("pk")
            )

    logger.info(f"Found {len(locations)} locations")

    return locations


@ai_function
async def get_stock_at_location(
    location_id: int,
    include_sublocation: bool = True,
    in_stock_only: bool = True,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    Get all stock items at a specific location.

    Returns a list of all stock items stored at the specified location,
    optionally including items in sub-locations.

    Args:
        location_id: The location ID to get stock for
        include_sublocation: If True, include stock from all sub-locations
                           (default True)
        in_stock_only: If True, only return items with quantity > 0
                      (default True)
        limit: Maximum number of items to return (default 100)

    Returns:
        List of stock items, each containing:
        - pk: Stock item ID
        - part: Part ID
        - part_name: Part name
        - part_ipn: Part IPN
        - quantity: Stock quantity
        - location: Location ID
        - location_name: Location name
        - serial: Serial number if applicable
        - batch: Batch code if applicable
        - status: Stock status code
        - status_text: Human-readable status

    Example:
        # Get all stock in Warehouse A
        stock = await get_stock_at_location(location_id=1)
        for item in stock:
            print(f"{item['part_name']}: {item['quantity']} units")
    """
    provider = get_data_provider()

    logger.info(f"Getting stock at location {location_id}")

    # Get stock at this location
    stock_items = await provider.get_stock_at_location(location_id)

    if include_sublocation:
        # Get all locations and find children
        all_locations = await provider.get_locations()
        child_ids = _get_location_with_children(all_locations, location_id)
        child_ids.discard(location_id)  # Already have parent's stock

        # Get stock from child locations
        for child_id in child_ids:
            child_stock = await provider.get_stock_at_location(child_id)
            stock_items.extend(child_stock)

    # Filter for in-stock only
    if in_stock_only:
        stock_items = [s for s in stock_items if (s.get("quantity") or 0) > 0]

    # Enrich with part and location names
    parts_cache: dict[int, dict] = {}
    locations = await provider.get_locations()
    location_map = {loc.get("pk"): loc.get("name") for loc in locations}

    status_map = {
        10: "OK",
        50: "Attention needed",
        55: "Damaged",
        60: "Destroyed",
        65: "Rejected",
        70: "Lost",
        85: "Returned",
    }

    for stock in stock_items:
        # Add location name
        stock["location_name"] = location_map.get(stock.get("location"), "Unknown")

        # Add part info
        part_id = stock.get("part")
        if part_id:
            if part_id not in parts_cache:
                part = await provider.get_part(part_id)
                parts_cache[part_id] = part or {}

            part = parts_cache[part_id]
            stock["part_name"] = part.get("name", "Unknown")
            stock["part_ipn"] = part.get("IPN", "")

        # Add status text
        stock["status_text"] = status_map.get(stock.get("status"), "Unknown")

    logger.info(f"Found {len(stock_items)} stock items at location {location_id}")

    return stock_items[:limit]


@ai_function
async def get_stock_items(
    part_id: int,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    Get all stock items for a specific part across all locations.

    Args:
        part_id: The part ID to find stock for
        limit: Maximum number of items to return (default 100)

    Returns:
        List of stock items
    """
    provider = get_data_provider()
    logger.info(f"Getting stock items for part {part_id}")
    return await provider.get_stock_items(part_id=part_id, limit=limit)


def stock_location_label(item: dict[str, Any]) -> str:
    """Human-readable location for one stock row.

    Prefers the full path ("Electronics Lab/Loose Parts") so a spoken answer is
    unambiguous across same-named bins.
    """
    detail = item.get("location_detail") or {}
    label = detail.get("pathstring") or detail.get("name") or item.get("location_name")
    if label:
        return str(label)
    location_id = item.get("location")
    return f"Location {location_id}" if location_id else "Unassigned"


def summarize_stock_items(
    items: list[dict[str, Any]],
    *,
    part: dict[str, Any] | None = None,
    part_id: int | None = None,
) -> dict[str, Any]:
    """Reduce raw stock rows to the answer a stock question actually asks for.

    The InvenTree stock serializer returns ~42 fields per row (barcode hashes,
    installed items, supplier part references...). Handing that to a model as
    the answer to "what is the stock level" costs ~13 KB for eight rows, buries
    the quantities, and -- observed in production -- gets reported as "I
    couldn't find any stock information" for a part holding 8,902 units. So the
    total is computed here and the payload is projected to the fields that
    answer the question.

    ``total_in_stock`` prefers the part record's own figure (InvenTree's
    authority, which accounts for rows beyond ``limit``) and falls back to
    summing the returned rows.
    """
    part = part or {}
    quantities: dict[str, float] = {}
    for item in items:
        try:
            quantity = float(item.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        quantities[stock_location_label(item)] = (
            quantities.get(stock_location_label(item), 0.0) + quantity
        )

    summed = sum(quantities.values())
    reported = part.get("in_stock", part.get("total_in_stock"))
    try:
        total = float(reported) if reported is not None else summed
    except (TypeError, ValueError):
        total = summed

    return {
        "part_id": part.get("pk", part_id),
        "part_name": part.get("name"),
        "part_ipn": part.get("IPN"),
        "description": part.get("description"),
        "units": part.get("units") or "units",
        "total_in_stock": total,
        "item_count": len(items),
        "locations": [
            {"name": name, "quantity": quantity}
            for name, quantity in sorted(quantities.items(), key=lambda pair: -pair[1])
        ],
        # Explicitly distinguishes "this part exists and holds zero" from
        # "no such part" -- an empty list alone reads as not-found.
        "resolved": True,
    }


@ai_function
async def get_bom(
    part_id: int,
    include_inherited: bool = True,
    recursive: bool = False,
    include_stock: bool = True,
) -> dict[str, Any]:
    """
    Get the Bill of Materials (BOM) for an assembly.

    Returns the list of components needed to build the assembly, with optional
    current stock information and recursive expansion.

    Args:
        part_id: The assembly part ID
        include_inherited: Include BOM items inherited from parent templates
                         (default True)
        recursive: If True, recursively expand sub-assemblies to show all
                  components at all levels (default False)
        include_stock: Include current stock levels for each component
                      (default True)

    Returns:
        BOM information including:
        - part_id: The assembly part ID
        - part_name: Assembly name
        - is_active: Whether the BOM is active
        - items: List of BOM items, each containing:
            - pk: BOM item ID
            - sub_part: Component part ID
            - sub_part_name: Component name
            - sub_part_ipn: Component IPN
            - quantity: Required quantity
            - reference: Reference designators (e.g., "C1, C2, C3")
            - optional: Whether the component is optional
            - consumable: Whether it's consumed during build
            - allow_variants: Whether variants can be substituted
            - inherited: Whether inherited from template
            - validated: Whether validated for production
            - note: Notes about the BOM line
            - in_stock: Current stock (if include_stock=True)
            - can_build: Quantity that can be built (if include_stock=True)
            - sub_bom: Nested BOM items (if recursive=True and component is assembly)
        - total_items: Total number of unique components
        - buildable_quantity: How many assemblies can be built with current stock

    Example:
        # Get flat BOM with stock levels
        bom = await get_bom(part_id=100)
        print(f"Assembly: {bom['part_name']}")
        print(f"Can build: {bom['buildable_quantity']}")
        for item in bom['items']:
            print(f"  {item['sub_part_name']} x{item['quantity']} (stock: {item['in_stock']})")
    """
    provider = get_data_provider()

    logger.info(f"Getting BOM for part {part_id}, recursive={recursive}")

    # Get part info
    part = await provider.get_part(part_id)
    if not part:
        raise ValueError(f"Part with ID {part_id} not found")

    if not part.get("assembly"):
        return {
            "part_id": part_id,
            "part_name": part.get("name"),
            "is_active": True,
            "items": [],
            "total_items": 0,
            "buildable_quantity": None,
            "message": "This part is not an assembly and has no BOM",
        }

    # Get BOM items
    bom_items = await provider.get_bom_items(part_id)

    # Filter inherited if requested
    if not include_inherited:
        bom_items = [b for b in bom_items if not b.get("inherited", False)]

    # Track minimum buildable quantity
    min_buildable: float | None = None

    # Enrich each BOM item
    for item in bom_items:
        sub_part_id = item.get("sub_part")
        if sub_part_id:
            sub_part = await provider.get_part(sub_part_id)
            if sub_part:
                item["sub_part_name"] = sub_part.get("name")
                item["sub_part_ipn"] = sub_part.get("IPN")

                if include_stock:
                    stock_qty = await provider.get_stock_quantity(sub_part_id)
                    item["in_stock"] = stock_qty

                    # Calculate how many assemblies can be built with this component
                    required_qty = item.get("quantity") or 1
                    if required_qty > 0 and not item.get("optional", False):
                        can_build = int(stock_qty / required_qty)
                        item["can_build"] = can_build
                        if min_buildable is None or can_build < min_buildable:
                            min_buildable = can_build

                # Recursive expansion
                if recursive and sub_part.get("assembly"):
                    sub_bom = await get_bom(
                        part_id=sub_part_id,
                        include_inherited=include_inherited,
                        recursive=True,
                        include_stock=include_stock,
                    )
                    item["sub_bom"] = sub_bom.get("items", [])

    result = {
        "part_id": part_id,
        "part_name": part.get("name"),
        "is_active": True,
        "items": bom_items,
        "total_items": len(bom_items),
        "buildable_quantity": int(min_buildable) if min_buildable is not None else None,
    }

    logger.info(f"BOM for part {part_id}: {len(bom_items)} items")

    return result


@ai_function
async def get_stock_history(
    stock_id: int,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """
    Get the history of a stock item.

    Returns a list of tracking events for a specific stock item, showing
    creation, movements, quantity changes, and other events.

    Args:
        stock_id: The stock item ID to get history for
        limit: Maximum number of events to return (default 50)
        offset: Pagination offset

    Returns:
        List of tracking events, each containing:
        - pk: Tracking entry ID
        - date: Event timestamp
        - label: Event title/label
        - notes: Event description/notes
        - tracking_type: Type of tracking event
        - user: User ID who performed the action
        - user_detail: Detailed user info

    Example:
        # Get history for a stock item
        history = await get_stock_history(stock_id=123)
        for event in history:
            print(f"{event['date']}: {event['label']}")
    """
    logger.info(f"Getting history for stock item {stock_id}")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        # InvenTree stores history in the 'stock/track' endpoint
        # Filter by the 'item' field (which is the stock_id)
        events = await client._request(
            "GET",
            "/stock/track/",
            params={
                "item": stock_id,
                "limit": limit,
                "offset": offset,
                "ordering": "-date",  # Newest first
            },
        )

        # The API returns a pagination object {count, next, previous, results}
        if isinstance(events, dict) and "results" in events:
            return events["results"]
        elif isinstance(events, list):
            return events
        else:
            return []

    except Exception as e:
        logger.error(f"Failed to get stock history: {e}")
        return []


# Export all read tools for stock
STOCK_READ_TOOLS = [
    get_stock_level,
    get_stock_item,
    get_stock_locations,
    get_stock_at_location,
    get_bom,
    get_stock_history,
]

__all__ = [
    "STOCK_READ_TOOLS",
    "get_bom",
    "get_stock_at_location",
    "get_stock_history",
    "get_stock_item",
    "get_stock_level",
    "get_stock_locations",
]
