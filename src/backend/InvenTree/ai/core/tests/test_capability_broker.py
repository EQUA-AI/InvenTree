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
from ai.core.tools import capabilities
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


@pytest.fixture(autouse=True)
def _pinned_category_lexicon(monkeypatch):
    """Pin the lexicon empty so pack assertions never depend on fixture data.

    ``category_lexicon`` reads live category names, so leaving it live would make
    every selection assertion in this module a function of whatever categories
    happen to exist. Tests that exercise the lexicon set their own.
    """
    monkeypatch.setattr(capabilities, "category_lexicon", frozenset)


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

    # The lookup type still pins the primary pack; the SQL escape hatch rides
    # along so a question the parts tools cannot express is still answerable.
    assert selected.pack_ids == ("parts.read", "analytics.read")
    assert selected.tool_ids == (
        "search_parts",
        "get_part",
        "get_part_parameters",
        "get_part_pricing",
        "get_categories",
        "list_database_tables",
        "query_database",
    )


def test_selector_fails_closed_without_an_authenticated_principal():
    selected = select_capabilities(
        "Show stock inventory",
        profile=ALL_VIEW_PROFILE,
        authenticated=False,
    )

    assert selected.pack_ids == ("stock.read", "analytics.read")
    assert selected.tools == ()
    assert selected.tool_ids == ()


#: Phrasings of the same question a user might reasonably type. Selection used to
#: key on superlatives only, so every threshold and counting form below reached a
#: toolset that could not express the question and the agent answered "0".
AGGREGATE_PHRASINGS = (
    "How many fasteners do we have in stock with a quantity over 2000?",
    "Which fasteners have more than 2000 in stock?",
    "List all fasteners with stock above 2000",
    "Show me every part with stock greater than 2000",
    "How many parts have over 2000 units on hand?",
    "count the fasteners with stock over 2000",
    "Give me a breakdown of stock by category",
    "What is the total stock for each fastener?",
    "Which fastener has the highest stock?",
    "How much stock do we have per location?",
    "Sum the stock quantity for all fasteners",
    "how many fastners over 2000",
    "qty > 2000 fastenrs",
    "anything over 2000?",
)

#: Ordinary lookups that must keep reaching tools, including two that the write
#: gate used to swallow whole because "purchase" and "order" are also write verbs.
ORDINARY_LOOKUPS = (
    "Show stock for part ABC",
    "Show the BOM for assembly 42",
    "Find the part datasheet",
    "List supplier purchase orders",
    "Show build order lines",
    "List sales orders for this customer",
)


@pytest.mark.parametrize("query", AGGREGATE_PHRASINGS)
def test_aggregate_and_threshold_phrasings_reach_the_sql_tool(query):
    selected = select_capabilities(query, profile=ALL_VIEW_PROFILE, authenticated=True)

    assert not selected.requires_specialist, query
    assert "query_database" in selected.tool_ids, (query, selected.pack_ids)
    assert len(selected.tools) <= MAX_INITIAL_TOOLS, (query, len(selected.tools))


@pytest.mark.parametrize("query", AGGREGATE_PHRASINGS + ORDINARY_LOOKUPS)
def test_no_ordinary_question_is_left_without_tools(query):
    selected = select_capabilities(query, profile=ALL_VIEW_PROFILE, authenticated=True)

    assert selected.tools, (query, selected.reason)


def test_count_reads_as_a_question_only_on_an_explicit_read_signal():
    # "count" is genuinely both: the stocktake verb and the counting question.
    assert select_capabilities("count stock", authenticated=True).requires_specialist
    assert select_capabilities(
        "count stock in every bin", authenticated=True
    ).requires_specialist

    for query in ("count the fasteners with stock over 2000", "count of fasteners"):
        assert not select_capabilities(query, authenticated=True).requires_specialist, query


def test_compound_order_nouns_are_not_write_intent():
    for query in ORDINARY_LOOKUPS:
        assert not select_capabilities(query, authenticated=True).requires_specialist, query

    # The imperative still routes to a specialist on its own verb.
    for query in ("create a purchase order", "order 10 units of ABC-123", "return stock"):
        assert select_capabilities(query, authenticated=True).requires_specialist, query


def test_sql_is_never_the_only_inventory_tool():
    # Only the shape matched, so analytics would otherwise be selected alone and
    # every answer would have to be hand-written SQL.
    selected = select_capabilities("anything over 2000?", profile=ALL_VIEW_PROFILE, authenticated=True)

    assert "query_database" in selected.tool_ids
    assert {"search_parts", "get_stock_levels"} <= set(selected.tool_ids)


def test_sql_hatch_is_not_attached_outside_the_inventree_data_graph():
    # SQL is not a fallback for a mailbox or a board.
    email = select_capabilities(
        "List emails in the inbox",
        profile=ALL_VIEW_PROFILE | frozenset({("email", "view")}),
        authenticated=True,
    )
    kanban = select_capabilities(
        "show my kanban board",
        profile=ALL_VIEW_PROFILE | frozenset({("kanban", "view")}),
        authenticated=True,
    )

    assert email.pack_ids == ("email.read",)
    assert kanban.pack_ids == ("kanban.read",)
    assert "query_database" not in email.tool_ids + kanban.tool_ids


def test_live_category_names_route_to_the_parts_pack(monkeypatch):
    # A deployment-specific noun with no pack term of its own. The lexicon is a
    # routing hint only -- the category is still resolved against live data.
    monkeypatch.setattr(capabilities, "category_lexicon", lambda: frozenset({"gaskets"}))
    selected = select_capabilities("gaskets", profile=ALL_VIEW_PROFILE, authenticated=True)

    assert "parts.read" in selected.pack_ids
    assert "lexicon" in selected.signals


def test_lexicon_variants_reject_collision_prone_names_before_deriving_forms():
    from ai.core.tools.capabilities import _lexicon_variants

    assert _lexicon_variants("Fasteners") == {"fasteners", "fastener"}
    assert _lexicon_variants("O-Rings") == {"o-rings", "o-ring"}
    assert _lexicon_variants("Batteries") == {"batteries", "battery"}
    assert _lexicon_variants("Hydraulic Seals") == {"hydraulic seals", "hydraulic seal"}

    # Rejected on the name itself. Filtering only the derived forms would let a
    # stop-listed "Misc" back in as "miscs", and a too-short "Box" as "boxs".
    assert _lexicon_variants("Misc") == set()
    assert _lexicon_variants("Parts") == set()
    assert _lexicon_variants("Box") == set()
    assert _lexicon_variants("") == set()

    # Junk-form guards: -ss and -is endings gain no bogus derived forms, and a
    # length-filtered singular never resurfaces.
    assert _lexicon_variants("Glass") == {"glass"}
    assert _lexicon_variants("Analysis") == {"analysis"}
    assert _lexicon_variants("Gases") == {"gases"}
    assert _lexicon_variants("Dies") == {"dies"}


def test_selection_reports_the_signals_that_widened_it():
    # The threshold shape is what earns the analytics pack here, so the hatch has
    # nothing left to add and correctly stays silent.
    shaped = select_capabilities(
        "How many fasteners do we have in stock with a quantity over 2000?",
        profile=ALL_VIEW_PROFILE,
        authenticated=True,
    )

    assert "shape" in shaped.signals
    assert "sql_escape_hatch" not in shaped.signals

    # Nothing about this question is analytical, so the hatch is what attaches it.
    hatched = select_capabilities(
        "Tell me about ABC-123",
        lookup_type="part_details",
        profile=ALL_VIEW_PROFILE,
        authenticated=True,
    )

    assert hatched.signals == ("sql_escape_hatch",)


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
        # A primary pack, up to two reviewed adjacent packs, and the SQL hatch.
        assert len(first.pack_ids) <= 3
        assert len(first.tools) <= MAX_INITIAL_TOOLS
        selected_ids = set(first.tool_ids)
        assert all(
            entry.effect is ToolEffect.READ
            for entry in capability_catalog()
            if entry.tool_id in selected_ids
        )


def test_tool_budget_holds_for_every_selectable_pack_combination():
    """Enumerate every primary + <=2 adjacent + hatch combo against the budget.

    The budget comment claims a worst case of 15; the load-bearing assertion is
    the ceiling itself, so a pack that grows past it fails here instead of
    silently triggering trims in production.
    """
    from itertools import combinations

    sizes = {
        pack_id: len(spec[1]) for pack_id, spec in capabilities._PACK_SPECS.items()
    }
    worst = 0
    for primary, adjacent in capabilities._ADJACENT_PACKS.items():
        for width in range(min(2, len(adjacent)) + 1):
            for chosen in combinations(sorted(adjacent), width):
                packs = {primary, *chosen, "analytics.read"}
                worst = max(worst, sum(sizes[pack_id] for pack_id in packs))

    assert worst <= MAX_INITIAL_TOOLS
    # Informational pin: update alongside deliberate pack-size changes.
    assert worst == 15


def test_forced_budget_trims_weakest_adjacent_and_terminates(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(capabilities, "MAX_INITIAL_TOOLS", 8)
    caplog.set_level(logging.INFO, logger=capabilities.__name__)

    selected = select_capabilities(
        "Which item has the highest total stock?",
        profile=ALL_VIEW_PROFILE,
        authenticated=True,
    )

    # The weakest-scoring adjacent pack is trimmed; the primary survives.
    assert selected.pack_ids[0] == "parts.read"
    assert "stock.read" not in selected.pack_ids
    assert "analytics.read" in selected.pack_ids
    assert len(selected.tools) <= 8
    assert "trimmed to fit the tool budget" in caplog.text


def test_single_oversized_pack_warns_and_still_answers(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(capabilities, "MAX_INITIAL_TOOLS", 4)
    caplog.set_level(logging.WARNING, logger=capabilities.__name__)

    selected = select_capabilities(
        "list part parameters",
        profile=ALL_VIEW_PROFILE,
        authenticated=True,
    )

    # The primary is never dropped, even alone over budget: warn, don't raise.
    assert selected.pack_ids == ("parts.read",)
    assert len(selected.tools) == 5
    assert "exceeds the tool budget" in caplog.text


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
    # "item" scores the parts pack and the superlative scores analytics, so the
    # stock primary carries both reviewed neighbours. Order is catalog order.
    assert tuple(tool_name(tool) for tool in agent.calls[0]["tools"]) == (
        "search_parts",
        "get_part",
        "check_low_stock",
        "get_part_parameters",
        "get_part_pricing",
        "get_stock_levels",
        "get_stock_quantity",
        "get_stock_item",
        "get_stock_at_location",
        "get_stock_locations",
        "get_categories",
        "list_database_tables",
        "query_database",
    )


@pytest.mark.asyncio
async def test_wf8_replays_conversation_history_into_the_turn(monkeypatch):
    from agent_framework import Role

    workflow, agent = _configure_fake_workflow(monkeypatch, list(INVENTORY_READ_TOOLS))

    # Without the transcript this turn has no antecedent for "the ones" and the
    # agent has to guess at the subject.
    await workflow.execute(
        "Just the ones with a quantity over 2000.",
        context={
            "conversation_history": [
                {"role": "user", "content": "How many fasteners are in stock?"},
                {"role": "assistant", "content": "Fasteners are stocked across four parts."},
            ]
        },
    )

    replayed = agent.calls[0]["query"]
    assert [message.role for message in replayed] == [Role.USER, Role.ASSISTANT, Role.USER]
    assert replayed[-1].text == "Just the ones with a quantity over 2000."


@pytest.mark.asyncio
async def test_wf8_sends_a_bare_query_when_there_is_no_history(monkeypatch):
    workflow, agent = _configure_fake_workflow(monkeypatch, list(INVENTORY_READ_TOOLS))

    await workflow.execute("Show stock for part ABC-123")

    assert agent.calls[0]["query"] == "Show stock for part ABC-123"


def test_wf8_run_input_skips_non_conversational_roles():
    """System/tool transcript rows never replay as user speech."""
    from agent_framework import Role
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    replayed = T1LookupWorkflow._run_input(
        "Just the ones over 2000.",
        {
            "conversation_history": [
                {"role": "system", "content": "internal system note"},
                {"role": "user", "content": "How many fasteners are in stock?"},
                {"role": "tool", "content": '{"tool_result": 4}'},
                {"role": "assistant", "content": "Four parts carry them."},
            ]
        },
    )

    assert [message.role for message in replayed] == [Role.USER, Role.ASSISTANT, Role.USER]
    assert all("internal system note" not in message.text for message in replayed)
    assert all("tool_result" not in message.text for message in replayed)


def test_wf8_run_input_degrades_to_bare_query_when_history_is_all_machine_rows():
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    replayed = T1LookupWorkflow._run_input(
        "Show stock",
        {
            "conversation_history": [
                {"role": "system", "content": "x"},
                {"role": "tool", "content": "y"},
            ]
        },
    )

    assert replayed == "Show stock"


@pytest.mark.asyncio
async def test_wf8_asks_for_clarification_instead_of_answering_toolless(monkeypatch):
    workflow, agent = _configure_fake_workflow(
        monkeypatch, list(INVENTORY_READ_TOOLS), enforce=True
    )

    # Nothing in this message identifies a subject. The turn used to run the
    # ordinary answering prompt with an empty toolset, which is how an empty
    # result became a confident figure.
    result = await workflow.execute("What about that one?")

    assert result.success is True
    assert agent.calls[0]["tools"] == []
    workflow._get_agent.assert_awaited_once_with(voice=False, read_only=False, clarify=True)


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
async def test_wf8_execute_redacts_internal_errors(monkeypatch, caplog):
    """An agent.run failure yields the generic answer and leaks no detail.

    Ports the coverage of the deleted stream_execute redaction test onto the
    live path: the selector-raises variant is already covered by
    test_wf8_enforcement_never_widens_after_selector_failure; this one proves
    the agent-raises variant.
    """
    from ai.core.workflows import wf8_lookup

    authorized_tools = list(INVENTORY_READ_TOOLS[:6])
    workflow, agent = _configure_fake_workflow(monkeypatch, authorized_tools)

    fail_run = AsyncMock(side_effect=RuntimeError("sensitive internal detail"))
    monkeypatch.setattr(agent, "run", fail_run)
    caplog.set_level("ERROR", logger=wf8_lookup.__name__)

    result = await workflow.execute("Show stock for part ABC-123")

    assert result.success is False
    assert result.error == "lookup_failed"
    assert result.formatted_response == "Unable to complete lookup."
    fail_run.assert_awaited_once()
    assert "sensitive internal detail" not in caplog.text
    assert "sensitive internal detail" not in result.formatted_response


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
