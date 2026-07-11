"""
Part Read Tools

Read-only tools for retrieving part information from InvenTree.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function

from ai.core.integrations.data_provider import get_data_provider

logger = logging.getLogger(__name__)


@ai_function
async def get_part(
    part_id: int | None = None,
    ipn: str | None = None,
) -> dict[str, Any] | None:
    """
    Get detailed information about a specific part by ID or Internal Part Number (IPN).
    
    You must provide either part_id OR ipn, but not both.
    
    Args:
        part_id: The unique ID of the part to retrieve
        ipn: The Internal Part Number (IPN) to look up
    
    Returns:
        Part details including:
        - pk: Part ID
        - name: Part name
        - description: Part description
        - IPN: Internal Part Number
        - category: Category ID and name
        - revision: Part revision
        - is_active: Whether part is active
        - is_purchaseable: Whether part can be purchased
        - is_saleable: Whether part can be sold
        - is_assembly: Whether part has a BOM
        - is_trackable: Whether stock is tracked by serial/batch
        - minimum_stock: Minimum stock threshold
        - in_stock: Current stock quantity
        - default_location: Default stock location
        - default_supplier: Default supplier
        - link: External reference URL
        - notes: Part notes
        
        Returns None if part not found.
    
    Example:
        # Get by ID
        part = await get_part(part_id=42)
        
        # Get by IPN
        part = await get_part(ipn="CAP-100UF-16V")
    """
    if part_id is None and ipn is None:
        raise ValueError("Either part_id or ipn must be provided")
    
    if part_id is not None and ipn is not None:
        raise ValueError("Provide either part_id or ipn, not both")
    
    provider = get_data_provider()
    
    if part_id is not None:
        logger.info(f"Getting part by ID: {part_id}")
        part = await provider.get_part(part_id)
    else:
        logger.info(f"Getting part by IPN: {ipn}")
        # Search by IPN and return first match
        parts = await provider.search_parts(query=ipn, limit=10)
        # Filter for exact IPN match
        part = next((p for p in parts if p.get("IPN") == ipn), None)
        if not part and parts:
            # If no exact match, try the first result
            part = parts[0]
    
    if part:
        # Enrich with stock quantity
        try:
            stock_qty = await provider.get_stock_quantity(part.get("pk"))
            part["in_stock"] = stock_qty
        except Exception:
            part["in_stock"] = None
        
        logger.info(f"Found part: {part.get('name')} (pk={part.get('pk')})")
    else:
        logger.info("Part not found")
    
    return part


@ai_function
async def search_parts(
    query: str | None = None,
    category_id: int | None = None,
    is_assembly: bool | None = None,
    is_purchaseable: bool | None = None,
    is_saleable: bool | None = None,
    is_active: bool = True,
    has_stock: bool | None = None,
    low_stock: bool | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """
    Search for parts in the inventory system with flexible filtering.
    
    Args:
        query: Search query string to match against part names, descriptions,
               IPN, or keywords. Supports partial matching.
        category_id: Filter by category ID. Use get_categories to find IDs.
        is_assembly: Filter for assemblies (parts with BOMs) if True,
                    non-assemblies if False
        is_purchaseable: Filter for purchaseable parts if True
        is_saleable: Filter for saleable parts if True
        is_active: Filter for active parts (default True). Set False to
                  include inactive parts.
        has_stock: Filter for parts that have stock > 0 if True,
                  out of stock if False
        low_stock: Filter for parts below minimum stock threshold if True
        limit: Maximum number of results to return (default 25, max 100)
    
    Returns:
        List of matching parts with their details including:
        - pk: Part ID
        - name: Part name
        - description: Part description
        - IPN: Internal Part Number
        - category: Category ID
        - in_stock: Current stock quantity
        - minimum_stock: Minimum stock level
        - is_assembly: Whether part has BOM
        - is_purchaseable: Can be purchased
        - is_saleable: Can be sold
    
    Example:
        # Search by keyword
        parts = await search_parts(query="capacitor 100uF")
        
        # Find purchaseable parts in a category
        parts = await search_parts(category_id=5, is_purchaseable=True)
        
        # Find parts with low stock
        parts = await search_parts(low_stock=True, limit=50)
    """
    provider = get_data_provider()
    
    logger.info(
        f"Searching parts: query='{query}', category={category_id}, "
        f"is_assembly={is_assembly}, limit={limit}"
    )
    
    # Clamp limit
    limit = min(max(1, limit), 100)
    
    # Basic search
    results = await provider.search_parts(
        query=query or "",
        category=category_id,
        limit=limit,
    )
    
    # Apply additional filters that may not be supported by the provider
    if is_assembly is not None:
        results = [p for p in results if p.get("assembly") == is_assembly]
    
    if is_purchaseable is not None:
        results = [p for p in results if p.get("purchaseable") == is_purchaseable]
    
    if is_saleable is not None:
        results = [p for p in results if p.get("saleable") == is_saleable]
    
    if is_active is not None:
        results = [p for p in results if p.get("active") == is_active]
    
    if has_stock is not None:
        if has_stock:
            results = [p for p in results if (p.get("in_stock") or 0) > 0]
        else:
            results = [p for p in results if (p.get("in_stock") or 0) <= 0]
    
    if low_stock:
        results = [
            p for p in results 
            if (p.get("in_stock") or 0) < (p.get("minimum_stock") or 0)
        ]
    
    logger.info(f"Found {len(results)} parts matching criteria")
    
    return results[:limit]


@ai_function
async def get_part_parameters(
    part_id: int,
) -> list[dict[str, Any]]:
    """
    Get all parameters (specifications) defined for a part.
    
    Parameters are key-value pairs that store technical specifications,
    dimensions, ratings, and other attributes for a part.
    
    Args:
        part_id: The part ID to get parameters for
    
    Returns:
        List of parameters, each containing:
        - pk: Parameter ID
        - template: Parameter template ID
        - template_name: Name of the parameter (e.g., "Voltage Rating")
        - template_units: Units for the value (e.g., "V", "Ω", "mm")
        - data: The parameter value as a string
        - data_numeric: Numeric value if applicable
    
    Example:
        params = await get_part_parameters(part_id=42)
        for p in params:
            print(f"{p['template_name']}: {p['data']} {p['template_units']}")
        # Output:
        # Capacitance: 100 µF
        # Voltage Rating: 16 V
        # Package: 0805
    """
    provider = get_data_provider()
    
    logger.info(f"Getting parameters for part_id={part_id}")
    
    # Get parameters via the provider
    params = await provider.get_part_parameters(part_id)
    
    logger.info(f"Found {len(params)} parameters for part {part_id}")
    
    return params


@ai_function
async def get_part_attachments(
    part_id: int,
) -> list[dict[str, Any]]:
    """
    Get all attachments (documents, images, files) associated with a part.
    
    Attachments can include datasheets, drawings, images, 3D models,
    specifications, and any other files linked to the part.
    
    Args:
        part_id: The part ID to get attachments for
    
    Returns:
        List of attachments, each containing:
        - pk: Attachment ID
        - attachment: URL/path to the file
        - filename: Original filename
        - comment: Description or notes about the attachment
        - upload_date: When the file was uploaded
        - user: Who uploaded the file
        - file_size: Size of the file in bytes
    
    Example:
        attachments = await get_part_attachments(part_id=42)
        for a in attachments:
            print(f"{a['filename']}: {a['comment']}")
        # Output:
        # datasheet.pdf: Component datasheet from manufacturer
        # image.jpg: Photo of physical part
    """
    provider = get_data_provider()
    
    logger.info(f"Getting attachments for part_id={part_id}")
    
    attachments = await provider.get_part_attachments(part_id)
    
    logger.info(f"Found {len(attachments)} attachments for part {part_id}")
    
    return attachments


@ai_function
async def get_part_pricing(
    part_id: int,
    include_supplier_prices: bool = True,
    include_bom_cost: bool = False,
    currency: str | None = None,
) -> dict[str, Any]:
    """
    Get comprehensive pricing information for a part.
    
    This includes internal prices, sale prices, supplier prices,
    and optionally BOM cost calculation for assemblies.
    
    Args:
        part_id: The part ID to get pricing for
        include_supplier_prices: Include prices from all suppliers (default True)
        include_bom_cost: Calculate BOM cost for assemblies (default False)
        currency: Convert prices to this currency (e.g., "USD", "EUR").
                 If None, prices are returned in their original currencies.
    
    Returns:
        Pricing information including:
        - part_id: The part ID
        - part_name: Part name
        - internal_price: Internal cost/price if set
        - sale_price: Sale price if set
        - bom_cost: Calculated BOM cost (if include_bom_cost=True and part is assembly)
        - supplier_prices: List of supplier prices (if include_supplier_prices=True):
            - supplier_id: Supplier ID
            - supplier_name: Supplier name
            - sku: Supplier SKU
            - price: Unit price
            - currency: Price currency
            - pack_quantity: Units per pack
            - effective_price: Price per unit (price / pack_quantity)
            - lead_time: Lead time in days
            - last_updated: When price was last updated
        - price_breaks: List of price breaks for quantity discounts
        - currency: Currency used for converted prices (if currency param provided)
    
    Example:
        pricing = await get_part_pricing(part_id=42, include_bom_cost=True)
        print(f"Internal price: {pricing['internal_price']}")
        print(f"Best supplier price: {min(p['effective_price'] for p in pricing['supplier_prices'])}")
    """
    provider = get_data_provider()
    
    logger.info(
        f"Getting pricing for part_id={part_id}, "
        f"include_suppliers={include_supplier_prices}, "
        f"include_bom={include_bom_cost}"
    )
    
    # Get basic part info
    part = await provider.get_part(part_id)
    if not part:
        raise ValueError(f"Part with ID {part_id} not found")
    
    result: dict[str, Any] = {
        "part_id": part_id,
        "part_name": part.get("name"),
        "internal_price": None,
        "sale_price": None,
        "bom_cost": None,
        "supplier_prices": [],
        "price_breaks": [],
        "currency": currency,
    }
    
    # Get pricing from the part data itself
    pricing_data = part.get("pricing_data", {})
    if pricing_data:
        result["internal_price"] = pricing_data.get("internal_cost")
        result["sale_price"] = pricing_data.get("sale_price")
        
        # Add price breaks if available
        for pb in pricing_data.get("price_breaks", []):
            result["price_breaks"].append({
                "type": "internal",
                "quantity": pb.get("quantity"),
                "price": pb.get("price"),
                "currency": pb.get("price_currency"),
            })
    
    # Get supplier prices
    if include_supplier_prices:
        try:
            supplier_parts = await provider.get_supplier_parts(part_id)
            for sp in supplier_parts:
                pack_qty = sp.get("pack_quantity") or 1
                price = sp.get("price") or 0
                result["supplier_prices"].append({
                    "supplier_id": sp.get("supplier"),
                    "supplier_name": sp.get("supplier_name") or sp.get("supplier_detail", {}).get("name"),
                    "sku": sp.get("SKU"),
                    "price": price,
                    "currency": sp.get("price_currency"),
                    "pack_quantity": pack_qty,
                    "effective_price": price / pack_qty if pack_qty > 0 else price,
                    "lead_time": sp.get("lead_time"),
                    "last_updated": sp.get("updated"),
                })
        except Exception as e:
            logger.warning(f"Could not get supplier prices: {e}")
    
    # Calculate BOM cost for assemblies
    if include_bom_cost and part.get("assembly"):
        try:
            bom_items = await provider.get_bom_items(part_id)
            total_cost = 0.0
            for item in bom_items:
                component_id = item.get("sub_part")
                quantity = item.get("quantity") or 0
                if component_id:
                    # Get component part for pricing info
                    comp_part = await provider.get_part(component_id)
                    if comp_part:
                        comp_pricing = comp_part.get("pricing_data", {})
                        comp_price = comp_pricing.get("internal_cost")
                        
                        # Fall back to supplier price if no internal cost
                        if not comp_price:
                            try:
                                comp_suppliers = await provider.get_supplier_parts(component_id)
                                if comp_suppliers:
                                    prices = [
                                        (sp.get("price") or 0) / (sp.get("pack_quantity") or 1)
                                        for sp in comp_suppliers
                                    ]
                                    comp_price = min(prices) if prices else None
                            except Exception:
                                pass
                        
                        if comp_price:
                            total_cost += comp_price * quantity
            
            result["bom_cost"] = total_cost
        except Exception as e:
            logger.warning(f"Could not calculate BOM cost: {e}")
    
    logger.info(f"Retrieved pricing for part {part_id}")
    
    return result


# Export all read tools for parts
PART_READ_TOOLS = [
    get_part,
    search_parts,
    get_part_parameters,
    get_part_attachments,
    get_part_pricing,
]

__all__ = [
    "get_part",
    "search_parts",
    "get_part_parameters",
    "get_part_attachments",
    "get_part_pricing",
    "PART_READ_TOOLS",
]
