"""
Attachment and Label Write Tools

Tools for managing attachments and labels in InvenTree.
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
    name="add_part_attachment",
    description="Add an attachment (file or link) to a part. Attachments can be documents, images, datasheets, or external URLs that provide additional information about the part.",
)
@require_hitl(reason="Adding attachments to parts requires approval")
async def add_part_attachment(
    part_id: int,
    attachment_type: str,
    comment: str = "",
    link: str = "",
    file_name: str = "",
    file_content_base64: str = "",
) -> dict[str, Any]:
    """
    Add an attachment to a part.

    Args:
        part_id: ID of the part to attach to
        attachment_type: Type of attachment - 'file' or 'link'
        comment: Description or comment for the attachment
        link: URL if attachment_type is 'link'
        file_name: Name of the file if attachment_type is 'file'
        file_content_base64: Base64 encoded file content if attachment_type is 'file'

    Returns:
        Created attachment details
    """
    tool = WriteTool("add_part_attachment")

    if attachment_type not in ["file", "link"]:
        return tool.error_response("Invalid attachment_type. Must be 'file' or 'link'.")

    if attachment_type == "link" and not link:
        return tool.error_response("Link URL is required for link attachments.")

    if attachment_type == "file" and (not file_name or not file_content_base64):
        return tool.error_response("File name and content are required for file attachments.")

    try:
        client = await tool.get_client()

        data = {
            "model_type": "part",
            "model_id": part_id,
            "comment": comment,
        }

        if attachment_type == "link":
            data["link"] = link
            result = await client.post("attachment/", json=data)
        else:
            # For file uploads, we need to use multipart form data
            import base64

            file_bytes = base64.b64decode(file_content_base64)
            files = {"attachment": (file_name, file_bytes)}
            result = await client.post(
                "attachment/",
                data=data,
                files=files,
            )

        logger.info(f"Added attachment to part {part_id}: {result.get('pk')}")
        return tool.success_response(
            data=result,
            message=f"Successfully added {attachment_type} attachment to part {part_id}",
        )

    except Exception as e:
        logger.error(f"Failed to add attachment to part {part_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="delete_attachment",
    description="Delete an attachment from a part, stock item, or other entity. This permanently removes the attachment.",
)
@require_hitl(reason="Deleting attachments requires approval")
async def delete_attachment(
    attachment_id: int,
    attachment_model: str = "part",
) -> dict[str, Any]:
    """
    Delete an attachment.

    Args:
        attachment_id: ID of the attachment to delete
        attachment_model: The model type - 'part', 'stock', 'build', 'order'

    Returns:
        Deletion confirmation
    """
    tool = WriteTool("delete_attachment")

    # Attachments are a single generic model - the per-model endpoints were
    # consolidated into /attachment/ upstream; attachment_model is retained
    # only for interface compatibility
    valid_models = ("part", "stock", "build", "order")
    if attachment_model not in valid_models:
        return tool.error_response(
            f"Invalid attachment_model. Must be one of: {list(valid_models)}"
        )

    try:
        client = await tool.get_client()

        await client.delete(f"attachment/{attachment_id}/")

        logger.info(f"Deleted {attachment_model} attachment {attachment_id}")
        return tool.success_response(
            data={"attachment_id": attachment_id, "deleted": True},
            message=f"Successfully deleted {attachment_model} attachment {attachment_id}",
        )

    except Exception as e:
        logger.error(f"Failed to delete attachment {attachment_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="add_stock_attachment",
    description="Add an attachment to a stock item. Useful for attaching inspection reports, certificates, photos, or other documentation specific to a stock item.",
)
@require_hitl(reason="Adding attachments to stock items requires approval")
async def add_stock_attachment(
    stock_item_id: int,
    attachment_type: str,
    comment: str = "",
    link: str = "",
    file_name: str = "",
    file_content_base64: str = "",
) -> dict[str, Any]:
    """
    Add an attachment to a stock item.

    Args:
        stock_item_id: ID of the stock item to attach to
        attachment_type: Type of attachment - 'file' or 'link'
        comment: Description or comment for the attachment
        link: URL if attachment_type is 'link'
        file_name: Name of the file if attachment_type is 'file'
        file_content_base64: Base64 encoded file content if attachment_type is 'file'

    Returns:
        Created attachment details
    """
    tool = WriteTool("add_stock_attachment")

    if attachment_type not in ["file", "link"]:
        return tool.error_response("Invalid attachment_type. Must be 'file' or 'link'.")

    if attachment_type == "link" and not link:
        return tool.error_response("Link URL is required for link attachments.")

    if attachment_type == "file" and (not file_name or not file_content_base64):
        return tool.error_response("File name and content are required for file attachments.")

    try:
        client = await tool.get_client()

        data = {
            "model_type": "stockitem",
            "model_id": stock_item_id,
            "comment": comment,
        }

        if attachment_type == "link":
            data["link"] = link
            result = await client.post("attachment/", json=data)
        else:
            import base64

            file_bytes = base64.b64decode(file_content_base64)
            files = {"attachment": (file_name, file_bytes)}
            result = await client.post(
                "attachment/",
                data=data,
                files=files,
            )

        logger.info(f"Added attachment to stock item {stock_item_id}: {result.get('pk')}")
        return tool.success_response(
            data=result,
            message=f"Successfully added {attachment_type} attachment to stock item {stock_item_id}",
        )

    except Exception as e:
        logger.error(f"Failed to add attachment to stock item {stock_item_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="print_label",
    description="Print a label for a part, stock item, or location. Generates and sends a label to a configured printer or returns the label data for download.",
)
@require_hitl(reason="Printing labels requires approval")
async def print_label(
    item_type: str,
    item_id: int,
    label_template_id: int | None = None,
    printer_id: int | None = None,
    quantity: int = 1,
) -> dict[str, Any]:
    """
    Print a label for an item.

    Args:
        item_type: Type of item - 'part', 'stock', 'location', 'build'
        item_id: ID of the item to print label for
        label_template_id: ID of the label template to use (optional, uses default if not specified)
        printer_id: ID of the printer to send to (optional, returns PDF if not specified)
        quantity: Number of labels to print

    Returns:
        Print job status or label data
    """
    tool = WriteTool("print_label")

    valid_types = ["part", "stock", "location", "build"]
    if item_type not in valid_types:
        return tool.error_response(f"Invalid item_type. Must be one of: {valid_types}")

    if quantity < 1:
        return tool.error_response("Quantity must be at least 1.")

    try:
        client = await tool.get_client()

        # Printing goes through the consolidated /label/print/ action
        # (the per-model label endpoints were removed upstream). Resolve a
        # template when the caller did not name one.
        if not label_template_id:
            templates = await client.get(
                "label/template/",
                params={"model_type": item_type, "enabled": "true", "limit": 1},
            )
            if isinstance(templates, dict) and "results" in templates:
                templates = templates["results"]
            if not templates:
                return tool.error_response(f"No enabled label template found for {item_type}")
            label_template_id = templates[0].get("pk")

        params = {
            "template": label_template_id,
            "items": [item_id] * quantity,
        }

        if printer_id:
            params["plugin"] = printer_id

        result = await client.post("label/print/", json=params)
        message = f"Label print job submitted for {item_type} {item_id}"

        logger.info(f"Printed label for {item_type} {item_id}")
        return tool.success_response(
            data=result,
            message=message,
        )

    except Exception as e:
        logger.error(f"Failed to print label for {item_type} {item_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="create_label_template",
    description="Create a new label template for printing part, stock, or location labels. Templates use HTML/CSS with Jinja2 templating for dynamic content.",
)
@require_hitl(reason="Creating label templates requires approval")
async def create_label_template(
    name: str,
    label_type: str,
    description: str = "",
    width: float = 50.0,
    height: float = 25.0,
    enabled: bool = True,
    template_content: str = "",
) -> dict[str, Any]:
    """
    Create a new label template.

    Args:
        name: Name of the label template
        label_type: Type of label - 'part', 'stock', 'location', 'build'
        description: Description of the template
        width: Label width in mm
        height: Label height in mm
        enabled: Whether the template is enabled
        template_content: HTML/Jinja2 template content for the label

    Returns:
        Created label template details
    """
    tool = WriteTool("create_label_template")

    valid_types = ["part", "stock", "location", "build"]
    if label_type not in valid_types:
        return tool.error_response(f"Invalid label_type. Must be one of: {valid_types}")

    if not name:
        return tool.error_response("Template name is required.")

    try:
        client = await tool.get_client()

        data = {
            "name": name,
            "description": description,
            "width": width,
            "height": height,
            "enabled": enabled,
            # Templates are generic now; the target model is a field, not
            # part of the URL
            "model_type": label_type,
        }

        endpoint = "label/template/"

        if template_content:
            # Upload template file
            files = {"template": ("template.html", template_content.encode())}
            result = await client.post(endpoint, data=data, files=files)
        else:
            result = await client.post(endpoint, json=data)

        logger.info(f"Created label template '{name}' for {label_type}")
        return tool.success_response(
            data=result,
            message=f"Successfully created {label_type} label template '{name}'",
        )

    except Exception as e:
        logger.error(f"Failed to create label template '{name}': {e}")
        return tool.error_response(str(e))


# Export all attachment write tools
ATTACHMENT_WRITE_TOOLS = [
    add_part_attachment,
    delete_attachment,
    add_stock_attachment,
    print_label,
    create_label_template,
]
