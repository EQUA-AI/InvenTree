"""
InvenTree AI-Function Tools

@ai_function decorated tools for MAF agents to interact with InvenTree.
Each tool includes proper docstrings for LLM understanding.
"""

from typing import Any

import structlog
from ai.core.integrations.inventree.client import (
    BusinessRuleError,
    TransientError,
    ValidationError,
    get_inventree_client,
)

logger = structlog.get_logger(__name__)


# Note: @ai_function decorator will be added when MAF SDK is available
# For now, these are regular async functions with comprehensive docstrings


async def search_parts(
    query: str | None = None,
    category: int | None = None,
    ipn: str | None = None,
    active: bool = True,
    limit: int = 25,
) -> dict[str, Any]:
    """
    Search for parts in the InvenTree inventory.

    Use this tool when you need to find parts by name, description, IPN,
    or category. Returns a list of matching parts with their details.

    Args:
        query: Search text to match against part name, description, or keywords.
               Example: "capacitor 100uf" or "resistor 10k"
        category: Filter by category ID. Use list_categories first to find IDs.
        ipn: Filter by exact Internal Part Number (IPN).
             Example: "CAP-100UF-16V" or "RES-10K-0805"
        active: If True (default), only return active parts.
        limit: Maximum number of results to return (default 25, max 100).

    Returns:
        A dictionary containing:
        - success: bool - Whether the search succeeded
        - parts: list - List of matching parts with fields:
            - pk: Part ID
            - name: Part name
            - IPN: Internal Part Number
            - description: Part description
            - category: Category ID
            - in_stock: Total quantity in stock
            - minimum_stock: Minimum stock level
            - active: Whether part is active
        - count: int - Number of results returned
        - error: str - Error message if search failed

    Example:
        >>> result = await search_parts(query="capacitor 100uf", limit=10)
        >>> if result["success"]:
        >>>     for part in result["parts"]:
        >>>         print(f"{part['IPN']}: {part['name']} - {part['in_stock']} in stock")
    """
    try:
        client = get_inventree_client()
        parts = await client.search_parts(
            query=query,
            category=category,
            ipn=ipn,
            active=active,
            limit=limit,
        )

        # Simplify response for LLM consumption
        simplified_parts = [
            {
                "pk": p.get("pk"),
                "name": p.get("name"),
                "IPN": p.get("IPN"),
                "description": p.get("description"),
                "category": p.get("category"),
                "in_stock": p.get("in_stock", 0),
                "minimum_stock": p.get("minimum_stock", 0),
                "active": p.get("active", True),
            }
            for p in parts
        ]

        logger.info(
            "Parts search completed",
            query=query,
            result_count=len(simplified_parts),
        )

        return {
            "success": True,
            "parts": simplified_parts,
            "count": len(simplified_parts),
        }

    except (TransientError, ValidationError, BusinessRuleError) as e:
        logger.error("Parts search failed", error=str(e), error_type=e.error_type)
        return {
            "success": False,
            "parts": [],
            "count": 0,
            "error": str(e),
            "error_type": e.error_type,
        }
    except Exception as e:
        logger.exception("Unexpected error in parts search")
        return {
            "success": False,
            "parts": [],
            "count": 0,
            "error": f"Unexpected error: {e}",
            "error_type": "UNKNOWN",
        }


async def get_part_details(
    part_id: int | None = None,
    ipn: str | None = None,
) -> dict[str, Any]:
    """
    Get detailed information about a specific part.

    Use this tool when you need complete details about a single part,
    including stock levels, pricing, and supplier information.
    Provide either part_id OR ipn, not both.

    Args:
        part_id: The numeric ID of the part (from search results).
        ipn: The Internal Part Number (if you know it exactly).

    Returns:
        A dictionary containing:
        - success: bool - Whether the lookup succeeded
        - part: dict - Part details including:
            - pk: Part ID
            - name: Part name
            - IPN: Internal Part Number
            - description: Full description
            - category: Category ID and name
            - in_stock: Total quantity in stock
            - minimum_stock: Minimum stock level
            - units: Unit of measure
            - purchaseable: Whether it can be purchased
            - assembly: Whether it's an assembly (has BOM)
            - active: Whether part is active
        - error: str - Error message if lookup failed

    Example:
        >>> result = await get_part_details(ipn="CAP-100UF-16V")
        >>> if result["success"]:
        >>>     print(f"Stock level: {result['part']['in_stock']}")
    """
    if not part_id and not ipn:
        return {
            "success": False,
            "part": None,
            "error": "Must provide either part_id or ipn",
            "error_type": "VALIDATION",
        }

    try:
        client = get_inventree_client()

        if ipn:
            part = await client.get_part_by_ipn(ipn)
        else:
            part = await client.get_part(part_id)

        if part is None:
            return {
                "success": False,
                "part": None,
                "error": f"Part not found: {ipn or part_id}",
                "error_type": "BUSINESS_RULE",
            }

        logger.info("Part details retrieved", part_id=part.get("pk"))

        return {
            "success": True,
            "part": part,
        }

    except (TransientError, ValidationError, BusinessRuleError) as e:
        logger.error("Part lookup failed", error=str(e))
        return {
            "success": False,
            "part": None,
            "error": str(e),
            "error_type": e.error_type,
        }
    except Exception as e:
        logger.exception("Unexpected error in part lookup")
        return {
            "success": False,
            "part": None,
            "error": f"Unexpected error: {e}",
            "error_type": "UNKNOWN",
        }


async def get_stock_levels(
    part_id: int,
    location: int | None = None,
) -> dict[str, Any]:
    """
    Get stock levels for a part across all or specific locations.

    Use this tool to check how much stock is available for a part,
    where it's located, and batch/serial information.

    Args:
        part_id: The numeric ID of the part to check stock for.
        location: Optional location ID to filter by specific location.

    Returns:
        A dictionary containing:
        - success: bool - Whether the query succeeded
        - stock_items: list - List of stock items:
            - pk: Stock item ID
            - quantity: Quantity at this location
            - location: Location ID and name
            - batch: Batch code if tracked
            - serial: Serial number if tracked
            - status: Stock status (good, damaged, etc.)
        - total_quantity: float - Total quantity across all locations
        - location_count: int - Number of distinct locations
        - error: str - Error message if query failed

    Example:
        >>> result = await get_stock_levels(part_id=42)
        >>> print(f"Total in stock: {result['total_quantity']}")
        >>> for item in result["stock_items"]:
        >>>     print(f"  {item['location']}: {item['quantity']}")
    """
    try:
        client = get_inventree_client()
        stock_items = await client.get_stock(
            part_id=part_id,
            location=location,
            in_stock=True,
        )

        total_quantity = sum(item.get("quantity", 0) for item in stock_items)
        locations = {item.get("location") for item in stock_items}

        simplified_items = [
            {
                "pk": item.get("pk"),
                "quantity": item.get("quantity", 0),
                "location": item.get("location"),
                "location_name": item.get("location_detail", {}).get("name", "Unknown"),
                "batch": item.get("batch"),
                "serial": item.get("serial"),
                "status": item.get("status_text", "OK"),
            }
            for item in stock_items
        ]

        logger.info(
            "Stock levels retrieved",
            part_id=part_id,
            total_quantity=total_quantity,
            location_count=len(locations),
        )

        return {
            "success": True,
            "stock_items": simplified_items,
            "total_quantity": total_quantity,
            "location_count": len(locations),
        }

    except (TransientError, ValidationError, BusinessRuleError) as e:
        logger.error("Stock query failed", error=str(e))
        return {
            "success": False,
            "stock_items": [],
            "total_quantity": 0,
            "location_count": 0,
            "error": str(e),
            "error_type": e.error_type,
        }
    except Exception as e:
        logger.exception("Unexpected error in stock query")
        return {
            "success": False,
            "stock_items": [],
            "total_quantity": 0,
            "location_count": 0,
            "error": f"Unexpected error: {e}",
            "error_type": "UNKNOWN",
        }


async def get_bom(
    part_id: int,
    include_sub_assemblies: bool = True,
) -> dict[str, Any]:
    """
    Get the Bill of Materials (BOM) for an assembly part.

    Use this tool to see what components are needed to build an assembly,
    including quantities, references, and whether sub-components have stock.

    Args:
        part_id: The numeric ID of the assembly part.
        include_sub_assemblies: If True, include inherited BOM items from
                                sub-assemblies (default True).

    Returns:
        A dictionary containing:
        - success: bool - Whether the query succeeded
        - is_assembly: bool - Whether the part is an assembly
        - bom_items: list - List of BOM items:
            - pk: BOM item ID
            - sub_part: Sub-part ID and name
            - quantity: Quantity needed per unit
            - reference: Reference designators (e.g., "C1,C2,C3")
            - optional: Whether the item is optional
            - consumable: Whether it's a consumable
            - available: Current stock of the sub-part
        - total_items: int - Total number of unique items
        - items_in_stock: int - Items with sufficient stock
        - items_short: int - Items with insufficient stock
        - error: str - Error message if query failed

    Example:
        >>> result = await get_bom(part_id=100)
        >>> if result["success"]:
        >>>     print(f"BOM has {result['total_items']} items")
        >>>     if result["items_short"] > 0:
        >>>         print(f"Warning: {result['items_short']} items are short")
    """
    try:
        client = get_inventree_client()

        # First check if it's an assembly
        part = await client.get_part(part_id)
        if part is None:
            return {
                "success": False,
                "is_assembly": False,
                "bom_items": [],
                "error": f"Part not found: {part_id}",
                "error_type": "BUSINESS_RULE",
            }

        if not part.get("assembly", False):
            return {
                "success": True,
                "is_assembly": False,
                "bom_items": [],
                "total_items": 0,
                "message": "This part is not an assembly and has no BOM",
            }

        bom_items = await client.get_bom(
            part_id=part_id,
            include_inherited=include_sub_assemblies,
        )

        items_in_stock = 0
        items_short = 0

        simplified_items = []
        for item in bom_items:
            sub_part = item.get("sub_part_detail", {})
            quantity_needed = item.get("quantity", 0)
            available = sub_part.get("in_stock", 0)

            is_sufficient = available >= quantity_needed
            if is_sufficient:
                items_in_stock += 1
            else:
                items_short += 1

            simplified_items.append({
                "pk": item.get("pk"),
                "sub_part_id": item.get("sub_part"),
                "sub_part_name": sub_part.get("name"),
                "sub_part_ipn": sub_part.get("IPN"),
                "quantity": quantity_needed,
                "reference": item.get("reference", ""),
                "optional": item.get("optional", False),
                "consumable": item.get("consumable", False),
                "available": available,
                "sufficient_stock": is_sufficient,
            })

        logger.info(
            "BOM retrieved",
            part_id=part_id,
            total_items=len(simplified_items),
            items_short=items_short,
        )

        return {
            "success": True,
            "is_assembly": True,
            "bom_items": simplified_items,
            "total_items": len(simplified_items),
            "items_in_stock": items_in_stock,
            "items_short": items_short,
        }

    except (TransientError, ValidationError, BusinessRuleError) as e:
        logger.error("BOM query failed", error=str(e))
        return {
            "success": False,
            "is_assembly": False,
            "bom_items": [],
            "error": str(e),
            "error_type": e.error_type,
        }
    except Exception as e:
        logger.exception("Unexpected error in BOM query")
        return {
            "success": False,
            "is_assembly": False,
            "bom_items": [],
            "error": f"Unexpected error: {e}",
            "error_type": "UNKNOWN",
        }


async def list_categories(
    parent: int | None = None,
) -> dict[str, Any]:
    """
    List part categories in the inventory.

    Use this tool to explore the category hierarchy and find category IDs
    for filtering parts.

    Args:
        parent: Optional parent category ID to list only subcategories.
                If None, lists top-level categories.

    Returns:
        A dictionary containing:
        - success: bool - Whether the query succeeded
        - categories: list - List of categories:
            - pk: Category ID
            - name: Category name
            - description: Category description
            - parent: Parent category ID (null for top-level)
            - part_count: Number of parts in this category
        - count: int - Number of categories returned
        - error: str - Error message if query failed

    Example:
        >>> result = await list_categories()  # Get top-level
        >>> for cat in result["categories"]:
        >>>     print(f"{cat['pk']}: {cat['name']} ({cat['part_count']} parts)")
    """
    try:
        client = get_inventree_client()
        categories = await client.list_categories(parent=parent)

        simplified = [
            {
                "pk": cat.get("pk"),
                "name": cat.get("name"),
                "description": cat.get("description", ""),
                "parent": cat.get("parent"),
                "part_count": cat.get("part_count", 0),
            }
            for cat in categories
        ]

        logger.info("Categories listed", count=len(simplified))

        return {
            "success": True,
            "categories": simplified,
            "count": len(simplified),
        }

    except (TransientError, ValidationError, BusinessRuleError) as e:
        logger.error("Category list failed", error=str(e))
        return {
            "success": False,
            "categories": [],
            "count": 0,
            "error": str(e),
            "error_type": e.error_type,
        }
    except Exception as e:
        logger.exception("Unexpected error listing categories")
        return {
            "success": False,
            "categories": [],
            "count": 0,
            "error": f"Unexpected error: {e}",
            "error_type": "UNKNOWN",
        }


async def list_suppliers() -> dict[str, Any]:
    """
    List active suppliers in the system.

    Use this tool to find suppliers for parts, especially when creating
    purchase orders or looking for alternative sources.

    Returns:
        A dictionary containing:
        - success: bool - Whether the query succeeded
        - suppliers: list - List of suppliers:
            - pk: Supplier ID
            - name: Supplier name
            - description: Supplier description
            - website: Supplier website
            - email: Contact email
            - phone: Contact phone
        - count: int - Number of suppliers
        - error: str - Error message if query failed

    Example:
        >>> result = await list_suppliers()
        >>> for supplier in result["suppliers"]:
        >>>     print(f"{supplier['name']}: {supplier['email']}")
    """
    try:
        client = get_inventree_client()
        suppliers = await client.list_suppliers(active=True)

        simplified = [
            {
                "pk": s.get("pk"),
                "name": s.get("name"),
                "description": s.get("description", ""),
                "website": s.get("website", ""),
                "email": s.get("email", ""),
                "phone": s.get("phone", ""),
            }
            for s in suppliers
        ]

        logger.info("Suppliers listed", count=len(simplified))

        return {
            "success": True,
            "suppliers": simplified,
            "count": len(simplified),
        }

    except (TransientError, ValidationError, BusinessRuleError) as e:
        logger.error("Supplier list failed", error=str(e))
        return {
            "success": False,
            "suppliers": [],
            "count": 0,
            "error": str(e),
            "error_type": e.error_type,
        }
    except Exception as e:
        logger.exception("Unexpected error listing suppliers")
        return {
            "success": False,
            "suppliers": [],
            "count": 0,
            "error": f"Unexpected error: {e}",
            "error_type": "UNKNOWN",
        }


async def get_supplier_parts(
    supplier_id: int | None = None,
    part_id: int | None = None,
) -> dict[str, Any]:
    """
    Get supplier part information - parts available from suppliers with pricing.

    Use this tool to find which suppliers stock a part, or what parts
    a supplier offers, including pricing and lead time information.

    Args:
        supplier_id: Filter by specific supplier ID.
        part_id: Filter by specific part ID.
        At least one filter should be provided.

    Returns:
        A dictionary containing:
        - success: bool - Whether the query succeeded
        - supplier_parts: list - List of supplier parts:
            - pk: Supplier part ID
            - supplier_name: Supplier name
            - supplier_id: Supplier ID
            - part_name: Part name
            - part_id: Part ID
            - SKU: Supplier's SKU/part number
            - manufacturer: Manufacturer name
            - MPN: Manufacturer Part Number
            - price: Unit price (may be null)
            - lead_time: Lead time in days (may be null)
        - count: int - Number of supplier parts
        - error: str - Error message if query failed

    Example:
        >>> result = await get_supplier_parts(part_id=42)
        >>> for sp in result["supplier_parts"]:
        >>>     print(f"{sp['supplier_name']}: {sp['SKU']} @ ${sp['price']}")
    """
    try:
        client = get_inventree_client()
        supplier_parts = await client.get_supplier_parts(
            supplier_id=supplier_id,
            part_id=part_id,
        )

        simplified = [
            {
                "pk": sp.get("pk"),
                "supplier_id": sp.get("supplier"),
                "supplier_name": sp.get("supplier_detail", {}).get("name"),
                "part_id": sp.get("part"),
                "part_name": sp.get("part_detail", {}).get("name"),
                "SKU": sp.get("SKU"),
                "manufacturer": sp.get("manufacturer_detail", {}).get("name"),
                "MPN": sp.get("MPN"),
                "price": None,  # Price is in separate price break model
                "link": sp.get("link"),
            }
            for sp in supplier_parts
        ]

        logger.info(
            "Supplier parts retrieved",
            supplier_id=supplier_id,
            part_id=part_id,
            count=len(simplified),
        )

        return {
            "success": True,
            "supplier_parts": simplified,
            "count": len(simplified),
        }

    except (TransientError, ValidationError, BusinessRuleError) as e:
        logger.error("Supplier parts query failed", error=str(e))
        return {
            "success": False,
            "supplier_parts": [],
            "count": 0,
            "error": str(e),
            "error_type": e.error_type,
        }
    except Exception as e:
        logger.exception("Unexpected error in supplier parts query")
        return {
            "success": False,
            "supplier_parts": [],
            "count": 0,
            "error": f"Unexpected error: {e}",
            "error_type": "UNKNOWN",
        }


async def check_low_stock(
    threshold_multiplier: float = 1.0,
) -> dict[str, Any]:
    """
    Check for parts that are below their minimum stock level.

    Use this tool to identify parts that need to be reordered.

    Args:
        threshold_multiplier: Multiply minimum_stock by this factor.
                             Use 1.5 to catch parts that will soon be low.
                             Default 1.0 for exact minimum.

    Returns:
        A dictionary containing:
        - success: bool - Whether the query succeeded
        - low_stock_parts: list - Parts below threshold:
            - pk: Part ID
            - name: Part name
            - IPN: Internal Part Number
            - in_stock: Current stock level
            - minimum_stock: Minimum stock level
            - shortage: How much below minimum
        - count: int - Number of low-stock parts
        - error: str - Error message if query failed

    Example:
        >>> result = await check_low_stock(threshold_multiplier=1.5)
        >>> for part in result["low_stock_parts"]:
        >>>     print(f"{part['IPN']}: {part['in_stock']}/{part['minimum_stock']}")
    """
    try:
        client = get_inventree_client()

        # Search for parts with low stock (InvenTree supports this filter)
        parts = await client.search_parts(active=True, limit=500)

        low_stock = []
        for part in parts:
            in_stock = part.get("in_stock", 0)
            minimum = part.get("minimum_stock", 0)
            threshold = minimum * threshold_multiplier

            if threshold > 0 and in_stock < threshold:
                low_stock.append({
                    "pk": part.get("pk"),
                    "name": part.get("name"),
                    "IPN": part.get("IPN"),
                    "in_stock": in_stock,
                    "minimum_stock": minimum,
                    "threshold": threshold,
                    "shortage": threshold - in_stock,
                })

        # Sort by shortage severity
        low_stock.sort(key=lambda x: x["shortage"], reverse=True)

        logger.info("Low stock check completed", count=len(low_stock))

        return {
            "success": True,
            "low_stock_parts": low_stock,
            "count": len(low_stock),
        }

    except (TransientError, ValidationError, BusinessRuleError) as e:
        logger.error("Low stock check failed", error=str(e))
        return {
            "success": False,
            "low_stock_parts": [],
            "count": 0,
            "error": str(e),
            "error_type": e.error_type,
        }
    except Exception as e:
        logger.exception("Unexpected error in low stock check")
        return {
            "success": False,
            "low_stock_parts": [],
            "count": 0,
            "error": f"Unexpected error: {e}",
            "error_type": "UNKNOWN",
        }


# Export all tools
INVENTREE_TOOLS = [
    search_parts,
    get_part_details,
    get_stock_levels,
    get_bom,
    list_categories,
    list_suppliers,
    get_supplier_parts,
    check_low_stock,
]
