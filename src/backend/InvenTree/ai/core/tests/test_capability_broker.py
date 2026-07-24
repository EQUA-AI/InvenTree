"""Contract tests for the RBAC-first capability broker."""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ai.core.integrations.document_search import DOCUMENT_SEARCH_TOOLS
from ai.core.integrations.email.tools import EMAIL_TOOLS
from ai.core.integrations.inventory_tools import INVENTORY_READ_TOOLS
from ai.core.integrations.kanban_tools import KANBAN_READ_TOOLS, KANBAN_TOOLS
from ai.core.tools.capabilities import (
    MAX_INITIAL_TOOLS,
    PolicyKind,
    ToolEffect,
    capability_catalog,
    catalog_manifest,
    manifest_json,
    select_capabilities,
    tool_name,
)

ALL_VIEW_PROFILE = frozenset({
    ("build", "view"),
    ("part", "view"),
    ("part_category", "view"),
    ("purchase_order", "view"),
    ("sales_order", "view"),
    ("stock", "view"),
    ("stock_location", "view"),
})


def test_catalog_covers_wf8_once_in_canonical_order():
    expected = tuple(INVENTORY_READ_TOOLS + EMAIL_TOOLS + KANBAN_TOOLS + DOCUMENT_SEARCH_TOOLS)
    catalog = capability_catalog()

    # 46, not 47: delete_kanban_card is withheld from KANBAN_TOOLS (see
    # test_delete_kanban_card_is_withheld_from_the_agent).
    assert len(catalog) == len(expected) == 46
    assert tuple(entry.tool for entry in catalog) == expected
    assert len({entry.tool_id for entry in catalog}) == len(catalog)
    assert all(entry.tool is expected[index] for index, entry in enumerate(catalog))


def test_catalog_has_expected_stable_pack_shapes():
    counts = Counter(entry.pack_id for entry in capability_catalog())

    assert counts == {
        "parts.read": 5,
        "stock.read": 6,
        "bom.read": 2,
        "documents.read": 2,
        "procurement.read": 5,
        "sales.read": 4,
        "build.read": 3,
        "analytics.read": 2,
        "email.read": 3,
        "email.write": 3,
        "kanban.read": 4,
        # 7, not 8: delete_kanban_card is withheld, though it remains listed in
        # the kanban.write pack spec so a re-add still resolves to a pack.
        "kanban.write": 7,
    }


def test_every_catalog_entry_has_an_explicit_policy():
    catalog = capability_catalog()
    policies = {entry.tool_id: entry.authorization for entry in catalog}

    assert all(entry.authorization.kind in PolicyKind for entry in catalog)
    assert not {
        entry.tool_id for entry in catalog if entry.authorization.kind is PolicyKind.DISABLED
    }
    for tool in EMAIL_TOOLS:
        permission = "view" if tool in EMAIL_TOOLS[:3] else "send"
        policy = policies[tool.__name__]
        assert policy.kind is PolicyKind.NATIVE_PERMISSION
        assert policy.all_of == (("email", permission),)
    for tool in KANBAN_TOOLS:
        permission = "view" if tool in KANBAN_READ_TOOLS else "change"
        policy = policies[tool.__name__]
        assert policy.kind is PolicyKind.NATIVE_PERMISSION
        assert policy.all_of == (("kanban", permission),)
    assert all(
        entry.authorization.reason
        for entry in catalog
        if entry.authorization.kind is PolicyKind.DISABLED
    )


def test_protected_resource_tools_have_resource_authorizers():
    policies = {
        entry.tool_id: entry.authorization
        for entry in capability_catalog()
        if entry.authorization.kind is PolicyKind.RESOURCE_AUTHORIZER
    }

    assert set(policies) == {
        "get_part_attachments",
        "list_database_tables",
        "query_database",
        "search_part_documents",
    }
    assert policies["query_database"].authorizer == "database_relation_access"
    assert policies["get_part_attachments"].all_of == (("part", "view"),)


def test_contract_manifest_is_stable_and_complete():
    first = catalog_manifest()
    second = catalog_manifest()

    assert first == second
    assert manifest_json() == manifest_json()
    assert len(first) == 46
    assert all(record["module"] for record in first)
    assert all(record["qualname"] for record in first)
    assert all(len(record["contract_digest"]) == 64 for record in first)


def test_stock_superlative_selects_stock_and_analytics():
    selected = select_capabilities(
        "Which fastener has the highest stock?",
        profile=ALL_VIEW_PROFILE,
        authenticated=True,
    )

    assert selected.pack_ids == ("stock.read", "analytics.read")
    assert selected.tool_ids == (
        "check_low_stock",
        "get_stock_levels",
        "get_stock_quantity",
        "get_stock_item",
        "get_stock_at_location",
        "get_stock_locations",
        "list_database_tables",
        "query_database",
    )


def test_lookup_type_selects_primary_pack_without_an_extra_classifier():
    selected = select_capabilities(
        "Tell me about ABC-123",
        lookup_type="part_details",
        profile=ALL_VIEW_PROFILE,
        authenticated=True,
    )

    assert selected.pack_ids == ("parts.read",)
    assert selected.tool_ids == (
        "search_parts",
        "get_part",
        "get_part_parameters",
        "get_part_pricing",
        "get_categories",
    )


def test_selector_fails_closed_without_an_authenticated_principal():
    selected = select_capabilities(
        "Show stock inventory",
        profile=ALL_VIEW_PROFILE,
        authenticated=False,
    )

    assert selected.pack_ids == ("stock.read",)
    assert selected.tools == ()
    assert selected.tool_ids == ()


def test_sql_requires_at_least_one_view_permission_for_exposure():
    selected = select_capabilities(
        "Which item has the highest total?",
        profile=frozenset(),
        authenticated=True,
    )

    assert "analytics.read" in selected.pack_ids
    assert "query_database" not in selected.tool_ids
    assert "list_database_tables" not in selected.tool_ids


def test_native_read_capabilities_require_their_explicit_profile():
    selected = select_capabilities(
        "List emails in the inbox",
        profile=ALL_VIEW_PROFILE,
        authenticated=True,
    )

    assert selected.pack_ids == ("email.read",)
    assert selected.tools == ()
    assert selected.tool_ids == ()

    email = select_capabilities(
        "List emails in the inbox",
        profile=ALL_VIEW_PROFILE | frozenset({("email", "view")}),
        authenticated=True,
    )
    kanban = select_capabilities(
        "List kanban cards on the board",
        profile=ALL_VIEW_PROFILE | frozenset({("kanban", "view")}),
        authenticated=True,
    )

    assert email.tool_ids == (
        "list_emails",
        "get_email_details",
        "download_attachment",
    )
    assert kanban.tool_ids == (
        "list_kanban_cards",
        "get_kanban_card",
        "get_kanban_summary",
        "check_kanban_card_stock",
    )


def test_write_intent_routes_to_a_specialist_without_tools():
    selected = select_capabilities(
        "Send an email and create a kanban card",
        profile=ALL_VIEW_PROFILE,
        authenticated=True,
    )

    assert selected.requires_specialist is True
    assert selected.pack_ids == ()
    assert selected.tools == ()


def test_every_text_action_name_routes_to_specialist_fallback():
    from ai.core.voice.tool_actions import text_chat_action_tools

    missed = [
        tool_name(tool)
        for tool in text_chat_action_tools()
        if not select_capabilities(
            tool_name(tool).replace("_", " "),
            authenticated=True,
        ).requires_specialist
    ]

    assert missed == []


def test_ambiguous_prompt_requires_clarification_without_broad_fallback():
    selected = select_capabilities(
        "What about that one?",
        profile=ALL_VIEW_PROFILE,
        authenticated=True,
    )

    assert selected.clarification_required is True
    assert selected.reason == "no_capability_match"
    assert selected.pack_ids == ()
    assert selected.tools == ()


def test_ordinary_selections_are_deterministic_read_only_and_bounded():
    prompts = (
        ("Show stock for part ABC", "stock_check"),
        ("Show the BOM for assembly 42", "bom_query"),
        ("Find the supplier for this component", "supplier_list"),
        ("List sales orders for this customer", None),
        ("Show build order lines", None),
        ("Find the part datasheet", None),
    )

    for query, lookup_type in prompts:
        first = select_capabilities(
            query,
            lookup_type=lookup_type,
            profile=ALL_VIEW_PROFILE,
            authenticated=True,
        )
        second = select_capabilities(
            query,
            lookup_type=lookup_type,
            profile=ALL_VIEW_PROFILE,
            authenticated=True,
        )

        assert first == second
        assert len(first.pack_ids) <= 2
        assert len(first.tools) <= MAX_INITIAL_TOOLS
        selected_ids = set(first.tool_ids)
        assert all(
            entry.effect is ToolEffect.READ
            for entry in capability_catalog()
            if entry.tool_id in selected_ids
        )


class _FakeAgent:
    def __init__(self):
        self.calls = []

    async def run(self, query, *, tools):
        self.calls.append({"query": query, "tools": tools})
        message = SimpleNamespace(text="ok")
        return SimpleNamespace(text="ok", messages=[message])


def _configure_fake_workflow(monkeypatch, authorized_tools, *, shadow_enabled=True, enforce=False):
    from ai.core.tools import rbac
    from ai.core.workflows import wf8_lookup

    workflow = wf8_lookup.T1LookupWorkflow()
    agent = _FakeAgent()

    get_agent = AsyncMock(return_value=agent)
    tools_for_current_user = AsyncMock(return_value=authorized_tools)

    monkeypatch.setattr(workflow, "_get_agent", get_agent)
    monkeypatch.setattr(rbac, "tools_for_current_user", tools_for_current_user)
    monkeypatch.setattr(
        wf8_lookup,
        "get_settings",
        lambda: SimpleNamespace(
            feature_capability_broker_shadow=shadow_enabled,
            feature_capability_broker_enforce=enforce,
        ),
    )
    return workflow, agent


@pytest.mark.asyncio
async def test_wf8_shadow_selection_preserves_current_runtime_tools(monkeypatch):
    from ai.core.workflows.wf8_lookup import LookupType

    authorized_tools = list(INVENTORY_READ_TOOLS[:6])
    workflow, agent = _configure_fake_workflow(monkeypatch, authorized_tools)

    result = await workflow.execute(
        "Show stock for part ABC-123",
        lookup_type=LookupType.STOCK_CHECK,
    )

    assert result.success is True
    assert len(agent.calls) == 1
    assert agent.calls[0]["tools"] is authorized_tools


@pytest.mark.asyncio
async def test_wf8_shadow_failure_does_not_fail_the_lookup(monkeypatch, caplog):
    from ai.core.tools import capabilities
    from ai.core.workflows import wf8_lookup

    authorized_tools = list(INVENTORY_READ_TOOLS[:6])
    workflow, agent = _configure_fake_workflow(monkeypatch, authorized_tools)

    def fail_selection(*args, **kwargs):
        raise RuntimeError("shadow-only failure")

    monkeypatch.setattr(capabilities, "select_capabilities", fail_selection)
    caplog.set_level("ERROR", logger=wf8_lookup.__name__)

    result = await workflow.execute("Show stock")

    assert result.success is True
    assert agent.calls[0]["tools"] is authorized_tools
    assert "Capability broker selection failed" in caplog.text


@pytest.mark.asyncio
async def test_wf8_enforcement_passes_only_selected_pack_tools(monkeypatch):
    from ai.core.auth import AIPrincipal, principal_context
    from ai.core.tools.capabilities import tool_name
    from ai.core.workflows.wf8_lookup import LookupType

    authorized_tools = list(INVENTORY_READ_TOOLS)
    workflow, agent = _configure_fake_workflow(
        monkeypatch,
        authorized_tools,
        enforce=True,
    )
    principal = AIPrincipal(
        subject="user:7",
        actor="user:7",
        user_pk="7",
        username="user-7",
        authentication_method="session",
        scope="chat",
        policy_version="test",
        is_staff=False,
        is_superuser=False,
    )
    token = principal_context.set(principal)
    try:
        result = await workflow.execute(
            "Which item has the highest stock?",
            lookup_type=LookupType.STOCK_CHECK,
        )
    finally:
        principal_context.reset(token)

    assert result.success is True
    assert agent.calls[0]["tools"] is not authorized_tools
    assert tuple(tool_name(tool) for tool in agent.calls[0]["tools"]) == (
        "check_low_stock",
        "get_stock_levels",
        "get_stock_quantity",
        "get_stock_item",
        "get_stock_at_location",
        "get_stock_locations",
        "list_database_tables",
        "query_database",
    )


@pytest.mark.asyncio
async def test_wf8_enforcement_preserves_full_authorized_tools_for_specialist(
    monkeypatch,
):
    authorized_tools = list(EMAIL_TOOLS)
    workflow, agent = _configure_fake_workflow(
        monkeypatch,
        authorized_tools,
        enforce=True,
    )

    result = await workflow.execute("Send an email to the supplier")

    assert result.success is True
    assert agent.calls[0]["tools"] is authorized_tools


@pytest.mark.asyncio
async def test_wf8_enforcement_never_widens_after_selector_failure(monkeypatch, caplog):
    from ai.core.tools import capabilities
    from ai.core.workflows import wf8_lookup

    authorized_tools = list(INVENTORY_READ_TOOLS)
    workflow, agent = _configure_fake_workflow(
        monkeypatch,
        authorized_tools,
        enforce=True,
    )

    def fail_selection(*args, **kwargs):
        raise RuntimeError("enforced selector failure")

    monkeypatch.setattr(capabilities, "select_capabilities", fail_selection)
    caplog.set_level("ERROR", logger=wf8_lookup.__name__)

    result = await workflow.execute("Show stock")

    assert result.success is False
    assert result.formatted_response == "Unable to complete lookup."
    assert result.error == "lookup_failed"
    assert agent.calls == []
    assert "Capability broker selection failed" in caplog.text
    assert "enforced selector failure" not in caplog.text


@pytest.mark.asyncio
async def test_wf8_stream_uses_current_runtime_tools(monkeypatch):
    from ai.core.workflows.wf8_lookup import LookupType

    authorized_tools = list(INVENTORY_READ_TOOLS[:6])
    workflow, agent = _configure_fake_workflow(monkeypatch, authorized_tools)

    chunks = [
        chunk
        async for chunk in workflow.stream_execute(
            "Show stock for part ABC-123",
            lookup_type=LookupType.STOCK_CHECK,
        )
    ]

    assert chunks == ["ok"]
    assert len(agent.calls) == 1
    assert agent.calls[0]["tools"] is authorized_tools


@pytest.mark.asyncio
async def test_wf8_agent_is_toolless_guarded_and_bounded(monkeypatch):
    from ai.core.tools.invocation_guard import CapabilityInvocationMiddleware
    from ai.core.workflows import wf8_lookup

    captured = {}
    invocation_config = SimpleNamespace(
        max_iterations=40,
        include_detailed_errors=True,
    )

    class FakeClient:
        function_invocation_config = invocation_config

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        wf8_lookup,
        "get_settings",
        lambda: SimpleNamespace(
            azure_openai_deployment="test-model",
            azure_openai_endpoint="https://example.invalid",
            azure_openai_api_key="test-key",
        ),
    )
    monkeypatch.setattr(
        wf8_lookup,
        "AzureOpenAIChatClient",
        lambda **_kwargs: FakeClient(),
    )
    monkeypatch.setattr(wf8_lookup, "ChatAgent", FakeAgent)

    workflow = wf8_lookup.T1LookupWorkflow()
    agent = await workflow._get_agent()

    assert isinstance(agent, FakeAgent)
    assert "tools" not in captured
    assert isinstance(captured["middleware"], CapabilityInvocationMiddleware)
    assert invocation_config.max_iterations == workflow.MAX_TOOL_ITERATIONS
    assert invocation_config.include_detailed_errors is False


@pytest.mark.asyncio
async def test_wf8_stream_redacts_internal_errors(monkeypatch, caplog):
    from ai.core.workflows import wf8_lookup

    authorized_tools = list(INVENTORY_READ_TOOLS[:6])
    workflow, agent = _configure_fake_workflow(monkeypatch, authorized_tools)

    fail_run = AsyncMock(side_effect=RuntimeError("sensitive internal detail"))

    monkeypatch.setattr(agent, "run", fail_run)
    caplog.set_level("ERROR", logger=wf8_lookup.__name__)

    chunks = [chunk async for chunk in workflow.stream_execute("Show stock")]

    assert chunks == ["Unable to complete lookup."]
    fail_run.assert_awaited_once()
    assert fail_run.await_args.kwargs["tools"] is authorized_tools
    assert "sensitive internal detail" not in caplog.text
