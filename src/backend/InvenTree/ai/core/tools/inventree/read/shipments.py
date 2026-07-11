"""
Shipment and Order Detail Read Tools

Read-only tools for retrieving shipment and detailed order information.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ai.core.maf_compat import ai_function

from ai.core.integrations.data_provider import get_data_provider

logger = logging.getLogger(__name__)


@ai_function
async def get_sales_order_shipments(
    order_id: int,
) -> list[dict[str, Any]]:
    """
    Get shipments for a sales order.
    
    Shipments track the physical delivery of items from a sales order,
    including tracking information and shipped quantities.
    
    Args:
        order_id: The sales order ID
    
    Returns:
        List of shipments, each containing:
        - pk: Shipment ID
        - order: Sales order ID
        - reference: Shipment reference/tracking number
        - shipment_date: When the shipment was sent
        - delivery_date: Expected or actual delivery date
        - tracking_number: Carrier tracking number
        - invoice_number: Invoice reference
        - link: Tracking URL
        - notes: Shipment notes
        - items: List of items in this shipment with quantities
    
    Example:
        shipments = await get_sales_order_shipments(order_id=100)
        for s in shipments:
            print(f"Shipment {s['reference']}: {s['shipment_date']}")
    """
    logger.info(f"Getting shipments for sales order {order_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        shipments = await client._request(
            "GET",
            "/order/so/shipment/",
            params={"order": order_id, "limit": 100},
        )
        if isinstance(shipments, dict) and "results" in shipments:
            shipments = shipments["results"]
    except Exception as e:
        logger.warning(f"Could not get shipments: {e}")
        shipments = []
    
    logger.info(f"Found {len(shipments)} shipments for order {order_id}")
    
    return shipments



@ai_function
async def get_company(
    company_id: int,
) -> dict[str, Any] | None:
    """
    Get detailed information about a company (supplier, customer, or manufacturer).
    
    Companies can have multiple roles - they can be suppliers, customers,
    and/or manufacturers.
    
    Args:
        company_id: The company ID
    
    Returns:
        Company details including:
        - pk: Company ID
        - name: Company name
        - description: Company description
        - website: Website URL
        - phone: Phone number
        - email: Email address
        - address: Physical address
        - currency: Default currency
        - is_supplier: Whether company is a supplier
        - is_customer: Whether company is a customer
        - is_manufacturer: Whether company is a manufacturer
        - is_active: Whether company is active
        - notes: Notes about the company
        - image: Company logo URL
        - primary_contact: Primary contact name
        - contacts: List of company contacts
        
        Returns None if company not found.
    
    Example:
        company = await get_company(company_id=10)
        roles = []
        if company['is_supplier']: roles.append("Supplier")
        if company['is_customer']: roles.append("Customer")
        print(f"{company['name']}: {', '.join(roles)}")
    """
    logger.info(f"Getting company {company_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        company = await client._request("GET", f"/company/{company_id}/")
        if not isinstance(company, dict):
            return None
        
        # Get contacts for this company
        try:
            contacts = await client._request(
                "GET",
                "/company/contact/",
                params={"company": company_id, "limit": 50},
            )
            if isinstance(contacts, dict) and "results" in contacts:
                contacts = contacts["results"]
            company["contacts"] = contacts
        except Exception:
            company["contacts"] = []
        
        return company
        
    except Exception as e:
        logger.warning(f"Could not get company: {e}")
        return None


@ai_function
async def get_manufacturer_parts(
    part_id: int | None = None,
    manufacturer_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    Get manufacturer parts (parts from manufacturers).
    
    Manufacturer parts link internal parts to manufacturer part numbers (MPNs),
    which can then be linked to multiple supplier parts.
    
    Args:
        part_id: Filter by internal part ID
        manufacturer_id: Filter by manufacturer company ID
    
    Returns:
        List of manufacturer parts, each containing:
        - pk: Manufacturer part ID
        - part: Internal part ID
        - part_name: Internal part name
        - manufacturer: Manufacturer company ID
        - manufacturer_name: Manufacturer name
        - MPN: Manufacturer Part Number
        - description: Manufacturer's description
        - link: URL to manufacturer's product page
        - supplier_parts_count: Number of linked supplier parts
    
    Example:
        # Get all manufacturers for a part
        mfr_parts = await get_manufacturer_parts(part_id=42)
        for mp in mfr_parts:
            print(f"{mp['manufacturer_name']}: MPN {mp['MPN']}")
    """
    provider = get_data_provider()
    
    logger.info(f"Getting manufacturer parts, part={part_id}, mfr={manufacturer_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        params: dict[str, Any] = {"limit": 100}
        if part_id:
            params["part"] = part_id
        if manufacturer_id:
            params["manufacturer"] = manufacturer_id
        
        mfr_parts = await client._request(
            "GET",
            "/company/part/manufacturer/",
            params=params,
        )
        if isinstance(mfr_parts, dict) and "results" in mfr_parts:
            mfr_parts = mfr_parts["results"]
    except Exception as e:
        logger.warning(f"Could not get manufacturer parts: {e}")
        mfr_parts = []
    
    # Enrich with part and manufacturer names
    for mp in mfr_parts:
        pid = mp.get("part")
        if pid:
            part = await provider.get_part(pid)
            if part:
                mp["part_name"] = part.get("name")
    
    logger.info(f"Found {len(mfr_parts)} manufacturer parts")
    
    return mfr_parts


@ai_function
async def search_stock(
    query: str | None = None,
    part_id: int | None = None,
    location_id: int | None = None,
    serial: str | None = None,
    batch: str | None = None,
    in_stock: bool = True,
    include_expired: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Search for stock items with flexible filtering.
    
    Provides a powerful search across all stock items with multiple
    filter options.
    
    Args:
        query: Search query (matches part name, serial, batch)
        part_id: Filter by part ID
        location_id: Filter by location ID (includes sub-locations)
        serial: Filter by serial number (exact or partial match)
        batch: Filter by batch code (exact or partial match)
        in_stock: Only items with quantity > 0 (default True)
        include_expired: Include expired stock items (default False)
        limit: Maximum results (default 50)
    
    Returns:
        List of stock items, each containing:
        - pk: Stock item ID
        - part: Part ID
        - part_name: Part name
        - part_ipn: Part IPN
        - quantity: Stock quantity
        - location: Location ID
        - location_name: Location name
        - serial: Serial number
        - batch: Batch code
        - status: Stock status code
        - status_text: Human-readable status
        - expiry_date: Expiry date if applicable
        - is_expired: True if past expiry date
        - purchase_order: Associated PO if received
        - supplier_part: Supplier part if from purchase
    
    Example:
        # Find stock by serial number
        stock = await search_stock(serial="SN12345")
        
        # Find stock for a part at a location
        stock = await search_stock(part_id=42, location_id=5)
    """
    provider = get_data_provider()
    
    logger.info(f"Searching stock: query={query}, part={part_id}, location={location_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        params: dict[str, Any] = {"limit": limit}
        if query:
            params["search"] = query
        if part_id:
            params["part"] = part_id
        if location_id:
            params["location"] = location_id
        if serial:
            params["serial"] = serial
        if batch:
            params["batch"] = batch
        if in_stock:
            params["in_stock"] = "true"
        if not include_expired:
            params["expired"] = "false"
        
        stock_items = await client._request("GET", "/stock/", params=params)
        if isinstance(stock_items, dict) and "results" in stock_items:
            stock_items = stock_items["results"]
    except Exception as e:
        logger.warning(f"Could not search stock: {e}")
        stock_items = []
    
    # Enrich with names and status
    status_map = {
        10: "OK",
        50: "Attention needed",
        55: "Damaged",
        60: "Destroyed",
        65: "Rejected",
        70: "Lost",
        85: "Returned",
    }
    
    locations = await provider.get_locations()
    location_map = {loc.get("pk"): loc.get("name") for loc in locations}
    
    today = datetime.now().date()
    
    for item in stock_items:
        # Add part info
        pid = item.get("part")
        if pid:
            part = await provider.get_part(pid)
            if part:
                item["part_name"] = part.get("name")
                item["part_ipn"] = part.get("IPN")
        
        # Add location name
        item["location_name"] = location_map.get(item.get("location"), "Unknown")
        
        # Add status text
        item["status_text"] = status_map.get(item.get("status"), "Unknown")
        
        # Check expiry
        expiry = item.get("expiry_date")
        if expiry and isinstance(expiry, str):
            try:
                expiry_date = datetime.fromisoformat(expiry.replace("Z", "+00:00")).date()
                item["is_expired"] = expiry_date < today
            except ValueError:
                item["is_expired"] = False
        else:
            item["is_expired"] = False
    
    logger.info(f"Found {len(stock_items)} stock items")
    
    return stock_items[:limit]


# Export all shipment/order detail read tools
SHIPMENT_READ_TOOLS = [
    get_sales_order_shipments,
    get_company,
    get_manufacturer_parts,
    search_stock,
]

__all__ = [
    "get_sales_order_shipments",
    "get_company",
    "get_manufacturer_parts",
    "search_stock",
    "SHIPMENT_READ_TOOLS",
]
