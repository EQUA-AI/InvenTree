"""
AIMMS Unified Data Provider

Provides a unified interface for inventory data access that can switch
between live InvenTree API and demo dataset based on configuration.

This module enables environment-based switching:
- USE_DEMO_DATASET=true  -> Uses DemoDatasetProvider (static JSON data)
- USE_DEMO_DATASET=false -> Uses InvenTreeClient (live API calls)

The provider implements the same interface so workflows can work with
either data source transparently.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Protocol

from ai.core.config import get_settings

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

    async def get_stock_items(self, part_id: int | None = None) -> list[dict[str, Any]]:
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

    async def get_supplier_parts(self, part_id: int) -> list[dict[str, Any]]:
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


class DemoDataProviderAsync:
    """
    Async wrapper around DemoDatasetProvider.

    Wraps the synchronous DemoDatasetProvider with async methods
    to match the InvenTreeClient interface.
    """

    def __init__(self) -> None:
        from ai.core.integrations.demo_dataset import DemoDatasetProvider

        self._provider = DemoDatasetProvider()
        self._is_demo = True
        logger.info("🧪 Using DEMO dataset provider (static data)")

    @property
    def is_demo_mode(self) -> bool:
        """Return True if using demo data."""
        return True

    def get_statistics(self) -> dict[str, int]:
        """Get demo dataset statistics."""
        return self._provider.get_statistics()

    async def search_parts(
        self,
        query: str | None = None,
        category: int | None = None,
        limit: int = 50,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Search for parts."""
        if query:
            return self._provider.search_parts(query=query, limit=limit)
        elif category:
            return self._provider.get_parts_by_category(category)[:limit]
        else:
            return self._provider.get_parts()[:limit]

    async def get_part(self, part_id: int) -> dict[str, Any] | None:
        """Get a single part by ID."""
        return self._provider.get_part(part_id)

    async def get_stock_items(self, part_id: int | None = None) -> list[dict[str, Any]]:
        """Get stock items."""
        return self._provider.get_stock_items(part_id=part_id)

    async def get_stock_quantity(self, part_id: int) -> float:
        """Get stock quantity for a part."""
        return self._provider.get_stock_quantity(part_id)

    async def get_bom_items(self, part_id: int) -> list[dict[str, Any]]:
        """Get BOM items for a part."""
        return self._provider.get_bom_items(part_id)

    async def get_categories(self) -> list[dict[str, Any]]:
        """Get all categories."""
        return self._provider.get_categories()

    async def get_suppliers(self) -> list[dict[str, Any]]:
        """Get all suppliers."""
        return self._provider.get_suppliers()

    async def get_supplier_parts(self, part_id: int) -> list[dict[str, Any]]:
        """Get supplier parts."""
        return self._provider.get_supplier_parts(part_id)

    async def get_low_stock_parts(self, threshold: float | None = None) -> list[dict[str, Any]]:
        """Get low stock parts."""
        return self._provider.get_low_stock_parts(threshold)

    async def get_locations(self) -> list[dict[str, Any]]:
        """Get all locations."""
        return self._provider.get_locations()

    async def get_stock_at_location(self, location_id: int) -> list[dict[str, Any]]:
        """Get stock at a location."""
        return self._provider.get_stock_at_location(location_id)

    async def get_where_used(self, part_id: int) -> list[dict[str, Any]]:
        """Get where a part is used."""
        return self._provider.get_where_used(part_id)

    async def get_part_parameters(self, part_id: int) -> list[dict[str, Any]]:
        """Get parameters for a part."""
        # Demo dataset may not have parameters, return empty list
        if hasattr(self._provider, "get_part_parameters"):
            return self._provider.get_part_parameters(part_id)
        return []

    async def get_part_attachments(self, part_id: int) -> list[dict[str, Any]]:
        """Get attachments for a part."""
        # Demo dataset may not have attachments, return empty list
        if hasattr(self._provider, "get_part_attachments"):
            return self._provider.get_part_attachments(part_id)
        return []

    async def get_part_pricing(
        self,
        part_id: int,
        include_supplier_prices: bool = True,
        include_bom_cost: bool = True,
    ) -> dict[str, Any]:
        """Get pricing information for a part."""
        # Build pricing info from available data
        pricing: dict[str, Any] = {"part_id": part_id}

        part = await self.get_part(part_id)
        if part:
            pricing["internal_price"] = part.get("pricing_data", {})

        if include_supplier_prices:
            supplier_parts = await self.get_supplier_parts(part_id)
            pricing["supplier_prices"] = [
                {
                    "supplier": sp.get("supplier_name", "Unknown"),
                    "sku": sp.get("SKU", ""),
                    "price": sp.get("price", 0),
                }
                for sp in supplier_parts
            ]

        if include_bom_cost:
            bom = await self.get_bom_items(part_id)
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
        """List purchase orders (not supported in demo)."""
        return []

    async def get_purchase_order(self, po_id: int) -> dict[str, Any] | None:
        """Get a single purchase order (not supported in demo)."""
        return None

    async def get_purchase_order_lines(self, po_id: int) -> list[dict[str, Any]]:
        """Get purchase order lines (not supported in demo)."""
        return []

    async def list_sales_orders(
        self,
        customer_id: int | None = None,
        status: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List sales orders (not supported in demo)."""
        return []

    async def get_sales_order(self, so_id: int) -> dict[str, Any] | None:
        """Get a single sales order (not supported in demo)."""
        return None

    async def get_sales_order_lines(self, so_id: int) -> list[dict[str, Any]]:
        """Get sales order lines (not supported in demo)."""
        return []

    async def list_build_orders(
        self,
        part_id: int | None = None,
        status: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List build orders (not supported in demo)."""
        return []

    async def get_build_order(self, bo_id: int) -> dict[str, Any] | None:
        """Get a single build order (not supported in demo)."""
        return None

    async def get_build_order_allocations(self, bo_id: int) -> list[dict[str, Any]]:
        """Get build order allocations (not supported in demo)."""
        return []

    async def create_part(
        self,
        name: str,
        category: int,
        description: str | None = None,
        ipn: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create a new part."""
        from ai.core.tools.read_only import READ_ONLY_MESSAGE, read_only_tools_active

        if read_only_tools_active():
            raise PermissionError(READ_ONLY_MESSAGE)
        return self._provider.create_part(
            name=name,
            category=category,
            description=description,
            ipn=ipn,
            **kwargs,
        )

    async def close(self) -> None:
        """Close the provider (no-op for demo)."""
        pass


class LiveDataProviderAsync:
    """
    Wrapper around InvenTreeClient.

    Provides the same interface as DemoDataProviderAsync but using
    the live InvenTree API.
    """

    def __init__(self) -> None:
        from ai.core.integrations.inventree import InvenTreeClient

        self._client = InvenTreeClient()
        self._is_demo = False
        logger.info("🔴 Using LIVE InvenTree API")

    @property
    def is_demo_mode(self) -> bool:
        """Return False for live mode."""
        return False

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

    async def get_stock_items(self, part_id: int | None = None) -> list[dict[str, Any]]:
        """Get stock items via API."""
        return await self._client.get_stock(part_id=part_id)

    async def get_stock_quantity(self, part_id: int) -> float:
        """Get stock quantity for a part."""
        stock = await self._client.get_stock(part_id=part_id)
        return sum(item.get("quantity", 0) for item in stock)

    async def get_bom_items(self, part_id: int) -> list[dict[str, Any]]:
        """Get BOM items for a part."""
        return await self._client.get_bom(part_id)

    async def get_categories(self) -> list[dict[str, Any]]:
        """Get all categories via API."""
        return await self._client.list_categories()

    async def get_suppliers(self) -> list[dict[str, Any]]:
        """Get all suppliers via API."""
        return await self._client.list_suppliers()

    async def get_supplier_parts(self, part_id: int) -> list[dict[str, Any]]:
        """Get supplier parts via API."""
        return await self._client.get_supplier_parts(part_id)

    async def get_low_stock_parts(self, threshold: float | None = None) -> list[dict[str, Any]]:
        """Get low stock parts via API."""
        return await self._client.check_low_stock(threshold)

    async def get_locations(self) -> list[dict[str, Any]]:
        """Get all locations via API."""
        return await self._client.list_locations()

    async def get_stock_at_location(self, location_id: int) -> list[dict[str, Any]]:
        """Get stock at a location via API."""
        return await self._client.get_stock(location_id=location_id)

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
            supplier_parts = await self._client.get_supplier_parts(part_id)
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

_provider_instance: DemoDataProviderAsync | LiveDataProviderAsync | None = None


def get_data_provider() -> DemoDataProviderAsync | LiveDataProviderAsync:
    """
    Get the appropriate data provider based on configuration.

    Uses USE_DEMO_DATASET environment variable to determine which
    provider to use.

    Returns:
        DemoDataProviderAsync if USE_DEMO_DATASET=true
        LiveDataProviderAsync if USE_DEMO_DATASET=false
    """
    global _provider_instance

    if _provider_instance is None:
        settings = get_settings()

        if settings.use_demo_dataset:
            _provider_instance = DemoDataProviderAsync()
            stats = _provider_instance.get_statistics()
            logger.info(
                f"Demo dataset loaded: {stats['parts']} parts, {stats['stock_items']} stock items"
            )
        else:
            _provider_instance = LiveDataProviderAsync()

    return _provider_instance


def reset_provider() -> None:
    """Reset the provider instance (used when switching modes)."""
    global _provider_instance
    _provider_instance = None


@asynccontextmanager
async def data_provider() -> AsyncGenerator[DemoDataProviderAsync | LiveDataProviderAsync, None]:
    """
    Context manager for data provider.

    Usage:
        async with data_provider() as provider:
            parts = await provider.search_parts("motor")
    """
    # Don't close here as we're using a singleton
    yield get_data_provider()


def is_demo_mode() -> bool:
    """Check if demo mode is currently enabled."""
    settings = get_settings()
    return settings.use_demo_dataset


def get_mode_status() -> dict[str, Any]:
    """Get current mode status information."""
    settings = get_settings()

    status = {
        "mode": "demo" if settings.use_demo_dataset else "live",
        "demo_dataset_path": str(settings.demo_dataset_path),
        "demo_dataset_json": str(settings.demo_dataset_json),
    }

    if settings.use_demo_dataset:
        try:
            provider = DemoDataProviderAsync()
            status["statistics"] = provider.get_statistics()
            status["status"] = "ready"
        except Exception as e:
            status["status"] = "error"
            status["error"] = str(e)
    else:
        from ai.core.config import get_inventree_settings

        inventree_config = get_inventree_settings()
        status["inventree_url"] = inventree_config.url
        status["status"] = "configured"

    return status
