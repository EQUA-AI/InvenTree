"""
Part Test Template Write Tools

Tools for managing part test templates in InvenTree.
Test templates define the tests that must be performed on parts during manufacturing or QC.
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
    name="create_test_template",
    description="Create a new test template for a part. Test templates define tests that must be performed on parts, such as functionality tests, QC checks, or calibration procedures.",
)
@require_hitl(reason="Creating test templates requires approval")
async def create_test_template(
    part_id: int,
    test_name: str,
    description: str = "",
    required: bool = True,
    requires_value: bool = False,
    requires_attachment: bool = False,
) -> dict[str, Any]:
    """
    Create a new test template for a part.

    Args:
        part_id: ID of the part to add the test template to
        test_name: Name of the test (e.g., 'Voltage Test', 'Visual Inspection')
        description: Detailed description of the test procedure
        required: Whether this test is required for part completion
        requires_value: Whether a numeric value must be recorded
        requires_attachment: Whether an attachment must be uploaded

    Returns:
        Created test template details
    """
    tool = WriteTool("create_test_template")

    if not test_name:
        return tool.error_response("Test name is required.")

    try:
        client = await tool.get_client()

        data = {
            "part": part_id,
            "test_name": test_name,
            "description": description,
            "required": required,
            "requires_value": requires_value,
            "requires_attachment": requires_attachment,
        }

        result = await client.post("part/test-template/", json=data)

        logger.info(f"Created test template '{test_name}' for part {part_id}: {result.get('pk')}")
        return tool.success_response(
            data=result,
            message=f"Successfully created test template '{test_name}' for part {part_id}",
        )

    except Exception as e:
        logger.error(f"Failed to create test template '{test_name}': {e}")
        return tool.error_response(str(e))


@ai_function(
    name="update_test_template",
    description="Update an existing test template. Changes affect future tests but not already recorded results.",
)
@require_hitl(reason="Updating test templates requires approval")
async def update_test_template(
    template_id: int,
    test_name: str | None = None,
    description: str | None = None,
    required: bool | None = None,
    requires_value: bool | None = None,
    requires_attachment: bool | None = None,
) -> dict[str, Any]:
    """
    Update a test template.

    Args:
        template_id: ID of the test template to update
        test_name: New name for the test
        description: New description
        required: Whether this test is required
        requires_value: Whether a numeric value must be recorded
        requires_attachment: Whether an attachment must be uploaded

    Returns:
        Updated test template details
    """
    tool = WriteTool("update_test_template")

    try:
        client = await tool.get_client()

        data = {}
        if test_name is not None:
            data["test_name"] = test_name
        if description is not None:
            data["description"] = description
        if required is not None:
            data["required"] = required
        if requires_value is not None:
            data["requires_value"] = requires_value
        if requires_attachment is not None:
            data["requires_attachment"] = requires_attachment

        if not data:
            return tool.error_response("No fields provided to update.")

        result = await client.patch(f"part/test-template/{template_id}/", json=data)

        logger.info(f"Updated test template {template_id}")
        return tool.success_response(
            data=result,
            message=f"Successfully updated test template {template_id}",
        )

    except Exception as e:
        logger.error(f"Failed to update test template {template_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="delete_test_template",
    description="Delete a test template from a part. This does not delete existing test results.",
)
@require_hitl(reason="Deleting test templates requires approval")
async def delete_test_template(
    template_id: int,
) -> dict[str, Any]:
    """
    Delete a test template.

    Args:
        template_id: ID of the test template to delete

    Returns:
        Deletion confirmation
    """
    tool = WriteTool("delete_test_template")

    try:
        client = await tool.get_client()

        # Get template details first
        template = await client.get(f"part/test-template/{template_id}/")

        await client.delete(f"part/test-template/{template_id}/")

        logger.info(f"Deleted test template {template_id} ({template.get('test_name')})")
        return tool.success_response(
            data={
                "template_id": template_id,
                "test_name": template.get("test_name"),
                "deleted": True,
            },
            message=f"Successfully deleted test template '{template.get('test_name')}'",
        )

    except Exception as e:
        logger.error(f"Failed to delete test template {template_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="update_stock_test_result",
    description="Update an existing test result for a stock item. Use this to correct test data or add notes.",
)
@require_hitl(reason="Updating test results requires approval")
async def update_stock_test_result(
    result_id: int,
    result: bool | None = None,
    value: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Update an existing stock test result.

    Args:
        result_id: ID of the test result to update
        result: Pass/fail status (True = pass, False = fail)
        value: New measured/recorded value
        notes: Updated notes or comments

    Returns:
        Updated test result details
    """
    tool = WriteTool("update_stock_test_result")

    try:
        client = await tool.get_client()

        data = {}
        if result is not None:
            data["result"] = result
        if value is not None:
            data["value"] = value
        if notes is not None:
            data["notes"] = notes

        if not data:
            return tool.error_response("No fields provided to update.")

        updated = await client.patch(f"stock/test/{result_id}/", json=data)

        logger.info(f"Updated test result {result_id}")
        return tool.success_response(
            data=updated,
            message=f"Successfully updated test result {result_id}",
        )

    except Exception as e:
        logger.error(f"Failed to update test result {result_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="delete_stock_test_result",
    description="Delete a test result from a stock item. Use with caution as this removes quality/test history.",
)
@require_hitl(reason="Deleting test results removes quality history")
async def delete_stock_test_result(
    result_id: int,
) -> dict[str, Any]:
    """
    Delete a stock test result.

    Args:
        result_id: ID of the test result to delete

    Returns:
        Deletion confirmation
    """
    tool = WriteTool("delete_stock_test_result")

    try:
        client = await tool.get_client()

        # Get result details first
        test_result = await client.get(f"stock/test/{result_id}/")

        await client.delete(f"stock/test/{result_id}/")

        logger.info(f"Deleted test result {result_id}")
        return tool.success_response(
            data={
                "result_id": result_id,
                "test_name": test_result.get("test_name"),
                "stock_item": test_result.get("stock_item"),
                "deleted": True,
            },
            message=f"Successfully deleted test result {result_id}",
        )

    except Exception as e:
        logger.error(f"Failed to delete test result {result_id}: {e}")
        return tool.error_response(str(e))


# Export all test template write tools
TEST_TEMPLATE_WRITE_TOOLS = [
    create_test_template,
    update_test_template,
    delete_test_template,
    update_stock_test_result,
    delete_stock_test_result,
]
