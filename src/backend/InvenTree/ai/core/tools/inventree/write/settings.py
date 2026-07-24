"""
Settings and Admin Write Tools

Tools for managing system settings and administrative functions in InvenTree.
"""

import logging
from typing import Any

from ai.core.tools.inventree.base import (
    WriteTool,
    ai_function,
    require_hitl,
)

logger = logging.getLogger(__name__)


@ai_function(
    name="update_global_setting",
    description="Update a global InvenTree setting. Global settings affect all users and system-wide behavior. Use with caution.",
)
@require_hitl(reason="Updating global settings affects all users")
async def update_global_setting(
    key: str,
    value: str,
) -> dict[str, Any]:
    """
    Update a global system setting.

    Args:
        key: Setting key (e.g., 'INVENTREE_INSTANCE', 'PART_ALLOW_DUPLICATE_IPN')
        value: New value for the setting

    Returns:
        Updated setting details
    """
    tool = WriteTool("update_global_setting")

    if not key:
        return tool.error_response("Setting key is required.")

    try:
        client = await tool.get_client()

        result = await client.patch(
            f"settings/global/{key}/",
            json={"value": value},
        )

        logger.info(f"Updated global setting '{key}' to '{value}'")
        return tool.success_response(
            data=result,
            message=f"Successfully updated global setting '{key}'",
        )

    except Exception as e:
        logger.error(f"Failed to update global setting '{key}': {e}")
        return tool.error_response(str(e))


@ai_function(
    name="update_user_setting",
    description="Update a user-specific setting. These settings only affect the current user's experience.",
)
@require_hitl(reason="Updating user settings requires approval")
async def update_user_setting(
    key: str,
    value: str,
) -> dict[str, Any]:
    """
    Update a user-specific setting.

    Args:
        key: Setting key (e.g., 'HOMEPAGE_PART_STARRED', 'SEARCH_PREVIEW_RESULTS')
        value: New value for the setting

    Returns:
        Updated setting details
    """
    tool = WriteTool("update_user_setting")

    if not key:
        return tool.error_response("Setting key is required.")

    try:
        client = await tool.get_client()

        result = await client.patch(
            f"settings/user/{key}/",
            json={"value": value},
        )

        logger.info(f"Updated user setting '{key}' to '{value}'")
        return tool.success_response(
            data=result,
            message=f"Successfully updated user setting '{key}'",
        )

    except Exception as e:
        logger.error(f"Failed to update user setting '{key}': {e}")
        return tool.error_response(str(e))


@ai_function(
    name="create_custom_state",
    description="Create a custom state for stock items or orders. Custom states allow tracking items through workflow stages specific to your organization.",
)
@require_hitl(reason="Creating custom states affects workflow")
async def create_custom_state(
    name: str,
    label: str,
    model: str,
    color: str = "secondary",
    logical_key: int = 10,
) -> dict[str, Any]:
    """
    Create a custom state.

    Args:
        name: Internal name for the state
        label: Display label for the state
        model: Model type - 'stock', 'build', 'purchaseorder', 'salesorder', 'returnorder'
        color: Bootstrap color class (primary, secondary, success, danger, warning, info)
        logical_key: Numeric key for state ordering

    Returns:
        Created custom state details
    """
    tool = WriteTool("create_custom_state")

    if not name or not label:
        return tool.error_response("State name and label are required.")

    valid_models = ["stock", "build", "purchaseorder", "salesorder", "returnorder"]
    if model not in valid_models:
        return tool.error_response(f"Invalid model. Must be one of: {valid_models}")

    valid_colors = ["primary", "secondary", "success", "danger", "warning", "info"]
    if color not in valid_colors:
        return tool.error_response(f"Invalid color. Must be one of: {valid_colors}")

    try:
        client = await tool.get_client()

        data = {
            "name": name,
            "label": label,
            "model": model,
            "color": color,
            "logical_key": logical_key,
        }

        result = await client.post("generic/status/", json=data)

        logger.info(f"Created custom state '{name}' for {model}")
        return tool.success_response(
            data=result,
            message=f"Successfully created custom state '{label}' for {model}",
        )

    except Exception as e:
        logger.error(f"Failed to create custom state '{name}': {e}")
        return tool.error_response(str(e))


@ai_function(
    name="update_custom_state",
    description="Update an existing custom state. Modify the label, color, or ordering of the state.",
)
@require_hitl(reason="Updating custom states affects workflow")
async def update_custom_state(
    state_id: int,
    name: str | None = None,
    label: str | None = None,
    color: str | None = None,
    logical_key: int | None = None,
) -> dict[str, Any]:
    """
    Update a custom state.

    Args:
        state_id: ID of the custom state to update
        name: New internal name
        label: New display label
        color: New Bootstrap color class
        logical_key: New numeric key for ordering

    Returns:
        Updated custom state details
    """
    tool = WriteTool("update_custom_state")

    try:
        client = await tool.get_client()

        data = {}
        if name is not None:
            data["name"] = name
        if label is not None:
            data["label"] = label
        if color is not None:
            valid_colors = ["primary", "secondary", "success", "danger", "warning", "info"]
            if color not in valid_colors:
                return tool.error_response(f"Invalid color. Must be one of: {valid_colors}")
            data["color"] = color
        if logical_key is not None:
            data["logical_key"] = logical_key

        if not data:
            return tool.error_response("No fields provided to update.")

        result = await client.patch(f"generic/status/{state_id}/", json=data)

        logger.info(f"Updated custom state {state_id}")
        return tool.success_response(
            data=result,
            message=f"Successfully updated custom state {state_id}",
        )

    except Exception as e:
        logger.error(f"Failed to update custom state {state_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="delete_custom_state",
    description="Delete a custom state. Items using this state will need to be reassigned to a different state.",
)
@require_hitl(reason="Deleting custom states may affect existing items")
async def delete_custom_state(
    state_id: int,
) -> dict[str, Any]:
    """
    Delete a custom state.

    Args:
        state_id: ID of the custom state to delete

    Returns:
        Deletion confirmation
    """
    tool = WriteTool("delete_custom_state")

    try:
        client = await tool.get_client()

        # Get state details first
        state = await client.get(f"generic/status/{state_id}/")

        await client.delete(f"generic/status/{state_id}/")

        logger.info(f"Deleted custom state {state_id} ({state.get('label')})")
        return tool.success_response(
            data={
                "state_id": state_id,
                "label": state.get("label"),
                "deleted": True,
            },
            message=f"Successfully deleted custom state '{state.get('label')}'",
        )

    except Exception as e:
        logger.error(f"Failed to delete custom state {state_id}: {e}")
        return tool.error_response(str(e))


# Export all settings write tools
SETTINGS_WRITE_TOOLS = [
    update_global_setting,
    update_user_setting,
    create_custom_state,
    update_custom_state,
    delete_custom_state,
]
