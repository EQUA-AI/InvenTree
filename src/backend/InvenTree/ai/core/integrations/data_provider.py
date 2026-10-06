"""AIMMS inventory access through the configured InvenTree API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


class InventoryProvider(Protocol):
    """Protocol defining the inventory data provider interface."""

    async def search_parts(
        self,
        query: str | None = None,
        category: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search for parts by query or category."""
        ...

    async def get_part(self, part_id: int) -> dict[str, Any] | None:
        """Get a single part by ID."""
        ...

    async def get_stock_items(
        self, part_id: int | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Get stock items, optionally filtered by part."""
        ...

    async def get_stock_quantity(self, part_id: int) -> float:
        """Get total stock quantity for a part."""
        ...

    async def get_bom_items(self, part_id: int) -> list[dict[str, Any]]:
        """Get BOM items for a part."""
        ...

    async def get_categories(self) -> list[dict[str, Any]]:
        """Get all part categories."""
        ...

    async def get_suppliers(self) -> list[dict[str, Any]]:
        """Get all suppliers."""
        ...

    async def get_supplier_parts(
        self, part_id: int | None = None, *, supplier_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Get supplier information for a part."""
        ...

    async def get_low_stock_parts(self, threshold: float | None = None) -> list[dict[str, Any]]:
        """Get parts with stock below minimum threshold."""
        ...

    async def get_locations(self) -> list[dict[str, Any]]:
        """Get all stock locations."""
        ...

    async def get_stock_at_location(self, location_id: int) -> list[dict[str, Any]]:
        """Get stock items at a specific location."""
        ...

    async def get_where_used(self, part_id: int) -> list[dict[str, Any]]:
        """Get assemblies where a part is used."""
        ...

    async def get_part_parameters(self, part_id: int) -> list[dict[str, Any]]:
        """Get parameters for a part."""
        ...

    async def get_part_attachments(self, part_id: int) -> list[dict[str, Any]]:
        """Get attachments for a part."""
        ...

    async def get_part_pricing(
        self,
        part_id: int,
        include_supplier_prices: bool = True,
        include_bom_cost: bool = True,
    ) -> dict[str, Any]:
        """Get pricing information for a part."""
        ...

    async def list_purchase_orders(
        self,
        supplier_id: int | None = None,
        status: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List purchase orders."""
        ...

    async def get_purchase_order(self, po_id: int) -> dict[str, Any] | None:
        """Get a single purchase order."""
        ...

    async def get_purchase_order_lines(self, po_id: int) -> list[dict[str, Any]]:
        """Get lines for a purchase order."""
        ...

    async def list_sales_orders(
        self,
        customer_id: int | None = None,
        status: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List sales orders."""
        ...

    async def get_sales_order(self, so_id: int) -> dict[str, Any] | None:
        """Get a single sales order."""
        ...

    async def get_sales_order_lines(self, so_id: int) -> list[dict[str, Any]]:
        """Get lines for a sales order."""
        ...

    async def list_build_orders(
        self,
        part_id: int | None = None,
        status: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List build orders."""
        ...

    async def get_build_order(self, bo_id: int) -> dict[str, Any] | None:
        """Get a single build order."""
        ...

    async def get_build_order_allocations(self, bo_id: int) -> list[dict[str, Any]]:
        """Get allocations for a build order."""
        ...


class LiveDataProviderAsync:
    """Async inventory operations backed by the configured InvenTree client."""

    def __init__(self) -> None:
        from ai.core.integrations.inventree import InvenTreeClient

        self._client = InvenTreeClient()
        logger.info("🔴 Using LIVE InvenTree API")

    async def search_parts(
        self,
        query: str | None = None,
        category: int | None = None,
        limit: int = 50,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Search for parts via API."""
        return await self._client.search_parts(
            query=query,
            category=category,
            limit=limit,
            **kwargs,
        )

    async def get_part(self, part_id: int) -> dict[str, Any] | None:
        """Get a single part by ID."""
        return await self._client.get_part(part_id)

    async def get_stock_items(
        self, part_id: int | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Get stock items via API."""
        if limit is not None:
            return await self._client.get_stock(part_id=part_id, limit=limit)
        return await self._client.get_stock(part_id=part_id)

    async def get_stock_quantity(self, part_id: int) -> float:
        """Sum every stock page so large inventories are not undercounted."""
        total = 0.0
        offset = 0
        page_size = 100
        while True:
            stock = await self._client.get_stock(part_id=part_id, limit=page_size, offset=offset)
            total += sum(item.get("quantity", 0) for item in stock)
            if len(stock) < page_size:
                return total
            offset += len(stock)

    async def get_bom_items(self, part_id: int) -> list[dict[str, Any]]:
        """Get BOM items for a part."""
        return await self._client.get_bom(part_id)

    async def get_categories(self) -> list[dict[str, Any]]:
        """Get all categories via API."""
        return await self._client.list_categories()

    async def get_suppliers(self) -> list[dict[str, Any]]:
        """Get all suppliers via API."""
        return await self._client.list_suppliers()

    async def get_supplier_parts(
        self, part_id: int | None = None, *, supplier_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Get supplier parts via API."""
        return await self._client.get_supplier_parts(part_id=part_id, supplier_id=supplier_id)

    async def get_low_stock_parts(self, threshold: float | None = None) -> list[dict[str, Any]]:
        """Get low stock parts via API."""
        return await self._client.check_low_stock(threshold)

    async def get_locations(self) -> list[dict[str, Any]]:
        """Get all locations via API."""
        return await self._client.list_locations()

    async def get_stock_at_location(self, location_id: int) -> list[dict[str, Any]]:
        """Get stock at a location via API."""
        return await self._client.get_stock(location=location_id)

    async def get_where_used(self, part_id: int) -> list[dict[str, Any]]:
        """Get where a part is used via API."""
        return await self._client.get_where_used(part_id)

    async def get_part_parameters(self, part_id: int) -> list[dict[str, Any]]:
        """Get parameters for a part via API."""
        return await self._client.get_part_parameters(part_id)

    async def get_part_attachments(self, part_id: int) -> list[dict[str, Any]]:
        """Get attachments for a part via API."""
        return await self._client.get_part_attachments(part_id)

    async def get_part_pricing(
        self,
        part_id: int,
        include_supplier_prices: bool = True,
        include_bom_cost: bool = True,
    ) -> dict[str, Any]:
        """Get pricing information for a part via API."""
        pricing: dict[str, Any] = {"part_id": part_id}

        # Get internal pricing from part endpoint
        part = await self._client.get_part(part_id)
        if part:
            pricing["internal_price"] = part.get("pricing_data", {})

        if include_supplier_prices:
            supplier_parts = await self._client.get_supplier_parts(part_id=part_id)
            pricing["supplier_prices"] = [
                {
                    "supplier": sp.get("supplier_name", "Unknown"),
                    "sku": sp.get("SKU", ""),
                    "price": sp.get("price", 0),
                }
                for sp in supplier_parts
            ]

        if include_bom_cost:
            bom = await self._client.get_bom(part_id)
            if bom:
                total = sum(item.get("total_price", 0) or 0 for item in bom)
                pricing["bom_cost"] = total

        return pricing

    async def list_purchase_orders(
        self,
        supplier_id: int | None = None,
        status: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List purchase orders via API."""
        return await self._client.list_purchase_orders(
            supplier_id=supplier_id,
            status=status,
            limit=limit,
        )

    async def get_purchase_order(self, po_id: int) -> dict[str, Any] | None:
        """Get a single purchase order via API."""
        return await self._client.get_purchase_order(po_id)

    async def get_purchase_order_lines(self, po_id: int) -> list[dict[str, Any]]:
        """Get purchase order lines via API."""
        return await self._client.get_purchase_order_lines(po_id)

    async def list_sales_orders(
        self,
        customer_id: int | None = None,
        status: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List sales orders via API."""
        return await self._client.list_sales_orders(
            customer_id=customer_id,
            status=status,
            limit=limit,
        )

    async def get_sales_order(self, so_id: int) -> dict[str, Any] | None:
        """Get a single sales order via API."""
        return await self._client.get_sales_order(so_id)

    async def get_sales_order_lines(self, so_id: int) -> list[dict[str, Any]]:
        """Get sales order lines via API."""
        return await self._client.get_sales_order_lines(so_id)

    async def list_build_orders(
        self,
        part_id: int | None = None,
        status: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List build orders via API."""
        return await self._client.list_build_orders(
            part_id=part_id,
            status=status,
            limit=limit,
        )

    async def get_build_order(self, bo_id: int) -> dict[str, Any] | None:
        """Get a single build order via API."""
        return await self._client.get_build_order(bo_id)

    async def get_build_order_allocations(self, bo_id: int) -> list[dict[str, Any]]:
        """Get build order allocations via API."""
        return await self._client.get_build_order_allocations(bo_id)

    async def create_part(
        self,
        name: str,
        category: int,
        description: str | None = None,
        ipn: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create a new part via API."""
        return await self._client.create_part(
            name=name,
            category=category,
            description=description,
            ipn=ipn,
            **kwargs,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.close()


# -----------------------------------------------------------------------------
# Factory Functions
# -----------------------------------------------------------------------------

_provider_instance: LiveDataProviderAsync | None = None


def get_data_provider() -> LiveDataProviderAsync:
    """Return the shared API-backed inventory provider."""
    global _provider_instance

    if _provider_instance is None:
        _provider_instance = LiveDataProviderAsync()

    return _provider_instance


def reset_provider() -> None:
    """Reset the shared provider instance."""
    global _provider_instance
    _provider_instance = None


@asynccontextmanager
async def data_provider() -> AsyncGenerator[LiveDataProviderAsync, None]:
    """
    Context manager for data provider.

    Usage:
        async with data_provider() as provider:
            parts = await provider.search_parts("motor")
    """
    # Don't close here as we're using a singleton
    yield get_data_provider()


def get_mode_status() -> dict[str, Any]:
    """Report the configured inventory API without opening a connection."""
    from ai.core.config import get_inventree_settings

    inventree_config = get_inventree_settings()
    return {"mode": "live", "inventree_url": inventree_config.url, "status": "configured"}
