"""
Category Write Tools

Write tools for managing part and stock location categories in InvenTree.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function

from ai.core.tools.inventree.base import require_hitl

logger = logging.getLogger(__name__)


@ai_function
@require_hitl(reason="Creating a part category")
async def create_part_category(
    name: str,
    parent_id: int | None = None,
    description: str | None = None,
    structural: bool = False,
    default_location_id: int | None = None,
    default_keywords: str | None = None,
) -> dict[str, Any]:
    """
    Create a new part category.
    
    Creates a category for organizing parts. Categories can be
    nested hierarchically.
    
    Args:
        name: Category name (required)
        parent_id: Parent category ID (None for root-level)
        description: Category description
        structural: If True, category is organizational only (no parts)
        default_location_id: Default stock location for parts in this category
        default_keywords: Default keywords for parts in this category
    
    Returns:
        Created category data
    
    Example:
        # Create a top-level category
        cat = await create_part_category(
            name="Electronics",
            description="Electronic components and assemblies"
        )
        
        # Create a subcategory
        subcat = await create_part_category(
            name="Resistors",
            parent_id=cat["pk"],
            default_location_id=5
        )
    """
    logger.info(f"Creating part category: {name}")
    
    data: dict[str, Any] = {
        "name": name,
        "structural": structural,
    }
    
    if parent_id is not None:
        data["parent"] = parent_id
    if description:
        data["description"] = description
    if default_location_id is not None:
        data["default_location"] = default_location_id
    if default_keywords:
        data["default_keywords"] = default_keywords
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/part/category/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Created part category pk={result.get('pk')}")
            return result
        
        return {"error": "Unexpected response format"}
        
    except Exception as e:
        logger.error(f"Failed to create part category: {e}")
        raise


@ai_function
@require_hitl(reason="Updating a part category")
async def update_part_category(
    category_id: int,
    name: str | None = None,
    parent_id: int | None = None,
    description: str | None = None,
    structural: bool | None = None,
    default_location_id: int | None = None,
) -> dict[str, Any]:
    """
    Update a part category's properties.
    
    Modifies an existing part category.
    
    Args:
        category_id: The category ID to update (required)
        name: New category name
        parent_id: New parent category ID
        description: New description
        structural: Update structural flag
        default_location_id: New default stock location
    
    Returns:
        Updated category data
    
    Example:
        # Update category name and move to new parent
        result = await update_part_category(
            category_id=10,
            name="SMD Resistors",
            parent_id=5
        )
    """
    logger.info(f"Updating part category {category_id}")
    
    data: dict[str, Any] = {}
    
    if name is not None:
        data["name"] = name
    if parent_id is not None:
        data["parent"] = parent_id
    if description is not None:
        data["description"] = description
    if structural is not None:
        data["structural"] = structural
    if default_location_id is not None:
        data["default_location"] = default_location_id
    
    if not data:
        raise ValueError("At least one field must be provided for update")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request(
            "PATCH", 
            f"/part/category/{category_id}/",
            json_data=data
        )
        
        if isinstance(result, dict):
            logger.info(f"Updated part category {category_id}")
            return result
        
        return {"success": True, "category_id": category_id}
        
    except Exception as e:
        logger.error(f"Failed to update part category: {e}")
        raise


@ai_function
@require_hitl(reason="Creating a stock location")
async def create_stock_location(
    name: str,
    parent_id: int | None = None,
    description: str | None = None,
    structural: bool = False,
    external: bool = False,
    custom_icon: str | None = None,
) -> dict[str, Any]:
    """
    Create a new stock location.
    
    Creates a location for storing stock items. Locations can be
    nested hierarchically.
    
    Args:
        name: Location name (required)
        parent_id: Parent location ID (None for root-level)
        description: Location description
        structural: If True, location is organizational only (no stock)
        external: If True, location is external (e.g., customer site)
        custom_icon: Custom icon name
    
    Returns:
        Created location data
    
    Example:
        # Create a warehouse location
        loc = await create_stock_location(
            name="Warehouse A",
            description="Main warehouse"
        )
        
        # Create a shelf location
        shelf = await create_stock_location(
            name="Shelf 1",
            parent_id=loc["pk"]
        )
    """
    logger.info(f"Creating stock location: {name}")
    
    data: dict[str, Any] = {
        "name": name,
        "structural": structural,
        "external": external,
    }
    
    if parent_id is not None:
        data["parent"] = parent_id
    if description:
        data["description"] = description
    if custom_icon:
        data["custom_icon"] = custom_icon
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/stock/location/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Created stock location pk={result.get('pk')}")
            return result
        
        return {"error": "Unexpected response format"}
        
    except Exception as e:
        logger.error(f"Failed to create stock location: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting a part category")
async def delete_part_category(
    category_id: int,
    delete_parts: bool = False,
    move_to_parent: bool = True,
) -> dict[str, Any]:
    """
    Delete a part category.
    
    Removes a part category. Parts and subcategories can be
    moved to parent or deleted.
    
    Args:
        category_id: The category ID to delete (required)
        delete_parts: If True, delete all parts in category
        move_to_parent: If True, move contents to parent category
    
    Returns:
        Deletion confirmation
    
    Example:
        # Delete category and move parts to parent
        result = await delete_part_category(
            category_id=10,
            move_to_parent=True
        )
    """
    logger.info(f"Deleting part category {category_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        params = {}
        if delete_parts:
            params["delete_parts"] = True
        if move_to_parent:
            params["cascade"] = False
        
        await client._request(
            "DELETE", 
            f"/part/category/{category_id}/",
            params=params
        )
        
        logger.info(f"Deleted part category {category_id}")
        return {"success": True, "deleted_id": category_id}
        
    except Exception as e:
        logger.error(f"Failed to delete part category: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting a stock location")
async def delete_stock_location(
    location_id: int,
    move_to_parent: bool = True,
) -> dict[str, Any]:
    """
    Delete a stock location.
    
    Removes a stock location. Stock items and sublocations can be
    moved to parent location.
    
    Args:
        location_id: The location ID to delete (required)
        move_to_parent: If True, move contents to parent location
    
    Returns:
        Deletion confirmation
    
    Example:
        # Delete location and move stock to parent
        result = await delete_stock_location(
            location_id=15,
            move_to_parent=True
        )
    """
    logger.info(f"Deleting stock location {location_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        params = {}
        if move_to_parent:
            params["cascade"] = False
        
        await client._request(
            "DELETE", 
            f"/stock/location/{location_id}/",
            params=params
        )
        
        logger.info(f"Deleted stock location {location_id}")
        return {"success": True, "deleted_id": location_id}
        
    except Exception as e:
        logger.error(f"Failed to delete stock location: {e}")
        raise


# Export all category write tools
CATEGORY_WRITE_TOOLS = [
    create_part_category,
    update_part_category,
    create_stock_location,
    delete_part_category,
    delete_stock_location,
]

__all__ = [
    "create_part_category",
    "update_part_category",
    "create_stock_location",
    "delete_part_category",
    "delete_stock_location",
    "CATEGORY_WRITE_TOOLS",
]
