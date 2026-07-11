"""
Parameter Template Write Tools

Tools for managing part parameter templates and values in InvenTree.
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
    name="create_parameter_template",
    description="Create a new parameter template that defines a type of parameter that can be assigned to parts. Templates define the name, units, and validation rules for parameters.",
)
@require_hitl(reason="Creating parameter templates requires approval")
async def create_parameter_template(
    name: str,
    units: str = "",
    description: str = "",
    checkbox: bool = False,
    choices: str = "",
) -> dict[str, Any]:
    """
    Create a new parameter template.

    Args:
        name: Name of the parameter template (e.g., 'Voltage Rating', 'Weight')
        units: Units for the parameter (e.g., 'V', 'kg', 'mm')
        description: Description of what this parameter represents
        checkbox: If True, parameter is a boolean checkbox
        choices: Comma-separated list of valid choices (for dropdown selection)

    Returns:
        Created parameter template details
    """
    tool = WriteTool("create_parameter_template")

    if not name:
        return tool.error_response("Parameter template name is required.")

    try:
        client = await tool.get_client()

        data = {
            "name": name,
            "units": units,
            "description": description,
            "checkbox": checkbox,
        }

        if choices:
            data["choices"] = choices

        result = await client.post("part/parameter/template/", json=data)

        logger.info(f"Created parameter template '{name}': {result.get('pk')}")
        return tool.success_response(
            data=result,
            message=f"Successfully created parameter template '{name}'",
        )

    except Exception as e:
        logger.error(f"Failed to create parameter template '{name}': {e}")
        return tool.error_response(str(e))


@ai_function(
    name="update_parameter_template",
    description="Update an existing parameter template. Changes affect all parts using this template.",
)
@require_hitl(reason="Updating parameter templates affects all parts using it")
async def update_parameter_template(
    template_id: int,
    name: str | None = None,
    units: str | None = None,
    description: str | None = None,
    checkbox: bool | None = None,
    choices: str | None = None,
) -> dict[str, Any]:
    """
    Update a parameter template.

    Args:
        template_id: ID of the parameter template to update
        name: New name for the template
        units: New units for the parameter
        description: New description
        checkbox: Whether parameter is a boolean checkbox
        choices: Comma-separated list of valid choices

    Returns:
        Updated parameter template details
    """
    tool = WriteTool("update_parameter_template")

    try:
        client = await tool.get_client()

        data = {}
        if name is not None:
            data["name"] = name
        if units is not None:
            data["units"] = units
        if description is not None:
            data["description"] = description
        if checkbox is not None:
            data["checkbox"] = checkbox
        if choices is not None:
            data["choices"] = choices

        if not data:
            return tool.error_response("No fields provided to update.")

        result = await client.patch(
            f"part/parameter/template/{template_id}/", json=data
        )

        logger.info(f"Updated parameter template {template_id}")
        return tool.success_response(
            data=result,
            message=f"Successfully updated parameter template {template_id}",
        )

    except Exception as e:
        logger.error(f"Failed to update parameter template {template_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="delete_parameter_template",
    description="Delete a parameter template. This will also delete all parameter values using this template from all parts.",
)
@require_hitl(reason="Deleting parameter templates removes data from all parts")
async def delete_parameter_template(
    template_id: int,
) -> dict[str, Any]:
    """
    Delete a parameter template.

    Args:
        template_id: ID of the parameter template to delete

    Returns:
        Deletion confirmation
    """
    tool = WriteTool("delete_parameter_template")

    try:
        client = await tool.get_client()

        # First get the template to confirm it exists
        template = await client.get(f"part/parameter/template/{template_id}/")

        await client.delete(f"part/parameter/template/{template_id}/")

        logger.info(
            f"Deleted parameter template {template_id} ({template.get('name')})"
        )
        return tool.success_response(
            data={
                "template_id": template_id,
                "name": template.get("name"),
                "deleted": True,
            },
            message=f"Successfully deleted parameter template '{template.get('name')}'",
        )

    except Exception as e:
        logger.error(f"Failed to delete parameter template {template_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="bulk_set_parameters",
    description="Set multiple parameter values for a part in a single operation. Efficiently updates or creates multiple parameters at once.",
)
@require_hitl(reason="Bulk parameter updates require approval")
async def bulk_set_parameters(
    part_id: int,
    parameters: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Set multiple parameters for a part.

    Args:
        part_id: ID of the part to set parameters for
        parameters: List of parameter dicts, each with 'template_id' and 'data' keys
                   Example: [{"template_id": 1, "data": "100"}, {"template_id": 2, "data": "5.5"}]

    Returns:
        Results of all parameter operations
    """
    tool = WriteTool("bulk_set_parameters")

    if not parameters:
        return tool.error_response("No parameters provided.")

    try:
        client = await tool.get_client()

        results = []
        errors = []

        for param in parameters:
            template_id = param.get("template_id")
            data_value = param.get("data")

            if not template_id:
                errors.append({"error": "Missing template_id", "param": param})
                continue

            try:
                # Check if parameter already exists for this part
                existing = await client.get(
                    "part/parameter/",
                    params={"part": part_id, "template": template_id},
                )

                if existing and len(existing) > 0:
                    # Update existing parameter
                    param_id = existing[0]["pk"]
                    result = await client.patch(
                        f"part/parameter/{param_id}/",
                        json={"data": str(data_value)},
                    )
                    results.append({"action": "updated", "result": result})
                else:
                    # Create new parameter
                    result = await client.post(
                        "part/parameter/",
                        json={
                            "part": part_id,
                            "template": template_id,
                            "data": str(data_value),
                        },
                    )
                    results.append({"action": "created", "result": result})

            except Exception as param_error:
                errors.append({
                    "template_id": template_id,
                    "error": str(param_error),
                })

        logger.info(
            f"Bulk set {len(results)} parameters for part {part_id}, {len(errors)} errors"
        )
        return tool.success_response(
            data={
                "part_id": part_id,
                "successful": results,
                "errors": errors,
                "total_updated": len(results),
                "total_errors": len(errors),
            },
            message=f"Set {len(results)} parameters for part {part_id}",
        )

    except Exception as e:
        logger.error(f"Failed to bulk set parameters for part {part_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="copy_parameters",
    description="Copy all parameter values from one part to another. Useful when creating similar parts that should share the same parameter values.",
)
@require_hitl(reason="Copying parameters between parts requires approval")
async def copy_parameters(
    source_part_id: int,
    target_part_id: int,
    overwrite: bool = False,
) -> dict[str, Any]:
    """
    Copy parameters from one part to another.

    Args:
        source_part_id: ID of the part to copy parameters from
        target_part_id: ID of the part to copy parameters to
        overwrite: If True, overwrite existing parameters on target part

    Returns:
        Copy operation results
    """
    tool = WriteTool("copy_parameters")

    if source_part_id == target_part_id:
        return tool.error_response("Source and target parts must be different.")

    try:
        client = await tool.get_client()

        # Get source part parameters
        source_params = await client.get(
            "part/parameter/",
            params={"part": source_part_id},
        )

        if not source_params:
            return tool.success_response(
                data={"copied": 0, "skipped": 0},
                message=f"No parameters found on source part {source_part_id}",
            )

        # Get existing target parameters
        target_params = await client.get(
            "part/parameter/",
            params={"part": target_part_id},
        )
        target_template_ids = {p["template"] for p in target_params}

        copied = 0
        skipped = 0

        for param in source_params:
            template_id = param["template"]

            if template_id in target_template_ids and not overwrite:
                skipped += 1
                continue

            if template_id in target_template_ids and overwrite:
                # Find and update existing parameter
                existing = next(
                    (p for p in target_params if p["template"] == template_id),
                    None,
                )
                if existing:
                    await client.patch(
                        f"part/parameter/{existing['pk']}/",
                        json={"data": param["data"]},
                    )
                    copied += 1
            else:
                # Create new parameter
                await client.post(
                    "part/parameter/",
                    json={
                        "part": target_part_id,
                        "template": template_id,
                        "data": param["data"],
                    },
                )
                copied += 1

        logger.info(
            f"Copied {copied} parameters from part {source_part_id} to {target_part_id}"
        )
        return tool.success_response(
            data={
                "source_part_id": source_part_id,
                "target_part_id": target_part_id,
                "copied": copied,
                "skipped": skipped,
            },
            message=f"Copied {copied} parameters, skipped {skipped}",
        )

    except Exception as e:
        logger.error(
            f"Failed to copy parameters from {source_part_id} to {target_part_id}: {e}"
        )
        return tool.error_response(str(e))


# Export all parameter write tools
PARAMETER_WRITE_TOOLS = [
    create_parameter_template,
    update_parameter_template,
    delete_parameter_template,
    bulk_set_parameters,
    copy_parameters,
]
