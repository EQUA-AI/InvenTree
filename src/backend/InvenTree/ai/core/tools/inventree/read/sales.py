"""
Sales Read Tools

Read-only tools for retrieving sales order and customer information from InvenTree.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ai.core.maf_compat import ai_function

from ai.core.integrations.data_provider import get_data_provider

logger = logging.getLogger(__name__)


@ai_function
async def get_sales_orders(
    customer_id: int | None = None,
    status: str | None = None,
    outstanding: bool | None = None,
    overdue: bool | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Get sales orders for tracking customer orders.
    
    Sales orders track orders from customers, including line items,
    shipping, and fulfillment status.
    
    Args:
        customer_id: Filter by customer company ID
        status: Filter by status. Options:
               - "pending": Order created but not issued
               - "in_progress": Order being fulfilled
               - "shipped": Order shipped to customer
               - "complete": Order completed
               - "cancelled": Order cancelled
        outstanding: If True, only orders with unshipped items.
                    If False, only fully shipped orders.
        overdue: If True, only orders past target date with outstanding items
        limit: Maximum orders to return (default 50)
    
    Returns:
        List of sales orders, each containing:
        - pk: Sales order ID
        - reference: Order reference number
        - customer: Customer company ID
        - customer_name: Customer name
        - description: Order description
        - status: Status code
        - status_text: Human-readable status
        - creation_date: When order was created
        - issue_date: When order was confirmed
        - target_date: Promised delivery date
        - shipment_date: When order was shipped
        - total_price: Total order value
        - currency: Order currency
        - line_items_count: Number of line items
        - shipped_count: Number of items shipped
        - outstanding_count: Number of items not yet shipped
        - is_overdue: True if past target date with outstanding items
        - notes: Order notes
        - link: External reference URL
    
    Example:
        # Get all pending orders
        orders = await get_sales_orders(status="pending")
        
        # Get overdue orders
        orders = await get_sales_orders(overdue=True)
    """
    provider = get_data_provider()
    
    logger.info(f"Getting sales orders, customer={customer_id}, status={status}")
    
    # Map status string to code
    status_map = {
        "pending": 10,
        "in_progress": 20,
        "shipped": 30,
        "complete": 40,
        "cancelled": 50,
    }
    
    status_code = status_map.get(status.lower()) if status else None
    
    # Get sales orders via client
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        params: dict[str, Any] = {"limit": limit}
        if customer_id:
            params["customer"] = customer_id
        if status_code:
            params["status"] = status_code
        
        orders = await client._request("GET", "/order/so/", params=params)
        if isinstance(orders, dict) and "results" in orders:
            orders = orders["results"]
    except Exception as e:
        logger.warning(f"Could not get sales orders: {e}")
        orders = []
    
    # Add status text and calculate overdue
    status_text_map = {
        10: "Pending",
        20: "In Progress",
        30: "Shipped",
        40: "Complete",
        50: "Cancelled",
    }
    
    today = datetime.now().date()
    
    for order in orders:
        order["status_text"] = status_text_map.get(order.get("status"), "Unknown")
        
        # Calculate overdue status
        target_date = order.get("target_date")
        if target_date and isinstance(target_date, str):
            try:
                target = datetime.fromisoformat(target_date.replace("Z", "+00:00")).date()
                order["is_overdue"] = target < today and order.get("status") in (10, 20)
            except ValueError:
                order["is_overdue"] = False
        else:
            order["is_overdue"] = False
    
    # Apply filters
    if outstanding is not None:
        if outstanding:
            orders = [o for o in orders if o.get("status") in (10, 20)]
        else:
            orders = [o for o in orders if o.get("status") in (30, 40)]
    
    if overdue:
        orders = [o for o in orders if o.get("is_overdue")]
    
    logger.info(f"Found {len(orders)} sales orders")
    
    return orders[:limit]


@ai_function
async def get_sales_order(
    order_id: int,
) -> dict[str, Any]:
    """
    Get detailed information about a sales order, including line items.
    
    Args:
        order_id: The ID of the sales order
        
    Returns:
        Dictionary containing order details and a 'lines' key with the line items.
    """
    logger.info(f"Getting sales order {order_id}")
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        order = await client._request("GET", f"/order/so/{order_id}/")
        
        if not order:
            return {"error": "Order not found"}
            
        lines = await get_sales_order_lines(order_id)
        order["lines"] = lines
        return order
        
    except Exception as e:
        logger.error(f"Error getting sales order {order_id}: {e}")
        return {"error": str(e)}


@ai_function
async def get_sales_order_lines(
    order_id: int,
    include_stock: bool = True,
) -> list[dict[str, Any]]:
    """
    Get line items for a sales order.
    
    Line items specify what parts and quantities are included in a sales order.
    
    Args:
        order_id: The sales order ID
        include_stock: Include current stock availability for each line
                      (default True)
    
    Returns:
        List of line items, each containing:
        - pk: Line item ID
        - order: Sales order ID
        - part: Part ID
        - part_name: Part name
        - part_ipn: Part IPN
        - quantity: Ordered quantity
        - sale_price: Unit sale price
        - sale_price_currency: Currency
        - total_price: Line total (quantity * sale_price)
        - shipped: Quantity already shipped
        - outstanding: Quantity remaining to ship
        - reference: Customer's reference for this line
        - notes: Line item notes
        - target_date: Requested delivery date
        - is_complete: True if fully shipped
        
        If include_stock=True, also includes:
        - in_stock: Current stock quantity
        - can_fulfill: True if sufficient stock available
    
    Example:
        lines = await get_sales_order_lines(order_id=100)
        for line in lines:
            print(f"{line['part_name']}: {line['shipped']}/{line['quantity']} shipped")
    """
    provider = get_data_provider()
    
    logger.info(f"Getting sales order lines for order {order_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        lines = await client._request(
            "GET",
            "/order/so-line/",
            params={"order": order_id, "limit": 500},
        )
        if isinstance(lines, dict) and "results" in lines:
            lines = lines["results"]
    except Exception as e:
        logger.warning(f"Could not get sales order lines: {e}")
        lines = []
    
    # Enrich with part info and stock
    for line in lines:
        part_id = line.get("part")
        if part_id:
            part = await provider.get_part(part_id)
            if part:
                line["part_name"] = part.get("name")
                line["part_ipn"] = part.get("IPN")
            
            if include_stock:
                stock_qty = await provider.get_stock_quantity(part_id)
                line["in_stock"] = stock_qty
                outstanding = line.get("quantity", 0) - line.get("shipped", 0)
                line["outstanding"] = outstanding
                line["can_fulfill"] = stock_qty >= outstanding
                line["is_complete"] = outstanding <= 0
    
    logger.info(f"Found {len(lines)} line items for order {order_id}")
    
    return lines


@ai_function
async def get_customers(
    active_only: bool = True,
    has_orders: bool | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    Get a list of customers (companies that buy products).
    
    Customers are companies to which products are sold via sales orders.
    
    Args:
        active_only: Only return active customers (default True)
        has_orders: If True, only return customers with sales orders.
                   If False, only customers without orders. If None, all.
        limit: Maximum number of customers to return (default 100)
    
    Returns:
        List of customers, each containing:
        - pk: Customer ID (company ID)
        - name: Company name
        - description: Company description
        - website: Company website URL
        - phone: Contact phone number
        - email: Contact email
        - address: Physical address
        - currency: Default currency for this customer
        - is_active: Whether customer is active
        - orders_count: Number of sales orders
        - notes: Notes about the customer
    
    Example:
        # Get all active customers
        customers = await get_customers()
        
        # Find customers with orders
        customers = await get_customers(has_orders=True)
    """
    logger.info(f"Getting customers, active_only={active_only}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        params: dict[str, Any] = {
            "limit": limit,
            "is_customer": "true",
        }
        if active_only:
            params["active"] = "true"
        
        customers = await client._request("GET", "/company/", params=params)
        if isinstance(customers, dict) and "results" in customers:
            customers = customers["results"]
    except Exception as e:
        logger.warning(f"Could not get customers: {e}")
        customers = []
    
    # Get order counts
    for customer in customers:
        customer_id = customer.get("pk")
        if customer_id:
            try:
                orders = await get_sales_orders(customer_id=customer_id, limit=1000)
                customer["orders_count"] = len(orders)
            except Exception:
                customer["orders_count"] = 0
    
    if has_orders is not None:
        if has_orders:
            customers = [c for c in customers if c.get("orders_count", 0) > 0]
        else:
            customers = [c for c in customers if c.get("orders_count", 0) == 0]
    
    logger.info(f"Found {len(customers)} customers")
    
    return customers[:limit]


@ai_function
async def get_build_orders(
    part_id: int | None = None,
    status: str | None = None,
    active_only: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Get build orders for manufacturing assemblies.
    
    Build orders track the production of assemblies from their BOM components.
    
    Args:
        part_id: Filter by assembly part ID
        status: Filter by status. Options:
               - "pending": Build not yet started
               - "production": Build in progress
               - "complete": Build completed
               - "cancelled": Build cancelled
        active_only: Only return active builds (pending/production)
                    (default True)
        limit: Maximum orders to return (default 50)
    
    Returns:
        List of build orders, each containing:
        - pk: Build order ID
        - reference: Build reference number
        - part: Assembly part ID
        - part_name: Assembly name
        - quantity: Quantity to build
        - completed: Quantity completed so far
        - status: Status code
        - status_text: Human-readable status
        - creation_date: When build was created
        - target_date: Target completion date
        - completion_date: Actual completion date
        - priority: Build priority (1-10)
        - sales_order: Linked sales order if building for order
        - parent_build: Parent build order if sub-assembly
        - issued_by: User who issued the build
        - notes: Build notes
        - is_overdue: True if past target date and not complete
    
    Example:
        # Get all active builds
        builds = await get_build_orders()
        
        # Get builds for a specific assembly
        builds = await get_build_orders(part_id=42)
    """
    logger.info(f"Getting build orders, part={part_id}, status={status}")
    
    status_map = {
        "pending": 10,
        "production": 20,
        "complete": 30,
        "cancelled": 40,
    }
    
    status_code = status_map.get(status.lower()) if status else None
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        params: dict[str, Any] = {"limit": limit}
        if part_id:
            params["part"] = part_id
        if status_code:
            params["status"] = status_code
        if active_only:
            params["active"] = "true"
        
        builds = await client._request("GET", "/build/", params=params)
        if isinstance(builds, dict) and "results" in builds:
            builds = builds["results"]
    except Exception as e:
        logger.warning(f"Could not get build orders: {e}")
        builds = []
    
    # Enrich with part names and status
    provider = get_data_provider()
    status_text_map = {
        10: "Pending",
        20: "Production",
        30: "Complete",
        40: "Cancelled",
    }
    
    today = datetime.now().date()
    
    for build in builds:
        build["status_text"] = status_text_map.get(build.get("status"), "Unknown")
        
        # Add part name
        pid = build.get("part")
        if pid:
            part = await provider.get_part(pid)
            if part:
                build["part_name"] = part.get("name")
        
        # Calculate overdue
        target_date = build.get("target_date")
        if target_date and isinstance(target_date, str):
            try:
                target = datetime.fromisoformat(target_date.replace("Z", "+00:00")).date()
                build["is_overdue"] = target < today and build.get("status") in (10, 20)
            except ValueError:
                build["is_overdue"] = False
        else:
            build["is_overdue"] = False
    
    logger.info(f"Found {len(builds)} build orders")
    
    return builds[:limit]


@ai_function
async def get_build_order_lines(
    build_id: int,
    include_stock: bool = True,
    include_allocated: bool = True,
) -> list[dict[str, Any]]:
    """
    Get BOM line items for a build order with allocation status.
    
    Shows what components are needed and what has been allocated/consumed.
    
    Args:
        build_id: The build order ID
        include_stock: Include current stock for each component (default True)
        include_allocated: Include allocation details (default True)
    
    Returns:
        List of build lines, each containing:
        - pk: Build line ID
        - build: Build order ID
        - bom_item: BOM item ID
        - part: Component part ID
        - part_name: Component name
        - part_ipn: Component IPN
        - quantity: Required quantity (per unit * build quantity)
        - allocated: Quantity allocated from stock
        - unallocated: Quantity still needed
        - is_fully_allocated: True if all required quantity allocated
        - reference: Reference designators
        - optional: Whether component is optional
        
        If include_stock=True:
        - in_stock: Current available stock
        - can_allocate: True if enough stock to fully allocate
        
        If include_allocated=True:
        - allocations: List of stock allocations for this line
    
    Example:
        lines = await get_build_order_lines(build_id=50)
        for line in lines:
            status = "✓" if line['is_fully_allocated'] else "✗"
            print(f"{status} {line['part_name']}: {line['allocated']}/{line['quantity']}")
    """
    provider = get_data_provider()
    
    logger.info(f"Getting build order lines for build {build_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        lines = await client._request(
            "GET",
            "/build/line/",
            params={"build": build_id, "limit": 500},
        )
        if isinstance(lines, dict) and "results" in lines:
            lines = lines["results"]
    except Exception as e:
        logger.warning(f"Could not get build order lines: {e}")
        lines = []
    
    # Enrich each line
    for line in lines:
        part_id = line.get("part")
        if part_id:
            part = await provider.get_part(part_id)
            if part:
                line["part_name"] = part.get("name")
                line["part_ipn"] = part.get("IPN")
            
            if include_stock:
                stock_qty = await provider.get_stock_quantity(part_id)
                line["in_stock"] = stock_qty
        
        # Calculate allocation status
        required = line.get("quantity", 0)
        allocated = line.get("allocated", 0)
        line["unallocated"] = max(0, required - allocated)
        line["is_fully_allocated"] = allocated >= required
        
        if include_stock and "in_stock" in line:
            line["can_allocate"] = line["in_stock"] >= line["unallocated"]
        
        if include_allocated:
            # Get allocations for this line
            try:
                allocations = await client._request(
                    "GET",
                    "/build/item/",
                    params={"build_line": line.get("pk"), "limit": 100},
                )
                if isinstance(allocations, dict) and "results" in allocations:
                    allocations = allocations["results"]
                line["allocations"] = allocations
            except Exception:
                line["allocations"] = []
    
    logger.info(f"Found {len(lines)} build lines for build {build_id}")
    
    return lines


# Export all read tools for sales/manufacturing
SALES_READ_TOOLS = [
    get_sales_orders,
    get_sales_order_lines,
    get_customers,
    get_build_orders,
    get_build_order_lines,
]

__all__ = [
    "get_sales_orders",
    "get_sales_order_lines",
    "get_customers",
    "get_build_orders",
    "get_build_order_lines",
    "SALES_READ_TOOLS",
]
