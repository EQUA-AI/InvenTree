"""
Part Read Tools

Read-only tools for retrieving part information from InvenTree.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.integrations.data_provider import get_data_provider
from ai.core.maf_compat import ai_function

logger = logging.getLogger(__name__)


def resolve_exact_ipn(ipn: str, revision: str | None = None) -> int | dict[str, Any]:
    """Resolve an exact identifier under native part-read permission.

    IPNs need not be unique across revisions. Never choose the first search
    hit, prefer a stocked revision, or reconstruct zeros lost during ASR.
    """
    from ai.core.tools.inventree.read.database import _current_user
    from part.models import Part
    from users.permissions import check_user_role

    user = _current_user()
    if user is None or not check_user_role(user, "part", "view"):
        return {"found": False, "resolved": False, "error": "Part lookup is unavailable to you"}
    matches = Part.objects.filter(IPN=ipn)
    if revision is not None:
        matches = matches.filter(revision=revision)
    count = matches.count()
    if count == 1:
        return matches.values_list("pk", flat=True).get()
    if count == 0:
        return {
            "found": False,
            "resolved": False,
            "note": "No exact IPN/revision match. Ask the user to check the identifier; do not substitute a search hit.",
        }
    candidates = list(matches.order_by("pk").values("pk", "name", "IPN", "revision")[:20])
    return {
        "found": False,
        "resolved": False,
        "requires_clarification": True,
        "note": "Several parts share this IPN. Ask the user to choose the exact part/revision before reporting stock or BOM facts.",
        "candidates": candidates,
        "candidate_count": count,
        "truncated": count > len(candidates),
    }


@ai_function
async def get_part(
    part_id: int | None = None,
    ipn: str | None = None,
    revision: str | None = None,
) -> dict[str, Any] | None:
    """
    Get detailed information about a specific part by ID or Internal Part Number (IPN).

    You must provide either part_id OR ipn, but not both.

    Args:
        part_id: The unique ID of the part to retrieve
        ipn: The Internal Part Number (IPN) to look up
        revision: Optional exact revision when looking up an IPN. Otherwise
                  multiple matching revisions require user clarification.

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

        Returns {"found": false, "note": ...} if no part matches.

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

    if revision is not None and ipn is None:
        raise ValueError("revision requires ipn; part_id already identifies an exact revision")
    if ipn is not None:
        from asgiref.sync import sync_to_async

        resolved = await sync_to_async(resolve_exact_ipn)(ipn, revision)
        if isinstance(resolved, dict):
            return resolved
        part_id = resolved
    provider = get_data_provider()
    part = await provider.get_part(part_id)

    if part:
        # Enrich with stock quantity
        try:
            stock_qty = await provider.get_stock_quantity(part.get("pk"))
            part["in_stock"] = stock_qty
        except Exception:
            part["in_stock"] = None

        logger.info(f"Found part: {part.get('name')} (pk={part.get('pk')})")
        return part

    logger.info("Part not found")
    # Explicit, serializable not-found (a bare None drops the tool message
    # from the provider request entirely — see invocation_guard).
    return {
        "found": False,
        "note": (
            "No part matched this id/IPN/name. Absence is not proof the part "
            "does not exist under a different identifier."
        ),
    }


@ai_function
async def search_parts(
    query: str | None = None,
    category_id: int | None = None,
    is_assembly: bool | None = None,
    is_purchaseable: bool | None = None,
    is_saleable: bool | None = None,
    is_active: bool | None = True,
    has_stock: bool | None = None,
    low_stock: bool | None = None,
    limit: int = 25,
    offset: int = 0,
    include_coverage: bool = False,
) -> list[dict[str, Any]] | dict[str, Any]:
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
        is_active: True for active parts, False for inactive parts, or None
                   to include both. Defaults to active parts.
        has_stock: Filter for parts that have stock > 0 if True,
                  out of stock if False
        low_stock: Filter for parts below minimum stock threshold if True
        limit: Maximum number of results to return (default 25, max 100)
        offset: Native pagination offset (default 0).
        include_coverage: Include exact native count and paging metadata.
                          Use True for complete catalog keyword listings.

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
        - is_template / variant_of: Preserve the exact selected part/revision
          when requesting a BOM. The native API includes applicable inherited
          lines; do not replace a variant with its template.

    Example:
        # Search by keyword
        parts = await search_parts(query="capacitor 100uF")

        # Find purchaseable parts in a category
        parts = await search_parts(category_id=5, is_purchaseable=True)

        # Find parts with low stock
        parts = await search_parts(low_stock=True, limit=50)
    """
    from ai.core.tools.invocation_guard import capability_run_context

    run_context = capability_run_context.get()
    if run_context is not None and run_context.catalog_search_query is not None:
        # Only an explicitly unqualified keyword request binds this predicate.
        # The native API and invocation guard still own all authorization.
        query, category_id, is_active = run_context.catalog_search_query, None, None
        is_assembly = is_purchaseable = is_saleable = has_stock = low_stock = None
        include_coverage = True
    provider = get_data_provider()

    logger.info(
        f"Searching parts: query='{query}', category={category_id}, "
        f"is_assembly={is_assembly}, limit={limit}"
    )

    # Clamp limit
    limit = min(max(1, limit), 100)

    offset = max(0, offset)
    if include_coverage:
        page = await provider.search_parts_page(
            query=query or "", category=category_id, active=is_active, limit=limit, offset=offset
        )
        results = page["results"]
    else:
        results = await provider.search_parts(
            query=query or "",
            category=category_id,
            limit=limit,
            **({"offset": offset} if offset else {}),
            **({"active": is_active} if is_active is not True else {}),
        )

    # Apply additional filters that may not be supported by the provider
    if is_assembly is not None:
        results = [p for p in results if p.get("assembly") == is_assembly]

    if is_purchaseable is not None:
        results = [p for p in results if p.get("purchaseable") == is_purchaseable]

    if is_saleable is not None:
        # NB: the API field is 'salable' (single l)
        results = [p for p in results if p.get("salable") == is_saleable]

    if is_active is not None:
        results = [p for p in results if p.get("active") == is_active]

    if has_stock is not None:
        if has_stock:
            results = [p for p in results if (p.get("in_stock") or 0) > 0]
        else:
            results = [p for p in results if (p.get("in_stock") or 0) <= 0]

    if low_stock:
        results = [p for p in results if (p.get("in_stock") or 0) < (p.get("minimum_stock") or 0)]

    logger.info(f"Found {len(results)} parts matching criteria")

    results = results[:limit]
    if include_coverage:
        count = page["population_count"]
        native_complete = (
            count is not None
            and offset == 0
            and len(page["results"]) == count
            and page["has_more"] is False
        )
        post_filtered = any(
            value is not None for value in (is_assembly, is_purchaseable, is_saleable, has_stock)
        ) or bool(low_stock)
        population = len(results) if native_complete else None if post_filtered else count
        return {
            "results": results,
            "population_count": population,
            "returned_count": len(results),
            "complete_population": native_complete,
            "has_more": page["has_more"],
            "display_truncated": not native_complete,
            "offset": offset,
            "limit": limit,
            "filters": {
                "query": query or "",
                "category_id": category_id,
                "active": is_active,
                "is_assembly": is_assembly,
                "is_purchaseable": is_purchaseable,
                "is_saleable": is_saleable,
                "has_stock": has_stock,
                "low_stock": low_stock,
            },
            "note": "Counts after additional stock/part filters are unknown unless the complete native population was evaluated."
            if post_filtered and not native_complete
            else "Use offset and limit for later pages when has_more is true; a page is not the complete population.",
        }
    return results


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
        # Capacitance: 100 uF
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

    # Pricing lives on the dedicated /part/{id}/pricing/ endpoint (the part
    # record itself only carries the pricing_min/pricing_max range)
    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        pricing_data = await get_inventree_client()._request("GET", f"/part/{part_id}/pricing/")
    except Exception as e:
        logger.warning(f"Could not get part pricing: {e}")
        pricing_data = None

    if isinstance(pricing_data, dict):
        result["internal_price"] = pricing_data.get("internal_cost_min") or pricing_data.get(
            "overall_min"
        )
        result["sale_price"] = pricing_data.get("sale_price_min")
        result["bom_cost"] = pricing_data.get("bom_cost_min")
        result["pricing_range"] = {
            "overall_min": pricing_data.get("overall_min"),
            "overall_max": pricing_data.get("overall_max"),
            "currency": pricing_data.get("currency"),
        }

    # Get supplier prices
    if include_supplier_prices:
        try:
            supplier_parts = await provider.get_supplier_parts(part_id)
            for sp in supplier_parts:
                pack_qty = float(sp.get("pack_quantity_native") or 1)
                # Supplier pricing lives in the opt-in price_breaks list;
                # take the lowest-quantity break as the base price
                breaks = sorted(
                    sp.get("price_breaks") or [],
                    key=lambda pb: pb.get("quantity") or 0,
                )
                base = breaks[0] if breaks else {}
                price = float(base.get("price") or 0)
                result["supplier_prices"].append({
                    "supplier_id": sp.get("supplier"),
                    "supplier_name": (sp.get("supplier_detail") or {}).get("name"),
                    "sku": sp.get("SKU"),
                    "price": price,
                    "currency": base.get("price_currency"),
                    "pack_quantity": pack_qty,
                    "effective_price": price / pack_qty if pack_qty > 0 else price,
                    "last_updated": sp.get("updated"),
                })
                for pb in breaks:
                    result["price_breaks"].append({
                        "type": "supplier",
                        "supplier_id": sp.get("supplier"),
                        "quantity": pb.get("quantity"),
                        "price": pb.get("price"),
                        "currency": pb.get("price_currency"),
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
                        # PartSerializer exposes the computed pricing range
                        # directly as pricing_min/pricing_max
                        comp_price = comp_part.get("pricing_min")
                        if comp_price is not None:
                            comp_price = float(comp_price)

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
    "PART_READ_TOOLS",
    "get_part",
    "get_part_attachments",
    "get_part_parameters",
    "get_part_pricing",
    "search_parts",
]
