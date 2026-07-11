"""
Return Orders and Additional Read Tools

Read-only tools for return orders, stock tracking, and other InvenTree data.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ai.core.maf_compat import ai_function

from ai.core.integrations.data_provider import get_data_provider

logger = logging.getLogger(__name__)


@ai_function
async def get_return_orders(
    customer_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Get return orders for tracking customer returns.
    
    Return orders track items being returned from customers, including
    the reason for return and resolution.
    
    Args:
        customer_id: Filter by customer company ID
        status: Filter by status. Options:
               - "pending": Return request received
               - "in_progress": Return being processed
               - "complete": Return completed
               - "cancelled": Return cancelled
        limit: Maximum orders to return (default 50)
    
    Returns:
        List of return orders, each containing:
        - pk: Return order ID
        - reference: Return reference number
        - customer: Customer company ID
        - customer_name: Customer name
        - description: Return description
        - status: Status code
        - status_text: Human-readable status
        - creation_date: When return was created
        - target_date: Target resolution date
        - complete_date: When return was completed
        - line_items_count: Number of line items
        - notes: Return notes
    
    Example:
        # Get all pending returns
        returns = await get_return_orders(status="pending")
        
        # Get returns from a specific customer
        returns = await get_return_orders(customer_id=10)
    """
    logger.info(f"Getting return orders, customer={customer_id}, status={status}")
    
    status_map = {
        "pending": 10,
        "in_progress": 20,
        "complete": 30,
        "cancelled": 40,
    }
    
    status_code = status_map.get(status.lower()) if status else None
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        params: dict[str, Any] = {"limit": limit}
        if customer_id:
            params["customer"] = customer_id
        if status_code:
            params["status"] = status_code
        
        orders = await client._request("GET", "/order/ro/", params=params)
        if isinstance(orders, dict) and "results" in orders:
            orders = orders["results"]
    except Exception as e:
        logger.warning(f"Could not get return orders: {e}")
        orders = []
    
    status_text_map = {
        10: "Pending",
        20: "In Progress",
        30: "Complete",
        40: "Cancelled",
    }
    
    for order in orders:
        order["status_text"] = status_text_map.get(order.get("status"), "Unknown")
    
    logger.info(f"Found {len(orders)} return orders")
    
    return orders[:limit]


@ai_function
async def get_stock_tracking(
    part_id: int | None = None,
    stock_id: int | None = None,
    tracking_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    Get stock tracking history for parts or stock items.
    
    Stock tracking records all movements and changes to stock items,
    including transfers, adjustments, consumption, and receipts.
    
    Args:
        part_id: Filter by part ID (get all tracking for a part)
        stock_id: Filter by stock item ID (get tracking for specific item)
        tracking_type: Filter by tracking type. Options:
                      - "add": Stock additions
                      - "remove": Stock removals
                      - "transfer": Location transfers
                      - "count": Stock counts/adjustments
                      - "build": Consumption in builds
                      - "purchase": Received from PO
                      - "sale": Shipped on SO
        limit: Maximum records to return (default 100)
    
    Returns:
        List of tracking entries, each containing:
        - pk: Tracking entry ID
        - item: Stock item ID
        - date: When the tracking event occurred
        - tracking_type: Type of event
        - quantity: Quantity changed (positive or negative)
        - label: Description of the event
        - notes: Additional notes
        - user: User who made the change
        - deltas: Detailed field changes
    
    Example:
        # Get all tracking for a part
        history = await get_stock_tracking(part_id=42)
        
        # Get transfers for a specific stock item
        history = await get_stock_tracking(stock_id=100, tracking_type="transfer")
    """
    logger.info(f"Getting stock tracking, part={part_id}, stock={stock_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        params: dict[str, Any] = {"limit": limit}
        if part_id:
            params["part"] = part_id
        if stock_id:
            params["item"] = stock_id
        if tracking_type:
            params["tracking_type"] = tracking_type
        
        tracking = await client._request("GET", "/stock/track/", params=params)
        if isinstance(tracking, dict) and "results" in tracking:
            tracking = tracking["results"]
    except Exception as e:
        logger.warning(f"Could not get stock tracking: {e}")
        tracking = []
    
    logger.info(f"Found {len(tracking)} tracking entries")
    
    return tracking[:limit]


@ai_function
async def get_part_test_templates(
    part_id: int,
) -> list[dict[str, Any]]:
    """
    Get test templates defined for a part.
    
    Test templates define quality control tests that should be performed
    on stock items of a part. Tests can be required before stock is used.
    
    Args:
        part_id: The part ID to get test templates for
    
    Returns:
        List of test templates, each containing:
        - pk: Test template ID
        - part: Part ID
        - test_name: Name of the test
        - description: Test description/procedure
        - required: Whether test is required
        - requires_value: Whether test requires a numeric value
        - requires_attachment: Whether test requires file attachment
    
    Example:
        templates = await get_part_test_templates(part_id=42)
        for t in templates:
            req = "Required" if t['required'] else "Optional"
            print(f"{t['test_name']} ({req})")
    """
    logger.info(f"Getting test templates for part {part_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        templates = await client._request(
            "GET",
            "/part/test-template/",
            params={"part": part_id, "limit": 100},
        )
        if isinstance(templates, dict) and "results" in templates:
            templates = templates["results"]
    except Exception as e:
        logger.warning(f"Could not get test templates: {e}")
        templates = []
    
    logger.info(f"Found {len(templates)} test templates for part {part_id}")
    
    return templates


@ai_function
async def get_stock_test_results(
    stock_id: int,
    test_name: str | None = None,
    passed_only: bool | None = None,
) -> list[dict[str, Any]]:
    """
    Get test results for a stock item.
    
    Test results record the outcome of quality control tests performed
    on specific stock items.
    
    Args:
        stock_id: The stock item ID to get test results for
        test_name: Filter by test name
        passed_only: If True, only passed tests. If False, only failed.
    
    Returns:
        List of test results, each containing:
        - pk: Test result ID
        - stock_item: Stock item ID
        - test: Test template name
        - result: Pass/fail result
        - value: Measured value if applicable
        - attachment: Attached file URL if any
        - notes: Test notes
        - user: User who performed the test
        - date: When test was performed
    
    Example:
        results = await get_stock_test_results(stock_id=100)
        passed = sum(1 for r in results if r['result'])
        print(f"{passed}/{len(results)} tests passed")
    """
    logger.info(f"Getting test results for stock item {stock_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        params: dict[str, Any] = {"stock_item": stock_id, "limit": 100}
        if test_name:
            params["test"] = test_name
        
        results = await client._request("GET", "/stock/test/", params=params)
        if isinstance(results, dict) and "results" in results:
            results = results["results"]
    except Exception as e:
        logger.warning(f"Could not get test results: {e}")
        results = []
    
    if passed_only is not None:
        results = [r for r in results if r.get("result") == passed_only]
    
    logger.info(f"Found {len(results)} test results for stock item {stock_id}")
    
    return results


@ai_function
async def get_low_stock_report(
    category_id: int | None = None,
    include_zero_stock: bool = True,
    sort_by: str = "deficit",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Get a report of parts with low or zero stock levels.
    
    Returns parts where current stock is below the minimum stock threshold,
    sorted by severity.
    
    Args:
        category_id: Filter by category ID (includes subcategories)
        include_zero_stock: Include parts with zero stock (default True)
        sort_by: Sort order. Options:
                - "deficit": Largest shortage first (default)
                - "percentage": Lowest stock percentage first
                - "name": Alphabetical by part name
        limit: Maximum parts to return (default 50)
    
    Returns:
        List of low stock parts, each containing:
        - pk: Part ID
        - name: Part name
        - ipn: Internal Part Number
        - category: Category name
        - in_stock: Current stock quantity
        - minimum_stock: Minimum stock threshold
        - deficit: Amount below minimum (minimum - in_stock)
        - percentage: Current stock as percentage of minimum
        - units: Unit of measure
        - default_supplier: Default supplier name
        - last_stocktake: Date of last stock count
        - on_order: Quantity on purchase orders
        - building: Quantity in active builds
    
    Example:
        report = await get_low_stock_report(sort_by="deficit")
        for part in report:
            print(f"{part['name']}: {part['in_stock']}/{part['minimum_stock']} "
                  f"(short {part['deficit']})")
    """
    provider = get_data_provider()
    
    logger.info(f"Generating low stock report, category={category_id}")
    
    # Get low stock parts
    low_stock_parts = await provider.get_low_stock_parts()
    
    # Filter by category if specified
    if category_id is not None:
        categories = await provider.get_categories()
        category_ids = _get_category_with_children(categories, category_id)
        low_stock_parts = [
            p for p in low_stock_parts
            if p.get("category") in category_ids
        ]
    
    # Filter zero stock if needed
    if not include_zero_stock:
        low_stock_parts = [p for p in low_stock_parts if (p.get("in_stock") or 0) > 0]
    
    # Enrich with calculated fields
    for part in low_stock_parts:
        in_stock = part.get("in_stock") or 0
        minimum = part.get("minimum_stock") or 0
        
        part["deficit"] = max(0, minimum - in_stock)
        part["percentage"] = (in_stock / minimum * 100) if minimum > 0 else 0
        part["ipn"] = part.get("IPN", "")
    
    # Sort
    if sort_by == "deficit":
        low_stock_parts.sort(key=lambda p: p.get("deficit", 0), reverse=True)
    elif sort_by == "percentage":
        low_stock_parts.sort(key=lambda p: p.get("percentage", 0))
    elif sort_by == "name":
        low_stock_parts.sort(key=lambda p: p.get("name", "").lower())
    
    logger.info(f"Found {len(low_stock_parts)} low stock parts")
    
    return low_stock_parts[:limit]


def _get_category_with_children(
    categories: list[dict[str, Any]],
    parent_id: int,
) -> set[int]:
    """Get a category ID and all its child category IDs."""
    result = {parent_id}
    for _ in range(5):
        for cat in categories:
            if cat.get("parent") in result:
                result.add(cat.get("pk"))
    return result


# Export all additional read tools
ADDITIONAL_READ_TOOLS = [
    get_return_orders,
    get_stock_tracking,
    get_part_test_templates,
    get_stock_test_results,
    get_low_stock_report,
]

__all__ = [
    "get_return_orders",
    "get_stock_tracking",
    "get_part_test_templates",
    "get_stock_test_results",
    "get_low_stock_report",
    "ADDITIONAL_READ_TOOLS",
]
