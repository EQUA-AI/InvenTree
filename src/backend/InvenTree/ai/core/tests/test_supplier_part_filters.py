"""Part and supplier identifiers remain distinct through provider adapters."""

import asyncio
from unittest.mock import AsyncMock, call, patch

import pytest
from ai.core.integrations.data_provider import DemoDataProviderAsync, LiveDataProviderAsync
from ai.core.integrations.demo_dataset import DemoDatasetProvider
from ai.core.tests.test_api_endpoint_migration import _client
from ai.core.tools.inventree.read import purchasing


@pytest.mark.parametrize(
    "filters, expected",
    [
        ({"part_id": 1081}, {"part": 1081}),
        ({"supplier_id": 7}, {"supplier": 7}),
        ({"part_id": 1081, "supplier_id": 7}, {"part": 1081, "supplier": 7}),
    ],
)
def test_api_provider_preserves_filter_identity(filters, expected):
    """Exercise the real adapter and client down to the outgoing HTTP parameters."""
    provider = LiveDataProviderAsync.__new__(LiveDataProviderAsync)
    provider._client, transport = _client()
    asyncio.run(provider.get_supplier_parts(**filters))
    params = transport.requests[0]["params"]
    assert {key: params[key] for key in ("part", "supplier") if key in params} == expected


def test_pricing_requests_suppliers_for_the_part():
    """The pricing helper must not query supplier 1081 for part 1081."""
    provider = LiveDataProviderAsync.__new__(LiveDataProviderAsync)
    provider._client, transport = _client()
    provider._client.get_part = AsyncMock(return_value={})
    asyncio.run(provider.get_part_pricing(1081, include_bom_cost=False))
    params = transport.requests[0]["params"]
    assert params["part"] == 1081
    assert "supplier" not in params


@pytest.mark.parametrize(
    "filters, expected",
    [
        ({"part_id": 7}, [1, 3]),
        ({"supplier_id": 7}, [2, 3]),
        ({"part_id": 7, "supplier_id": 7}, [3]),
    ],
)
def test_demo_provider_matches_live_filter_semantics(filters, expected):
    """Overlapping numeric IDs must not cross the part/supplier boundary."""
    data = DemoDatasetProvider.__new__(DemoDatasetProvider)
    data._loaded = True
    data._data = {
        "company_supplierpart": [
            {"pk": 1, "part": 7, "supplier": 9},
            {"pk": 2, "part": 9, "supplier": 7},
            {"pk": 3, "part": 7, "supplier": 7},
        ]
    }
    provider = DemoDataProviderAsync.__new__(DemoDataProviderAsync)
    provider._provider = data
    result = asyncio.run(provider.get_supplier_parts(**filters))
    assert [row["pk"] for row in result] == expected


def test_supplier_tool_preserves_both_filters_without_scanning_inventory():
    """A supplier lookup goes directly to the filtered endpoint."""
    provider = AsyncMock()
    provider.get_supplier_parts.return_value = []
    with patch.object(purchasing, "get_data_provider", return_value=provider):
        assert asyncio.run(purchasing.get_supplier_parts(part_id=1081, supplier_id=7)) == []
    provider.get_supplier_parts.assert_awaited_once_with(part_id=1081, supplier_id=7)
    provider.search_parts.assert_not_awaited()


def test_supplier_listing_counts_by_supplier_identity():
    """The supplier list uses the supplier filter when checking linked parts."""
    provider = AsyncMock()
    provider.get_suppliers.return_value = [{"pk": 7, "active": True}]
    provider.get_supplier_parts.return_value = [{"pk": 101}]
    with patch.object(purchasing, "get_data_provider", return_value=provider):
        result = asyncio.run(purchasing.get_suppliers(has_parts=True))
    assert result[0]["parts_count"] == 1
    provider.get_supplier_parts.assert_awaited_once_with(supplier_id=7)


@pytest.mark.parametrize("has_parts, expected", [(None, [0, 234]), (True, [234]), (False, [0])])
def test_supplier_listing_reuses_complete_api_counts(has_parts, expected):
    """Zero and counts beyond one page must not trigger supplier-part scans."""
    provider = AsyncMock()
    provider.get_suppliers.return_value = [
        {"pk": 7, "parts_supplied": 0},
        {"pk": 8, "parts_supplied": 234},
    ]
    with patch.object(purchasing, "get_data_provider", return_value=provider):
        result = asyncio.run(purchasing.get_suppliers(has_parts=has_parts))
    assert [row["parts_count"] for row in result] == expected
    provider.get_supplier_parts.assert_not_awaited()


def test_supplier_parts_reuse_embedded_details_across_skus():
    """A large supplier page needs no detail requests for already included parts."""
    provider = AsyncMock()
    provider.get_supplier_parts.return_value = [
        {
            "pk": index,
            "part": index % 40 + 1,
            "part_detail": {"name": f"Part {index % 40 + 1}", "IPN": f"IPN-{index % 40 + 1}"},
            "price": 12,
            "pack_quantity": 3,
        }
        for index in range(100)
    ]
    with patch.object(purchasing, "get_data_provider", return_value=provider):
        result = asyncio.run(purchasing.get_supplier_parts(supplier_id=7))
    assert len(result) == 100
    assert all(row["part_name"] == f"Part {row['part']}" for row in result)
    assert all(row["part_ipn"] == f"IPN-{row['part']}" for row in result)
    assert all(row["effective_price"] == 4 for row in result)
    provider.get_part.assert_not_awaited()


def test_supplier_parts_fallback_is_once_per_part_including_missing_parts():
    """Legacy responses reuse details and missing results within the tool call."""
    provider = AsyncMock()
    provider.get_supplier_parts.return_value = [
        {"pk": index, "part": part_id} for index, part_id in enumerate([1, 2, 1, 2, 3, 3])
    ]
    # A later row can supply details for an earlier SKU for the same part.
    provider.get_supplier_parts.return_value[-1]["part_detail"] = {"name": "Embedded", "IPN": "EMB"}
    provider.get_part.side_effect = [{"name": "Legacy", "IPN": "OLD"}, None]
    with patch.object(purchasing, "get_data_provider", return_value=provider):
        result = asyncio.run(purchasing.get_supplier_parts(supplier_id=7))
    assert [row.get("part_name") for row in result] == [
        "Legacy",
        None,
        "Legacy",
        None,
        "Embedded",
        "Embedded",
    ]
    assert provider.get_part.await_args_list == [call(1), call(2)]


def test_reverse_bom_reuses_returned_parent_details():
    """A filtered BOM page already carries the parent identity and activity."""
    provider = AsyncMock()
    provider.get_where_used.return_value = [
        {"part": 11, "part_detail": {"name": "Assembly", "IPN": "ASM", "active": True}},
        {"part": 12, "part_detail": {"name": "Retired", "active": False}},
    ]
    with patch.object(purchasing, "get_data_provider", return_value=provider):
        result = asyncio.run(purchasing.get_where_used(1081))
    assert [(row["part"], row["part_name"]) for row in result] == [(11, "Assembly")]
    provider.get_part.assert_not_awaited()
