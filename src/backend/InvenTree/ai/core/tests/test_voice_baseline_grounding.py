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


@pytest.mark.parametrize(
    "operator,quantity,expected",
    [
        ("lt", 10, {"base", "template", "empty"}),
        ("lte", 10, {"base", "template", "empty", "ten"}),
        ("gt", 10, {"twelve"}),
        ("gte", 10, {"ten", "twelve"}),
        ("eq", 0, {"template", "empty"}),
    ],
)
def test_native_quantity_comparisons_include_zeros_and_exact_boundaries(
    stock_fixture, operator, quantity, expected
):
    part, template, empty, category = stock_fixture
    ten = Part.objects.create(name="Ten", category=category)
    twelve = Part.objects.create(name="Twelve", category=category)
    StockItem.objects.create(part=ten, quantity=10, batch="")
    StockItem.objects.create(part=twelve, quantity=12, batch="")
    identities = {
        "base": part.pk,
        "template": template.pk,
        "empty": empty.pk,
        "ten": ten.pk,
        "twelve": twelve.pk,
    }
    data = category_stock_summary(
        category.pk, None, quantity_comparison=operator, quantity_value=quantity
    )
    assert data["resolved"] is True
    assert {p["part_id"] for p in data["parts"]} == {identities[k] for k in expected}
    assert data["part_count"] == len(expected)


@pytest.mark.parametrize(
    "operator,value,legacy",
    [("bad", 1, None), ("lt", float("nan"), None), ("lt", -1, None), ("lt", 10, 2)],
)
def test_native_quantity_comparison_rejects_invalid_or_conflicting_filters(
    stock_fixture, operator, value, legacy
):
    category = stock_fixture[3]
    data = category_stock_summary(
        category.pk, legacy, quantity_comparison=operator, quantity_value=value
    )
    assert data["resolved"] is False


def test_live_exact_part_and_under_threshold_prompts_use_native_evidence(
    stock_fixture, monkeypatch
):
    from ai.core.tools import capabilities
    from ai.core.tools.stock_queries import resolve_stock_query

    part, template, empty, category = stock_fixture
    category.name = "PCBA"
    category.save()
    part.revision = "REV-A"
    part.save()
    monkeypatch.setattr(capabilities, "category_lexicon", lambda: frozenset({"pcba"}))
    for query in [
        f"Where is stock for part ID {part.pk} located?",
        f"Show current stock for exact part ID {part.pk}.",
        f"For exact part ID {part.pk} revision REV-A, list current in-stock locations and quantities only. Exclude historical, depleted and non-current stock rows.",
    ]:
        result = resolve_stock_query(query)
        assert result is not None
        assert Decimal(result["evidence"]["total_in_stock"]) == 4
        assert "totals 4" in result["text"] or "on hand 4" in result["text"]
        assert "rounding" not in result["text"] and "900" not in result["text"]
    result = resolve_stock_query("Which PCBA parts have under10 units in stock?")
    assert {p["part_id"] for p in result["evidence"]["parts"]} == {part.pk, template.pk, empty.pk}
    assert "3 exact parts" in result["text"]
    scoped = resolve_stock_query("Which parts in PCBA have under 10 units in stock?")
    assert scoped["evidence"]["part_count"] == 3
    for filtered in [
        f"What stock is at Room 101 for part {part.pk}?",
        "Which PCBA parts have under 10 available stock?",
        "Which PCBA parts have under 10 stock at Room 101?",
        f"Show stock for part {part.pk} batch TEST.",
    ]:
        assert resolve_stock_query(filtered)["evidence"]["resolved"] is False
    precise = resolve_stock_query("Which PCBA parts have stock under 4.0000000000000001?")
    assert precise["evidence"]["quantity_value"] == "4.0000000000000001"
    assert resolve_stock_query("Show stock for part 002.01-PCBA.") is None
    with patch("ai.core.tools.inventree.read.database._current_user", return_value=None):
        assert (
            resolve_stock_query(f"Where is stock for part {part.pk} located?")["evidence"][
                "resolved"
            ]
            is False
        )


@pytest.mark.asyncio
async def test_exact_part_workflow_is_native_in_text_and_streaming_and_never_calls_model(
    stock_fixture,
):
    from ai.core.integrations.inventory_tools import get_stock_levels
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    part = stock_fixture[0]
    workflow = T1LookupWorkflow()
    for modality in ("text", "voice"):
        with (
            patch(
                "ai.core.tools.rbac.tools_for_current_user",
                new=AsyncMock(return_value=[get_stock_levels]),
            ),
            patch.object(
                workflow,
                "_get_agent",
                new=AsyncMock(side_effect=AssertionError("No model stock answer")),
            ),
        ):
            query = f"Where is stock for part ID {part.pk} located?"
            result = await workflow.execute(query, context={"modality": modality})
            assert result.success and "totals 4" in result.formatted_response
            chunks = [
                s async for s in workflow.execute_streaming(query, context={"modality": modality})
            ]
            assert len(chunks) == 1 and "totals 4" in chunks[0]


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
            side_effect=[
                {"pk": 77, "name": "Assembly", "assembly": True},
                {"pk": 2, "name": "Bolt"},
                {"pk": 2, "name": "Bolt"},
            ]
        ),
        get_bom_items=AsyncMock(
            return_value=[
                {
                    "pk": 1,
                    "sub_part": 2,
                    "quantity": 2,
                    "optional": False,
                    "available_stock": 3,
                    "can_build": 1,
                },
                {
                    "pk": 2,
                    "sub_part": 2,
                    "quantity": 3,
                    "optional": False,
                    "available_stock": 3,
                    "can_build": 1,
                },
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
            side_effect=[
                {"pk": 77, "name": "Assembly", "assembly": True},
                {"pk": 2, "name": "Own component"},
            ]
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
        get_part=AsyncMock(side_effect=[{"pk": 77, "name": "Assembly", "assembly": True}, None]),
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


@pytest.mark.asyncio
async def test_bom_unknown_flags_cannot_certify_required_components_or_variant_exclusivity():
    provider = SimpleNamespace(
        get_part=AsyncMock(
            side_effect=[
                {"pk": 77, "name": "Assembly", "assembly": True},
                {"pk": 2, "name": "Board"},
            ]
        ),
        get_bom_items=AsyncMock(return_value=[{"pk": 1, "part": 77, "sub_part": 2, "quantity": 1}]),
        get_stock_quantity=AsyncMock(return_value=55),
    )
    with patch("ai.core.tools.inventree.read.stock.get_data_provider", return_value=provider):
        result = await get_bom(77)
    assert result["items"][0]["optional"] is None
    assert result["items"][0]["allow_variants"] is None
    assert result["qualifier_evidence"] == {
        "optional_flags_complete": False,
        "variant_flags_complete": False,
        "all_components_required": None,
        "variant_lines": None,
    }
    assert result["arithmetic_buildable_quantity"] is None


@pytest.mark.asyncio
async def test_catalog_keyword_page_keeps_parts_scope_and_native_coverage(stock_fixture):
    from ai.core.tools.catalog_queries import catalog_request, resolve_catalog_query

    provider = SimpleNamespace(
        search_parts_page=AsyncMock(
            return_value={
                "results": [
                    {"pk": 1, "name": "Bearing A", "IPN": "A"},
                    {"pk": 2, "name": "Bearing B", "IPN": "B"},
                ],
                "population_count": 5,
                "has_more": True,
            }
        )
    )
    query = "Show only the first2 keyword matches for bearing and state total, returned count and whether more pages exist."
    assert catalog_request(query) == {"query": "bearing", "limit": 2, "offset": 0}
    with patch("ai.core.tools.inventree.read.parts.get_data_provider", return_value=provider):
        result = await resolve_catalog_query(query)
    provider.search_parts_page.assert_awaited_once_with(
        query="bearing", category=None, active=None, limit=2, offset=0
    )
    assert "Total matches: 5; returned count: 2; more pages: yes" in result["text"]
    assert (
        "across catalog parts" in result["text"] and "category name matches" not in result["text"]
    )
    assert result["evidence"]["complete_population"] is False


@pytest.mark.asyncio
async def test_catalog_duplicate_category_names_require_native_selection(stock_fixture):
    from ai.core.tools.catalog_queries import resolve_catalog_query
    from asgiref.sync import sync_to_async

    def setup():
        parent = PartCategory.objects.create(name=f"Parent {uuid.uuid4().hex[:8]}")
        first = PartCategory.objects.create(name="Bearings")
        second = PartCategory.objects.create(name="Bearings", parent=parent)
        return first.pk, second.pk

    first, second = await sync_to_async(setup)()
    with patch(
        "ai.core.tools.inventree.read.parts.get_data_provider",
        side_effect=AssertionError("No guessed category read"),
    ):
        result = await resolve_catalog_query("List parts specifically in the Bearings category.")
    assert result["evidence"]["requires_clarification"] is True
    assert str(first) in result["text"] and str(second) in result["text"]


def test_received_word_threshold_matches_typed_population_with_honest_spoken_coverage(
    stock_fixture, monkeypatch
):
    from ai.core.tools import capabilities
    from ai.core.tools.stock_queries import resolve_stock_query

    _part, _, _, category = stock_fixture
    category.name = "PCBA"
    category.save()
    for n in range(3):
        Part.objects.create(name=f"Additional {n}", category=category)
    monkeypatch.setattr(capabilities, "category_lexicon", lambda: frozenset({"pcba"}))
    typed = resolve_stock_query("Which PCBA parts have under10 units in stock?")
    spoken = resolve_stock_query(
        "Which PCBA parts have under ten units in stock?", {"modality": "voice"}
    )
    assert spoken["evidence"] == typed["evidence"]
    assert spoken["evidence"]["part_count"] == 6
    assert spoken["text"].count("revision ") <= 3
    assert "3 more matching parts are not listed" in spoken["text"]
    assert "only" not in spoken["text"].lower()


def test_location_amounts_compact_identity_and_unavailable_exclusion(stock_fixture):
    from ai.core.tools.stock_queries import resolve_stock_query

    part = stock_fixture[0]
    room = StockLocation.objects.create(name="Room 101")
    StockItem.objects.create(part=part, quantity=15, location=room, batch="")
    answer = resolve_stock_query(
        f"Where is stock for part{part.pk} located? Include on-hand, allocated and available amounts."
    )
    assert "Allocated 0" in answer["text"] and "available 19" in answer["text"]
    for label in ("Room101", "Room 101", f"location ID {room.pk}"):
        answer = resolve_stock_query(f"What stock is at {label} for part{part.pk}?")
        assert Decimal(answer["evidence"]["total_in_stock"]) == 15
        assert answer["evidence"]["location_id"] == room.pk
        assert "not verified" in answer["text"] or answer["evidence"]["allocated_stock"] is None
    StockLocation.objects.create(name="Room 101")
    assert resolve_stock_query(f"What stock is at Room101 for part{part.pk}?")["evidence"][
        "requires_clarification"
    ]
    part.revision = "REV-A"
    part.save()
    conflict = resolve_stock_query(f"Show stock for part{part.pk} (IPN00123, REV-B).")
    assert conflict["evidence"]["resolved"] is False
    conflict = resolve_stock_query(f"Show stock for part{part.pk} (IPN99999, REV-A).")
    assert conflict["evidence"]["resolved"] is False
    assert "IPN" in conflict["text"]
    unsupported = resolve_stock_query(f"Show stock for part{part.pk} except quarantined stock.")
    assert unsupported["evidence"]["filters_verified"] is False


@pytest.mark.asyncio
async def test_exact_record_workflow_rechecks_native_identity_in_both_modalities(stock_fixture):
    from ai.core.tools.inventree.read.maintenance import get_work_order_overview
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow
    from asgiref.sync import sync_to_async

    def seed():
        return WorkOrder.objects.create(
            title="Blower low airflow and motor thermal trips",
            lifecycle_status="planned",
            status="review",
        )

    work = await sync_to_async(seed)()
    for modality in ("text", "voice"):
        workflow = T1LookupWorkflow()
        with (
            patch(
                "ai.core.tools.rbac.tools_for_current_user",
                new=AsyncMock(return_value=[get_work_order_overview]),
            ),
            patch(
                "ai.core.tools.inventree.read.maintenance._current_user",
                return_value=SimpleNamespace(is_authenticated=True, is_active=True),
            ),
            patch.object(ai_read, "maintenance_ai_read_enabled", return_value=True),
            patch.object(ai_read, "work_order_scope_filter", return_value=Q(pk=work.pk)),
            patch.object(
                workflow,
                "_get_agent",
                new=AsyncMock(side_effect=AssertionError("No model-selected record")),
            ),
        ):
            result = await workflow.execute(
                f"What is the status of work order {work.pk}?", context={"modality": modality}
            )
            evidence = result.data["native_record"]
            assert evidence["requested_id"] == evidence["returned_id"] == work.pk
            assert evidence["tool_arguments"] == {"work_order_id": work.pk}
            assert "planned" in result.formatted_response and "review" in result.formatted_response
            assert "thermal trips" in result.formatted_response
            assert "[UNTRUSTED-CONTENT-" not in result.formatted_response
            assert "[UNTRUSTED-CONTENT-BEGIN]" in evidence["record"]["title"]
            unavailable = await workflow.execute(
                "What is the status of work order 99999999?", context={"modality": modality}
            )
            assert unavailable.data["native_record"]["resolved"] is False


@pytest.mark.asyncio
async def test_bom_exact_received_target_and_component_identity_reject_substitution(stock_fixture):
    from ai.core.tools.record_queries import resolve_record_query

    provider = SimpleNamespace(
        get_part=AsyncMock(
            side_effect=lambda pk: {
                "pk": pk,
                "name": "Doohickey" if pk == 87 else "Screw",
                "IPN": "D.123" if pk == 87 else "M3x8",
                "revision": "",
                "assembly": pk == 87,
            }
        ),
        get_bom_items=AsyncMock(
            return_value=[
                {
                    "pk": 1,
                    "part": 87,
                    "sub_part": 66,
                    "quantity": "4",
                    "optional": False,
                    "allow_variants": False,
                    "inherited": True,
                }
            ]
        ),
    )
    with patch("ai.core.tools.inventree.read.stock.get_data_provider", return_value=provider):
        answer = await resolve_record_query(
            "Show the BOM for Part 87.", {"modality": "voice"}, {"get_bom"}
        )
        assert answer["evidence"]["requested_id"] == answer["evidence"]["returned_id"] == 87
        assert "D.123" in answer["text"] and "revision not recorded" in answer["text"]
        assert "quantity 4" in answer["text"] and "variants allowed no" in answer["text"]
        original = provider.get_bom_items.return_value[0]
        provider.get_bom_items.return_value = [{**original, "pk": n} for n in range(1, 5)]
        bounded = await resolve_record_query(
            "Show the BOM for Part 87.", {"modality": "voice"}, {"get_bom"}
        )
        assert "4 lines" in bounded["text"]
        assert bounded["text"].count("Component part") <= 3
        assert "more lines are not listed here" in bounded["text"]
        assert len(bounded["text"]) <= 700
        for wrong in (88, 999):
            provider.get_part = AsyncMock(
                return_value={"pk": wrong, "name": "Wrong board", "assembly": True}
            )
            refused = await resolve_record_query("Show the BOM for Part 87.", None, {"get_bom"})
            assert refused["evidence"]["resolved"] is False
            assert "Wrong board" not in refused["text"]
        provider.get_part = AsyncMock(
            side_effect=[{"pk": 87, "assembly": True}, {"pk": 88, "name": "Wrong component"}]
        )
        refused = await resolve_record_query("Show the BOM for Part 87.", None, {"get_bom"})
        assert refused["evidence"]["resolved"] is False
        assert "Wrong component" not in refused["text"]
    with (
        patch("ai.core.tools.inventree.read.database._current_user", return_value=None),
        patch(
            "ai.core.tools.inventree.read.stock.get_bom",
            new=AsyncMock(side_effect=AssertionError("No privileged lookup")),
        ),
    ):
        assert (await resolve_record_query("Show the BOM for Part 87.", None, {"get_bom"}))[
            "evidence"
        ]["resolved"] is False


@pytest.mark.asyncio
async def test_word_work_order_target_mismatch_cannot_be_narrated():
    from ai.core.tools.record_queries import resolve_record_query

    read = AsyncMock(return_value={"work_order_id": 116, "title": "Wrong factory job"})
    received = "What is the status of work order one hundred fifteen?"
    with patch("ai.core.tools.inventree.read.maintenance.get_work_order_overview", new=read):
        result = await resolve_record_query(
            received, {"modality": "voice"}, {"get_work_order_overview"}
        )
    read.assert_awaited_once_with(115)
    assert result["evidence"]["requested_id"] == 115
    assert result["evidence"]["returned_id"] == 116
    assert result["evidence"]["resolved"] is False
    assert "Wrong factory job" not in result["text"]


@pytest.mark.asyncio
async def test_filtered_keyword_retains_native_keyword_population_and_current_stock(stock_fixture):
    from ai.core.tools.catalog_queries import resolve_catalog_query

    part, template, empty, _ = stock_fixture
    ids = [part.pk, template.pk, empty.pk]
    provider = SimpleNamespace(
        search_parts_page=AsyncMock(
            return_value={
                "results": [{"pk": pk, "active": True} for pk in ids],
                "population_count": 3,
                "has_more": False,
            }
        )
    )
    with patch("ai.core.tools.inventree.read.parts.get_data_provider", return_value=provider):
        result = await resolve_catalog_query("List parts matching bearing with active stock only.")
    assert result["evidence"]["resolved"]
    assert [row["pk"] for row in result["evidence"]["results"]] == [part.pk]
    assert result["evidence"]["evaluated_count"] == 3
    assert result["evidence"]["applied_filters"]["category_id"] is None
    assert result["evidence"]["results"][0]["total_in_stock"] == "4"
    provider.search_parts_page.assert_awaited_once_with(
        query="bearing", category=None, active=True, limit=100, offset=0
    )


@pytest.mark.asyncio
async def test_stock_category_switch_leaves_entity_focus_alone(stock_fixture, monkeypatch):
    from ai.core.focus.commands import maybe_handle
    from ai.core.tools import capabilities
    from ai.core.tools.stock_queries import resolve_stock_query, stock_category_switch
    from asgiref.sync import sync_to_async

    category = stock_fixture[3]
    category.name = "Wire"
    await sync_to_async(category.save)()
    monkeypatch.setattr(capabilities, "category_lexicon", lambda: frozenset({"wire"}))
    for received in ("Switch to wire, how much stock is there?", "Switch to wire stock now."):
        snapshot = {"focus": {"kind": "machine", "pk": 24}, "version": 2}
        run = SimpleNamespace(content=received, focus_snapshot=snapshot)
        assert await maybe_handle(SimpleNamespace(), run) is None
        assert run.focus_snapshot == snapshot
        result = await sync_to_async(resolve_stock_query)(received)
        assert Decimal(result["evidence"]["total_in_stock"]) == 4
    assert not await sync_to_async(stock_category_switch)("Switch to machine Wire stock now.")
    await sync_to_async(PartCategory.objects.create)(name="Wire")
    result = await sync_to_async(resolve_stock_query)("Switch to wire stock now.")
    assert result["evidence"]["requires_clarification"]
    assert "authorized target" not in result["text"]
