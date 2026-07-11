"""
BOM (Bill of Materials) Write Tools

Write tools for managing bill of materials in InvenTree.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function

from ai.core.integrations.data_provider import get_data_provider
from ai.core.tools.inventree.base import require_hitl

logger = logging.getLogger(__name__)


@ai_function
@require_hitl(reason="Adding item to bill of materials")
async def add_bom_item(
    part_id: int,
    sub_part_id: int,
    quantity: float,
    reference: str | None = None,
    note: str | None = None,
    optional: bool = False,
    consumable: bool = False,
    allow_variants: bool = True,
    inherited: bool = False,
) -> dict[str, Any]:
    """
    Add a component to a part's bill of materials.
    
    Creates a new BOM item linking a sub-part to a parent assembly.
    
    Args:
        part_id: Parent/assembly part ID (required)
        sub_part_id: Component part ID to add (required)
        quantity: Quantity required per assembly (required)
        reference: Reference designators (e.g., "R1, R2, R3")
        note: Notes about this BOM item
        optional: If True, component is optional
        consumable: If True, component is consumed but not tracked
        allow_variants: If True, variants of sub_part can be used
        inherited: If True, BOM item is inherited by variants
    
    Returns:
        Created BOM item data
    
    Example:
        # Add 10 resistors to an assembly
        bom_item = await add_bom_item(
            part_id=100,  # PCB Assembly
            sub_part_id=42,  # 10K Resistor
            quantity=10,
            reference="R1-R10",
            note="0603 package"
        )
    """
    # Verify parts exist
    provider = get_data_provider()
    parent = await provider.get_part(part_id)
    if not parent:
        raise ValueError(f"Parent part with ID {part_id} not found")
    
    sub_part = await provider.get_part(sub_part_id)
    if not sub_part:
        raise ValueError(f"Sub-part with ID {sub_part_id} not found")
    
    logger.info(f"Adding BOM item: part {sub_part_id} to assembly {part_id}")
    
    data: dict[str, Any] = {
        "part": part_id,
        "sub_part": sub_part_id,
        "quantity": quantity,
        "optional": optional,
        "consumable": consumable,
        "allow_variants": allow_variants,
        "inherited": inherited,
    }
    
    if reference:
        data["reference"] = reference
    if note:
        data["note"] = note
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/bom/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Added BOM item pk={result.get('pk')}")
            return result
        
        return {"error": "Unexpected response format"}
        
    except Exception as e:
        logger.error(f"Failed to add BOM item: {e}")
        raise


@ai_function
@require_hitl(reason="Updating bill of materials item")
async def update_bom_item(
    bom_item_id: int,
    quantity: float | None = None,
    reference: str | None = None,
    note: str | None = None,
    optional: bool | None = None,
    consumable: bool | None = None,
    allow_variants: bool | None = None,
) -> dict[str, Any]:
    """
    Update an existing BOM item.
    
    Modifies properties of an existing bill of materials entry.
    
    Args:
        bom_item_id: The BOM item ID to update (required)
        quantity: New quantity required
        reference: New reference designators
        note: Updated notes
        optional: Update optional flag
        consumable: Update consumable flag
        allow_variants: Update allow_variants flag
    
    Returns:
        Updated BOM item data
    
    Example:
        # Update quantity and reference
        result = await update_bom_item(
            bom_item_id=55,
            quantity=12,
            reference="R1-R12"
        )
    """
    logger.info(f"Updating BOM item {bom_item_id}")
    
    data: dict[str, Any] = {}
    
    if quantity is not None:
        data["quantity"] = quantity
    if reference is not None:
        data["reference"] = reference
    if note is not None:
        data["note"] = note
    if optional is not None:
        data["optional"] = optional
    if consumable is not None:
        data["consumable"] = consumable
    if allow_variants is not None:
        data["allow_variants"] = allow_variants
    
    if not data:
        raise ValueError("At least one field must be provided for update")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("PATCH", f"/bom/{bom_item_id}/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Updated BOM item {bom_item_id}")
            return result
        
        return {"success": True, "bom_item_id": bom_item_id}
        
    except Exception as e:
        logger.error(f"Failed to update BOM item: {e}")
        raise


@ai_function
@require_hitl(reason="Removing item from bill of materials")
async def delete_bom_item(
    bom_item_id: int,
) -> dict[str, Any]:
    """
    Delete a BOM item from the bill of materials.
    
    Removes a component from an assembly's BOM. This action cannot be undone.
    
    Args:
        bom_item_id: The BOM item ID to delete (required)
    
    Returns:
        Confirmation of deletion
    
    Example:
        # Remove a component from the BOM
        result = await delete_bom_item(bom_item_id=55)
    """
    logger.info(f"Deleting BOM item {bom_item_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        await client._request("DELETE", f"/bom/{bom_item_id}/")
        
        logger.info(f"Deleted BOM item {bom_item_id}")
        return {"success": True, "deleted_id": bom_item_id}
        
    except Exception as e:
        logger.error(f"Failed to delete BOM item: {e}")
        raise


@ai_function
@require_hitl(reason="Substituting BOM component")
async def add_bom_substitute(
    bom_item_id: int,
    substitute_part_id: int,
) -> dict[str, Any]:
    """
    Add a substitute part for a BOM item.
    
    Allows an alternative part to be used in place of the
    specified BOM component.
    
    Args:
        bom_item_id: The BOM item to add substitute for (required)
        substitute_part_id: The substitute part ID (required)
    
    Returns:
        Created substitute data
    
    Example:
        # Add an alternative resistor as substitute
        result = await add_bom_substitute(
            bom_item_id=55,
            substitute_part_id=43  # Alternative 10K resistor
        )
    """
    # Verify substitute part exists
    provider = get_data_provider()
    sub_part = await provider.get_part(substitute_part_id)
    if not sub_part:
        raise ValueError(f"Substitute part with ID {substitute_part_id} not found")
    
    logger.info(f"Adding substitute part {substitute_part_id} to BOM item {bom_item_id}")
    
    data: dict[str, Any] = {
        "bom_item": bom_item_id,
        "part": substitute_part_id,
    }
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/bom/substitute/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Added substitute to BOM item {bom_item_id}")
            return result
        
        return {"success": True, "substitute_part": substitute_part_id}
        
    except Exception as e:
        logger.error(f"Failed to add BOM substitute: {e}")
        raise


@ai_function
@require_hitl(reason="Validating bill of materials")
async def validate_bom(
    part_id: int,
) -> dict[str, Any]:
    """
    Validate a part's bill of materials.
    
    Marks the BOM as validated/approved. A validated BOM indicates
    it has been reviewed and is ready for production.
    
    Args:
        part_id: The part ID whose BOM to validate (required)
    
    Returns:
        Validation result
    
    Example:
        # Validate the BOM for production
        result = await validate_bom(part_id=100)
    """
    # Verify part exists
    provider = get_data_provider()
    part = await provider.get_part(part_id)
    if not part:
        raise ValueError(f"Part with ID {part_id} not found")
    
    logger.info(f"Validating BOM for part {part_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        # InvenTree validates BOM via the part endpoint
        result = await client._request(
            "POST", 
            f"/part/{part_id}/bom-validate/",
            json_data={"valid": True}
        )
        
        if isinstance(result, dict):
            logger.info(f"Validated BOM for part {part_id}")
            return result
        
        return {"success": True, "part_id": part_id, "bom_validated": True}
        
    except Exception as e:
        logger.error(f"Failed to validate BOM: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting BOM substitute")
async def delete_bom_substitute(
    substitute_id: int,
) -> dict[str, Any]:
    """
    Delete a BOM substitute.
    
    Args:
        substitute_id: The BOM substitute ID
        
    Returns:
        Success confirmation
    """
    logger.info(f"Deleting BOM substitute {substitute_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        await client._request("DELETE", f"/bom/substitute/{substitute_id}/")
        
        logger.info(f"Deleted BOM substitute {substitute_id}")
        return {"success": True, "substitute_id": substitute_id}
        
    except Exception as e:
        logger.error(f"Failed to delete BOM substitute: {e}")
        raise


@ai_function
@require_hitl(reason="Updating BOM substitute")
async def update_bom_substitute(
    substitute_id: int,
    part_id: int | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    """
    Update a BOM substitute.
    
    Args:
        substitute_id: The BOM substitute ID
        part_id: New substitute part ID
        priority: New priority
        
    Returns:
        Updated BOM substitute data
    """
    logger.info(f"Updating BOM substitute {substitute_id}")
    
    data: dict[str, Any] = {}
    if part_id is not None:
        data["part"] = part_id
    if priority is not None:
        data["priority"] = priority
        
    if not data:
        raise ValueError("No fields to update provided")
        
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("PATCH", f"/bom/substitute/{substitute_id}/", json_data=data)
        logger.info(f"Updated BOM substitute {substitute_id}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to update BOM substitute: {e}")
        raise


# Export all BOM write tools
BOM_WRITE_TOOLS = [
    add_bom_item,
    update_bom_item,
    delete_bom_item,
    add_bom_substitute,
    validate_bom,
    delete_bom_substitute,
    update_bom_substitute,
]

__all__ = [
    "add_bom_item",
    "update_bom_item",
    "delete_bom_item",
    "add_bom_substitute",
    "validate_bom",
    "delete_bom_substitute",
    "update_bom_substitute",
    "BOM_WRITE_TOOLS",
]
