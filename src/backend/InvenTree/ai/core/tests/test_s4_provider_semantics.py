"""S4 native stock semantics; transport and permission profiles are scripted.

Exercise the actual client/provider/router/root, not native HTTP, allocation
accounting, real authorization or a strict client-scoped integration source.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from ai.core.agents.routing import RoutingDecision, UnifiedRouter, WorkflowType
from ai.core.config import Settings
from ai.core.integrations.data_provider import LiveDataProviderAsync
from ai.core.integrations.inventree.client import InvenTreeClient
from ai.core.workflows.root import RootWorkflow


def _voice_stock_answer(query):
    calls, decisions = [], []
    part = {"pk": 41, "name": "Synthetic bolts", "in_stock": 8, "available_stock": 3}

    async def request(method, endpoint, params=None, **_kwargs):  # noqa: RUF029 - async transport fixture
        calls.append((method, endpoint, dict(params or {})))
        if endpoint == "/part/":
            return {"results": [dict(part)], "count": 1, "next": None}
        assert endpoint == "/stock/"
        return {
            "results": [{"pk": 17, "part": 41, "quantity": 8}],
            "count": 1,
            "next": None,
        }

    class Conversations:
        def get_or_create_state(self, thread_id, user_id):
            return SimpleNamespace()

        async def gather_context(self, query, thread_id, user_id):
            return {"modality": "voice"}

    class Fallback:
        async def run_stream(self, **kwargs):
            decisions.append(kwargs["decision"])
            yield "synthetic fallback"

    async def execute():
        client = InvenTreeClient.__new__(InvenTreeClient)
        client._request = request
        provider = LiveDataProviderAsync.__new__(LiveDataProviderAsync)
        provider._client = client
        router = UnifiedRouter()
        router.fast_path._data_provider = provider
        router.semantic.route = AsyncMock(
            return_value=RoutingDecision(
                workflow_type=WorkflowType.T1_LOOKUP, confidence=1.0, reasoning="synthetic fallback"
            )
        )
        root = RootWorkflow(
            router=router,
            registry=SimpleNamespace(get_workflow=lambda _name: Fallback()),
            conversation_manager=Conversations(),
        )
        settings = Settings(
            _env_file=None,
            TURN_WALL_CLOCK_CAP_S=10,
            TURN_ROUTING_BUDGET_S=5,
            FEATURE_VOICE_FAST_PATH=True,
        )
        with (
            patch("ai.core.config.get_settings", return_value=settings),
            patch("ai.core.workflows.root.get_settings", return_value=settings),
            patch("ai.core.auth.get_current_principal", return_value=SimpleNamespace(user_pk="1")),
            patch(
                "ai.core.tools.invocation_guard.fresh_permission_profile",
                new=AsyncMock(return_value=frozenset({("part", "view"), ("stock", "view")})),
            ),
        ):
            return [
                chunk async for chunk in root.run_stream(query, thread_id="synthetic", user_id="1")
            ]

    return asyncio.run(execute()), calls, decisions


@pytest.mark.parametrize(
    "query,supported",
    [
        ("How many bolts do we have available?", False),
        ("Availability of bolts?", False),
        ("Do we have bolts available?", False),
        ("How much stock of bolts is unallocated?", False),
        ("Stock quantity of bolts excluding reservations?", False),
        ("How much stock of bolts after allocations?", False),
        ("How many bolts do we have in stock?", True),
        ("How much stock of bolts?", True),
    ],
)
def test_in_stock_fast_path_does_not_claim_allocation_aware_availability(query, supported):
    """In-stock eight and available three are different fixture observations."""
    chunks, calls, decisions = _voice_stock_answer(query)
    if supported:
        assert calls
        assert chunks != ["synthetic fallback"]
        assert "8" in " ".join(chunks)
        assert "in stock" in " ".join(chunks)
        assert not decisions
    else:
        assert calls == []
        assert chunks == ["synthetic fallback"]
        assert len(decisions) == 1
        assert decisions[0].fast_path_result is None


def _stock_quantity_provider(pages):
    """Use the actual native client/provider with a scripted HTTP response seam."""
    calls = []

    async def request(method, endpoint, params=None, **_kwargs):  # noqa: RUF029 - async transport fixture
        assert method == "GET" and endpoint == "/stock/"
        calls.append(dict(params))
        response = pages[params["offset"]]
        if isinstance(response, Exception):
            raise response
        return response

    client = InvenTreeClient.__new__(InvenTreeClient)
    client._request = request
    provider = LiveDataProviderAsync.__new__(LiveDataProviderAsync)
    provider._client = client
    return provider, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("server_capped", [False, True])
async def test_native_stock_quantity_follows_metadata_not_short_page_length(server_capped):
    """A cap of two must not truncate the independently seeded three rows."""
    rows = [
        {"pk": 11, "part": 41, "quantity": 2},
        {"pk": 12, "part": 41, "quantity": 3},
        {"pk": 13, "part": 41, "quantity": 4},
    ]
    pages = {0: {"results": rows, "count": 3, "next": None}}
    if server_capped:
        pages = {
            0: {"results": rows[:2], "count": 3, "next": "https://untrusted.invalid/stock/"},
            2: {"results": rows[2:], "count": 3, "next": None},
        }
    provider, calls = _stock_quantity_provider(pages)
    assert await provider.get_stock_quantity(41) == 9
    expected_offsets = [0, 2] if server_capped else [0]
    assert calls == [
        {
            "part": 41,
            "limit": 100,
            "offset": offset,
            "location_detail": "true",
            "in_stock": "true",
            "include_variants": "false",
        }
        for offset in expected_offsets
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["replayed-id", "wrong-part", "boolean-id", "missing-id", "missing-part"]
)
async def test_native_stock_quantity_rejects_unqualified_row_identity(invalid):
    """Consistent counts cannot authorize replayed or foreign Part records."""
    second = {"pk": 12, "part": 41, "quantity": 4}
    if invalid == "replayed-id":
        second["pk"] = 11
    elif invalid == "wrong-part":
        second["part"] = 99
    elif invalid == "boolean-id":
        second["pk"] = True
    elif invalid == "missing-id":
        second.pop("pk")
    else:
        second.pop("part")
    provider, calls = _stock_quantity_provider({
        0: {"results": [{"pk": 11, "part": 41, "quantity": 2}], "count": 2, "next": "more"},
        1: {"results": [second], "count": 2, "next": None},
    })
    with pytest.raises(ValueError, match="Stock quantity is unavailable"):
        await provider.get_stock_quantity(41)
    assert [item["offset"] for item in calls] == [0, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["missing", "boolean", "negative", "nan", "infinite"])
async def test_native_stock_quantity_never_defaults_invalid_quantity_to_a_total(invalid):
    """A missing/invalid value is not stock zero or a usable scalar total."""
    second = {"pk": 12, "part": 41, "quantity": 4}
    if invalid == "missing":
        second.pop("quantity")
    else:
        second["quantity"] = {
            "boolean": True,
            "negative": -1,
            "nan": float("nan"),
            "infinite": float("inf"),
        }[invalid]
    provider, calls = _stock_quantity_provider({
        0: {"results": [{"pk": 11, "part": 41, "quantity": 2}], "count": 2, "next": "more"},
        1: {"results": [second], "count": 2, "next": None},
    })
    with pytest.raises(ValueError, match="Stock quantity is unavailable"):
        await provider.get_stock_quantity(41)
    assert [item["offset"] for item in calls] == [0, 1]


@pytest.mark.asyncio
async def test_native_stock_quantity_rejects_nonfinite_accumulation():
    """Finite rows can still overflow the existing float return contract."""
    provider, _ = _stock_quantity_provider({
        0: {
            "results": [
                {"pk": 11, "part": 41, "quantity": 1e308},
                {"pk": 12, "part": 41, "quantity": 1e308},
            ],
            "count": 2,
            "next": None,
        },
    })
    with pytest.raises(ValueError, match="Stock quantity is unavailable"):
        await provider.get_stock_quantity(41)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        "missing-count",
        "boolean-count",
        "missing-next",
        "early-end",
        "empty-nonterminal",
        "count-change",
        "oversized-page",
        "invalid-next",
    ],
)
async def test_native_stock_quantity_requires_consistent_paging_evidence(invalid):
    """Working coverage of the repaired page gate, not another first RED."""
    page = {"results": [{"pk": 11, "part": 41, "quantity": 2}], "count": 1, "next": None}
    pages = {0: page}
    if invalid == "missing-count":
        page.pop("count")
    elif invalid == "boolean-count":
        page["count"] = True
    elif invalid == "missing-next":
        page.pop("next")
    elif invalid == "early-end":
        page["count"] = 2
    elif invalid == "empty-nonterminal":
        page.update(results=[], next="more")
    elif invalid == "count-change":
        page.update(count=2, next="more")
        pages[1] = {"results": [{"pk": 12, "part": 41, "quantity": 4}], "count": 3, "next": None}
    elif invalid == "oversized-page":
        page.update(
            results=[{"pk": i + 1, "part": 41, "quantity": 1} for i in range(101)], count=101
        )
    else:
        page["next"] = False
    provider, calls = _stock_quantity_provider(pages)
    with pytest.raises(ValueError, match="Stock quantity is unavailable"):
        await provider.get_stock_quantity(41)
    assert len(calls) == (2 if invalid == "count-change" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("nonempty", [False, True])
async def test_native_stock_quantity_keeps_confirmed_zero(nonempty):
    """Complete empty and an observed zero-quantity record are valid zeroes."""
    rows = [{"pk": 11, "part": 41, "quantity": 0}] if nonempty else []
    provider, _ = _stock_quantity_provider({0: {"results": rows, "count": len(rows), "next": None}})
    assert await provider.get_stock_quantity(41) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [False, True])
async def test_shared_part_body_keeps_unknown_stock_distinct_from_zero(monkeypatch, malformed):
    """Actual shared body composition; lookup/transport and authority are scripted."""
    from ai.core.tools.inventree.read import parts

    row = {"pk": 11, "part": 41, "quantity": 7}
    if malformed:
        row.pop("quantity")
    provider, _ = _stock_quantity_provider({0: {"results": [row], "count": 1, "next": None}})
    provider.get_part = AsyncMock(return_value={"pk": 41, "name": "Synthetic part"})
    monkeypatch.setattr(parts, "get_data_provider", lambda: provider)
    result = await parts.get_part(part_id=41)
    assert result["name"] == "Synthetic part"
    assert result["in_stock"] == (None if malformed else 7)


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [False, True])
async def test_shared_variant_body_keeps_unknown_stock_distinct_from_zero(monkeypatch, malformed):
    """A scalar refusal is not silently turned into a variant with stock zero."""
    from ai.core.integrations.inventree import client as native_client
    from ai.core.tools.inventree.read import relationships

    row = {"pk": 11, "part": 41, "quantity": 7}
    if malformed:
        row.pop("quantity")
    provider, _ = _stock_quantity_provider({0: {"results": [row], "count": 1, "next": None}})
    provider.get_part = AsyncMock(return_value={"pk": 10, "is_template": True})
    lookup = SimpleNamespace(_request=AsyncMock(return_value={"results": [{"pk": 41}]}))
    monkeypatch.setattr(native_client, "get_inventree_client", lambda: lookup)
    monkeypatch.setattr(relationships, "get_data_provider", lambda: provider)
    result = await relationships.get_part_variants(template_part_id=10)
    assert len(result) == 1
    assert result[0]["in_stock"] == (None if malformed else 7)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sales", "build"])
async def test_line_readiness_is_not_certified_by_in_stock_quantity(monkeypatch, kind):
    """Eight on-hand and source-reported three available are different facts."""
    from ai.core.integrations.inventree import client as native_client
    from ai.core.tools.inventree.read import sales

    provider, _ = _stock_quantity_provider({
        0: {"results": [{"pk": 11, "part": 41, "quantity": 8}], "count": 1, "next": None}
    })
    provider.get_part = AsyncMock(return_value={"pk": 41, "name": "Synthetic component"})
    line = {
        "pk": 51,
        "part": 41,
        "quantity": 9,
        "shipped": 2,
        "allocated": 2,
        "consumed": 0,
        "available_stock": 3,
    }
    lookup = SimpleNamespace(_request=AsyncMock(return_value={"results": [line]}))
    monkeypatch.setattr(native_client, "get_inventree_client", lambda: lookup)
    monkeypatch.setattr(sales, "get_data_provider", lambda: provider)
    if kind == "sales":
        result = await sales.get_sales_order_lines(order_id=61)
        readiness, arithmetic = "can_fulfill", "on_hand_covers_outstanding"
    else:
        result = await sales.get_build_order_lines(build_id=62, include_allocated=False)
        readiness, arithmetic = "can_allocate", "on_hand_covers_unallocated"
    assert len(result) == 1
    assert result[0]["in_stock"] == 8
    assert result[0]["available_stock"] == 3
    assert result[0]["part_name"] == "Synthetic component"
    assert result[0][readiness] is None
    assert result[0][arithmetic] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sales", "build"])
@pytest.mark.parametrize("include_stock", [False, True])
async def test_line_readiness_keeps_opt_out_and_insufficient_on_hand_distinct(
    monkeypatch, kind, include_stock
):
    """Unknown readiness is not false, and opt-out does no stock work."""
    from ai.core.integrations.inventree import client as native_client
    from ai.core.tools.inventree.read import sales

    provider, calls = _stock_quantity_provider({
        0: {"results": [{"pk": 11, "part": 41, "quantity": 2}], "count": 1, "next": None}
    })
    provider.get_part = AsyncMock(return_value={"pk": 41, "name": "Synthetic component"})
    line = {
        "pk": 51,
        "part": 41,
        "quantity": 9,
        "shipped": 2,
        "allocated": 2,
        "consumed": 0,
        "available_stock": 1,
    }
    lookup = SimpleNamespace(_request=AsyncMock(return_value={"results": [line]}))
    monkeypatch.setattr(native_client, "get_inventree_client", lambda: lookup)
    monkeypatch.setattr(sales, "get_data_provider", lambda: provider)
    if kind == "sales":
        result = await sales.get_sales_order_lines(order_id=61, include_stock=include_stock)
        readiness, arithmetic = "can_fulfill", "on_hand_covers_outstanding"
    else:
        result = await sales.get_build_order_lines(
            build_id=62, include_stock=include_stock, include_allocated=False
        )
        readiness, arithmetic = "can_allocate", "on_hand_covers_unallocated"
    assert result[0]["available_stock"] == 1
    if include_stock:
        assert result[0]["in_stock"] == 2
        assert result[0][readiness] is None
        assert result[0][arithmetic] is False
        assert result[0]["stock_comparison_basis"] == "in_stock_not_allocation_availability"
        assert len(calls) == 1
    else:
        assert calls == []
        assert "in_stock" not in result[0]
        assert readiness not in result[0] and arithmetic not in result[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["none", "other-user", "role"])
async def test_root_generator_delivery_is_fresh_after_text_end(monkeypatch, mutation):
    """Earlier authorized event delivery does not authorize a later yield."""
    chunks, wire, fallbacks, calls, mutations, label = await _root_answer_after_context_change(
        monkeypatch, mutation=mutation, stage="text-end"
    )
    assert calls and set(calls) == {"41"}
    assert label in wire  # The earlier content event preceded the revocation.
    assert fallbacks == []  # Never rerun a workflow after content was already sent.
    if mutation == "none":
        assert chunks == [label + "."] and mutations == []
    else:
        assert mutations == ["text-end"]
        assert chunks == []


async def _root_answer_after_context_change(monkeypatch, *, mutation, stage="profile"):
    """Run real root/formatter/events with scripted routing and role lookup."""
    import json
    from dataclasses import replace

    from ai.core.auth import principal_context
    from ai.core.streaming import EventCollector, EventType, InMemoryEventEmitter
    from ai.core.tests.test_s4_cache_and_faults import _cache_principal
    from ai.core.tools import invocation_guard
    from ai.core.workflows import fast_path

    private_label = "Synthetic private source label"
    decision = RoutingDecision(
        workflow_type=WorkflowType.T1_LOOKUP,
        confidence=1.0,
        reasoning="synthetic precomputed lookup",
        fast_path_result={"type": "part_details", "part": {"pk": 41, "name": private_label}},
        use_fast_path=True,
    )
    fallback_decisions, profile_calls, mutations = [], [], []
    source_principal = None

    def mutate():
        if mutation == "none" or mutations:
            return
        assert source_principal is not None
        mutations.append(stage)
        if mutation == "role":
            return
        if mutation == "other-user":
            replacement = replace(source_principal, user_pk="42", actor="synthetic-42")
        elif mutation == "scope":
            replacement = replace(source_principal, scope="changed-synthetic-site")
        elif mutation == "policy":
            replacement = replace(source_principal, policy_version="changed-synthetic-policy")
        elif mutation == "equivalent-rebind":
            replacement = replace(source_principal)
        else:
            assert mutation == "cleared"
            replacement = None
        principal_context.set(replacement)

    async def profile(actor_pk):  # noqa: RUF029 - asynchronous role lookup seam
        profile_calls.append(actor_pk)
        if stage == "profile":
            mutate()
        if mutation == "role" and mutations:
            return frozenset()
        return frozenset({("part", "view")})

    original_format = fast_path.format_fast_path_answer

    def formatter(result):
        answer = original_format(result)
        if stage == "format":
            mutate()
        return answer

    monkeypatch.setattr(invocation_guard, "fresh_permission_profile", profile)
    monkeypatch.setattr(fast_path, "format_fast_path_answer", formatter)
    settings = Settings(_env_file=None, FEATURE_VOICE_FAST_PATH=True, TURN_WALL_CLOCK_CAP_S=10)
    monkeypatch.setattr("ai.core.workflows.root.get_settings", lambda: settings)

    class Fallback:
        async def run_stream(self, **kwargs):
            fallback_decisions.append(kwargs["decision"])
            yield "synthetic fallback"

    root = RootWorkflow(
        router=SimpleNamespace(route=AsyncMock(return_value=decision)),
        registry=SimpleNamespace(get_workflow=lambda _name: Fallback()),
        conversation_manager=SimpleNamespace(
            get_or_create_state=lambda *_args: SimpleNamespace(),
            gather_context=AsyncMock(return_value={"modality": "voice"}),
        ),
    )
    emitter, collector = InMemoryEventEmitter(), EventCollector()
    unsubscribe = await emitter.subscribe(collector)

    class ContextChangeHandler:
        async def handle(self, event):
            targets = {
                "workflow-event": EventType.WORKFLOW_STARTED,
                "text-start": EventType.TEXT_MESSAGE_START,
                "text-end": EventType.TEXT_MESSAGE_END,
            }
            if event.event_type == targets.get(stage):
                mutate()

    unsubscribe_mutation = await emitter.subscribe(ContextChangeHandler())
    try:
        with _cache_principal() as principal:
            source_principal = principal
            chunks = [
                chunk
                async for chunk in root.run_stream(
                    "Tell me about a synthetic part",
                    thread_id="synthetic-root-context",
                    user_id="41",
                    emitter=emitter,
                )
            ]
    finally:
        unsubscribe_mutation()
        unsubscribe()
    wire = json.dumps([event.to_dict() for event in collector.events])
    return chunks, wire, fallback_decisions, profile_calls, mutations, private_label


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["none", "other-user", "scope", "policy", "equivalent-rebind", "cleared"]
)
async def test_root_does_not_emit_fast_path_for_a_principal_changed_during_permission_lookup(
    monkeypatch, mutation
):
    """The final lookup's permission result belongs to its original principal."""
    chunks, wire, fallbacks, calls, mutations, label = await _root_answer_after_context_change(
        monkeypatch, mutation=mutation
    )
    assert calls and set(calls) == {"41"}
    if mutation == "none":
        assert chunks == [label + "."]
        assert label in wire
        assert fallbacks == [] and mutations == []
    else:
        assert mutations == ["profile"]
        assert chunks == ["synthetic fallback"]
        assert label not in wire
        assert len(fallbacks) == 1
        assert fallbacks[0].fast_path_result is None
        assert fallbacks[0].use_fast_path is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["none", "other-user", "scope", "policy", "equivalent-rebind", "cleared"]
)
async def test_root_does_not_emit_fast_path_if_formatting_rebinds_the_principal(
    monkeypatch, mutation
):
    """Fallible formatting is not authority to emit a prior principal's data."""
    chunks, wire, fallbacks, calls, mutations, label = await _root_answer_after_context_change(
        monkeypatch, mutation=mutation, stage="format"
    )
    assert calls and set(calls) == {"41"}
    if mutation == "none":
        assert chunks == [label + "."] and label in wire
        assert fallbacks == [] and mutations == []
    else:
        assert mutations == ["format"]
        assert chunks == ["synthetic fallback"]
        assert label not in wire
        assert len(fallbacks) == 1
        assert fallbacks[0].fast_path_result is None
        assert fallbacks[0].use_fast_path is False


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["workflow-event", "text-start"])
@pytest.mark.parametrize(
    "mutation", ["none", "other-user", "scope", "policy", "equivalent-rebind", "cleared", "role"]
)
async def test_root_rechecks_answer_binding_after_awaited_noncontent_events(
    monkeypatch, mutation, stage
):
    """A noncontent event hook may run between formatting and text delivery."""
    chunks, wire, fallbacks, calls, mutations, label = await _root_answer_after_context_change(
        monkeypatch, mutation=mutation, stage=stage
    )
    assert calls and set(calls) == {"41"}
    if mutation == "none":
        assert chunks == [label + "."] and label in wire
        assert fallbacks == [] and mutations == []
    else:
        assert mutations == [stage]
        assert chunks == ["synthetic fallback"]
        assert label not in wire
        assert len(fallbacks) == 1
        assert fallbacks[0].fast_path_result is None
        assert fallbacks[0].use_fast_path is False
