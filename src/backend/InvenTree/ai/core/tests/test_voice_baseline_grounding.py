"""Baseline regressions against independent native stock and target fixtures."""

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from ai.core.integrations.inventree.client import InvenTreeClient
from ai.core.tests.focus_test_db import ensure_island_tables
from ai.core.tools.inventree.read.stock import (
    category_stock_summary,
    current_stock_summary,
    get_bom,
)
from assets import ai_read as machine_reads
from assets.ai_read import _numbered_name_variants
from assets.models import AssetMachine
from django.db.models import Q
from part.models import Part, PartCategory
from stock.models import StockItem, StockLocation
from tasks import ai_read
from tasks.models import WorkOrder


@pytest.fixture(scope="module", autouse=True)
def _database():
    ensure_island_tables()


@pytest.fixture(autouse=True)
def _no_background_worker():
    # Native rows are real; unrelated pricing/notification jobs must never
    # contact the developer's Redis or background worker from this fixture.
    models = (WorkOrder, AssetMachine, StockItem, Part, PartCategory, StockLocation)
    existing = {model: set(model.objects.values_list("pk", flat=True)) for model in models}
    with patch("InvenTree.tasks.offload_task", return_value=False):
        try:
            yield
        finally:
            # This island shares its scratch database between modules. Remove
            # this test's rows in FK order, preserving earlier fixtures. In
            # particular, machine-less Work is legitimately shared and must
            # not leak into a later actor's trend population.
            for model in models:
                model.objects.exclude(pk__in=existing[model]).delete()


@pytest.fixture
def stock_fixture():
    suffix = uuid.uuid4().hex[:8]
    category = PartCategory.objects.create(name=f"Stock baseline {suffix}")
    template = Part.objects.create(name=f"Template {suffix}", category=category, is_template=True)
    part = Part.objects.create(
        name=f"Revision {suffix}", IPN="00123", category=category, variant_of=template
    )
    empty = Part.objects.create(name=f"Empty {suffix}", category=category)
    location = StockLocation.objects.create(name=f"Shelf {suffix}")
    for quantity in (Decimal("1.25"), Decimal("2.75")):
        StockItem.objects.create(part=part, quantity=quantity, location=location, batch="")
    StockItem.objects.create(part=part, quantity=900, location=location, status=60, batch="")
    StockItem.objects.create(part=part, quantity=800, location=location, status=70, batch="")
    with (
        patch(
            "ai.core.tools.inventree.read.database._current_user",
            return_value=SimpleNamespace(is_authenticated=True),
        ),
        patch("users.permissions.check_user_role", return_value=True),
    ):
        yield part, template, empty, category


def test_exact_current_stock_excludes_variants_and_historical_rows(stock_fixture):
    part, template, empty, _category = stock_fixture
    result = current_stock_summary(part.pk)
    assert result["part_ipn"] == "00123"
    assert Decimal(result["total_in_stock"]) == Decimal("4.00")
    assert Decimal(result["available_stock"]) == Decimal("4.00")
    assert result["item_count"] == 2
    assert len(result["locations"]) == 1
    assert result["locations"][0]["name"].startswith("Shelf ")
    assert Decimal(result["locations"][0]["quantity"]) == Decimal("4.00")
    for candidate in (template, empty):
        zero = current_stock_summary(candidate.pk)
        assert zero["resolved"] is True
        assert Decimal(zero["total_in_stock"]) == 0


def test_category_total_counts_each_physical_row_once_and_ranking_returns_ties(stock_fixture):
    part, _template, _empty, category = stock_fixture
    tied = Part.objects.create(name=f"Tie {uuid.uuid4().hex[:8]}", category=category)
    StockItem.objects.create(part=tied, quantity=4, batch="")
    summary = category_stock_summary(category.pk, None)
    assert summary["part_count"] == 2
    assert Decimal(summary["total_in_stock"]) == 8
    highest = category_stock_summary(category.pk, None, highest_stock=True)
    assert {row["part_id"] for row in highest["parts"]} == {part.pk, tied.pk}
    assert all(Decimal(row["total_stock"]) == 4 for row in highest["parts"])
    location_id = StockItem.objects.filter(part=part, status=10).first().location_id
    assert Decimal(current_stock_summary(part.pk, location_id)["total_in_stock"]) == 4


def test_native_part_summary_does_not_stop_at_a_stock_api_page(stock_fixture):
    part, _template, _empty, _category = stock_fixture
    StockItem.objects.bulk_create([StockItem(part=part, quantity=1, batch="") for _ in range(205)])
    result = current_stock_summary(part.pk)
    assert Decimal(result["total_in_stock"]) == 209
    assert result["item_count"] == 207


def test_native_stock_denies_without_an_actor_instead_of_using_service_credentials():
    with patch("ai.core.tools.inventree.read.database._current_user", return_value=None):
        assert current_stock_summary(1)["resolved"] is False
        assert category_stock_summary(1, None)["resolved"] is False


def test_work_order_numeric_lookup_stays_inside_the_authorized_population():
    visible = WorkOrder.objects.create(title=f"Visible {uuid.uuid4().hex[:8]}")
    hidden = WorkOrder.objects.create(title=f"Hidden {uuid.uuid4().hex[:8]}")
    with (
        patch.object(ai_read, "maintenance_ai_read_enabled", return_value=True),
        patch.object(ai_read, "work_order_scope_filter", return_value=Q(pk=visible.pk)),
    ):
        user = SimpleNamespace(is_authenticated=True)
        assert ai_read.work_orders_page(user, query=str(visible.pk))["rows"] == [visible]
        assert ai_read.work_orders_page(user, query=visible.reference)["rows"] == [visible]
        assert ai_read.work_orders_page(user, query=str(hidden.pk))["rows"] == []


def test_numbered_machine_names_do_not_strip_leading_zeros_or_normalize_serials():
    assert "Pump No. 2" in _numbered_name_variants("Pump number two")
    assert "Pump No. 002" in _numbered_name_variants("Pump number 002")
    assert _numbered_name_variants("SERIAL-002") == ()


def test_numbered_machine_alias_resolves_only_inside_the_authorized_population():
    suffix = uuid.uuid4().hex[:8]
    visible = AssetMachine.objects.create(name=f"Pump No. 2 {suffix}")
    AssetMachine.objects.create(name=f"Pump No. 3 {suffix}")
    with (
        patch.object(machine_reads, "machine_ai_read_enabled", return_value=True),
        patch("tasks.scope.machine_scope_filter", return_value=Q(pk=visible.pk)),
    ):
        user = SimpleNamespace(is_authenticated=True)
        assert machine_reads.machines_page(user, query=f"Pump number two {suffix}")["rows"] == [
            visible
        ]
        assert machine_reads.machines_page(user, query=f"Pump number three {suffix}")["rows"] == []


@pytest.mark.asyncio
async def test_bom_fetches_every_page_and_refuses_repeated_pages():
    client = object.__new__(InvenTreeClient)
    with patch.object(
        client,
        "_request",
        new=AsyncMock(
            side_effect=[
                {"results": [{"pk": 1}], "count": 2, "next": "next-page"},
                {"results": [{"pk": 2}], "count": 2, "next": None},
            ]
        ),
    ) as fetch:
        assert [row["pk"] for row in await client.get_bom(77)] == [1, 2]
        assert fetch.call_args_list[1].kwargs["params"]["offset"] == 1
    with (
        patch.object(
            client,
            "_request",
            new=AsyncMock(
                return_value={
                    "results": [{"pk": 1}],
                    "count": 2,
                    "next": "next-page",
                }
            ),
        ),
        pytest.raises(ValueError, match="repeated"),
    ):
        await client.get_bom(77)


@pytest.mark.asyncio
async def test_bom_keeps_native_availability_and_separates_arithmetic_from_readiness():
    provider = SimpleNamespace(
        get_part=AsyncMock(
            side_effect=[{"name": "Assembly", "assembly": True}, {"name": "Bolt"}, {"name": "Bolt"}]
        ),
        get_bom_items=AsyncMock(
            return_value=[
                {"pk": 1, "sub_part": 2, "quantity": 2, "available_stock": 3, "can_build": 1},
                {"pk": 2, "sub_part": 2, "quantity": 3, "available_stock": 3, "can_build": 1},
            ]
        ),
        get_stock_quantity=AsyncMock(return_value=10),
    )
    with patch("ai.core.tools.inventree.read.stock.get_data_provider", return_value=provider):
        result = await get_bom(77)
    assert result["buildable_quantity"] is None
    assert result["arithmetic_buildable_quantity"] == 2
    assert all(row["available_stock"] == 3 and row["can_build"] == 1 for row in result["items"])


@pytest.mark.asyncio
async def test_bom_inheritance_flag_is_not_mistaken_for_the_defining_assembly():
    provider = SimpleNamespace(
        get_part=AsyncMock(
            side_effect=[{"name": "Assembly", "assembly": True}, {"name": "Own component"}]
        ),
        get_bom_items=AsyncMock(
            return_value=[
                {"pk": 1, "part": 77, "sub_part": 2, "quantity": 1, "inherited": True},
                {"pk": 2, "part": 7, "sub_part": 3, "quantity": 1, "inherited": True},
            ]
        ),
    )
    with patch("ai.core.tools.inventree.read.stock.get_data_provider", return_value=provider):
        result = await get_bom(77, include_inherited=False, include_stock=False)
    assert [row["pk"] for row in result["items"]] == [1]
    assert result["items"][0]["inherited"] is True


@pytest.mark.asyncio
async def test_unresolved_required_component_cannot_produce_a_buildability_estimate():
    provider = SimpleNamespace(
        get_part=AsyncMock(side_effect=[{"name": "Assembly", "assembly": True}, None]),
        get_bom_items=AsyncMock(return_value=[{"part": 77, "sub_part": 2, "quantity": 1}]),
        get_stock_quantity=AsyncMock(),
    )
    with patch("ai.core.tools.inventree.read.stock.get_data_provider", return_value=provider):
        result = await get_bom(77)
    assert result["arithmetic_buildable_quantity"] is None
    assert result["buildable_quantity"] is None
    provider.get_stock_quantity.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_stock_tools_use_the_live_native_summary(stock_fixture):
    from ai.core.integrations.inventory_tools import get_stock_levels
    from ai.core.tools.inventree.read.stock import get_stock_level

    part, _template, _empty, _category = stock_fixture
    single = await get_stock_level(part.pk)
    unified = await get_stock_levels(part_id=part.pk)
    assert single["resolved"] is True
    assert Decimal(single["quantity"]) == 4
    assert unified["resolved"] is True
    assert Decimal(unified["total_in_stock"]) == 4
