"""
Part Write Tools

Write tools for creating and modifying parts in InvenTree.
These tools require HITL approval for certain operations.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function

from ai.core.integrations.data_provider import get_data_provider
from ai.core.tools.inventree.base import require_hitl

logger = logging.getLogger(__name__)


@ai_function
@require_hitl(reason="Creating a new part in the inventory system")
async def create_part(
    name: str,
    category_id: int,
    description: str | None = None,
    ipn: str | None = None,
    revision: str | None = None,
    keywords: str | None = None,
    is_template: bool = False,
    is_assembly: bool = False,
    is_component: bool = True,
    is_purchaseable: bool = True,
    is_saleable: bool = False,
    is_trackable: bool = False,
    is_virtual: bool = False,
    minimum_stock: float = 0,
    units: str | None = None,
    default_location_id: int | None = None,
    default_supplier_id: int | None = None,
    notes: str | None = None,
    link: str | None = None,
) -> dict[str, Any]:
    """
    Create a new part in the inventory system.
    
    This is a write operation that requires human approval.
    
    Args:
        name: Part name (required)
        category_id: Category ID for the part (required). Use get_categories to find IDs.
        description: Part description
        ipn: Internal Part Number (must be unique if provided)
        revision: Part revision code
        keywords: Searchable keywords (comma-separated)
        is_template: If True, this part is a template for variants
        is_assembly: If True, this part has a BOM (Bill of Materials)
        is_component: If True, can be used as a component in assemblies (default True)
        is_purchaseable: If True, can be purchased from suppliers (default True)
        is_saleable: If True, can be sold to customers
        is_trackable: If True, stock items require serial/batch tracking
        is_virtual: If True, part is virtual (no physical stock)
        minimum_stock: Minimum stock level for low stock alerts (default 0)
        units: Unit of measure (e.g., "pcs", "m", "kg")
        default_location_id: Default stock location ID
        default_supplier_id: Default supplier company ID
        notes: Markdown notes about the part
        link: External URL reference
    
    Returns:
        Created part data including:
        - pk: The new part ID
        - name: Part name
        - full_name: Full hierarchical name
        - IPN: Internal Part Number
        - All other part fields
    
    Example:
        part = await create_part(
            name="Capacitor 100µF 16V",
            category_id=15,  # Capacitors category
            description="Ceramic capacitor, 100µF, 16V, 0805 package",
            ipn="CAP-100UF-16V-0805",
            is_purchaseable=True,
            minimum_stock=100,
        )
        print(f"Created part {part['pk']}: {part['name']}")
    """
    logger.info(f"Creating part: {name} in category {category_id}")
    
    # Build part data
    data: dict[str, Any] = {
        "name": name,
        "category": category_id,
        "component": is_component,
        "purchaseable": is_purchaseable,
        "saleable": is_saleable,
        "trackable": is_trackable,
        "virtual": is_virtual,
        "is_template": is_template,
        "assembly": is_assembly,
        "minimum_stock": minimum_stock,
    }
    
    if description:
        data["description"] = description
    if ipn:
        data["IPN"] = ipn
    if revision:
        data["revision"] = revision
    if keywords:
        data["keywords"] = keywords
    if units:
        data["units"] = units
    if default_location_id:
        data["default_location"] = default_location_id
    if default_supplier_id:
        data["default_supplier"] = default_supplier_id
    if notes:
        data["notes"] = notes
    if link:
        data["link"] = link
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("POST", "/part/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Created part: pk={result.get('pk')}, name={result.get('name')}")
            return result
        
        return {"error": "Unexpected response format"}
        
    except Exception as e:
        logger.error(f"Failed to create part: {e}")
        raise


@ai_function
@require_hitl(reason="Updating part information")
async def update_part(
    part_id: int,
    name: str | None = None,
    description: str | None = None,
    ipn: str | None = None,
    revision: str | None = None,
    keywords: str | None = None,
    category_id: int | None = None,
    is_active: bool | None = None,
    is_purchaseable: bool | None = None,
    is_saleable: bool | None = None,
    is_trackable: bool | None = None,
    minimum_stock: float | None = None,
    units: str | None = None,
    default_location_id: int | None = None,
    notes: str | None = None,
    link: str | None = None,
) -> dict[str, Any]:
    """
    Update an existing part's information.
    
    Only the fields you provide will be updated. Other fields remain unchanged.
    This is a write operation that requires human approval.
    
    Args:
        part_id: The part ID to update (required)
        name: New part name
        description: New description
        ipn: New Internal Part Number
        revision: New revision code
        keywords: New keywords
        category_id: New category ID
        is_active: Set active/inactive status
        is_purchaseable: Update purchaseable status
        is_saleable: Update saleable status
        is_trackable: Update trackable status
        minimum_stock: New minimum stock level
        units: New unit of measure
        default_location_id: New default location ID
        notes: New notes (Markdown)
        link: New external URL
    
    Returns:
        Updated part data with all fields
    
    Example:
        # Update minimum stock level
        part = await update_part(part_id=42, minimum_stock=200)
        
        # Deactivate a part
        part = await update_part(part_id=42, is_active=False)
    """
    provider = get_data_provider()
    
    # Verify part exists
    existing = await provider.get_part(part_id)
    if not existing:
        raise ValueError(f"Part with ID {part_id} not found")
    
    logger.info(f"Updating part {part_id}: {existing.get('name')}")
    
    # Build update data - only include provided fields
    data: dict[str, Any] = {}
    
    if name is not None:
        data["name"] = name
    if description is not None:
        data["description"] = description
    if ipn is not None:
        data["IPN"] = ipn
    if revision is not None:
        data["revision"] = revision
    if keywords is not None:
        data["keywords"] = keywords
    if category_id is not None:
        data["category"] = category_id
    if is_active is not None:
        data["active"] = is_active
    if is_purchaseable is not None:
        data["purchaseable"] = is_purchaseable
    if is_saleable is not None:
        data["saleable"] = is_saleable
    if is_trackable is not None:
        data["trackable"] = is_trackable
    if minimum_stock is not None:
        data["minimum_stock"] = minimum_stock
    if units is not None:
        data["units"] = units
    if default_location_id is not None:
        data["default_location"] = default_location_id
    if notes is not None:
        data["notes"] = notes
    if link is not None:
        data["link"] = link
    
    if not data:
        raise ValueError("No fields to update provided")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("PATCH", f"/part/{part_id}/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Updated part {part_id}")
            return result
        
        return {"error": "Unexpected response format"}
        
    except Exception as e:
        logger.error(f"Failed to update part: {e}")
        raise


@ai_function
@require_hitl(reason="Deactivating a part (soft delete)")
async def deactivate_part(
    part_id: int,
    reason: str | None = None,
) -> dict[str, Any]:
    """
    Deactivate a part (soft delete).
    
    Deactivated parts are not shown in searches by default but their
    data and history are preserved. This is preferred over hard deletion.
    
    Args:
        part_id: The part ID to deactivate
        reason: Optional reason for deactivation (added to notes)
    
    Returns:
        Updated part data showing active=False
    
    Example:
        # Deactivate an obsolete part
        result = await deactivate_part(
            part_id=42,
            reason="Replaced by new design CAP-100UF-25V"
        )
    """
    provider = get_data_provider()
    
    # Verify part exists
    existing = await provider.get_part(part_id)
    if not existing:
        raise ValueError(f"Part with ID {part_id} not found")
    
    logger.info(f"Deactivating part {part_id}: {existing.get('name')}")
    
    data: dict[str, Any] = {"active": False}
    
    # Append reason to notes if provided
    if reason:
        existing_notes = existing.get("notes") or ""
        timestamp = __import__("datetime").datetime.now().isoformat()
        new_note = f"\n\n---\n**Deactivated** ({timestamp}): {reason}"
        data["notes"] = existing_notes + new_note
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        result = await client._request("PATCH", f"/part/{part_id}/", json_data=data)
        
        if isinstance(result, dict):
            logger.info(f"Deactivated part {part_id}")
            return result
        
        return {"error": "Unexpected response format"}
        
    except Exception as e:
        logger.error(f"Failed to deactivate part: {e}")
        raise


@ai_function
@require_hitl(reason="Duplicating a part")
async def duplicate_part(
    source_part_id: int,
    new_name: str,
    new_ipn: str | None = None,
    copy_parameters: bool = True,
    copy_bom: bool = False,
    copy_image: bool = True,
) -> dict[str, Any]:
    """
    Create a duplicate of an existing part.
    
    Useful for creating similar parts based on existing ones.
    Optionally copies parameters, BOM, and images.
    
    Args:
        source_part_id: The part ID to duplicate
        new_name: Name for the new part
        new_ipn: IPN for the new part (must be unique)
        copy_parameters: Copy parameters from source (default True)
        copy_bom: Copy BOM items from source (default False)
        copy_image: Copy part image from source (default True)
    
    Returns:
        The newly created part data
    
    Example:
        # Duplicate a capacitor for a new voltage rating
        new_part = await duplicate_part(
            source_part_id=42,
            new_name="Capacitor 100µF 25V",
            new_ipn="CAP-100UF-25V-0805",
            copy_parameters=True,
        )
    """
    provider = get_data_provider()
    
    # Get source part
    source = await provider.get_part(source_part_id)
    if not source:
        raise ValueError(f"Source part with ID {source_part_id} not found")
    
    logger.info(f"Duplicating part {source_part_id}: {source.get('name')} -> {new_name}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        # Use InvenTree's copy endpoint
        copy_data = {
            "part": source_part_id,
            "name": new_name,
            "copy_image": copy_image,
            "copy_bom": copy_bom,
            "copy_parameters": copy_parameters,
        }
        
        if new_ipn:
            copy_data["IPN"] = new_ipn
        
        result = await client._request("POST", "/part/copy/", json_data=copy_data)
        
        if isinstance(result, dict):
            logger.info(f"Duplicated part to pk={result.get('pk')}")
            return result
        
        return {"error": "Unexpected response format"}
        
    except Exception as e:
        logger.error(f"Failed to duplicate part: {e}")
        raise


@ai_function
@require_hitl(reason="Setting part parameter value")
async def set_part_parameter(
    part_id: int,
    template_id: int | None = None,
    template_name: str | None = None,
    value: str | float | int = "",
) -> dict[str, Any]:
    """
    Set or update a parameter value for a part.
    
    Parameters store technical specifications like voltage, capacitance,
    dimensions, etc. You must specify either template_id or template_name.
    
    Args:
        part_id: The part ID to set parameter for
        template_id: Parameter template ID (use this OR template_name)
        template_name: Parameter template name to find (use this OR template_id)
        value: The parameter value (string, number, or boolean)
    
    Returns:
        The created or updated parameter data
    
    Example:
        # Set voltage rating using template ID
        param = await set_part_parameter(
            part_id=42,
            template_id=5,  # "Voltage Rating" template
            value="16V"
        )
        
        # Set using template name
        param = await set_part_parameter(
            part_id=42,
            template_name="Capacitance",
            value="100µF"
        )
    """
    if template_id is None and template_name is None:
        raise ValueError("Either template_id or template_name must be provided")
    
    provider = get_data_provider()
    
    # Verify part exists
    part = await provider.get_part(part_id)
    if not part:
        raise ValueError(f"Part with ID {part_id} not found")
    
    logger.info(f"Setting parameter for part {part_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        # If template_name provided, find the template ID
        if template_id is None and template_name:
            templates = await client._request(
                "GET",
                "/part/parameter/template/",
                params={"search": template_name, "limit": 10},
            )
            if isinstance(templates, dict) and "results" in templates:
                templates = templates["results"]
            
            # Find exact match
            template = next(
                (t for t in templates if t.get("name", "").lower() == template_name.lower()),
                None
            )
            if not template:
                # Fall back to first result
                template = templates[0] if templates else None
            
            if not template:
                raise ValueError(f"Parameter template '{template_name}' not found")
            
            template_id = template.get("pk")
        
        # Check if parameter already exists for this part
        existing_params = await client._request(
            "GET",
            "/part/parameter/",
            params={"part": part_id, "template": template_id},
        )
        if isinstance(existing_params, dict) and "results" in existing_params:
            existing_params = existing_params["results"]
        
        if existing_params:
            # Update existing parameter
            param_id = existing_params[0].get("pk")
            result = await client._request(
                "PATCH",
                f"/part/parameter/{param_id}/",
                json_data={"data": str(value)},
            )
            logger.info(f"Updated parameter {param_id} for part {part_id}")
        else:
            # Create new parameter
            result = await client._request(
                "POST",
                "/part/parameter/",
                json_data={
                    "part": part_id,
                    "template": template_id,
                    "data": str(value),
                },
            )
            logger.info(f"Created parameter for part {part_id}")
        
        if isinstance(result, dict):
            return result
        
        return {"error": "Unexpected response format"}
        
    except Exception as e:
        logger.error(f"Failed to set parameter: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting part")
async def delete_part(
    part_id: int,
) -> dict[str, Any]:
    """
    Delete a part.
    
    Permanently removes a part from the database.
    WARNING: This action cannot be undone.
    
    Args:
        part_id: The part ID to delete
        
    Returns:
        Success confirmation
    """
    logger.info(f"Deleting part {part_id}")
    
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        await client._request("DELETE", f"/part/{part_id}/")
        
        logger.info(f"Deleted part {part_id}")
        return {"success": True, "part_id": part_id}
        
    except Exception as e:
        logger.error(f"Failed to delete part: {e}")
        raise


# Export all part write tools
PART_WRITE_TOOLS = [
    create_part,
    update_part,
    deactivate_part,
    duplicate_part,
    set_part_parameter,
    delete_part,
]

__all__ = [
    "create_part",
    "update_part",
    "deactivate_part",
    "duplicate_part",
    "set_part_parameter",
    "delete_part",
    "PART_WRITE_TOOLS",
]
