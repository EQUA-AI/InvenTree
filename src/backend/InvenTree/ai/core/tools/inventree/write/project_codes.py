"""
Project Code Write Tools

Tools for managing project codes in InvenTree.
Project codes are used to track and group related parts, orders, and builds.
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
    name="create_project_code",
    description="Create a new project code for organizing and tracking related inventory items, orders, and builds. Project codes help group items by customer project, internal project, or any logical grouping.",
)
@require_hitl(reason="Creating project codes requires approval")
async def create_project_code(
    code: str,
    description: str = "",
    responsible_user_id: int | None = None,
) -> dict[str, Any]:
    """
    Create a new project code.

    Args:
        code: Unique project code identifier (e.g., 'PROJ-001', 'CUST-ABC')
        description: Description of the project
        responsible_user_id: ID of the user responsible for this project

    Returns:
        Created project code details
    """
    tool = WriteTool("create_project_code")

    if not code:
        return tool.error_response("Project code is required.")

    try:
        client = await tool.get_client()

        data = {
            "code": code,
            "description": description,
        }

        if responsible_user_id:
            data["responsible"] = responsible_user_id

        result = await client.post("project-code/", json=data)

        logger.info(f"Created project code '{code}': {result.get('pk')}")
        return tool.success_response(
            data=result,
            message=f"Successfully created project code '{code}'",
        )

    except Exception as e:
        logger.error(f"Failed to create project code '{code}': {e}")
        return tool.error_response(str(e))


@ai_function(
    name="update_project_code",
    description="Update an existing project code. Modify the description or responsible user.",
)
@require_hitl(reason="Updating project codes requires approval")
async def update_project_code(
    project_code_id: int,
    code: str | None = None,
    description: str | None = None,
    responsible_user_id: int | None = None,
) -> dict[str, Any]:
    """
    Update a project code.

    Args:
        project_code_id: ID of the project code to update
        code: New project code identifier
        description: New description
        responsible_user_id: New responsible user ID

    Returns:
        Updated project code details
    """
    tool = WriteTool("update_project_code")

    try:
        client = await tool.get_client()

        data = {}
        if code is not None:
            data["code"] = code
        if description is not None:
            data["description"] = description
        if responsible_user_id is not None:
            data["responsible"] = responsible_user_id

        if not data:
            return tool.error_response("No fields provided to update.")

        result = await client.patch(f"project-code/{project_code_id}/", json=data)

        logger.info(f"Updated project code {project_code_id}")
        return tool.success_response(
            data=result,
            message=f"Successfully updated project code {project_code_id}",
        )

    except Exception as e:
        logger.error(f"Failed to update project code {project_code_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="delete_project_code",
    description="Delete a project code. This will unlink the project code from any associated items but not delete the items themselves.",
)
@require_hitl(reason="Deleting project codes requires approval")
async def delete_project_code(
    project_code_id: int,
) -> dict[str, Any]:
    """
    Delete a project code.

    Args:
        project_code_id: ID of the project code to delete

    Returns:
        Deletion confirmation
    """
    tool = WriteTool("delete_project_code")

    try:
        client = await tool.get_client()

        # Get project code details first
        project_code = await client.get(f"project-code/{project_code_id}/")

        await client.delete(f"project-code/{project_code_id}/")

        logger.info(f"Deleted project code {project_code_id} ({project_code.get('code')})")
        return tool.success_response(
            data={
                "project_code_id": project_code_id,
                "code": project_code.get("code"),
                "deleted": True,
            },
            message=f"Successfully deleted project code '{project_code.get('code')}'",
        )

    except Exception as e:
        logger.error(f"Failed to delete project code {project_code_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="assign_project_code",
    description="Assign a project code to an order (purchase, sales, build, or return order). This links the order to the project for tracking and reporting.",
)
@require_hitl(reason="Assigning project codes to orders requires approval")
async def assign_project_code(
    project_code_id: int,
    order_type: str,
    order_id: int,
) -> dict[str, Any]:
    """
    Assign a project code to an order.

    Args:
        project_code_id: ID of the project code to assign
        order_type: Type of order - 'purchase', 'sales', 'build', 'return'
        order_id: ID of the order to assign the project code to

    Returns:
        Updated order details with project code
    """
    tool = WriteTool("assign_project_code")

    order_endpoints = {
        "purchase": "order/po",
        "sales": "order/so",
        "build": "build",
        "return": "order/ro",
    }

    if order_type not in order_endpoints:
        return tool.error_response(
            f"Invalid order_type. Must be one of: {list(order_endpoints.keys())}"
        )

    try:
        client = await tool.get_client()

        endpoint = f"{order_endpoints[order_type]}/{order_id}/"
        result = await client.patch(
            endpoint,
            json={"project_code": project_code_id},
        )

        logger.info(f"Assigned project code {project_code_id} to {order_type} order {order_id}")
        return tool.success_response(
            data=result,
            message=f"Successfully assigned project code to {order_type} order {order_id}",
        )

    except Exception as e:
        logger.error(
            f"Failed to assign project code {project_code_id} to {order_type} order {order_id}: {e}"
        )
        return tool.error_response(str(e))


@ai_function(
    name="remove_project_code",
    description="Remove a project code from an order. The order will no longer be associated with the project.",
)
@require_hitl(reason="Removing project codes from orders requires approval")
async def remove_project_code(
    order_type: str,
    order_id: int,
) -> dict[str, Any]:
    """
    Remove a project code from an order.

    Args:
        order_type: Type of order - 'purchase', 'sales', 'build', 'return'
        order_id: ID of the order to remove the project code from

    Returns:
        Updated order details without project code
    """
    tool = WriteTool("remove_project_code")

    order_endpoints = {
        "purchase": "order/po",
        "sales": "order/so",
        "build": "build",
        "return": "order/ro",
    }

    if order_type not in order_endpoints:
        return tool.error_response(
            f"Invalid order_type. Must be one of: {list(order_endpoints.keys())}"
        )

    try:
        client = await tool.get_client()

        endpoint = f"{order_endpoints[order_type]}/{order_id}/"
        result = await client.patch(
            endpoint,
            json={"project_code": None},
        )

        logger.info(f"Removed project code from {order_type} order {order_id}")
        return tool.success_response(
            data=result,
            message=f"Successfully removed project code from {order_type} order {order_id}",
        )

    except Exception as e:
        logger.error(f"Failed to remove project code from {order_type} order {order_id}: {e}")
        return tool.error_response(str(e))


# Export all project code write tools
PROJECT_CODE_WRITE_TOOLS = [
    create_project_code,
    update_project_code,
    delete_project_code,
    assign_project_code,
    remove_project_code,
]
