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


@pytest.mark.asyncio
async def test_exact_ipn_requires_a_revision_and_never_repairs_missing_zeros(stock_fixture):
    from ai.core.tools.inventree.read.parts import get_part, resolve_exact_ipn
    from asgiref.sync import sync_to_async

    def setup():
        part, _template, _empty, category = stock_fixture
        part.revision = "REV-A"
        part.save()
        other = Part.objects.create(
            name="Other revision", IPN="00123", revision="REV-B", category=category
        )
        return other

    other = await sync_to_async(setup)()
    provider = SimpleNamespace(
        get_part=AsyncMock(return_value={"pk": other.pk, "IPN": "00123", "revision": "REV-B"}),
        get_stock_quantity=AsyncMock(return_value=0),
    )
    with patch("ai.core.tools.inventree.read.parts.get_data_provider", return_value=provider):
        ambiguous = await get_part(ipn="00123")
        assert ambiguous["requires_clarification"] is True
        assert {row["revision"] for row in ambiguous["candidates"]} == {"REV-A", "REV-B"}
        provider.get_part.assert_not_awaited()
        missing = await get_part(ipn="123")
        assert missing["found"] is False
        provider.get_part.assert_not_awaited()
        resolved = await get_part(ipn="00123", revision="REV-B")
        assert resolved["pk"] == other.pk and resolved["in_stock"] == 0
        provider.get_part.assert_awaited_once_with(other.pk)
    with patch("ai.core.tools.inventree.read.database._current_user", return_value=None):
        assert resolve_exact_ipn("00123")["resolved"] is False


@pytest.mark.asyncio
async def test_category_location_followup_uses_the_latest_subject_and_full_native_population(
    stock_fixture, monkeypatch
):
    from ai.core.integrations.inventory_tools import get_stock_levels
    from ai.core.tools import capabilities
    from ai.core.tools.invocation_guard import bind_capability_run
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow
    from asgiref.sync import sync_to_async

    def setup():
        part, _template, _empty, category = stock_fixture
        category.name = "PCBA"
        category.save()
        child = PartCategory.objects.create(name="PCBA child", parent=category)
        factory = StockLocation.objects.create(name="Factory")
        room = StockLocation.objects.create(name="Room 101", parent=factory)
        second = Part.objects.create(name="Test Board 2", category=child)
        third = Part.objects.create(name="Test Board 3", category=child)
        StockItem.objects.filter(part=part).delete()
        for item, quantity, location in [
            (part, 40, factory),
            (part, 15, room),
            (second, 6, factory),
            (second, 24, room),
            (third, 5, room),
        ]:
            StockItem.objects.create(part=item, quantity=quantity, location=location, batch="")
        StockItem.objects.create(part=second, quantity=900, status=60, location=factory, batch="")
        wire = PartCategory.objects.create(name="Wire")
        return category, wire, factory, room

    category, wire, factory, room = await sync_to_async(setup)()
    monkeypatch.setattr(
        capabilities, "category_lexicon", lambda: frozenset({"pcba", "wire", "widget"})
    )
    context = {
        "conversation_history": [
            {"role": "user", "content": "Show Wire stock."},
            {"role": "assistant", "content": "Wire and Widget are empty."},
            {"role": "user", "content": "How many PCBA are currently in stock?"},
            {"role": "assistant", "content": "90 PCBA; Wire is unrelated."},
        ]
    }
    selected = await sync_to_async(T1LookupWorkflow._stock_location_category)(
        "And where are those located?", context
    )
    assert selected == category.pk
    assert capabilities.matched_category_terms(
        "And where are those located?", context["conversation_history"]
    ) == ("pcba",)
    for modality in ("text", "voice"):
        with bind_capability_run(
            workflow="wf8",
            modality=modality,
            selected_tools=[get_stock_levels],
            stock_location_category_id=selected,
        ):
            # Wrong model arguments cannot replace the server-derived subject.
            result = await get_stock_levels(category_id=wire.pk)
        assert Decimal(result["total_in_stock"]) == 90
        assert {row["location_id"]: Decimal(row["quantity"]) for row in result["locations"]} == {
            factory.pk: 46,
            room.pk: 44,
        }
        assert result["locations_complete"] is True
        assert result["part_count"] == 3
    changed = await sync_to_async(T1LookupWorkflow._stock_location_category)(
        "Where is Wire stock located?", context
    )
    assert changed == wire.pk
    assert (
        await sync_to_async(T1LookupWorkflow._stock_location_category)(
            "Where is stock for part 00123?", context
        )
        is None
    )
    assert capabilities.matched_category_terms("Hello", context["conversation_history"]) == ()
    with (
        bind_capability_run(
            workflow="wf8",
            modality="voice",
            selected_tools=[get_stock_levels],
            stock_location_category_id=selected,
        ),
        patch("ai.core.tools.inventree.read.database._current_user", return_value=None),
    ):
        denied = await get_stock_levels(category_id=category.pk)
    assert denied["resolved"] is False
    assert "total_in_stock" not in denied


def test_category_locations_aggregate_past_the_part_display_cap(stock_fixture):
    part, _template, _empty, category = stock_fixture
    location = StockItem.objects.filter(part=part).first().location
    # Native saves populate the part's MPTT coordinates; bulk_create would
    # bypass that contract and fail before exercising the aggregate.
    parts = [
        Part.objects.create(name=f"Additional board {index}", category=category)
        for index in range(205)
    ]
    StockItem.objects.bulk_create([
        StockItem(part=row, quantity=1, location=location, batch="") for row in parts
    ])
    result = category_stock_summary(category.pk, None, group_by="location")
    assert result["part_count"] == 206
    assert len(result["parts"]) == 200 and result["truncated"] is True
    assert Decimal(result["total_in_stock"]) == 209
    assert Decimal(result["locations"][0]["quantity"]) == 209
    assert result["locations_complete"] is True


@pytest.mark.asyncio
async def test_prepared_workflow_binds_the_category_in_both_modalities(stock_fixture, monkeypatch):
    import time

    from ai.core.integrations.inventory_tools import get_stock_levels
    from ai.core.tools import capabilities
    from ai.core.tools.inventree.read.parts import get_part
    from ai.core.workflows.wf8_lookup import LookupType, T1LookupWorkflow
    from asgiref.sync import sync_to_async

    _part, _template, _empty, category = stock_fixture
    name = "pcba" + uuid.uuid4().hex.translate(str.maketrans("0123456789", "ghijklmnop"))

    def rename():
        category.name = name
        category.save()

    await sync_to_async(rename)()
    monkeypatch.setattr(capabilities, "category_lexicon", lambda: frozenset({name}))
    context = {
        "conversation_history": [{"role": "user", "content": f"How many {name} are in stock?"}]
    }
    received = []

    class Agent:
        async def run(self, *_args, **_kwargs):
            received.append(await get_stock_levels(category_id=99999999))
            return SimpleNamespace(text="There are no PCBA units in stock.", usage_details=None)

        async def run_stream(self, *_args, **_kwargs):
            received.append(await get_stock_levels(category_id=99999999))
            yield SimpleNamespace(text="There are no PCBA units in stock.")

    workflow = T1LookupWorkflow()
    tools = [get_stock_levels, get_part]
    selected = SimpleNamespace(
        requires_specialist=False,
        clarification_required=False,
        tools=tools,
        pack_ids=("stock.read", "parts.read"),
    )
    with (
        patch.object(workflow, "_capability_selection", return_value=selected),
        patch.object(workflow, "_get_agent", new=AsyncMock(return_value=Agent())),
        patch("ai.core.tools.rbac.tools_for_current_user", new=AsyncMock(return_value=tools)),
        patch(
            "ai.core.workflows.wf8_lookup.get_settings",
            return_value=SimpleNamespace(feature_capability_broker_enforce=True),
        ),
        patch("ai.core.agents.factory.prompt_cache_options", return_value={}),
    ):
        prepared = await workflow._prepare_run(
            query="And where are those located?",
            lookup_type=LookupType.STOCK_CHECK,
            context=context,
            start_time=time.perf_counter(),
        )
    assert prepared.stock_location_category_id == category.pk
    assert prepared.runtime_tools == [get_stock_levels]
    for modality in ("text", "voice"):
        prepared.modality = modality
        with patch.object(workflow, "_prepare_run", new=AsyncMock(return_value=prepared)):
            result = await workflow.execute("And where are those located?", context=context)
        assert result.success is True
        assert "totals 4" in result.formatted_response
        assert "no PCBA units" not in result.formatted_response
        with patch.object(workflow, "_prepare_run", new=AsyncMock(return_value=prepared)):
            chunks = [
                chunk
                async for chunk in workflow.execute_streaming(
                    "And where are those located?", context=context
                )
            ]
        assert len(chunks) == 1 and "totals 4" in chunks[0]
        assert "no PCBA units" not in chunks[0]
    assert all(
        item["category_id"] == category.pk and Decimal(item["total_in_stock"]) == 4
        for item in received
    )

    class NoReadAgent:
        async def run(self, *_args, **_kwargs):
            return SimpleNamespace(text="There are no PCBA units in stock.", usage_details=None)

    prepared.agent = NoReadAgent()
    with patch.object(workflow, "_prepare_run", new=AsyncMock(return_value=prepared)):
        result = await workflow.execute("And where are those located?", context=context)
    assert "could not be verified" in result.formatted_response


def test_category_location_subject_resolves_plural_and_clarifies_duplicate_names(
    stock_fixture, monkeypatch
):
    from ai.core.tools import capabilities
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    _part, _template, _empty, category = stock_fixture
    name = "Board" + uuid.uuid4().hex.translate(str.maketrans("0123456789", "ghijklmnop"))
    category.name = name
    category.save()
    monkeypatch.setattr(
        capabilities,
        "category_lexicon",
        lambda: frozenset({name.casefold(), name.casefold() + "s"}),
    )
    query = f"Where is the {name}s stock located?"
    assert T1LookupWorkflow._stock_location_category(query, {}) == category.pk
    parent = PartCategory.objects.create(name=f"Another parent {uuid.uuid4().hex[:8]}")
    duplicate = PartCategory.objects.create(name=name, parent=parent)
    result = T1LookupWorkflow._stock_location_category(query, {})
    assert str(category.pk) in result["question"]
    assert str(duplicate.pk) in result["question"]
    assert "Which stock category" in result["question"]


def test_category_location_answer_requires_fresh_consistent_evidence_and_reports_coverage():
    from ai.core.workflows.wf8_lookup import category_location_answer

    for evidence in [
        {},
        {"resolved": False},
        {
            "resolved": True,
            "category_id": 7,
            "total_in_stock": "4",
            "locations_complete": True,
            "locations": [{"name": "Shelf", "quantity": "0"}],
        },
    ]:
        assert "could not be verified" in category_location_answer(
            evidence, category_id=7, modality="voice"
        )
    partial = {
        "resolved": True,
        "category_id": 7,
        "category_name": "PCBA",
        "total_in_stock": "1000",
        "locations_complete": False,
        "location_count": 400,
        "locations": [{"name": f"Shelf {index}", "quantity": "1"} for index in range(200)],
    }
    text = category_location_answer(partial, category_id=7, modality="voice")
    assert "totals 1000" in text and "397 more locations" in text
    assert len(text) < 700
