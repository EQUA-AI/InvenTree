"""Live inventory totals must include stock beyond the first API page."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from ai.core.integrations.data_provider import LiveDataProviderAsync


@pytest.mark.asyncio
async def test_total_stock_includes_later_pages():
    provider = object.__new__(LiveDataProviderAsync)
    provider._client = SimpleNamespace(
        get_stock=AsyncMock(
            side_effect=[
                [{"quantity": 1}] * 100,
                [{"quantity": 15}] * 7,
            ]
        )
    )
    assert await provider.get_stock_quantity(42) == 205
    assert provider._client.get_stock.call_args_list == [
        call(part_id=42, limit=100, offset=0),
        call(part_id=42, limit=100, offset=100),
    ]


@pytest.mark.asyncio
async def test_total_stock_propagates_later_page_failure():
    provider = object.__new__(LiveDataProviderAsync)
    provider._client = SimpleNamespace(
        get_stock=AsyncMock(
            side_effect=[
                [{"quantity": 1}] * 100,
                RuntimeError("stock page unavailable"),
            ]
        )
    )
    with pytest.raises(RuntimeError, match="stock page unavailable"):
        await provider.get_stock_quantity(42)


@pytest.mark.asyncio
async def test_total_stock_empty_inventory():
    provider = object.__new__(LiveDataProviderAsync)
    provider._client = SimpleNamespace(get_stock=AsyncMock(return_value=[]))
    assert await provider.get_stock_quantity(42) == 0


def test_inventory_factory_ignores_retired_dataset_switch(monkeypatch, tmp_path):
    """Legacy deployment settings cannot replace real inventory with static data."""
    from importlib import import_module

    from ai.core.config import get_settings

    inventory = import_module("ai.core.integrations.data_provider")
    dataset = tmp_path / "inventory.json"
    dataset.write_text("[]")
    monkeypatch.setenv("USE_DEMO_DATASET", "true")
    monkeypatch.setenv("DEMO_DATASET_JSON", str(dataset))
    live = object()
    monkeypatch.setattr(inventory, "LiveDataProviderAsync", lambda: live)
    get_settings.cache_clear()
    inventory.reset_provider()
    try:
        assert inventory.get_data_provider() is live
    finally:
        inventory.reset_provider()
        get_settings.cache_clear()
