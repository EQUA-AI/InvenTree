"""
Address and Contact Write Tools

Tools for managing addresses and contacts for companies in InvenTree.
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
    name="create_company_address",
    description="Create a new address for a company. Addresses can be used for shipping, billing, or other purposes.",
)
@require_hitl(reason="Creating company addresses requires approval")
async def create_company_address(
    company_id: int,
    title: str = "",
    primary: bool = False,
    line1: str = "",
    line2: str = "",
    city: str = "",
    state: str = "",
    postal_code: str = "",
    country: str = "",
    shipping_notes: str = "",
) -> dict[str, Any]:
    """
    Create a new address for a company.

    Args:
        company_id: ID of the company to add address to
        title: Title/name for this address (e.g., 'Headquarters', 'Warehouse')
        primary: Whether this is the primary address
        line1: Street address line 1
        line2: Street address line 2
        city: City name
        state: State/province/region
        postal_code: Postal/ZIP code
        country: Country name or code
        shipping_notes: Special instructions for shipping to this address

    Returns:
        Created address details
    """
    tool = WriteTool("create_company_address")

    try:
        client = await tool.get_client()

        data = {
            "company": company_id,
            "title": title,
            "primary": primary,
            "line1": line1,
            "line2": line2,
            "city": city,
            "state": state,
            "postal_code": postal_code,
            "country": country,
            "shipping_notes": shipping_notes,
        }

        result = await client.post("company/address/", json=data)

        logger.info(f"Created address for company {company_id}: {result.get('pk')}")
        return tool.success_response(
            data=result,
            message=f"Successfully created address '{title}' for company {company_id}",
        )

    except Exception as e:
        logger.error(f"Failed to create address for company {company_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="update_company_address",
    description="Update an existing company address. Modify address details or change primary status.",
)
@require_hitl(reason="Updating company addresses requires approval")
async def update_company_address(
    address_id: int,
    title: str | None = None,
    primary: bool | None = None,
    line1: str | None = None,
    line2: str | None = None,
    city: str | None = None,
    state: str | None = None,
    postal_code: str | None = None,
    country: str | None = None,
    shipping_notes: str | None = None,
) -> dict[str, Any]:
    """
    Update a company address.

    Args:
        address_id: ID of the address to update
        title: New title for the address
        primary: Whether this is the primary address
        line1: Street address line 1
        line2: Street address line 2
        city: City name
        state: State/province/region
        postal_code: Postal/ZIP code
        country: Country name or code
        shipping_notes: Special shipping instructions

    Returns:
        Updated address details
    """
    tool = WriteTool("update_company_address")

    try:
        client = await tool.get_client()

        data = {}
        if title is not None:
            data["title"] = title
        if primary is not None:
            data["primary"] = primary
        if line1 is not None:
            data["line1"] = line1
        if line2 is not None:
            data["line2"] = line2
        if city is not None:
            data["city"] = city
        if state is not None:
            data["state"] = state
        if postal_code is not None:
            data["postal_code"] = postal_code
        if country is not None:
            data["country"] = country
        if shipping_notes is not None:
            data["shipping_notes"] = shipping_notes

        if not data:
            return tool.error_response("No fields provided to update.")

        result = await client.patch(f"company/address/{address_id}/", json=data)

        logger.info(f"Updated address {address_id}")
        return tool.success_response(
            data=result,
            message=f"Successfully updated address {address_id}",
        )

    except Exception as e:
        logger.error(f"Failed to update address {address_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="delete_company_address",
    description="Delete a company address. The address will be permanently removed.",
)
@require_hitl(reason="Deleting company addresses requires approval")
async def delete_company_address(
    address_id: int,
) -> dict[str, Any]:
    """
    Delete a company address.

    Args:
        address_id: ID of the address to delete

    Returns:
        Deletion confirmation
    """
    tool = WriteTool("delete_company_address")

    try:
        client = await tool.get_client()

        # Get address details first
        address = await client.get(f"company/address/{address_id}/")

        await client.delete(f"company/address/{address_id}/")

        logger.info(f"Deleted address {address_id}")
        return tool.success_response(
            data={
                "address_id": address_id,
                "title": address.get("title"),
                "deleted": True,
            },
            message=f"Successfully deleted address '{address.get('title')}'",
        )

    except Exception as e:
        logger.error(f"Failed to delete address {address_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="update_company_contact",
    description="Update an existing company contact. Modify contact details like name, phone, or email.",
)
@require_hitl(reason="Updating company contacts requires approval")
async def update_company_contact(
    contact_id: int,
    name: str | None = None,
    role: str | None = None,
    phone: str | None = None,
    email: str | None = None,
) -> dict[str, Any]:
    """
    Update a company contact.

    Args:
        contact_id: ID of the contact to update
        name: Contact's full name
        role: Contact's role/title
        phone: Phone number
        email: Email address

    Returns:
        Updated contact details
    """
    tool = WriteTool("update_company_contact")

    try:
        client = await tool.get_client()

        data = {}
        if name is not None:
            data["name"] = name
        if role is not None:
            data["role"] = role
        if phone is not None:
            data["phone"] = phone
        if email is not None:
            data["email"] = email

        if not data:
            return tool.error_response("No fields provided to update.")

        result = await client.patch(f"company/contact/{contact_id}/", json=data)

        logger.info(f"Updated contact {contact_id}")
        return tool.success_response(
            data=result,
            message=f"Successfully updated contact {contact_id}",
        )

    except Exception as e:
        logger.error(f"Failed to update contact {contact_id}: {e}")
        return tool.error_response(str(e))


@ai_function(
    name="delete_company_contact",
    description="Delete a company contact. The contact will be permanently removed.",
)
@require_hitl(reason="Deleting company contacts requires approval")
async def delete_company_contact(
    contact_id: int,
) -> dict[str, Any]:
    """
    Delete a company contact.

    Args:
        contact_id: ID of the contact to delete

    Returns:
        Deletion confirmation
    """
    tool = WriteTool("delete_company_contact")

    try:
        client = await tool.get_client()

        # Get contact details first
        contact = await client.get(f"company/contact/{contact_id}/")

        await client.delete(f"company/contact/{contact_id}/")

        logger.info(f"Deleted contact {contact_id}")
        return tool.success_response(
            data={
                "contact_id": contact_id,
                "name": contact.get("name"),
                "deleted": True,
            },
            message=f"Successfully deleted contact '{contact.get('name')}'",
        )

    except Exception as e:
        logger.error(f"Failed to delete contact {contact_id}: {e}")
        return tool.error_response(str(e))


# Export all address write tools
ADDRESS_WRITE_TOOLS = [
    create_company_address,
    update_company_address,
    delete_company_address,
    update_company_contact,
    delete_company_contact,
]
