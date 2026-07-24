"""
Relationship and Notification Read Tools

Read-only tools for part relationships, variants, and user notifications.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.integrations.data_provider import get_data_provider
from ai.core.maf_compat import ai_function

logger = logging.getLogger(__name__)


@ai_function
async def get_part_related(
    part_id: int,
) -> list[dict[str, Any]]:
    """
    Get related parts for a specific part.

    Related parts are parts that are linked together, such as alternates,
    substitutes, or parts that are commonly used together.

    Args:
        part_id: The part ID to get related parts for

    Returns:
        List of related parts, each containing:
        - pk: Relationship ID
        - part_1: First part ID
        - part_1_name: First part name
        - part_2: Second part ID
        - part_2_name: Second part name
        - related_part: The related part (the one that isn't part_id)
        - related_part_name: Related part name
        - related_part_ipn: Related part IPN

    Example:
        related = await get_part_related(part_id=42)
        print(f"Part has {len(related)} related parts:")
        for r in related:
            print(f"  - {r['related_part_name']}")
    """
    provider = get_data_provider()

    logger.info(f"Getting related parts for part {part_id}")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        related = await client._request(
            "GET",
            "/part/related/",
            params={"part": part_id, "limit": 100},
        )
        if isinstance(related, dict) and "results" in related:
            related = related["results"]
    except Exception as e:
        logger.warning(f"Could not get related parts: {e}")
        related = []

    # Enrich with part names and identify the "other" part
    for rel in related:
        part_1 = rel.get("part_1")
        part_2 = rel.get("part_2")

        # Determine which is the related part (not the one we queried)
        related_id = part_2 if part_1 == part_id else part_1
        rel["related_part"] = related_id

        # Get related part info
        if related_id:
            part = await provider.get_part(related_id)
            if part:
                rel["related_part_name"] = part.get("name")
                rel["related_part_ipn"] = part.get("IPN")

        # Also get names for part_1 and part_2
        if part_1:
            p1 = await provider.get_part(part_1)
            if p1:
                rel["part_1_name"] = p1.get("name")
        if part_2:
            p2 = await provider.get_part(part_2)
            if p2:
                rel["part_2_name"] = p2.get("name")

    logger.info(f"Found {len(related)} related parts for part {part_id}")

    return related


@ai_function
async def get_part_variants(
    template_part_id: int,
    include_stock: bool = True,
) -> list[dict[str, Any]]:
    """
    Get all variants of a template part.

    Template parts can have variants - parts that share the same base
    design but differ in specific parameters (e.g., different values
    of a resistor or capacitor).

    Args:
        template_part_id: The template part ID
        include_stock: Include stock levels for each variant (default True)

    Returns:
        List of variant parts, each containing:
        - pk: Part ID
        - name: Part name
        - description: Part description
        - IPN: Internal Part Number
        - variant_of: Template part ID (should match template_part_id)
        - is_active: Whether variant is active
        - variant_parameters: Parameters that differ from template

        If include_stock=True:
        - in_stock: Current stock quantity
        - minimum_stock: Minimum stock threshold

    Example:
        # Get all variants of a resistor template
        variants = await get_part_variants(template_part_id=100)
        for v in variants:
            print(f"{v['name']}: {v['in_stock']} in stock")
    """
    provider = get_data_provider()

    logger.info(f"Getting variants for template part {template_part_id}")

    # First verify this is a template
    template = await provider.get_part(template_part_id)
    if not template:
        raise ValueError(f"Part {template_part_id} not found")

    if not template.get("is_template"):
        return [
            {
                "message": f"Part {template_part_id} is not a template part",
                "is_template": False,
            }
        ]

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        variants = await client._request(
            "GET",
            "/part/",
            params={"variant_of": template_part_id, "limit": 500},
        )
        if isinstance(variants, dict) and "results" in variants:
            variants = variants["results"]
    except Exception as e:
        logger.warning(f"Could not get variants: {e}")
        variants = []

    # Enrich with stock levels
    if include_stock:
        for variant in variants:
            vid = variant.get("pk")
            if vid:
                try:
                    stock_qty = await provider.get_stock_quantity(vid)
                    variant["in_stock"] = stock_qty
                except Exception:
                    variant["in_stock"] = None

    logger.info(f"Found {len(variants)} variants for template {template_part_id}")

    return variants


@ai_function
async def get_notifications(
    read: bool | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Get user notifications from InvenTree.

    Notifications alert users to important events like low stock,
    order status changes, build completions, etc.

    Args:
        read: Filter by read status. True for read only, False for unread only,
             None for all notifications.
        limit: Maximum notifications to return (default 50)

    Returns:
        List of notifications, each containing:
        - pk: Notification ID
        - name: Notification title
        - message: Notification message
        - category: Notification category
        - creation_date: When notification was created
        - read_date: When notification was read (None if unread)
        - is_read: Whether notification has been read
        - target_type: Type of object the notification is about
        - target_id: ID of the target object
        - link: URL to the related object

    Example:
        # Get all unread notifications
        notifications = await get_notifications(read=False)
        print(f"You have {len(notifications)} unread notifications")
        for n in notifications:
            print(f"  [{n['category']}] {n['name']}")
    """
    logger.info(f"Getting notifications, read={read}")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        params: dict[str, Any] = {"limit": limit}
        if read is not None:
            params["read"] = str(read).lower()

        notifications = await client._request(
            "GET",
            "/notifications/",
            params=params,
        )
        if isinstance(notifications, dict) and "results" in notifications:
            notifications = notifications["results"]
    except Exception as e:
        logger.warning(f"Could not get notifications: {e}")
        notifications = []

    # Add is_read convenience field
    for n in notifications:
        n["is_read"] = n.get("read_date") is not None

    logger.info(f"Found {len(notifications)} notifications")

    return notifications[:limit]


# Export final read tools
RELATIONSHIP_READ_TOOLS = [
    get_part_related,
    get_part_variants,
    get_notifications,
]

__all__ = [
    "RELATIONSHIP_READ_TOOLS",
    "get_notifications",
    "get_part_related",
    "get_part_variants",
]
