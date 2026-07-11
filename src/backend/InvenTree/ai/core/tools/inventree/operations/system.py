"""
System Operation Tools

Tools for generating reports and running scheduled tasks in InvenTree.
These are high-level operation tools that perform complex system actions.
"""

import logging
from typing import Any

from ai.core.maf_compat import ai_function
from ai.core.tools.inventree.base import (
    WriteTool,
    require_hitl,
)

logger = logging.getLogger(__name__)


@ai_function(
    name="generate_report",
    description="Generate a report for parts, stock, orders, or builds. Reports can be exported in various formats including PDF, CSV, and Excel. Useful for inventory analysis, order summaries, and build tracking.",
)
@require_hitl(reason="Generating reports may access sensitive data")
async def generate_report(
    report_type: str,
    output_format: str = "pdf",
    filters: dict[str, Any] | None = None,
    include_images: bool = False,
) -> dict[str, Any]:
    """
    Generate a report.

    Args:
        report_type: Type of report - 'part', 'stock', 'build', 'purchase_order', 
                     'sales_order', 'return_order', 'bom', 'test_report'
        output_format: Output format - 'pdf', 'csv', 'xlsx'
        filters: Dictionary of filters to apply (e.g., {"category": 1, "active": True})
        include_images: Whether to include part/stock images in the report

    Returns:
        Report generation result with download information
    """
    tool = WriteTool("generate_report")

    valid_report_types = [
        "part",
        "stock",
        "build",
        "purchase_order",
        "sales_order",
        "return_order",
        "bom",
        "test_report",
    ]
    if report_type not in valid_report_types:
        return tool.error_response(
            f"Invalid report_type. Must be one of: {valid_report_types}"
        )

    valid_formats = ["pdf", "csv", "xlsx"]
    if output_format not in valid_formats:
        return tool.error_response(
            f"Invalid output_format. Must be one of: {valid_formats}"
        )

    try:
        client = await tool.get_client()

        # Map report types to endpoints
        report_endpoints = {
            "part": "report/part/",
            "stock": "report/stock/",
            "build": "report/build/",
            "purchase_order": "report/po/",
            "sales_order": "report/so/",
            "return_order": "report/ro/",
            "bom": "report/bom/",
            "test_report": "report/test/",
        }

        endpoint = report_endpoints[report_type]

        # Build request parameters
        params = {
            "export": output_format,
        }

        if filters:
            params.update(filters)

        if include_images:
            params["include_images"] = True

        # Request report generation
        result = await client.get(endpoint, params=params)

        # Check if result contains report data or a task ID for async generation
        if isinstance(result, dict) and "task_id" in result:
            logger.info(
                f"Report generation started for {report_type}, task: {result['task_id']}"
            )
            return tool.success_response(
                data={
                    "report_type": report_type,
                    "output_format": output_format,
                    "status": "generating",
                    "task_id": result["task_id"],
                },
                message=f"Report generation started. Task ID: {result['task_id']}",
            )
        else:
            logger.info(f"Generated {report_type} report in {output_format} format")
            return tool.success_response(
                data={
                    "report_type": report_type,
                    "output_format": output_format,
                    "status": "complete",
                    "result": result,
                },
                message=f"Successfully generated {report_type} report in {output_format} format",
            )

    except Exception as e:
        logger.error(f"Failed to generate {report_type} report: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="run_scheduled_task",
    description="Run a scheduled background task immediately. Tasks include inventory updates, price updates, cleanup operations, and data synchronization. Useful for triggering maintenance operations on demand.",
)
@require_hitl(reason="Running scheduled tasks can affect system data")
async def run_scheduled_task(
    task_name: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Run a scheduled task immediately.

    Args:
        task_name: Name of the task to run:
                   - 'update_exchange_rates': Update currency exchange rates
                   - 'check_for_updates': Check for InvenTree updates
                   - 'delete_old_notifications': Clean up old notifications
                   - 'delete_expired_sessions': Clean up expired user sessions
                   - 'update_part_pricing': Recalculate part pricing data
                   - 'rebuild_search_index': Rebuild the search index
                   - 'cleanup_old_tasks': Remove old background task records
        parameters: Optional parameters for the task

    Returns:
        Task execution result
    """
    tool = WriteTool("run_scheduled_task")

    valid_tasks = [
        "update_exchange_rates",
        "check_for_updates",
        "delete_old_notifications",
        "delete_expired_sessions",
        "update_part_pricing",
        "rebuild_search_index",
        "cleanup_old_tasks",
    ]

    if task_name not in valid_tasks:
        return tool.error_response(
            f"Invalid task_name. Must be one of: {valid_tasks}"
        )

    try:
        client = await tool.get_client()

        # Map task names to their API endpoints/actions
        task_endpoints = {
            "update_exchange_rates": "settings/currency-refresh/",
            "check_for_updates": "settings/version/",
            "delete_old_notifications": "notifications/cleanup/",
            "delete_expired_sessions": "auth/sessions/cleanup/",
            "update_part_pricing": "part/pricing/refresh/",
            "rebuild_search_index": "settings/search-index/rebuild/",
            "cleanup_old_tasks": "background-task/cleanup/",
        }

        endpoint = task_endpoints.get(task_name)

        if not endpoint:
            return tool.error_response(f"Task '{task_name}' endpoint not configured.")

        # Execute the task
        data = parameters or {}
        result = await client.post(endpoint, json=data)

        logger.info(f"Executed scheduled task '{task_name}'")
        return tool.success_response(
            data={
                "task_name": task_name,
                "status": "executed",
                "result": result,
            },
            message=f"Successfully executed task '{task_name}'",
        )

    except Exception as e:
        logger.error(f"Failed to run scheduled task '{task_name}': {e}")
        return tool.error_response(str(e))


# Export all operation tools
OPERATION_TOOLS = [
    generate_report,
    run_scheduled_task,
]
