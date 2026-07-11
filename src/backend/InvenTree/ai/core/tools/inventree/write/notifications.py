"""
Notification and Alert Write Tools

Tools for managing notifications, alerts, and user messaging in InvenTree.
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
    name="mark_notification_read",
    description="Mark a notification as read for the current user. This removes it from the unread notifications list.",
)
@require_hitl(reason="Marking notifications as read requires approval")
async def mark_notification_read(
    notification_id: int,
) -> dict[str, Any]:
    """
    Mark a notification as read.

    Args:
        notification_id: ID of the notification to mark as read

    Returns:
        Updated notification status
    """
    tool = WriteTool("mark_notification_read")

    try:
        client = await tool.get_client()

        result = await client.patch(
            f"notifications/{notification_id}/",
            json={"read": True},
        )

        logger.info(f"Marked notification {notification_id} as read")
        return tool.success_response(
            data=result,
            message=f"Successfully marked notification {notification_id} as read",
        )

    except Exception as e:
        logger.error(f"Failed to mark notification {notification_id} as read: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="mark_all_notifications_read",
    description="Mark all notifications as read for the current user. Clears the notification inbox.",
)
@require_hitl(reason="Marking all notifications as read requires approval")
async def mark_all_notifications_read() -> dict[str, Any]:
    """
    Mark all notifications as read.

    Returns:
        Number of notifications marked as read
    """
    tool = WriteTool("mark_all_notifications_read")

    try:
        client = await tool.get_client()

        # Get all unread notifications
        unread = await client.get(
            "notifications/",
            params={"read": False},
        )

        count = 0
        for notification in unread:
            await client.patch(
                f"notifications/{notification['pk']}/",
                json={"read": True},
            )
            count += 1

        logger.info(f"Marked {count} notifications as read")
        return tool.success_response(
            data={"marked_read": count},
            message=f"Successfully marked {count} notifications as read",
        )

    except Exception as e:
        logger.error(f"Failed to mark all notifications as read: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="delete_notification",
    description="Delete a notification permanently. The notification will be removed from the system.",
)
@require_hitl(reason="Deleting notifications requires approval")
async def delete_notification(
    notification_id: int,
) -> dict[str, Any]:
    """
    Delete a notification.

    Args:
        notification_id: ID of the notification to delete

    Returns:
        Deletion confirmation
    """
    tool = WriteTool("delete_notification")

    try:
        client = await tool.get_client()

        await client.delete(f"notifications/{notification_id}/")

        logger.info(f"Deleted notification {notification_id}")
        return tool.success_response(
            data={"notification_id": notification_id, "deleted": True},
            message=f"Successfully deleted notification {notification_id}",
        )

    except Exception as e:
        logger.error(f"Failed to delete notification {notification_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="create_notification",
    description="Create a notification for a user or group. Used to send alerts about important events, reminders, or system messages.",
)
@require_hitl(reason="Creating notifications requires approval")
async def create_notification(
    target_user_id: int | None = None,
    target_group_id: int | None = None,
    name: str = "",
    message: str = "",
    category: str = "info",
    link: str = "",
) -> dict[str, Any]:
    """
    Create a notification.

    Args:
        target_user_id: ID of the user to notify (optional if group specified)
        target_group_id: ID of the group to notify (optional if user specified)
        name: Short title/name for the notification
        message: Full notification message
        category: Notification category - 'info', 'warning', 'error', 'success'
        link: Optional URL link related to the notification

    Returns:
        Created notification details
    """
    tool = WriteTool("create_notification")

    if not target_user_id and not target_group_id:
        return tool.error_response(
            "Either target_user_id or target_group_id is required."
        )

    if not name:
        return tool.error_response("Notification name is required.")

    valid_categories = ["info", "warning", "error", "success"]
    if category not in valid_categories:
        return tool.error_response(
            f"Invalid category. Must be one of: {valid_categories}"
        )

    try:
        client = await tool.get_client()

        data = {
            "name": name,
            "message": message,
            "category": category,
        }

        if target_user_id:
            data["target_user"] = target_user_id
        if target_group_id:
            data["target_group"] = target_group_id
        if link:
            data["link"] = link

        result = await client.post("notifications/", json=data)

        logger.info(f"Created notification '{name}'")
        return tool.success_response(
            data=result,
            message=f"Successfully created notification '{name}'",
        )

    except Exception as e:
        logger.error(f"Failed to create notification '{name}': {e}")
        return tool.error_response(str(e))


@ai_function(
    name="send_stock_alert",
    description="Send an alert about stock levels. Notifies relevant users when stock is low, out of stock, or requires attention.",
)
@require_hitl(reason="Sending stock alerts requires approval")
async def send_stock_alert(
    part_id: int,
    alert_type: str,
    message: str = "",
    notify_users: list[int] | None = None,
) -> dict[str, Any]:
    """
    Send a stock alert notification.

    Args:
        part_id: ID of the part to alert about
        alert_type: Type of alert - 'low_stock', 'out_of_stock', 'overstock', 'expiring'
        message: Custom message for the alert
        notify_users: List of user IDs to notify (optional, uses defaults if not specified)

    Returns:
        Alert sending confirmation
    """
    tool = WriteTool("send_stock_alert")

    valid_alert_types = ["low_stock", "out_of_stock", "overstock", "expiring"]
    if alert_type not in valid_alert_types:
        return tool.error_response(
            f"Invalid alert_type. Must be one of: {valid_alert_types}"
        )

    try:
        client = await tool.get_client()

        # Get part details for the alert
        part = await client.get(f"part/{part_id}/")

        # Build alert message
        alert_name = f"Stock Alert: {part.get('name', f'Part {part_id}')}"
        if not message:
            alert_messages = {
                "low_stock": f"Low stock warning for {part.get('name')}",
                "out_of_stock": f"Out of stock: {part.get('name')}",
                "overstock": f"Overstock condition for {part.get('name')}",
                "expiring": f"Stock expiring soon for {part.get('name')}",
            }
            message = alert_messages.get(alert_type, f"Stock alert for {part.get('name')}")

        # Create notifications for specified users or use system defaults
        notifications_created = 0

        if notify_users:
            for user_id in notify_users:
                await client.post(
                    "notifications/",
                    json={
                        "name": alert_name,
                        "message": message,
                        "category": "warning",
                        "target_user": user_id,
                        "link": f"/part/{part_id}/",
                    },
                )
                notifications_created += 1
        else:
            # Create a general notification (system will route based on settings)
            result = await client.post(
                "notifications/",
                json={
                    "name": alert_name,
                    "message": message,
                    "category": "warning",
                    "link": f"/part/{part_id}/",
                },
            )
            notifications_created = 1

        logger.info(
            f"Sent stock alert '{alert_type}' for part {part_id} to {notifications_created} users"
        )
        return tool.success_response(
            data={
                "part_id": part_id,
                "alert_type": alert_type,
                "notifications_created": notifications_created,
            },
            message=f"Successfully sent {alert_type} alert for part {part_id}",
        )

    except Exception as e:
        logger.error(f"Failed to send stock alert for part {part_id}: {e}")
        return tool.error_response(str(e))


# Export all notification write tools
NOTIFICATION_WRITE_TOOLS = [
    mark_notification_read,
    mark_all_notifications_read,
    delete_notification,
    create_notification,
    send_stock_alert,
]
