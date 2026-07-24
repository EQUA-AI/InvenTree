"""
Company Write Tools

Write tools for managing companies (suppliers, manufacturers, customers) in InvenTree.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function
from ai.core.tools.inventree.base import require_hitl

logger = logging.getLogger(__name__)


@ai_function
@require_hitl(reason="Creating a company")
async def create_company(
    name: str,
    is_supplier: bool = False,
    is_manufacturer: bool = False,
    is_customer: bool = False,
    description: str | None = None,
    website: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    address: str | None = None,
    currency: str | None = None,
    link: str | None = None,
) -> dict[str, Any]:
    """
    Create a new company.

    Creates a company record that can be a supplier, manufacturer,
    and/or customer. At least one role must be specified.

    Args:
        name: Company name (required)
        is_supplier: True if company supplies parts
        is_manufacturer: True if company manufactures parts
        is_customer: True if company is a customer
        description: Company description
        website: Company website URL
        phone: Phone number
        email: Email address
        address: Physical address
        currency: Default currency code (e.g., "USD")
        link: External link

    Returns:
        Created company data

    Example:
        # Create a supplier
        company = await create_company(
            name="Acme Electronics",
            is_supplier=True,
            is_manufacturer=True,
            website="https://acme-electronics.com",
            email="sales@acme-electronics.com"
        )
    """
    if not any([is_supplier, is_manufacturer, is_customer]):
        raise ValueError("Company must be at least one of: supplier, manufacturer, or customer")

    logger.info(f"Creating company: {name}")

    data: dict[str, Any] = {
        "name": name,
        "is_supplier": is_supplier,
        "is_manufacturer": is_manufacturer,
        "is_customer": is_customer,
    }

    if description:
        data["description"] = description
    if website:
        data["website"] = website
    if phone:
        data["phone"] = phone
    if email:
        data["email"] = email
    if address:
        data["address"] = address
    if currency:
        data["currency"] = currency
    if link:
        data["link"] = link

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", "/company/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Created company pk={result.get('pk')}")
            return result

        return {"error": "Unexpected response format"}

    except Exception as e:
        logger.error(f"Failed to create company: {e}")
        raise


@ai_function
@require_hitl(reason="Updating a company")
async def update_company(
    company_id: int,
    name: str | None = None,
    is_supplier: bool | None = None,
    is_manufacturer: bool | None = None,
    is_customer: bool | None = None,
    description: str | None = None,
    website: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    address: str | None = None,
    currency: str | None = None,
    active: bool | None = None,
) -> dict[str, Any]:
    """
    Update a company's properties.

    Modifies metadata of an existing company record.

    Args:
        company_id: The company ID to update (required)
        name: New company name
        is_supplier: Update supplier status
        is_manufacturer: Update manufacturer status
        is_customer: Update customer status
        description: New description
        website: New website URL
        phone: New phone number
        email: New email address
        address: New physical address
        currency: New default currency
        active: Set active status (False to deactivate)

    Returns:
        Updated company data

    Example:
        # Update company contact info
        result = await update_company(
            company_id=5,
            email="new-sales@acme.com",
            phone="+1-555-123-4567"
        )
    """
    logger.info(f"Updating company {company_id}")

    data: dict[str, Any] = {}

    if name is not None:
        data["name"] = name
    if is_supplier is not None:
        data["is_supplier"] = is_supplier
    if is_manufacturer is not None:
        data["is_manufacturer"] = is_manufacturer
    if is_customer is not None:
        data["is_customer"] = is_customer
    if description is not None:
        data["description"] = description
    if website is not None:
        data["website"] = website
    if phone is not None:
        data["phone"] = phone
    if email is not None:
        data["email"] = email
    if address is not None:
        data["address"] = address
    if currency is not None:
        data["currency"] = currency
    if active is not None:
        data["active"] = active

    if not data:
        raise ValueError("At least one field must be provided for update")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("PATCH", f"/company/{company_id}/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Updated company {company_id}")
            return result

        return {"success": True, "company_id": company_id}

    except Exception as e:
        logger.error(f"Failed to update company: {e}")
        raise


@ai_function
@require_hitl(reason="Creating a supplier part link")
async def create_supplier_part(
    part_id: int,
    supplier_id: int,
    sku: str,
    manufacturer_part_id: int | None = None,
    description: str | None = None,
    link: str | None = None,
    note: str | None = None,
    pack_quantity: float | None = None,
    pack_quantity_native: float | None = None,
) -> dict[str, Any]:
    """
    Create a supplier part link.

    Links a part to a supplier with SKU and pricing information.

    Args:
        part_id: The part ID (required)
        supplier_id: The supplier company ID (required)
        sku: Supplier's SKU/part number (required)
        manufacturer_part_id: Link to manufacturer part
        description: Supplier's description
        link: Link to supplier's product page
        note: Additional notes
        pack_quantity: Quantity per package
        pack_quantity_native: Native pack quantity

    Returns:
        Created supplier part data

    Example:
        # Link a part to a supplier
        sp = await create_supplier_part(
            part_id=42,
            supplier_id=5,
            sku="ACM-RES-10K-0603",
            description="10K Ohm Resistor 0603",
            link="https://acme.com/products/res-10k"
        )
    """
    logger.info(f"Creating supplier part: part {part_id} from supplier {supplier_id}")

    data: dict[str, Any] = {
        "part": part_id,
        "supplier": supplier_id,
        "SKU": sku,
    }

    if manufacturer_part_id:
        data["manufacturer_part"] = manufacturer_part_id
    if description:
        data["description"] = description
    if link:
        data["link"] = link
    if note:
        data["note"] = note
    if pack_quantity is not None:
        data["pack_quantity"] = pack_quantity
    if pack_quantity_native is not None:
        data["pack_quantity_native"] = pack_quantity_native

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", "/company/part/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Created supplier part pk={result.get('pk')}")
            return result

        return {"error": "Unexpected response format"}

    except Exception as e:
        logger.error(f"Failed to create supplier part: {e}")
        raise


@ai_function
@require_hitl(reason="Creating a manufacturer part link")
async def create_manufacturer_part(
    part_id: int,
    manufacturer_id: int,
    mpn: str,
    description: str | None = None,
    link: str | None = None,
) -> dict[str, Any]:
    """
    Create a manufacturer part link.

    Links a part to a manufacturer with MPN (Manufacturer Part Number).

    Args:
        part_id: The part ID (required)
        manufacturer_id: The manufacturer company ID (required)
        mpn: Manufacturer Part Number (required)
        description: Manufacturer's description
        link: Link to manufacturer's product page

    Returns:
        Created manufacturer part data

    Example:
        # Link a part to a manufacturer
        mp = await create_manufacturer_part(
            part_id=42,
            manufacturer_id=10,
            mpn="RC0603FR-0710KL",
            description="10K Ohm 1% 0603 Thick Film Resistor",
            link="https://yageo.com/products/rc0603fr-0710kl"
        )
    """
    logger.info(f"Creating manufacturer part: part {part_id} from manufacturer {manufacturer_id}")

    data: dict[str, Any] = {
        "part": part_id,
        "manufacturer": manufacturer_id,
        "MPN": mpn,
    }

    if description:
        data["description"] = description
    if link:
        data["link"] = link

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", "/company/part/manufacturer/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Created manufacturer part pk={result.get('pk')}")
            return result

        return {"error": "Unexpected response format"}

    except Exception as e:
        logger.error(f"Failed to create manufacturer part: {e}")
        raise


@ai_function
@require_hitl(reason="Creating a company contact")
async def create_company_contact(
    company_id: int,
    name: str,
    role: str | None = None,
    phone: str | None = None,
    email: str | None = None,
) -> dict[str, Any]:
    """
    Create a contact for a company.

    Adds a contact person to a company record.

    Args:
        company_id: The company ID (required)
        name: Contact person's name (required)
        role: Job title or role
        phone: Phone number
        email: Email address

    Returns:
        Created contact data

    Example:
        # Add a sales contact
        contact = await create_company_contact(
            company_id=5,
            name="John Smith",
            role="Sales Manager",
            email="john.smith@acme.com",
            phone="+1-555-987-6543"
        )
    """
    logger.info(f"Creating contact for company {company_id}: {name}")

    data: dict[str, Any] = {
        "company": company_id,
        "name": name,
    }

    if role:
        data["role"] = role
    if phone:
        data["phone"] = phone
    if email:
        data["email"] = email

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        result = await client._request("POST", "/company/contact/", json_data=data)

        if isinstance(result, dict):
            logger.info(f"Created contact pk={result.get('pk')}")
            return result

        return {"error": "Unexpected response format"}

    except Exception as e:
        logger.error(f"Failed to create contact: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting a company")
async def delete_company(
    company_id: int,
) -> dict[str, Any]:
    """
    Delete a company.

    Args:
        company_id: The company ID to delete

    Returns:
        Success confirmation
    """
    logger.info(f"Deleting company {company_id}")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        await client._request("DELETE", f"/company/{company_id}/")

        logger.info(f"Deleted company {company_id}")
        return {"success": True, "company_id": company_id}

    except Exception as e:
        logger.error(f"Failed to delete company: {e}")
        raise


@ai_function
@require_hitl(reason="Deleting a company contact")
async def delete_company_contact(
    contact_id: int,
) -> dict[str, Any]:
    """
    Delete a company contact.

    Args:
        contact_id: The contact ID to delete

    Returns:
        Success confirmation
    """
    logger.info(f"Deleting company contact {contact_id}")

    try:
        from ai.core.integrations.inventree.client import get_inventree_client

        client = get_inventree_client()

        await client._request("DELETE", f"/company/contact/{contact_id}/")

        logger.info(f"Deleted company contact {contact_id}")
        return {"success": True, "contact_id": contact_id}

    except Exception as e:
        logger.error(f"Failed to delete company contact: {e}")
        raise


# Export all company write tools
COMPANY_WRITE_TOOLS = [
    create_company,
    update_company,
    create_supplier_part,
    create_manufacturer_part,
    create_company_contact,
    delete_company,
    delete_company_contact,
]

__all__ = [
    "COMPANY_WRITE_TOOLS",
    "create_company",
    "create_company_contact",
    "create_manufacturer_part",
    "create_supplier_part",
    "delete_company",
    "delete_company_contact",
    "update_company",
]
