"""
Report Read Tools

Read tools for fetching report templates.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function

logger = logging.getLogger(__name__)


@ai_function
async def get_report_templates(
    model_type: str | None = None,
    enabled: bool | None = True,
    limit: int = 20,
    offset: int = 0,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """
    Get a list of report templates.
    
    Args:
        model_type: Filter by model type (e.g., 'build', 'part', 'purchaseorder', etc.)
        enabled: Filter by enabled status (default: True)
        limit: Max results
        offset: Pagination offset
        search: Search term (name/description)
        
    Returns:
        List of report templates
    """
    params: dict[str, Any] = {
        "limit": limit,
        "offset": offset,
    }
    
    if model_type:
        params["model_type"] = model_type
    if enabled is not None:
        params["enabled"] = enabled
    if search:
        params["search"] = search

    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        response = await client._request("GET", "/report/template/", params=params)
        
        if isinstance(response, dict) and "results" in response:
            return response["results"]
        elif isinstance(response, list):
            return response
        return []

    except Exception as e:
        logger.error(f"Failed to fetch report templates: {e}")
        return []


@ai_function
async def get_report_template_detail(
    template_id: int,
) -> dict[str, Any]:
    """
    Get details for a specific report template.
    
    Args:
        template_id: The template ID (required)
        
    Returns:
        Template details
    """
    try:
        from ai.core.integrations.inventree.client import get_inventree_client
        client = get_inventree_client()
        
        return await client._request("GET", f"/report/template/{template_id}/")

    except Exception as e:
        logger.error(f"Failed to fetch report template {template_id}: {e}")
        raise


# Export
REPORT_READ_TOOLS = [
    get_report_templates,
    get_report_template_detail,
]

__all__ = [
    "get_report_templates",
    "get_report_template_detail",
    "REPORT_READ_TOOLS",
]
