"""Contract tests for the RBAC-first capability broker."""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ai.core.integrations.attachment_corpus import ATTACHMENT_CORPUS_TOOLS
from ai.core.integrations.controlled_document_corpus import CONTROLLED_CORPUS_TOOLS
from ai.core.integrations.email.tools import EMAIL_TOOLS
from ai.core.integrations.inventory_tools import INVENTORY_READ_TOOLS
from ai.core.integrations.kanban_tools import (
    BOARD_TASK_READ_TOOLS,
    KANBAN_READ_TOOLS,
    KANBAN_TOOLS,
)
from ai.core.integrations.media_corpus import EVIDENCE_MEDIA_TOOLS
from ai.core.integrations.memory_tools import MEMORY_ACTION_TOOLS
from ai.core.integrations.source_inventory_tools import SOURCE_INVENTORY_TOOLS
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
from ai.core.tools.inventree.read.action_status import get_last_action_status

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


def test_catalog_covers_every_workflow_toolset_once_in_canonical_order():
    """Since S11 the catalog spans every rail, not wf8 alone.

    An uncatalogued tool is denied as ``unknown_tool``, so a workflow whose
    tools are missing here cannot run at all once its middleware is attached.
    wf8's toolset stays the canonical prefix so the manifest order is stable.
    """
    wf8_tools = (
        *INVENTORY_READ_TOOLS,
        get_last_action_status,
        *EMAIL_TOOLS,
        *KANBAN_TOOLS,
        *BOARD_TASK_READ_TOOLS,
        *CONTROLLED_CORPUS_TOOLS,
        *ATTACHMENT_CORPUS_TOOLS,
        *EVIDENCE_MEDIA_TOOLS,
        *SOURCE_INVENTORY_TOOLS,
        *MEMORY_ACTION_TOOLS,
    )
    catalog = capability_catalog()

    # 64 wf8 tools (delete_kanban_card is withheld) + the specialist writes
    # wf2/wf3/wf4/wf6 carry: parts/stock/company/sales writes and the nine
    # purchase-order write tools. R2 added search_attachment_docs; R3 added
    # search_evidence_media; S8a added list_document_sources; R5 retired
    # search_part_documents (59 -> 58, catalog 95 -> 94).
    # Connected mailbox discovery and the owner-bound receipt reader are
    # additive. S5b board-task reads (list/get/check_board_task_*) joined
    # wf8 and the catalog: 61 -> 64 tools, catalog 97 -> 100.
    assert len(wf8_tools) == 64
    assert len(catalog) == 100
    assert tuple(entry.tool for entry in catalog[:64]) == wf8_tools
    assert len({entry.tool_id for entry in catalog}) == len(catalog)


def test_wf8_never_gains_a_specialist_write_pack():
    """The rail boundary is the point of the workflow map.

    wf8 is the everyday chat rail; the specialist write packs exist so
    wf2/wf4/wf6 can be enforced, not so a lookup turn can reach them.
    """
    catalog = capability_catalog()
    wf8_packs = {entry.pack_id for entry in catalog if "wf8" in entry.workflows}
    assert not {pack for pack in wf8_packs if pack.endswith(".write")} - {
        "email.write",
        "kanban.write",
    }
    procurement = next(entry for entry in catalog if entry.tool_id == "issue_purchase_order")
    assert procurement.workflows == frozenset({"wf4"})


def test_catalog_has_expected_stable_pack_shapes():
    counts = Counter(entry.pack_id for entry in capability_catalog())

    assert counts == {
        "memory.proposals": 1,
        "parts.read": 5,
        "stock.read": 6,
        "bom.read": 2,
        # Grew to 3 at R2 when search_attachment_docs joined the legacy pair;
        # relaxed back to 2 at R5 when search_part_documents was retired.
        "documents.read": 2,
        "procurement.read": 5,
        "action_status.read": 1,
        "sales.read": 4,
        "build.read": 3,
        "analytics.read": 2,
        # Every machine-page tab, plus search (spoken-name resolution) and the
        # signal trend: the machine detail page is unreachable without them.
        "machines.read": 9,
        "email.read": 4,
        "email.write": 3,
        "kanban.read": 7,
        # 7, not 8: delete_kanban_card is withheld, though it remains listed in
        # the kanban.write pack spec so a re-add still resolves to a pack.
        # Maintenance work orders: search plus the per-order drill-downs
        # (overview, readiness, repair state) and the per-machine open-repairs
        # view -- a job question is unanswerable without the full set.
        "maintenance.read": 7,
        # Controlled documentation is a single site-scoped retrieval tool.
        "manuals.read": 1,
        # Evidence-media retrieval (R3) is likewise a single-tool pack.
        "evidence.read": 1,
        # S8a registry inventory is a single-tool pack; reachability comes
        # from the sources-primary rider, not term sprawl.
        "sources.read": 1,
        # Specialist write packs (S11): catalogued so wf2/wf3/wf4/wf6 can be
        # enforced at all. Not selectable -- _pack_scores only scores reads.
        "parts.write": 6,
        "stock.write": 16,
        "company.write": 3,
        "procurement.write": 9,
        "sales.write": 2,
    }


def test_every_catalog_entry_has_an_explicit_policy():
    catalog = capability_catalog()
    policies = {entry.tool_id: entry.authorization for entry in catalog}

    assert all(entry.authorization.kind in PolicyKind for entry in catalog)
    # search_attachment_docs and search_evidence_media are DELIBERATELY dark
    # until their retrieval flags flip (R2/R3 rollouts); everything else must
    # carry a live policy.
    assert {
        entry.tool_id for entry in catalog if entry.authorization.kind is PolicyKind.DISABLED
    } == {"search_attachment_docs", "search_evidence_media"}
    for tool in EMAIL_TOOLS:
        permission = (
            "view"
            if tool.__name__
            in ("list_mailboxes", "list_emails", "get_email_details", "download_attachment")
            else "send"
        )
        policy = policies[tool.__name__]
        assert policy.kind is PolicyKind.NATIVE_PERMISSION
        assert policy.all_of == (("email", permission),)
    # A kanban card is a work order's tracked work on the board, and InvenTree
    # governs tasks_workorder with the WORK_ORDER ruleset -- so that is the pair
    # the catalog must declare. This previously asserted an AIMMS-native
    # ("kanban", ...) capability, which no ruleset backed and no migration
    # granted.
    for tool in KANBAN_TOOLS:
        if tool in KANBAN_READ_TOOLS:
            permission = "view"
        elif tool.__name__ == "create_kanban_card":
            permission = "add"
        else:
            permission = "change"
        policy = policies[tool.__name__]
        assert policy.kind is PolicyKind.NATIVE_PERMISSION
        assert policy.all_of == (("work_order", permission),)
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
        # Machine reads are tenant-scoped, not role-scoped: a work_order:view
        # grant is global while asset rows belong to a customer or client, so
        # the role can only ever say "may read assets", never "may read this
        # one". Plain NATIVE_PERMISSION would be the wrong shape here.
        "search_machines",
        "get_machine_overview",
        "get_machine_health",
        "get_machine_signals",
        "get_machine_signal_trend",
        "get_machine_anomalies",
        "get_machine_parts",
        "get_machine_maintenance_history",
        "get_machine_attachments",
        # Maintenance work orders share the machines rationale AND the
        # authorizer string: the same tenant scope governs both packs, so the
        # guard branch is deliberately shared rather than duplicated.
        "search_work_orders",
        "get_work_order_history",
        "get_work_order_closeout",
        "get_work_order_overview",
        "get_work_order_readiness",
        "get_work_order_repair_state",
        "get_open_repairs_for_machine",
        # Controlled-corpus search is site-scoped, not machine-scoped: the
        # filter comes from deployment constants, so it gets its own branch.
        "search_manuals",
    }
    assert policies["query_database"].authorizer == "database_relation_access"
    assert policies["get_part_attachments"].all_of == (("part", "view"),)
    assert policies["get_machine_health"].authorizer == "machine_scope_access"
    assert policies["get_machine_health"].all_of == (("work_order", "view"),)
    assert policies["search_work_orders"].authorizer == "machine_scope_access"
    assert policies["search_work_orders"].all_of == (("work_order", "view"),)
    assert policies["search_manuals"].authorizer == "controlled_corpus_access"
    assert policies["search_manuals"].all_of == (("work_order", "view"),)


def test_attachment_docs_policy_follows_the_retrieval_flag(monkeypatch):
    """Explicitly dark refuses; flag-on grants the any_of two-arm policy.

    The catalog is process-cached, so both directions clear it -- and the
    teardown clear keeps the flag-on catalog from leaking into other tests.
    R5 default-on: BOTH phases use explicit stubs — the dark phase must not
    depend on this machine's providers happening to degrade the default.
    """
    from ai.core import config as ai_config

    real_settings = ai_config.get_settings()
    dark_settings = SimpleNamespace(
        single_site_policy_key=real_settings.single_site_policy_key,
        feature_attachment_rag_retrieval=False,
        feature_media_rag_retrieval=False,
    )
    monkeypatch.setattr(ai_config, "get_settings", lambda: dark_settings)
    capability_catalog.cache_clear()
    policies = {entry.tool_id: entry.authorization for entry in capability_catalog()}
    dark = policies["search_attachment_docs"]
    assert dark.kind is PolicyKind.DISABLED
    assert "FEATURE_ATTACHMENT_RAG_RETRIEVAL" in (dark.reason or "")

    lit_settings = SimpleNamespace(**{
        **{name: getattr(real_settings, name) for name in ("single_site_policy_key",)},
        "feature_attachment_rag_retrieval": True,
        # The media branch reads its own flag during the same catalog build;
        # keep it dark here so this test pins the attachment flag alone.
        "feature_media_rag_retrieval": False,
    })
    monkeypatch.setattr(ai_config, "get_settings", lambda: lit_settings)
    capability_catalog.cache_clear()
    try:
        policies = {entry.tool_id: entry.authorization for entry in capability_catalog()}
        lit = policies["search_attachment_docs"]
        assert lit.kind is PolicyKind.RESOURCE_AUTHORIZER
        assert lit.authorizer == "attachment_corpus_access"
        assert lit.any_of == (("part", "view"), ("work_order", "view"))
        assert lit.all_of == ()
        # The media tool must stay dark under an attachment-only flag flip.
        assert policies["search_evidence_media"].kind is PolicyKind.DISABLED
    finally:
        capability_catalog.cache_clear()


def test_evidence_media_policy_follows_the_retrieval_flag(monkeypatch):
    """Explicitly dark refuses; flag-on grants the single-arm authorizer.

    all_of, not any_of: one role grants the whole evidence corpus -- every
    owner type is an evidence surface under maintenance scope, and part-owned
    media never ingests, so there is no second arm. R5 default-on: both
    phases use explicit stubs (see the attachment twin).
    """
    from ai.core import config as ai_config

    real_settings = ai_config.get_settings()
    dark_settings = SimpleNamespace(
        single_site_policy_key=real_settings.single_site_policy_key,
        feature_attachment_rag_retrieval=False,
        feature_media_rag_retrieval=False,
    )
    monkeypatch.setattr(ai_config, "get_settings", lambda: dark_settings)
    capability_catalog.cache_clear()
    policies = {entry.tool_id: entry.authorization for entry in capability_catalog()}
    dark = policies["search_evidence_media"]
    assert dark.kind is PolicyKind.DISABLED
    assert "FEATURE_MEDIA_RAG_RETRIEVAL" in (dark.reason or "")

    lit_settings = SimpleNamespace(**{
        **{name: getattr(real_settings, name) for name in ("single_site_policy_key",)},
        "feature_attachment_rag_retrieval": False,
        "feature_media_rag_retrieval": True,
    })
    monkeypatch.setattr(ai_config, "get_settings", lambda: lit_settings)
    capability_catalog.cache_clear()
    try:
        policies = {entry.tool_id: entry.authorization for entry in capability_catalog()}
        lit = policies["search_evidence_media"]
        assert lit.kind is PolicyKind.RESOURCE_AUTHORIZER
        assert lit.authorizer == "evidence_media_access"
        assert lit.all_of == (("work_order", "view"),)
        assert lit.any_of == ()
        # And the attachment tool must stay dark under a media-only flip.
        assert policies["search_attachment_docs"].kind is PolicyKind.DISABLED
    finally:
        capability_catalog.cache_clear()


def test_contract_manifest_is_stable_and_complete():
    first = catalog_manifest()
    second = catalog_manifest()

    assert first == second
    assert manifest_json() == manifest_json()
    # Matches the catalog pin above: 100 entries (wf8 64 + specialist writes).
    assert len(first) == 100
    assert all(record["module"] for record in first)
    assert all(record["qualname"] for record in first)
    assert all(len(record["contract_digest"]) == 64 for record in first)


def test_stock_superlative_selects_stock_and_analytics():
    selected = select_capabilities(
        "Which fastener has the highest stock?",
        profile=ALL_VIEW_PROFILE,
        authenticated=True,
    )

    assert selected.pack_ids == ("stock.read", "parts.read", "analytics.read")
    assert selected.tool_ids == (
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

    assert selected.pack_ids == ("stock.read", "parts.read", "analytics.read")
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
    "how many fastners over 2000",  # codespell:ignore fastners
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
    assert select_capabilities("count stock in every bin", authenticated=True).requires_specialist

    for query in ("count the fasteners with stock over 2000", "count of fasteners"):
        assert not select_capabilities(query, authenticated=True).requires_specialist, query


def test_compound_order_nouns_are_not_write_intent():
    for query in ORDINARY_LOOKUPS:
        assert not select_capabilities(query, authenticated=True).requires_specialist, query

    # The imperative still routes to a specialist on its own verb.
    for query in (
        "create a purchase order",
        "order 10 units of ABC-123",
        "return stock",
        "receive the shipment",
        "issue parts to the build",
        "complete the work order",
    ):
        assert select_capabilities(query, authenticated=True).requires_specialist, query


#: Noun/participle uses of write-pattern words and questions about actions.
#: Every phrasing here used to die to a specialist; each must now reach tools
#: or, lacking any domain term, a clarification -- never requires_specialist.
READ_PHRASINGS_WITH_WRITE_WORDS = (
    # review residuals
    "what's the status of order 42",
    "How many parts did we receive this week?",
    "was PO-100 marked complete?",
    "items in order SO-100",
    # noun uses
    "any update on po-100?",
    "is there an issue with the bom?",
    "show the transfer history for part 42",
    "show the email from acme",
    "show me the return orders",
    "what's the purchase order status",
    "the purchase order for acme",
    "show the last order",
    "last purchase price for part 42",
    # questions about actions
    "is the build complete?",
    "did we receive the shipment yesterday?",
    "when did we receive po-100?",
    "was stock split into batches?",
    "is the location set to bin a?",
    "who marked po-100 complete",
    "how many did we order last month",
    "did we order 500 units last month?",
    "is po-100 marked as complete?",
    "was po-100, the big one, marked complete?",
    "was the order placed yesterday?",
    # discourse-prefixed questions
    "hey claude, did we receive the shipment?",
    "and how many parts did we receive this week?",
    "so did we order more m3 screws?",
    "ok, was po-100 marked complete?",
    # gated order references (nominal position)
    "show order so-100",
    "order so-100",
    # round-2: questions with non-pronoun subjects and numeric tails
    "did the warehouse transfer 200 units to bin B?",
    "did acme return 50 units last week?",
    "did the supplier send 3 shipments this month?",
    "did the night shift move 40 pallets?",
    # round-2: participle-tail noun uses and telegraphic aggregates
    "was the stock count updated this morning?",
    "average order value for acme this quarter",
    "what's on order for part 42?",
    # round-2: clause-final compounds after 'and' stay nominal
    "list parts and purchase orders",
    "show stock levels and purchase orders",
    # round-2: phrasal-particle participles and novel spot-checks
    "was anything set aside for order so-9?",
    "did the morning shift count the fasteners?",
    "was the transfer completed by bob?",
    "total order count for this month",
    "did production issue 40 kits last week?",
    "show orders and returns for acme",
)

#: Imperatives -- including request forms, splices, discourse prefixes, light
#: verbs, and the compound phrasings the earlier blind mask regressed -- that
#: must always route to a specialist.
IMPERATIVE_WRITE_PHRASINGS = (
    # request forms, direct and indirect
    "can you receive the shipment?",
    "could you complete the work order for me",
    "please order 10 units of M3 screws",
    "can you cancel po-100",
    "are you able to cancel po-100?",
    "is it possible to order 100 more units of part 42?",
    "is there any way to receive this shipment today?",
    "would it be possible to cancel po-100",
    "any chance you can order more m4 bolts",
    # imperatives spliced onto questions
    "what's low on stock, and order more",
    "what's low, order more",
    "what's low, just order 50 more",
    "what's low - order 50 more",
    "what's low — order 50 more",
    "what's low on stock order 50 more",
    "which parts are low order 50 of each",
    "how many m3 screws do we have order 100 more",
    "what needs restocking? also, order more m3 screws",
    # discourse-prefixed imperatives
    "so order more m3 screws",
    "hey claude, receive the shipment",
    "first order 50 units of M3 screws",
    "next order 50 units",
    # light verbs and verb-position order references
    "place an order for 50 m3 screws",
    "place order po-100",
    "please place order po-100 with the supplier",
    "ship order so-100",
    "can you get order po-100 cancelled",
    "process a return",
    "i'd like an order placed",
    "order po-100 again",
    # verbal compounds (regressed by the earlier blind mask; restored)
    "Return orders 4512 and 4513 to the supplier",
    "return orders to the supplier",
    "ship sales order 55",
    "Purchase order 500 units of M3 screws from Acme",
    "build order 300 more fasteners",
    # participle-anchored 'as' stays write in verb position
    "record po-100 as complete",
    "flag part 5 as complete",
    "Would you mind cancelling sales order 88?",
    # round-2 verification probes: splices with non-quantity tails
    "which parts are low order them all today",
    "which bins are empty move stock there",
    "how many screws are left order a fresh batch",
    "did we receive po-100 if not receive it now",
    "what's low order replacements",
    "what's low ok order some more",
    # bare and/then boundaries before a verbal compound
    "check stock then return orders 4512 to acme",
    "see below and return orders 8501 to the vendor",
    # particle-shifted and initiation light verbs
    "put a purchase order in for the screws",
    "put an order in for 50 m3 screws",
    "start a return for part 42",
    "start a return for order 88",
    # order-reference urgency tails
    "order po-100 now",
    "order po-100 asap",
    "order po-100 today",
    "order po-100 immediately",
    # gerund complements of request shells
    "would you mind moving card 7 to the done column",
    "would you mind archiving card 12?",
    "would you mind canceling po-100",
    "any chance of cancelling po-100",
    "mind archiving po-100 for me?",
    # assorted round-2 clean probes worth pinning
    "let's cancel po-100",
    "go ahead and cancel po-100",
    "kindly cancel order po-100",
    "resubmit order po-100",
    "expedite order po-100",
    "show order po-100 and cancel it",
    "immediately return orders 7001 and 7002 to acme",
    "work order 20 more fasteners for line 2",
    "thanks, cancel po-100",
    "btw cancel po-100",
    "right then, order 50 more m3 screws",
    "ok now archive the completed cards",
    "order po-100 again please",
    "raise a return for the damaged screws",
    "log a return against po-100",
    "get an order placed for 50 screws",
    "what's low, and order more m4 nuts",
    "what's low on stock order more asap",
    "is there a way to merge these two parts",
    "would someone receive this delivery",
    "can u cancel the po",
)


@pytest.mark.parametrize("query", READ_PHRASINGS_WITH_WRITE_WORDS)
def test_noun_and_question_uses_of_write_words_are_not_write_intent(query):
    profile = ALL_VIEW_PROFILE | frozenset({("email", "view")})
    selected = select_capabilities(query, profile=profile, authenticated=True)

    assert not selected.requires_specialist, (query, selected.reason)
    assert selected.tools or selected.clarification_required, query


@pytest.mark.parametrize("query", IMPERATIVE_WRITE_PHRASINGS)
def test_request_forms_and_spliced_imperatives_stay_specialist(query):
    selected = select_capabilities(query, profile=ALL_VIEW_PROFILE, authenticated=True)

    assert selected.requires_specialist, (query, selected.pack_ids)


def test_bare_order_reference_asks_for_clarification():
    # No pack term matches a bare numeric order id; the order type is genuinely
    # unknown, so asking beats both dying to a specialist and guessing.
    selected = select_capabilities(
        "what's the status of order 42", profile=ALL_VIEW_PROFILE, authenticated=True
    )

    assert not selected.requires_specialist
    assert selected.clarification_required


def test_write_intent_is_linear_on_pathological_input():
    """The classifier must not stall the async turn path on pasted walls of text.

    The old per-match residual rebuild was quadratic ('count '*1667 took
    seconds); the reworked rules complete in milliseconds.
    """
    import time

    from ai.core.tools.capabilities import _write_intent

    for payload in ("add " * 2500, "count " * 1667, "did order? " * 910):
        normalized = " ".join(payload.casefold().split())
        started = time.perf_counter()
        _write_intent(normalized)
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert elapsed_ms < 100, elapsed_ms


def test_sql_is_never_the_only_inventory_tool():
    # Only the shape matched, so analytics would otherwise be selected alone and
    # every answer would have to be hand-written SQL.
    selected = select_capabilities(
        "anything over 2000?", profile=ALL_VIEW_PROFILE, authenticated=True
    )

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
        profile=ALL_VIEW_PROFILE | frozenset({("work_order", "view")}),
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
    # stop-listed "Misc" back in as "miscs", and a too-short "Box" as "boxs".  # codespell:ignore boxs
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
        profile=ALL_VIEW_PROFILE | frozenset({("work_order", "view")}),
        authenticated=True,
    )

    assert email.tool_ids == (
        "list_mailboxes",
        "list_emails",
        "get_email_details",
        "download_attachment",
    )
    assert kanban.tool_ids == (
        "list_kanban_cards",
        "get_kanban_card",
        "get_kanban_summary",
        "check_kanban_card_stock",
        "list_board_tasks",
        "get_board_task",
        "check_board_task_stock",
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

    sizes = {pack_id: len(spec[1]) for pack_id, spec in capabilities._PACK_SPECS.items()}
    worst = 0
    for primary, adjacent in capabilities._ADJACENT_PACKS.items():
        for width in range(min(2, len(adjacent)) + 1):
            for chosen in combinations(sorted(adjacent), width):
                packs = {primary, *chosen}
                # The SQL hatch only attaches inside the InvenTree data graph;
                # a kanban primary never receives it (SQL is not a board tool).
                if primary in capabilities._SQL_HATCH_PACKS:
                    packs.add("analytics.read")
                worst = max(worst, sum(sizes[pack_id] for pack_id in packs))

    assert worst <= MAX_INITIAL_TOOLS
    # Informational pin: update alongside deliberate pack-size changes.
    # 20 after the S5b board-task reads grew kanban.read 4 -> 7 (the previous
    # pin was 17 = maintenance(7, +closeout S5b) + machines(9) + SQL(1)).
    assert worst == 20


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
    from ai.core.auth import principal_context

    token = principal_context.set(_authenticated_principal())
    try:
        await workflow.execute(
            "Just the ones with a quantity over 2000.",
            context={
                "conversation_history": [
                    {"role": "user", "content": "How many fasteners are in stock?"},
                    {
                        "role": "assistant",
                        "content": "Fasteners are stocked across four parts.",
                    },
                ]
            },
        )
    finally:
        principal_context.reset(token)

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


# --------------------------------------------------------------------------- #
# Deterministic category hint (matched_category_terms + wf8 injection)        #
# --------------------------------------------------------------------------- #
def test_matched_category_terms_scans_query_and_history(monkeypatch):
    from ai.core.tools.capabilities import matched_category_terms

    monkeypatch.setattr(
        capabilities, "category_lexicon", lambda: frozenset({"fasteners", "fastener"})
    )

    # Word-boundary safety: a substring hit is not a mention.
    assert matched_category_terms("fastenering the panel") == ()
    # Casefold; the reported term is the form that matched (the category name).
    assert matched_category_terms("How many Fasteners are in stock?") == ("fasteners",)
    # When both family forms appear, they collapse to one reported term.
    assert matched_category_terms("a fastener or several fasteners") == ("fastener",)
    # History-only match: the noun lives in the transcript, not the query.
    history = [
        {"role": "user", "content": "How many fasteners are in stock?"},
        {"role": "assistant", "content": "Four parts carry them."},
    ]
    assert matched_category_terms("Just the ones over 2000.", history) == ("fasteners",)
    # Empty lexicon degrades to no terms.
    monkeypatch.setattr(capabilities, "category_lexicon", frozenset)
    assert matched_category_terms("fasteners", history) == ()


def test_matched_category_terms_ignores_machine_roles_and_bad_history(monkeypatch):
    """Tool/system rows never steer the hint, and a non-list history is inert."""
    from ai.core.tools.capabilities import matched_category_terms

    monkeypatch.setattr(
        capabilities, "category_lexicon", lambda: frozenset({"fasteners", "fastener"})
    )

    machine_rows = [
        {"role": "tool", "content": '{"result": "fasteners"}'},
        {"role": "system", "content": "fasteners are relevant"},
    ]
    assert matched_category_terms("Just the ones over 2000.", machine_rows) == ()
    # A malformed history (wrong type) degrades to query-only scanning.
    assert matched_category_terms("Just the ones over 2000.", 42) == ()
    assert matched_category_terms("fasteners please", 42) == ("fasteners",)


def test_matched_category_terms_caps_reported_terms(monkeypatch):
    from ai.core.tools.capabilities import matched_category_terms

    monkeypatch.setattr(
        capabilities,
        "category_lexicon",
        lambda: frozenset({"gaskets", "bearings", "washers", "grommets"}),
    )

    terms = matched_category_terms("gaskets bearings washers grommets")
    assert len(terms) == 3


def test_category_hint_never_contributes_to_selection(monkeypatch):
    """The hint layer is observational: selection output is unchanged by it."""
    monkeypatch.setattr(
        capabilities, "category_lexicon", lambda: frozenset({"fasteners", "fastener"})
    )

    selected = select_capabilities(
        "Just the ones over 2000.", profile=ALL_VIEW_PROFILE, authenticated=True
    )

    assert "shape" in selected.signals
    assert not selected.requires_specialist


def _authenticated_principal():
    """A principal so exposure_authorized passes and the selection has tools.

    Without one, select_capabilities returns zero tools even when packs match --
    and a hint about calling get_categories would then be advice a tool-less
    turn cannot act on.
    """
    from ai.core.auth import AIPrincipal

    return AIPrincipal(
        subject="user:7",
        actor="user:7",
        user_pk="7",
        username="operator",
        authentication_method="django_session",
        scope="site:main",
        policy_version="1",
        is_staff=False,
        is_superuser=True,
    )


@pytest.mark.asyncio
async def test_wf8_enforced_turn_carries_the_category_hint(monkeypatch):
    monkeypatch.setattr(
        capabilities, "category_lexicon", lambda: frozenset({"fasteners", "fastener"})
    )
    workflow, agent = _configure_fake_workflow(
        monkeypatch, list(INVENTORY_READ_TOOLS), enforce=True
    )

    from ai.core.auth import principal_context

    token = principal_context.set(_authenticated_principal())
    try:
        await workflow.execute(
            "Just the ones with a quantity over 2000.",
            context={
                "conversation_history": [
                    {"role": "user", "content": "How many fasteners are in stock?"},
                    {
                        "role": "assistant",
                        "content": "Fasteners are stocked across four parts.",
                    },
                ]
            },
        )
    finally:
        principal_context.reset(token)

    replayed = agent.calls[0]["query"]
    assert isinstance(replayed, list)
    note = replayed[-1].text
    assert "[inventory context]" in note
    assert "'fasteners'" in note
    assert "part_partcategory" in note


@pytest.mark.asyncio
async def test_wf8_bare_turn_mentioning_a_category_still_gets_the_hint(monkeypatch):
    # No history: _run_input returns a plain string, which must be wrapped, not
    # crashed into the broad except.
    monkeypatch.setattr(
        capabilities, "category_lexicon", lambda: frozenset({"fasteners", "fastener"})
    )
    workflow, agent = _configure_fake_workflow(
        monkeypatch, list(INVENTORY_READ_TOOLS), enforce=True
    )

    from ai.core.auth import principal_context

    token = principal_context.set(_authenticated_principal())
    try:
        result = await workflow.execute("List all fasteners with stock above 2000")
    finally:
        principal_context.reset(token)

    assert result.success is True
    replayed = agent.calls[0]["query"]
    assert isinstance(replayed, list)
    assert replayed[0].text == "List all fasteners with stock above 2000"
    assert "[inventory context]" in replayed[-1].text


@pytest.mark.asyncio
async def test_wf8_clarify_and_specialist_turns_never_get_the_hint(monkeypatch):
    monkeypatch.setattr(
        capabilities, "category_lexicon", lambda: frozenset({"fasteners", "fastener"})
    )
    history = {
        "conversation_history": [
            {"role": "user", "content": "How many fasteners are in stock?"},
        ]
    }

    # Clarify turn: no tools, so hinting a tool call would contradict its prompt.
    workflow, agent = _configure_fake_workflow(
        monkeypatch, list(INVENTORY_READ_TOOLS), enforce=True
    )
    await workflow.execute("What about that one?", context=history)
    replayed = agent.calls[0]["query"]
    texts = [m.text for m in replayed] if isinstance(replayed, list) else [replayed]
    assert all("[inventory context]" not in text for text in texts)

    # Specialist turn: enforcement is off for it, and the hint must not attach.
    workflow, agent = _configure_fake_workflow(
        monkeypatch, list(INVENTORY_READ_TOOLS), enforce=True
    )
    await workflow.execute("receive the shipment", context=history)
    replayed = agent.calls[0]["query"]
    texts = [m.text for m in replayed] if isinstance(replayed, list) else [replayed]
    assert all("[inventory context]" not in text for text in texts)


def test_query_database_docstring_keeps_the_category_subtree_example():
    from ai.core.tools.inventree.read.database import query_database

    assert "part_partcategory" in query_database.__doc__
    assert "lft" in query_database.__doc__


def test_read_prompt_answers_only_on_a_result_that_answers():
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    assert "after a result that answers the question" in T1LookupWorkflow.READ_SYSTEM_PROMPT


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

        def __init__(self):
            self.client = self

        def with_options(self, **kwargs):
            return self

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    from ai.core.agents import factory
    from ai.core.integrations import azure_openai_client

    monkeypatch.setattr(
        wf8_lookup,
        "get_settings",
        lambda: SimpleNamespace(
            azure_openai_deployment="test-model",
            azure_openai_endpoint="https://example.invalid",
            azure_openai_api_key="test-key",
        ),
    )
    # M1 PR A: construction happens in the factory; patch its seams.
    monkeypatch.setattr(
        azure_openai_client, "AzureOpenAIChatClient", lambda **_kwargs: FakeClient()
    )
    monkeypatch.setattr(
        azure_openai_client,
        "get_settings",
        lambda: SimpleNamespace(
            azure_openai_endpoint="https://example.invalid", azure_openai_api_key="k"
        ),
    )
    monkeypatch.setattr(factory, "ChatAgent", FakeAgent)

    workflow = wf8_lookup.T1LookupWorkflow()
    agent = await workflow._get_agent()

    assert isinstance(agent, FakeAgent)
    assert captured["tools"] is None
    assert captured["context_providers"] is None
    assert captured["chat_message_store_factory"] is None
    assert isinstance(captured["middleware"], CapabilityInvocationMiddleware)
    assert invocation_config.max_iterations == workflow.MAX_TOOL_ITERATIONS
    assert invocation_config.include_detailed_errors is False


def test_manual_fact_intent_carries_the_uploaded_corpus(monkeypatch):
    """The typed manual_fact intent must include documents.read.

    The S3 intent tuple REPLACES lexical selection, so a tuple without
    documents.read silently removes search_attachment_docs from every
    uploaded-manual question (live golden regression, 2026-08-31).
    """
    from ai.core import config as ai_config

    lit = SimpleNamespace(
        single_site_policy_key="site-a",
        feature_attachment_rag_retrieval=True,
        feature_media_rag_retrieval=False,
    )
    monkeypatch.setattr(ai_config, "get_settings", lambda: lit)
    capability_catalog.cache_clear()
    try:
        selection = select_capabilities(
            "What minimum flow does the uploaded HX-200 manual require?",
            profile=frozenset({("part", "view"), ("work_order", "view")}),
            authenticated=True,
            task_intent="manual_fact",
        )
        assert "documents.read" in selection.pack_ids
        assert "search_attachment_docs" in selection.tool_ids
        assert "search_manuals" not in selection.tool_ids
        assert "list_document_sources" in selection.tool_ids
        assert len(selection.tool_ids) <= MAX_INITIAL_TOOLS
    finally:
        capability_catalog.cache_clear()


def test_typed_intent_naming_evidence_carries_the_evidence_pack(monkeypatch):
    """The explicit-evidence rider must fire on the typed path too.

    The lexical rider (2026-08-21) exists because a sentence NAMING photos
    or recordings must keep search_evidence_media reachable; the S3 intent
    tuple bypassed it, so "does the nameplate photo agree with the manual"
    (manual_fact) and "show the recording on WO-…" (record_retrieval) both
    lost the tool (live golden, 2026-09-01).
    """
    from ai.core import config as ai_config

    lit = SimpleNamespace(
        single_site_policy_key="site-a",
        feature_attachment_rag_retrieval=True,
        feature_media_rag_retrieval=True,
    )
    monkeypatch.setattr(ai_config, "get_settings", lambda: lit)
    capability_catalog.cache_clear()
    try:
        for intent, query in (
            (
                "manual_fact",
                "Do the uploaded manual and the nameplate photo agree on the pressure?",
            ),
            (
                "record_retrieval",
                "In the recording on work order WO-104, show where the seal was replaced.",
            ),
        ):
            selection = select_capabilities(
                query,
                profile=frozenset({("part", "view"), ("work_order", "view")}),
                authenticated=True,
                task_intent=intent,
            )
            assert "evidence.read" in selection.pack_ids, (intent, selection.pack_ids)
            assert "search_evidence_media" in selection.tool_ids, intent
            assert "evidence_rider" in selection.signals
            assert len(selection.tool_ids) <= MAX_INITIAL_TOOLS, (
                intent,
                len(selection.tool_ids),
            )
    finally:
        capability_catalog.cache_clear()


def test_typed_intent_without_evidence_words_stays_pure(monkeypatch):
    """A records question with no evidence noun must not grow the pack.

    The rider is keyword-gated by design — pure analysis intents keep the
    exact tuple the S3 design pinned ("no lexical primary, no adjacency
    walk").
    """
    from ai.core import config as ai_config

    lit = SimpleNamespace(
        single_site_policy_key="site-a",
        feature_attachment_rag_retrieval=True,
        feature_media_rag_retrieval=True,
    )
    monkeypatch.setattr(ai_config, "get_settings", lambda: lit)
    capability_catalog.cache_clear()
    try:
        selection = select_capabilities(
            "How many work orders were opened for each inverter last year?",
            profile=frozenset({("part", "view"), ("work_order", "view")}),
            authenticated=True,
            task_intent="fleet_aggregate",
        )
        assert "evidence.read" not in selection.pack_ids
        assert "evidence_rider" not in selection.signals
    finally:
        capability_catalog.cache_clear()


def test_typed_evidence_rider_never_exposes_a_dark_media_tool(monkeypatch):
    """Rider + FEATURE_MEDIA_RAG_RETRIEVAL off: the pack rides, the tool stays dark."""
    from ai.core import config as ai_config

    dark = SimpleNamespace(
        single_site_policy_key="site-a",
        feature_attachment_rag_retrieval=True,
        feature_media_rag_retrieval=False,
    )
    monkeypatch.setattr(ai_config, "get_settings", lambda: dark)
    capability_catalog.cache_clear()
    try:
        selection = select_capabilities(
            "In the recording on work order WO-104, show where the seal was replaced.",
            profile=frozenset({("part", "view"), ("work_order", "view")}),
            authenticated=True,
            task_intent="record_retrieval",
        )
        assert "search_evidence_media" not in selection.tool_ids
    finally:
        capability_catalog.cache_clear()


# --------------------------------------------------------------------------- #
# M1 (GR-33): thread-stable tool prefix                                        #
# --------------------------------------------------------------------------- #
_STICKY_QUERY = "Which parts are running low on stock in the warehouse?"


def _select(query, *, history=None):
    context = {"tool_pack_history": history} if history is not None else None
    return select_capabilities(query, context=context, profile=ALL_VIEW_PROFILE, authenticated=True)


def test_sticky_packs_are_off_by_default(monkeypatch):
    monkeypatch.setattr(capabilities, "stable_tool_prefix_enabled", lambda: False)
    baseline = _select(_STICKY_QUERY)
    with_history = _select(_STICKY_QUERY, history=["documents.read", "machines.read"])
    assert with_history.pack_ids == baseline.pack_ids
    assert with_history.tool_ids == baseline.tool_ids
    assert "sticky_packs" not in with_history.signals


def test_sticky_packs_ride_after_the_turns_own_picks(monkeypatch):
    """Earlier packs are appended (never promoted) and re-authorized like any other."""
    monkeypatch.setattr(capabilities, "stable_tool_prefix_enabled", lambda: True)
    baseline = _select(_STICKY_QUERY)
    assert baseline.pack_ids, "the query must select something on its own"
    sticky = _select(_STICKY_QUERY, history=["documents.read", "machines.read"])
    own = tuple(pack_id for pack_id in sticky.pack_ids if pack_id in baseline.pack_ids)
    assert own == baseline.pack_ids  # the turn's own selection is untouched, order kept
    added = tuple(pack_id for pack_id in sticky.pack_ids if pack_id not in baseline.pack_ids)
    assert set(added) >= {"documents.read", "machines.read"} - set(baseline.pack_ids)
    assert sticky.pack_ids[0] == baseline.pack_ids[0]  # primary stays at index 0
    assert "sticky_packs" in sticky.signals
    assert len(sticky.tool_ids) <= MAX_INITIAL_TOOLS
    assert set(baseline.tool_ids) <= set(sticky.tool_ids)


def test_sticky_packs_ignore_unknown_write_and_duplicate_ids(monkeypatch):
    monkeypatch.setattr(capabilities, "stable_tool_prefix_enabled", lambda: True)
    baseline = _select(_STICKY_QUERY)
    garbage = [
        "email.write",  # write pack: never sticky
        "not.a.pack",
        42,
        None,
        *baseline.pack_ids,  # already selected: not duplicated
        "documents.read",
        "documents.read",
    ]
    selection = _select(_STICKY_QUERY, history=garbage)
    assert selection.pack_ids.count("documents.read") == 1
    assert "email.write" not in selection.pack_ids and "not.a.pack" not in selection.pack_ids
    assert len(selection.pack_ids) == len(set(selection.pack_ids))


def test_same_pack_set_renders_the_same_tool_order_regardless_of_history_order(monkeypatch):
    """Prompt caching needs identical tool definitions AND ordering (provider rule)."""
    monkeypatch.setattr(capabilities, "stable_tool_prefix_enabled", lambda: True)
    forward = _select(_STICKY_QUERY, history=["documents.read", "machines.read"])
    backward = _select(_STICKY_QUERY, history=["machines.read", "documents.read"])
    assert forward.tool_ids == backward.tool_ids
    catalog_order = [tool_name(entry.tool) for entry in capability_catalog()]
    positions = [catalog_order.index(tool_id) for tool_id in forward.tool_ids]
    assert positions == sorted(positions)


def test_sticky_packs_are_evicted_before_anything_the_sentence_named(monkeypatch):
    monkeypatch.setattr(capabilities, "stable_tool_prefix_enabled", lambda: True)
    baseline = _select(_STICKY_QUERY)
    history = [
        "maintenance.read",
        "machines.read",
        "documents.read",
        "manuals.read",
        "evidence.read",
        "sources.read",
        "kanban.read",
    ]
    selection = _select(_STICKY_QUERY, history=history)
    assert len(selection.tool_ids) <= MAX_INITIAL_TOOLS
    assert all(pack_id in selection.pack_ids for pack_id in baseline.pack_ids)
    dropped = [pack_id for pack_id in history if pack_id not in selection.pack_ids]
    # At most six ride along, and whatever fell to the budget was sticky only.
    assert len([p for p in selection.pack_ids if p not in baseline.pack_ids]) <= 6
    assert all(pack_id not in baseline.pack_ids for pack_id in dropped)


def test_sticky_eviction_drops_the_packs_at_the_tail_of_the_catalog_first(monkeypatch):
    """Keeping the head of the canonical tool list cached is the whole point."""
    monkeypatch.setattr(capabilities, "stable_tool_prefix_enabled", lambda: True)
    baseline = _select(_STICKY_QUERY)
    # Six entries: exactly the sticky cap, so every one is admitted and any
    # absence below is a budget eviction rather than the cap.
    history = [
        "maintenance.read",
        "machines.read",
        "documents.read",
        "manuals.read",
        "evidence.read",
        "sources.read",
    ]
    selection = _select(_STICKY_QUERY, history=history)
    assert len(selection.tool_ids) <= MAX_INITIAL_TOOLS
    kept = [p for p in selection.pack_ids if p not in baseline.pack_ids]
    dropped = [p for p in history if p not in selection.pack_ids and p not in baseline.pack_ids]
    if dropped:
        position = capabilities._catalog_pack_position
        assert max(position(p) for p in kept) < min(position(p) for p in dropped)
    # The sticky packs' own scores never outrank a named pack.
    assert selection.pack_ids[0] == baseline.pack_ids[0]


@pytest.mark.parametrize(
    "query", ["How many ABC-123 are on hand?", "Show the bill of materials for Control Cabinet."]
)
def test_stock_and_bom_resolve_names_without_sql(query):
    selected = select_capabilities(query, profile=ALL_VIEW_PROFILE, authenticated=True)
    assert "search_parts" in selected.tool_ids
    assert "get_categories" in selected.tool_ids
    assert len(selected.tool_ids) <= MAX_INITIAL_TOOLS


def test_memory_proposals_require_native_permission_and_never_select_fact_execution():
    selected = select_capabilities(
        "forget all my memories", authenticated=True, profile=frozenset({("ai_memory", "write")})
    )
    assert selected.tool_ids == ("propose_memory_action",)
    denied = select_capabilities(
        "forget all my memories", authenticated=True, profile=ALL_VIEW_PROFILE
    )
    assert denied.tools == ()


# ---------------------------------------------------------------------------
# A1 routing-1: source-reported document facts must reach the real
# search_attachment_docs callable and its real SDK input model.
# ---------------------------------------------------------------------------

A1_EXACT_PROMPT = (
    "Read uploaded attachment 60, the Waterman SS-250 slide-gate datasheet "
    "attached to machine 174 at Cedar Creek Houston. In its self-adjusting "
    "seal-system description, what leakage limit does the manufacturer state, "
    "under which head conditions, and how many operating cycles were tested "
    "with what reported wear? Cite the exact PDF page and provide the source "
    "citation so I can open it. Treat these as claims in an unapproved family "
    "reference, not verified installed performance or operating instructions."
)

A1_ORDINARY_PROFILE = frozenset({("part", "view"), ("work_order", "view")})


def _a1_lit(monkeypatch):
    from types import SimpleNamespace as _NS

    from ai.core import config as ai_config

    monkeypatch.setattr(
        ai_config,
        "get_settings",
        lambda: _NS(
            single_site_policy_key="site-a",
            feature_attachment_rag_retrieval=True,
            feature_media_rag_retrieval=False,
        ),
    )
    capability_catalog.cache_clear()


def _a1_offered_callable(selection, tool_id: str):
    """The ACTUAL selected callable for tool_id, matched by identity."""
    catalog_tool = next(e.tool for e in capability_catalog() if e.tool_id == tool_id)
    return next((tool for tool in selection.tools if tool is catalog_tool), None)


def _a1_assert_sdk_strict_selector(selected) -> None:
    from agent_framework import ai_function as sdk_ai_function

    model = sdk_ai_function(selected).input_model
    parsed = model.model_validate({"query": "source fact", "attachment_id": 60})
    assert type(parsed.attachment_id) is int and parsed.attachment_id == 60
    assert model.model_validate({"query": "source fact"}).attachment_id is None
    assert (
        model.model_validate({"query": "source fact", "attachment_id": None}).attachment_id is None
    )
    schema = model.model_json_schema()["properties"]["attachment_id"]
    parts = schema.get("anyOf", [schema])
    assert {"type": "null"} in parts
    integer = next(part for part in parts if part.get("type") == "integer")
    assert integer.get("exclusiveMinimum") == 0 or integer.get("minimum", 0) >= 1


def test_a1_exact_prompt_offers_search_attachment_docs_via_real_sdk(monkeypatch):
    from ai.core.analysis.intent import EffectIntent, TaskIntent, classify_rules

    decision = classify_rules(A1_EXACT_PROMPT)
    assert decision is not None
    assert decision.intent is TaskIntent.MANUAL_FACT
    assert decision.effect is EffectIntent.READ_ONLY
    _a1_lit(monkeypatch)
    try:
        selection = select_capabilities(
            A1_EXACT_PROMPT,
            profile=A1_ORDINARY_PROFILE,
            authenticated=True,
            task_intent=decision.intent.value,
        )
        assert "search_attachment_docs" in selection.tool_ids
        selected = _a1_offered_callable(selection, "search_attachment_docs")
        assert selected is not None, "search_attachment_docs callable not actually offered"
        _a1_assert_sdk_strict_selector(selected)
    finally:
        capability_catalog.cache_clear()


def test_a1_known_good_source_question_offers_attachment_tool(monkeypatch):
    from ai.core.analysis.intent import classify_rules

    query = "What leakage does attachment 60 state?"
    decision = classify_rules(query)
    assert decision is not None
    _a1_lit(monkeypatch)
    try:
        selection = select_capabilities(
            query,
            profile=A1_ORDINARY_PROFILE,
            authenticated=True,
            task_intent=decision.intent.value,
        )
        assert decision.intent.value == "manual_fact"
        assert "search_attachment_docs" in selection.tool_ids
        selected = _a1_offered_callable(selection, "search_attachment_docs")
        assert selected is not None
        _a1_assert_sdk_strict_selector(selected)
    finally:
        capability_catalog.cache_clear()


def test_a1_denied_profiles_gain_no_attachment_tool(monkeypatch):
    from ai.core.analysis.intent import classify_rules

    decision = classify_rules(A1_EXACT_PROMPT)
    assert decision is not None
    _a1_lit(monkeypatch)
    try:
        empty = select_capabilities(
            A1_EXACT_PROMPT,
            profile=frozenset(),
            authenticated=True,
            task_intent=decision.intent.value,
        )
        assert "search_attachment_docs" not in empty.tool_ids
        unauth = select_capabilities(
            A1_EXACT_PROMPT,
            profile=A1_ORDINARY_PROFILE,
            authenticated=False,
            task_intent=decision.intent.value,
        )
        assert "search_attachment_docs" not in unauth.tool_ids
    finally:
        capability_catalog.cache_clear()


def test_a1_source_fact_variants_offer_attachment_tool(monkeypatch):
    from ai.core.analysis.intent import EffectIntent, TaskIntent, classify_rules

    for prompt in (
        "How many cycles does the uploaded datasheet report testing?",
        "Read attachment 61 and report the rated flow the datasheet gives",
        "Search attachment 62 for the operating hours the datasheet states",
    ):
        decision = classify_rules(prompt)
        assert decision is not None, prompt
        assert decision.intent is TaskIntent.MANUAL_FACT, prompt
        assert decision.effect is EffectIntent.READ_ONLY, prompt
        _a1_lit(monkeypatch)
        try:
            selection = select_capabilities(
                prompt,
                profile=A1_ORDINARY_PROFILE,
                authenticated=True,
                task_intent=decision.intent.value,
            )
            assert "search_attachment_docs" in selection.tool_ids, prompt
            selected = _a1_offered_callable(selection, "search_attachment_docs")
            assert selected is not None, prompt
            _a1_assert_sdk_strict_selector(selected)
        finally:
            capability_catalog.cache_clear()


@pytest.mark.parametrize(
    "prompt",
    [
        "Search uploaded attachment 60 and report the tested cycle count it states",
        "Read uploaded attachment 60 and report the tested cycle count it states",
        "Read uploaded attachment 61 and report the tested stroke count it lists",
    ],
)
def test_a3_noun_count_source_questions_offer_attachment_tool(monkeypatch, prompt):
    """Nominal noun-count source questions must offer the attachment tool."""
    from ai.core.analysis.intent import EffectIntent, TaskIntent, classify_rules

    decision = classify_rules(prompt)
    assert decision is not None
    assert decision.intent is TaskIntent.MANUAL_FACT
    assert decision.effect is EffectIntent.READ_ONLY
    _a1_lit(monkeypatch)
    try:
        selection = select_capabilities(
            prompt,
            profile=A1_ORDINARY_PROFILE,
            authenticated=True,
            task_intent=decision.intent.value,
        )
        assert not selection.requires_specialist
        assert "search_attachment_docs" in selection.tool_ids
        selected = _a1_offered_callable(selection, "search_attachment_docs")
        assert selected is not None
        _a1_assert_sdk_strict_selector(selected)
    finally:
        capability_catalog.cache_clear()


@pytest.mark.parametrize(
    "prompt",
    [
        "Search uploaded attachment 60 and report the tested cycle count it states",
        "Read uploaded attachment 60 and report the tested cycle count it states",
    ],
)
@pytest.mark.parametrize(
    ("authenticated", "profile"),
    [(True, frozenset()), (False, A1_ORDINARY_PROFILE)],
)
def test_a3_count_source_denials_are_preserved(monkeypatch, prompt, authenticated, profile):
    """Denied cohorts keep no attachment tool for nominal-count source questions."""
    from ai.core.analysis.intent import classify_rules

    decision = classify_rules(prompt)
    _a1_lit(monkeypatch)
    try:
        selection = select_capabilities(
            prompt,
            profile=profile,
            authenticated=authenticated,
            task_intent=decision.intent.value if decision else None,
        )
        assert "search_attachment_docs" not in selection.tool_ids
        assert _a1_offered_callable(selection, "search_attachment_docs") is None
    finally:
        capability_catalog.cache_clear()


@pytest.mark.parametrize(
    "prompt",
    [
        "Count stock in bin A-3",
        "Count it now",
        (
            "Read uploaded attachment 60 and report the tested cycle count it states "
            "then count stock in bin A-3"
        ),
        (
            "Read uploaded attachment 60 and report the tested cycle count it states "
            "then create a work order"
        ),
    ],
)
def test_a3_real_writes_remain_specialist(monkeypatch, prompt):
    """A nominal count must not hide a later count command or decisive write."""
    from ai.core.analysis.intent import classify_rules

    decision = classify_rules(prompt)
    _a1_lit(monkeypatch)
    try:
        selection = select_capabilities(
            prompt,
            profile=A1_ORDINARY_PROFILE,
            authenticated=True,
            task_intent=decision.intent.value if decision else None,
        )
        assert selection.requires_specialist
        assert selection.reason == "write_or_external_effect_requires_specialist"
        assert selection.tool_ids == ()
        assert _a1_offered_callable(selection, "search_attachment_docs") is None
    finally:
        capability_catalog.cache_clear()


def test_a1_fleet_aggregates_do_not_gain_the_attachment_tool(monkeypatch):
    from ai.core.analysis.intent import TaskIntent, classify_rules

    for prompt in (
        "How many pumps were repaired last month?",
        "How many teardowns occurred since the last manual revision?",
    ):
        decision = classify_rules(prompt)
        assert decision is not None, prompt
        assert decision.intent is TaskIntent.FLEET_AGGREGATE, prompt
        _a1_lit(monkeypatch)
        try:
            selection = select_capabilities(
                prompt,
                profile=A1_ORDINARY_PROFILE,
                authenticated=True,
                task_intent=decision.intent.value,
            )
            assert "search_attachment_docs" not in selection.tool_ids, prompt
            assert "documents.read" not in selection.pack_ids, prompt
        finally:
            capability_catalog.cache_clear()


def test_a1_read_adjacent_event_count_stays_an_aggregate(monkeypatch):
    """A Read-document-adjacent real event count keeps the aggregate intent.

    The documents.read pack here is carried ONLY by the pre-existing
    content-driven 'uploaded_document_content' broker rider (unchanged in
    this lane); the typed intent stays fleet_aggregate and the genuine
    aggregate packs are intact.
    """
    from ai.core.analysis.intent import TaskIntent, classify_rules

    prompt = "Read the uploaded manual and report how many pumps were repaired last month."
    decision = classify_rules(prompt)
    assert decision is not None
    assert decision.intent is TaskIntent.FLEET_AGGREGATE
    _a1_lit(monkeypatch)
    try:
        selection = select_capabilities(
            prompt,
            profile=A1_ORDINARY_PROFILE,
            authenticated=True,
            task_intent=decision.intent.value,
        )
        assert not selection.requires_specialist
        assert "analytics.read" in selection.pack_ids
        assert "maintenance.read" in selection.pack_ids
        assert "uploaded_document_content" in selection.signals
    finally:
        capability_catalog.cache_clear()


def test_a1_source_inventory_keeps_its_contract(monkeypatch):
    from ai.core.analysis.intent import TaskIntent, classify_rules

    prompt = "Which manuals do you have for the inverters?"
    decision = classify_rules(prompt)
    assert decision is not None
    assert decision.intent is TaskIntent.SOURCE_INVENTORY
    _a1_lit(monkeypatch)
    try:
        selection = select_capabilities(
            prompt,
            profile=A1_ORDINARY_PROFILE,
            authenticated=True,
            task_intent=decision.intent.value,
        )
        baseline = select_capabilities(
            prompt,
            profile=A1_ORDINARY_PROFILE,
            authenticated=True,
            task_intent="source_inventory",
        )
        assert selection.pack_ids[0] == "sources.read"
        assert selection.pack_ids == baseline.pack_ids
        assert selection.tool_ids == baseline.tool_ids
    finally:
        capability_catalog.cache_clear()


def test_a1_governed_effect_with_document_mention_stays_specialist(monkeypatch):
    from ai.core.analysis.intent import TaskIntent, classify_rules

    prompt = "Create a work order to replace the seal the manual describes."
    decision = classify_rules(prompt)
    assert decision is not None
    assert decision.intent is TaskIntent.GOVERNED_ACTION
    _a1_lit(monkeypatch)
    try:
        selection = select_capabilities(
            prompt,
            profile=A1_ORDINARY_PROFILE,
            authenticated=True,
            task_intent=decision.intent.value,
        )
        assert selection.requires_specialist
        assert "search_attachment_docs" not in selection.tool_ids
    finally:
        capability_catalog.cache_clear()


@pytest.mark.parametrize(
    "prompt",
    [
        "Read the manual then count stock in bin A-3",
        "Read the manual. Count stock in bin A-3",
        "Read the manual and count stock in bin A-3",
    ],
)
def test_a3r_clause_boundaries_keep_count_commands_specialist(monkeypatch, prompt):
    """A genuine count command must not inherit nominal protection across clauses."""
    from ai.core.analysis.intent import classify_rules

    decision = classify_rules(prompt)
    _a1_lit(monkeypatch)
    try:
        selection = select_capabilities(
            prompt,
            profile=A1_ORDINARY_PROFILE,
            authenticated=True,
            task_intent=decision.intent.value if decision else None,
        )
        assert selection.requires_specialist
        assert selection.reason == "write_or_external_effect_requires_specialist"
        assert selection.tool_ids == ()
        assert _a1_offered_callable(selection, "search_attachment_docs") is None
    finally:
        capability_catalog.cache_clear()


@pytest.mark.parametrize("repetitions", [64, 128])
def test_a3b_write_detection_keeps_slice_allocation_linear(monkeypatch, repetitions):
    """Per-match write detection must keep slice materialization linear in input."""
    from ai.core.tools import capabilities

    query = " ".join(["count"] * repetitions)
    copied = []

    class ObservedText(str):
        def __getitem__(self, key):
            value = super().__getitem__(key)
            if isinstance(key, slice):
                copied.append(len(value))
            return value

    original_mask = capabilities._mask_noun_phrases

    def observed_mask(text, clauses=None):
        return ObservedText(original_mask(text, clauses))

    monkeypatch.setattr(capabilities, "_mask_noun_phrases", observed_mask)
    assert capabilities._write_intent(query) == "count"
    assert sum(copied) <= 8 * len(query)


class TestA4RelativeQualifier:
    @pytest.mark.parametrize(
        "query",
        [
            "How many cycles does the datasheet that we uploaded report testing?",
            "How many cycles does the datasheet that I uploaded report testing?",
        ],
    )
    def test_relative_upload_source_fact_offers_attachment_tool(self, monkeypatch, query):
        from ai.core.analysis.intent import EffectIntent, TaskIntent, classify_rules

        decision = classify_rules(query)
        assert decision is not None
        assert decision.intent == TaskIntent.MANUAL_FACT
        assert decision.effect == EffectIntent.READ_ONLY
        _a1_lit(monkeypatch)
        try:
            selected = select_capabilities(
                query,
                profile=A1_ORDINARY_PROFILE,
                authenticated=True,
                task_intent=decision.intent.value,
            )
            assert "search_attachment_docs" in selected.tool_ids
            offered = _a1_offered_callable(selected, "search_attachment_docs")
            assert offered is not None
            _a1_assert_sdk_strict_selector(offered)
        finally:
            capability_catalog.cache_clear()

    @pytest.mark.parametrize(
        "query",
        [
            "How many cycles does the datasheet that we uploaded report testing?",
            "How many cycles does the datasheet that I uploaded report testing?",
        ],
    )
    @pytest.mark.parametrize(
        ("authenticated", "profile"),
        [
            (True, frozenset()),
            (False, A1_ORDINARY_PROFILE),
        ],
    )
    def test_relative_upload_denials_remain_closed(
        self, monkeypatch, query, authenticated, profile
    ):
        from ai.core.analysis.intent import classify_rules

        decision = classify_rules(query)
        assert decision is not None
        _a1_lit(monkeypatch)
        try:
            selected = select_capabilities(
                query,
                profile=profile,
                authenticated=authenticated,
                task_intent=decision.intent.value,
            )
            assert "search_attachment_docs" not in selected.tool_ids
            assert _a1_offered_callable(selected, "search_attachment_docs") is None
        finally:
            capability_catalog.cache_clear()

    @pytest.mark.parametrize(
        "query",
        [
            "Read the datasheet that we uploaded while I report how many pumps were repaired last month.",
            "Read the datasheet that I uploaded before you report how many teardowns occurred last month.",
        ],
    )
    def test_relative_upload_does_not_hide_independent_reporting_subject(self, query):
        from ai.core.analysis.intent import EffectIntent, TaskIntent, classify_rules

        decision = classify_rules(query)
        assert decision is not None
        assert decision.intent == TaskIntent.FLEET_AGGREGATE
        assert decision.effect == EffectIntent.READ_ONLY


class TestA5SourceRelativeGrammar:
    @pytest.mark.parametrize(
        "clause",
        [
            "that I uploaded",
            "that I have uploaded",
            "that I had uploaded",
            "that we uploaded",
            "that we have uploaded",
            "that we had uploaded",
            "that you uploaded",
            "that you have uploaded",
            "that you had uploaded",
            "which I uploaded",
            "which I have uploaded",
            "which I had uploaded",
            "which we uploaded",
            "which we have uploaded",
            "which we had uploaded",
            "which you uploaded",
            "which you have uploaded",
            "which you had uploaded",
        ],
    )
    def test_source_owned_relative_grammar(self, monkeypatch, clause):
        query = f"How many cycles does the datasheet {clause} report testing?"
        TestA4RelativeQualifier().test_relative_upload_source_fact_offers_attachment_tool(
            monkeypatch,
            query,
        )

    @pytest.mark.parametrize(
        "query",
        [
            "How many cycles does the datasheet that you uploaded report testing?",
            "How many cycles does the datasheet that I have uploaded report testing?",
            "How many cycles does the datasheet that we have uploaded report testing?",
            "How many cycles does the datasheet which I uploaded report testing?",
        ],
    )
    @pytest.mark.parametrize(
        ("authenticated", "profile"),
        [
            (True, frozenset()),
            (False, A1_ORDINARY_PROFILE),
        ],
    )
    def test_relative_grammar_denials(self, monkeypatch, query, authenticated, profile):
        TestA4RelativeQualifier().test_relative_upload_denials_remain_closed(
            monkeypatch,
            query,
            authenticated,
            profile,
        )

    @pytest.mark.parametrize(
        "query",
        [
            "Read the datasheet that we have uploaded while I report how many pumps were repaired last month.",
            "Read the datasheet which you have uploaded before we report how many teardowns occurred last month.",
            "Read the manual which I had uploaded AND report how many pumps were repaired last month.",
            "Read the manual that you had uploaded then report how many teardowns occurred last month.",
        ],
    )
    def test_relative_grammar_independent_subject(self, query):
        TestA4RelativeQualifier().test_relative_upload_does_not_hide_independent_reporting_subject(
            query,
        )


class TestA6SourceOwnership:
    @pytest.mark.parametrize(
        "query",
        [
            "How many documents were read last month?",
            "How many revisions were published since 2020?",
            "How many procedures were tested in the audit?",
        ],
    )
    def test_document_subject_event_counts(self, query):
        TestA4RelativeQualifier().test_relative_upload_does_not_hide_independent_reporting_subject(
            query
        )

    @pytest.mark.parametrize(
        "clause",
        [
            "which I uploaded",
            "which I have uploaded",
            "which I had uploaded",
            "which we uploaded",
            "which we have uploaded",
            "which we had uploaded",
            "which you uploaded",
            "which you have uploaded",
            "which you had uploaded",
        ],
    )
    def test_comma_source_owned_relative_grammar(self, monkeypatch, clause):
        query = f"How many cycles does the datasheet, {clause}, report testing?"
        TestA4RelativeQualifier().test_relative_upload_source_fact_offers_attachment_tool(
            monkeypatch, query
        )

    @pytest.mark.parametrize(
        "query",
        [
            "How many cycles does the datasheet, which we uploaded, report testing?",
            "How many cycles does the datasheet, which I have uploaded, report testing?",
        ],
    )
    @pytest.mark.parametrize(
        ("authenticated", "profile"), [(True, frozenset()), (False, A1_ORDINARY_PROFILE)]
    )
    def test_comma_grammar_denials(self, monkeypatch, query, authenticated, profile):
        TestA4RelativeQualifier().test_relative_upload_denials_remain_closed(
            monkeypatch, query, authenticated, profile
        )

    @pytest.mark.parametrize(
        "query",
        [
            "Read the datasheet, which we uploaded, while I report how many pumps were repaired last month.",
            "Read the datasheet, which you have uploaded, before we report how many teardowns occurred last month.",
            "Read the manual, which I had uploaded, AND report how many pumps were repaired last month.",
            "Read the manual, which you had uploaded, then report how many teardowns occurred last month.",
        ],
    )
    def test_comma_grammar_independent_subject(self, query):
        TestA4RelativeQualifier().test_relative_upload_does_not_hide_independent_reporting_subject(
            query
        )


class TestA6RVoiceOwnership:
    @pytest.mark.parametrize(
        "query",
        [
            "How many documents were previously read last month?",
            "How many documents were recently read last month?",
            "How many documents were actually read last month?",
            "How many documents were not read last month?",
            "How many revisions were previously published since 2020?",
            "How many revisions were recently published since 2020?",
            "How many revisions were actually published since 2020?",
            "How many revisions were not published since 2020?",
            "How many procedures were previously tested in the audit?",
            "How many procedures were recently tested in the audit?",
            "How many procedures were actually tested in the audit?",
            "How many procedures were not tested in the audit?",
        ],
    )
    def test_passive_event_modifiers(self, query):
        TestA6SourceOwnership().test_document_subject_event_counts(query)

    @pytest.mark.parametrize(
        "query",
        [
            "Read the datasheet and report how many cycles the manufacturer is reporting.",
            "Read the datasheet and report how many cycles the manufacturer was reporting.",
            "Read the datasheet and report how many cycles the manufacturer has been reporting.",
        ],
    )
    def test_active_progressive_source_fact(self, monkeypatch, query):
        TestA4RelativeQualifier().test_relative_upload_source_fact_offers_attachment_tool(
            monkeypatch, query
        )

    @pytest.mark.parametrize(
        "query",
        [
            "Read the datasheet and report how many cycles the manufacturer is actively reporting.",
            "Read the datasheet and report how many cycles the manufacturer was actively reporting.",
            "Read the datasheet and report how many cycles the manufacturer has been actively reporting.",
        ],
    )
    def test_active_modifier_source_control(self, monkeypatch, query):
        TestA4RelativeQualifier().test_relative_upload_source_fact_offers_attachment_tool(
            monkeypatch, query
        )

    @pytest.mark.parametrize(
        "query",
        [
            "Read the datasheet and report how many cycles the manufacturer is reporting.",
            "Read the datasheet and report how many cycles the manufacturer has been reporting.",
        ],
    )
    @pytest.mark.parametrize(
        ("authenticated", "profile"), [(True, frozenset()), (False, A1_ORDINARY_PROFILE)]
    )
    def test_progressive_source_denials(self, monkeypatch, query, authenticated, profile):
        TestA4RelativeQualifier().test_relative_upload_denials_remain_closed(
            monkeypatch, query, authenticated, profile
        )


class TestA6R3GeneralOwnership:
    @pytest.mark.parametrize(
        ("noun", "verb", "tail"),
        [
            ("documents", "read", "last month"),
            ("revisions", "published", "since 2020"),
            ("procedures", "tested", "in the audit"),
        ],
    )
    @pytest.mark.parametrize("prefix", ["How many ", "How many of the ", "What number of "])
    @pytest.mark.parametrize("modifier", ["never", "already", "often", "always", "just", "once"])
    def test_counted_source_passive_modifiers(self, noun, verb, tail, prefix, modifier):
        query = f"{prefix}{noun} were {modifier} {verb} {tail}?"
        TestA6SourceOwnership().test_document_subject_event_counts(query)

    @pytest.mark.parametrize(
        ("noun", "verb", "tail"),
        [
            ("documents", "read", "last month"),
            ("revisions", "published", "since 2020"),
            ("procedures", "tested", "in the audit"),
        ],
    )
    @pytest.mark.parametrize("auxiliary", ["weren't", "were seldom", "were also", "were not"])
    def test_counted_source_contraction_controls(self, noun, verb, tail, auxiliary):
        query = f"How many {noun} {auxiliary} {verb} {tail}?"
        TestA6SourceOwnership().test_document_subject_event_counts(query)

    @pytest.mark.parametrize(
        "query",
        [
            "Read the datasheet and report how many cycles the manufacturer is reporting.",
            "Read the datasheet and report how many cycles the manufacturer was reporting.",
            "Read the datasheet and report how many cycles the manufacturer has been reporting.",
            "Read the datasheet and report how many cycles the manufacturer is often reporting.",
            "Read the datasheet and report how many cycles the manufacturer was often reporting.",
            "Read the datasheet and report how many cycles the manufacturer has been often reporting.",
            "Read the datasheet and report how many cycles the manufacturer is able to report.",
        ],
    )
    def test_active_source_reporting_controls(self, monkeypatch, query):
        TestA4RelativeQualifier().test_relative_upload_source_fact_offers_attachment_tool(
            monkeypatch, query
        )

    @pytest.mark.parametrize(
        "query",
        [
            "Read the datasheet and report how many cycles the manufacturer is reporting.",
            "Read the datasheet and report how many cycles the manufacturer is able to report.",
        ],
    )
    @pytest.mark.parametrize(
        ("authenticated", "profile"), [(True, frozenset()), (False, A1_ORDINARY_PROFILE)]
    )
    def test_source_permission_denials(self, monkeypatch, query, authenticated, profile):
        TestA4RelativeQualifier().test_relative_upload_denials_remain_closed(
            monkeypatch, query, authenticated, profile
        )


class TestA6R4QuantityOwnership:
    @pytest.mark.parametrize("auxiliary", ["is", "was", "has been"])
    @pytest.mark.parametrize(
        "complement",
        [
            "confident it tested",
            "certain it tested",
            "confident it can report",
            "certain that it tested",
        ],
    )
    def test_nested_source_claims(self, monkeypatch, auxiliary, complement):
        query = f"Read the datasheet and report how many cycles the manufacturer {auxiliary} {complement}."
        TestA4RelativeQualifier().test_relative_upload_source_fact_offers_attachment_tool(
            monkeypatch, query
        )

    @pytest.mark.parametrize(
        ("noun", "verb", "tail"),
        [
            ("documents", "read", "last month"),
            ("revisions", "published", "since 2020"),
            ("procedures", "tested", "in the audit"),
        ],
    )
    @pytest.mark.parametrize("quantifier", ["How many ", "How many of these "])
    @pytest.mark.parametrize("head_modifier", ["archived ", "previously uploaded ", "new "])
    def test_modified_quantity_heads(self, noun, verb, tail, quantifier, head_modifier):
        query = f"{quantifier}{head_modifier}{noun} were already {verb} {tail}?"
        TestA6SourceOwnership().test_document_subject_event_counts(query)

    @pytest.mark.parametrize(
        "modifier", ["frequently", "ever", "not often", "hardly ever", "already", "seldom"]
    )
    def test_quantified_patient_preserved(self, modifier):
        query = f"How many archived documents were {modifier} read last month?"
        TestA6SourceOwnership().test_document_subject_event_counts(query)

    @pytest.mark.parametrize(
        "query",
        [
            "Read the datasheet and report how many cycles the manufacturer is confident it tested.",
            "Read the datasheet and report how many cycles the manufacturer is able to report.",
        ],
    )
    @pytest.mark.parametrize(
        ("authenticated", "profile"), [(True, frozenset()), (False, A1_ORDINARY_PROFILE)]
    )
    def test_nested_source_denials(self, monkeypatch, query, authenticated, profile):
        TestA4RelativeQualifier().test_relative_upload_denials_remain_closed(
            monkeypatch, query, authenticated, profile
        )


class TestA6R4RLocalOwnership:
    @pytest.mark.parametrize(
        "query",
        [
            "How many cycles does attachment 60 report testing?",
            "Read the datasheet and report how many cycles OEM reports testing.",
            "How many cycles can attachment 60 report?",
            "Read attachment 60 and report how many cycles OEM states it tested.",
        ],
    )
    def test_quantifier_does_not_own_independent_reporter(self, monkeypatch, query):
        TestA4RelativeQualifier().test_relative_upload_source_fact_offers_attachment_tool(
            monkeypatch, query
        )

    @pytest.mark.parametrize("repetitions", [64, 128])
    def test_quantity_head_observation_is_local(self, monkeypatch, repetitions):
        from ai.core.analysis import intent as intent_module

        pattern = intent_module._AGGREGATE
        spans = []
        sliced = []
        enumerated = [0]

        class ObservedText(str):
            def __getitem__(self, key):
                if isinstance(key, slice):
                    sliced.append(len(range(*key.indices(len(self)))))
                return super().__getitem__(key)

        class RegexObserver:
            def __getattr__(self, name):
                target = getattr(pattern, name)
                if name not in {"search", "finditer", "match", "fullmatch", "findall"}:
                    return target

                def observe(value, pos=0, endpos=None):
                    end = len(value) if endpos is None else min(len(value), endpos)
                    spans.append(max(0, end - pos))
                    result = target(value, pos, end)
                    if name == "finditer":

                        def counted():
                            for match in result:
                                enumerated[0] += 1
                                yield match

                        return counted()
                    if name == "findall":
                        enumerated[0] += len(result)
                    else:
                        enumerated[0] += int(result is not None)
                    return result

                return observe

        raw = "How many documents were read last month? " * repetitions
        assert intent_module._source_reported_fact(raw) is False
        query = ObservedText(raw)
        monkeypatch.setattr(intent_module, "_AGGREGATE", RegexObserver())
        assert intent_module._source_reported_fact(query) is False
        assert max(spans, default=0) <= 80
        assert max(sliced, default=0) <= 80
        assert sum(sliced) <= repetitions * 240
        assert enumerated[0] <= repetitions * 3


class TestM1NamedProposalOwnership:
    @pytest.mark.parametrize(
        "query", ["propose memory action", "Please propose memory action.", "PROPOSE MEMORY ACTION"]
    )
    @pytest.mark.parametrize(
        "profile", [frozenset(), ALL_VIEW_PROFILE, frozenset({("ai_memory", "write")})]
    )
    def test_named_memory_action_requires_specialist_without_tools(self, query, profile):
        selected = select_capabilities(query, profile=profile, authenticated=True)
        assert selected.requires_specialist is True
        assert selected.pack_ids == ()
        assert selected.tool_ids == ()
        assert selected.tools == ()

    @pytest.mark.parametrize(
        "query",
        [
            "Propose a maintenance plan.",
            "What does propose memory action do?",
            "Show the proposed memory action history.",
        ],
    )
    def test_read_and_advice_proposals_stay_non_specialist(self, query):
        selected = select_capabilities(query, profile=ALL_VIEW_PROFILE, authenticated=True)
        assert selected.requires_specialist is False

    @pytest.mark.parametrize(
        ("authenticated", "profile", "expected"),
        [
            (True, frozenset({("ai_memory", "write")}), ("propose_memory_action",)),
            (True, ALL_VIEW_PROFILE, ()),
            (False, frozenset({("ai_memory", "write")}), ()),
        ],
    )
    def test_native_memory_permission_and_proposal_boundary(self, authenticated, profile, expected):
        selected = select_capabilities(
            "forget all my memories", profile=profile, authenticated=authenticated
        )
        assert selected.tool_ids == expected
        assert tuple(tool_name(tool) for tool in selected.tools) == expected
        assert selected.requires_specialist is False


class TestA6R5NominalOwnership:
    @pytest.mark.parametrize("auxiliary", ["is", "was", "has been"])
    @pytest.mark.parametrize(
        "subject", ["the factory", "the laboratory", "the team", "its engineers"]
    )
    def test_nominal_embedded_source_claim(self, monkeypatch, auxiliary, subject):
        query = f"Read the datasheet and report how many cycles the manufacturer {auxiliary} confident {subject} tested."
        TestA4RelativeQualifier().test_relative_upload_source_fact_offers_attachment_tool(
            monkeypatch, query
        )


class TestA6R6ArticlelessOwnership:
    @pytest.mark.parametrize("source", ["OEM", "attachment 60", "the OEM", "the attachment 60"])
    @pytest.mark.parametrize("auxiliary", ["is", "was", "has been"])
    @pytest.mark.parametrize("subject", ["it", "the factory"])
    def test_articleless_and_determined_nested_reporters(
        self, monkeypatch, source, auxiliary, subject
    ):
        query = f"Read the datasheet and report how many cycles {source} {auxiliary} confident {subject} tested."
        TestA4RelativeQualifier().test_relative_upload_source_fact_offers_attachment_tool(
            monkeypatch, query
        )


class TestA6R9IndexedSourceOwnership:
    @pytest.mark.parametrize(
        ("query", "aggregate"),
        [
            pytest.param(
                "How many of the documents 1 through 10 were read last month?",
                True,
                id="indexed-patient-documents",
            ),
            pytest.param(
                "How many of these archived documents 1 through 10 were read last month?",
                True,
                id="indexed-modified-patient-documents",
            ),
            pytest.param(
                "How many of these archived documents numbered 1 through 10 were read last month?",
                True,
                id="explicitly-numbered-control-documents",
            ),
            pytest.param(
                "How many of these archived documents were read last month?",
                True,
                id="unindexed-control-documents",
            ),
            pytest.param(
                "How many of the revisions 1 through 10 were published since 2020?",
                True,
                id="indexed-patient-revisions",
            ),
            pytest.param(
                "How many of these new revisions 1 through 10 were published since 2020?",
                True,
                id="indexed-modified-patient-revisions",
            ),
            pytest.param(
                "How many of these new revisions numbered 1 through 10 were published since 2020?",
                True,
                id="explicitly-numbered-control-revisions",
            ),
            pytest.param(
                "How many of these new revisions were published since 2020?",
                True,
                id="unindexed-control-revisions",
            ),
            pytest.param(
                "How many of the procedures 1 through 10 were tested in the audit?",
                True,
                id="indexed-patient-procedures",
            ),
            pytest.param(
                "How many of these previously uploaded procedures 1 through 10 were tested in the audit?",
                True,
                id="indexed-modified-patient-procedures",
            ),
            pytest.param(
                "How many of these previously uploaded procedures numbered 1 through 10 were tested in the audit?",
                True,
                id="explicitly-numbered-control-procedures",
            ),
            pytest.param(
                "How many of these previously uploaded procedures were tested in the audit?",
                True,
                id="unindexed-control-procedures",
            ),
            pytest.param(
                "Read the datasheet and report how many cycles attachment 61 is confident the factory tested.",
                False,
                id="indexed-reporter-61",
            ),
            pytest.param(
                "Read the datasheet and report how many cycles attachment 75 is confident the factory tested.",
                False,
                id="indexed-reporter-75",
            ),
        ],
    )
    def test_complete_indexed_source_ownership(self, monkeypatch, query, aggregate):
        if aggregate:
            from ai.core.analysis.intent import classify_rules

            TestA6SourceOwnership().test_document_subject_event_counts(query)
            decision = classify_rules(query)
            assert decision is not None
            _a1_lit(monkeypatch)
            try:
                selected = select_capabilities(
                    query,
                    profile=frozenset({("part", "view"), ("work_order", "view")}),
                    authenticated=True,
                    task_intent=decision.intent.value,
                )
                assert not selected.requires_specialist
                assert "query_database" in selected.tool_ids
                assert _a1_offered_callable(selected, "query_database") is not None
            finally:
                capability_catalog.cache_clear()
        else:
            TestA4RelativeQualifier().test_relative_upload_source_fact_offers_attachment_tool(
                monkeypatch, query
            )


class TestA6R10SourcePhraseRoles:
    @pytest.mark.parametrize(
        ("query", "aggregate"),
        [
            pytest.param(
                "How many OEM documents were read last month?", True, id="prenominal-oem-plain"
            ),
            pytest.param(
                "How many of the OEM documents were read last month?",
                True,
                id="prenominal-oem-partitive",
            ),
            pytest.param(
                "How many archived OEM documents were read last month?",
                True,
                id="prenominal-oem-modified",
            ),
            pytest.param(
                "How many documents from OEM were read last month?", True, id="pp-oem-from-bare"
            ),
            pytest.param(
                "How many documents from the OEM were read last month?",
                True,
                id="pp-oem-from-determined",
            ),
            pytest.param(
                "How many documents in OEM were read last month?", True, id="pp-oem-in-bare"
            ),
            pytest.param(
                "How many documents in the OEM were read last month?",
                True,
                id="pp-oem-in-determined",
            ),
            pytest.param(
                "Read the datasheet and report how many cycles OEM is confident it tested.",
                False,
                id="reporter-oem-pronoun",
            ),
            pytest.param(
                "Read the datasheet and report how many cycles OEM is confident the factory tested.",
                False,
                id="reporter-oem-nominal",
            ),
            pytest.param(
                "How many manufacturer documents were read last month?",
                True,
                id="prenominal-manufacturer-plain",
            ),
            pytest.param(
                "How many of the manufacturer documents were read last month?",
                True,
                id="prenominal-manufacturer-partitive",
            ),
            pytest.param(
                "How many archived manufacturer documents were read last month?",
                True,
                id="prenominal-manufacturer-modified",
            ),
            pytest.param(
                "How many documents from manufacturer were read last month?",
                True,
                id="pp-manufacturer-from-bare",
            ),
            pytest.param(
                "How many documents from the manufacturer were read last month?",
                True,
                id="pp-manufacturer-from-determined",
            ),
            pytest.param(
                "How many documents in manufacturer were read last month?",
                True,
                id="pp-manufacturer-in-bare",
            ),
            pytest.param(
                "How many documents in the manufacturer were read last month?",
                True,
                id="pp-manufacturer-in-determined",
            ),
            pytest.param(
                "Read the datasheet and report how many cycles manufacturer is confident it tested.",
                False,
                id="reporter-manufacturer-pronoun",
            ),
            pytest.param(
                "Read the datasheet and report how many cycles manufacturer is confident the factory tested.",
                False,
                id="reporter-manufacturer-nominal",
            ),
            pytest.param(
                "How many vendor documents were read last month?",
                True,
                id="prenominal-vendor-plain",
            ),
            pytest.param(
                "How many of the vendor documents were read last month?",
                True,
                id="prenominal-vendor-partitive",
            ),
            pytest.param(
                "How many archived vendor documents were read last month?",
                True,
                id="prenominal-vendor-modified",
            ),
            pytest.param(
                "How many documents from vendor were read last month?",
                True,
                id="pp-vendor-from-bare",
            ),
            pytest.param(
                "How many documents from the vendor were read last month?",
                True,
                id="pp-vendor-from-determined",
            ),
            pytest.param(
                "How many documents in vendor were read last month?", True, id="pp-vendor-in-bare"
            ),
            pytest.param(
                "How many documents in the vendor were read last month?",
                True,
                id="pp-vendor-in-determined",
            ),
            pytest.param(
                "Read the datasheet and report how many cycles vendor is confident it tested.",
                False,
                id="reporter-vendor-pronoun",
            ),
            pytest.param(
                "Read the datasheet and report how many cycles vendor is confident the factory tested.",
                False,
                id="reporter-vendor-nominal",
            ),
            pytest.param(
                "How many documents from attachment 61 were read last month?",
                True,
                id="pp-indexed-from-bare",
            ),
            pytest.param(
                "How many documents from the attachment 61 were read last month?",
                True,
                id="pp-indexed-from-determined",
            ),
            pytest.param(
                "How many documents in attachment 61 were read last month?",
                True,
                id="pp-indexed-in-bare",
            ),
            pytest.param(
                "How many documents in the attachment 61 were read last month?",
                True,
                id="pp-indexed-in-determined",
            ),
        ],
    )
    def test_source_phrase_role(self, monkeypatch, query, aggregate):
        if aggregate:
            from ai.core.analysis.intent import classify_rules

            TestA6SourceOwnership().test_document_subject_event_counts(query)
            decision = classify_rules(query)
            assert decision is not None
            _a1_lit(monkeypatch)
            try:
                selected = select_capabilities(
                    query,
                    profile=frozenset({("part", "view"), ("work_order", "view")}),
                    authenticated=True,
                    task_intent=decision.intent.value,
                )
                assert not selected.requires_specialist
                assert "query_database" in selected.tool_ids
                assert _a1_offered_callable(selected, "query_database") is not None
            finally:
                capability_catalog.cache_clear()
        else:
            TestA4RelativeQualifier().test_relative_upload_source_fact_offers_attachment_tool(
                monkeypatch, query
            )


class TestA6R11VariableSourcePhrases:
    @pytest.mark.parametrize(
        ("noun", "verb", "tail"),
        [
            ("documents", "read", "last month"),
            ("revisions", "published", "since 2020"),
            ("procedures", "tested", "in the audit"),
        ],
    )
    @pytest.mark.parametrize("source", ["OEM", "manufacturer", "vendor"])
    @pytest.mark.parametrize("quantifier", ["How many ", "How many of the "])
    @pytest.mark.parametrize("modifier", ["technical ", "maintenance "])
    @pytest.mark.parametrize("outer", ["", "archived "])
    def test_source_before_modified_counted_head(
        self, monkeypatch, noun, verb, tail, source, quantifier, modifier, outer
    ):
        query = f"{quantifier}{outer}{source} {modifier}{noun} were {verb} {tail}?"
        TestA6R9IndexedSourceOwnership().test_complete_indexed_source_ownership(
            monkeypatch, query, True
        )

    @pytest.mark.parametrize(
        ("noun", "verb", "tail"),
        [
            ("documents", "read", "last month"),
            ("revisions", "published", "since 2020"),
            ("procedures", "tested", "in the audit"),
        ],
    )
    @pytest.mark.parametrize("source", ["OEM", "manufacturer", "attachment 61"])
    @pytest.mark.parametrize("preposition", ["from", "in", "of"])
    @pytest.mark.parametrize("qualifier", ["", "the ", "the original ", "the current equipment "])
    def test_prepositional_source_width(
        self, monkeypatch, noun, verb, tail, source, preposition, qualifier
    ):
        query = f"How many of the {noun} {preposition} {qualifier}{source} were {verb} {tail}?"
        TestA6R9IndexedSourceOwnership().test_complete_indexed_source_ownership(
            monkeypatch, query, True
        )

    @pytest.mark.parametrize("source", ["OEM", "manufacturer", "vendor"])
    @pytest.mark.parametrize("auxiliary", ["is", "was", "has been"])
    @pytest.mark.parametrize("subject", ["it", "the factory", "the manual"])
    @pytest.mark.parametrize("adverb", ["", "usually "])
    def test_reporter_complement_and_auxiliary(
        self, monkeypatch, source, auxiliary, subject, adverb
    ):
        verb = "reported" if subject == "the manual" else "tested"
        query = f"Read the datasheet and report how many cycles {source} {adverb}{auxiliary} confident {subject} {verb}."
        TestA4RelativeQualifier().test_relative_upload_source_fact_offers_attachment_tool(
            monkeypatch, query
        )


class TestB2CountHeadOwnership:
    @pytest.mark.parametrize(
        "prefix", ["Count", "Please count", "Read the manual then count", "Read the manual. Count"]
    )
    @pytest.mark.parametrize("determiner", ["these", "those"])
    @pytest.mark.parametrize("noun", ["records", "lists", "claims", "notes"])
    @pytest.mark.parametrize("tail", ["", " before the audit", " to verify the kit"])
    def test_imperative_count_keeps_specialist(self, monkeypatch, prefix, determiner, noun, tail):
        prompt = f"{prefix} {determiner} {noun}{tail}."
        test_a3_real_writes_remain_specialist(monkeypatch, prompt)

    @pytest.mark.parametrize("prefix", ["Read", "Search"])
    @pytest.mark.parametrize("noun", ["cycle", "stroke"])
    @pytest.mark.parametrize("modifier", ["", "tested "])
    @pytest.mark.parametrize("determiner", ["the ", ""])
    def test_clause_final_nominal_count_stays_readable(
        self, monkeypatch, prefix, noun, modifier, determiner
    ):
        query = f"{prefix} uploaded attachment 60 and report {determiner}{modifier}{noun} count."
        TestA4RelativeQualifier().test_relative_upload_source_fact_offers_attachment_tool(
            monkeypatch, query
        )

    @pytest.mark.parametrize("prefix", ["Read", "Search"])
    @pytest.mark.parametrize("noun", ["cycle", "stroke"])
    @pytest.mark.parametrize("relative", ["those records list", "these reports specify"])
    @pytest.mark.parametrize("determiner", ["the ", ""])
    def test_nominal_relative_count_stays_readable(
        self, monkeypatch, prefix, noun, relative, determiner
    ):
        from ai.core.analysis.intent import classify_rules
        from ai.core.tools import capabilities

        query = f"{prefix} uploaded attachment 60 and report {determiner}{noun} count {relative}."
        decision = classify_rules(query)
        _a1_lit(monkeypatch)
        try:
            selection = select_capabilities(
                query,
                profile=A1_ORDINARY_PROFILE,
                authenticated=True,
                task_intent=decision.intent.value if decision else None,
            )
            assert capabilities._write_intent(query) is None
            assert not selection.requires_specialist
            assert selection.tool_ids
        finally:
            capability_catalog.cache_clear()

    @pytest.mark.parametrize(
        "query",
        [
            "Read uploaded attachment 60 and report the tested cycle count.",
            "Search uploaded attachment 60 and report the stroke count.",
        ],
    )
    @pytest.mark.parametrize(
        ("authenticated", "profile"), [(True, frozenset()), (False, A1_ORDINARY_PROFILE)]
    )
    def test_nominal_count_denials_stay_closed(self, monkeypatch, query, authenticated, profile):
        TestA4RelativeQualifier().test_relative_upload_denials_remain_closed(
            monkeypatch, query, authenticated, profile
        )

    @pytest.mark.parametrize(
        "prompt",
        [
            "Read uploaded attachment 60 and report the tested cycle count it states then count those claims.",
            "Read uploaded attachment 60 and report the tested cycle count. Then count these records.",
            "Read uploaded attachment 60 and report tested cycle count it states then count those claims.",
            "Read uploaded attachment 60 and report tested cycle count. Then count these records.",
            "Count stock in bin A-3.",
        ],
    )
    def test_mixed_count_and_stock_controls(self, monkeypatch, prompt):
        test_a3_real_writes_remain_specialist(monkeypatch, prompt)


class TestB2RequestFormOwnership:
    @pytest.mark.parametrize("prefix", ["Can you", "Could you please", "Would you"])
    @pytest.mark.parametrize("determiner", ["these", "those"])
    @pytest.mark.parametrize("noun", ["records", "lists", "claims", "notes"])
    def test_request_keeps_specialist(self, monkeypatch, prefix, determiner, noun):
        test_a3_real_writes_remain_specialist(monkeypatch, f"{prefix} count {determiner} {noun}?")


class TestB2NominalQualifierOwnership:
    @pytest.mark.parametrize("prefix", ["Read", "Search"])
    @pytest.mark.parametrize("noun", ["cycle", "stroke"])
    @pytest.mark.parametrize(
        "qualifier", ["for this unit", "in the datasheet", "on physical page 2"]
    )
    def test_qualified_nominal_offers_source_tool(self, monkeypatch, prefix, noun, qualifier):
        query = f"{prefix} uploaded attachment 60 and report the tested {noun} count {qualifier}."
        TestA4RelativeQualifier().test_relative_upload_source_fact_offers_attachment_tool(
            monkeypatch, query
        )

    @pytest.mark.parametrize(
        "qualifier", ["for this unit", "in the datasheet", "on physical page 2"]
    )
    def test_imperative_count_remains_specialist(self, monkeypatch, qualifier):
        test_a3_real_writes_remain_specialist(monkeypatch, f"Count those records {qualifier}.")

    @pytest.mark.parametrize(
        "qualifier", ["for this unit", "in the datasheet", "on physical page 2"]
    )
    @pytest.mark.parametrize(
        "connector", [" then count those records", ". Then count these records"]
    )
    def test_qualified_nominal_preserves_later_count(self, monkeypatch, qualifier, connector):
        query = (
            f"Read uploaded attachment 60 and report the tested cycle count {qualifier}{connector}."
        )
        test_a3_real_writes_remain_specialist(monkeypatch, query)


class TestB2GOOwnershipReassessment:
    @pytest.mark.parametrize(
        "query",
        [
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count to verify the kit.",
                id="source-0",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count, and verify the kit.",
                id="source-1",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count.", id="source-2"
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count for this unit.",
                id="source-3",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count in the datasheet.",
                id="source-4",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count on physical page 2.",
                id="source-5",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count that the vendor is reporting.",
                id="source-6",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested stroke count for this unit.",
                id="source-7",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested stroke count in the datasheet.",
                id="source-8",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested stroke count on physical page 2.",
                id="source-9",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested stroke count to verify the kit.",
                id="source-10",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested stroke count, and verify the kit.",
                id="source-11",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested stroke count that the vendor is reporting.",
                id="source-12",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested cycle count for this unit.",
                id="source-13",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested cycle count in the datasheet.",
                id="source-14",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested cycle count on physical page 2.",
                id="source-15",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested cycle count to verify the kit.",
                id="source-16",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested cycle count, and verify the kit.",
                id="source-17",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested cycle count that the vendor is reporting.",
                id="source-18",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested stroke count for this unit.",
                id="source-19",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested stroke count in the datasheet.",
                id="source-20",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested stroke count on physical page 2.",
                id="source-21",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested stroke count to verify the kit.",
                id="source-22",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested stroke count, and verify the kit.",
                id="source-23",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested stroke count that the vendor is reporting.",
                id="source-24",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested cycle count for this unit.",
                id="source-25",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested cycle count in the datasheet.",
                id="source-26",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested cycle count on physical page 2.",
                id="source-27",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested cycle count to verify the kit.",
                id="source-28",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested cycle count, and verify the kit.",
                id="source-29",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested cycle count that the vendor is reporting.",
                id="source-30",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested stroke count for this unit.",
                id="source-31",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested stroke count in the datasheet.",
                id="source-32",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested stroke count on physical page 2.",
                id="source-33",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested stroke count to verify the kit.",
                id="source-34",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested stroke count, and verify the kit.",
                id="source-35",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested stroke count that the vendor is reporting.",
                id="source-36",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested cycle count for this unit.",
                id="source-37",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested cycle count in the datasheet.",
                id="source-38",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested cycle count on physical page 2.",
                id="source-39",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested cycle count to verify the kit.",
                id="source-40",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested cycle count, and verify the kit.",
                id="source-41",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested cycle count that the vendor is reporting.",
                id="source-42",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested stroke count for this unit.",
                id="source-43",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested stroke count in the datasheet.",
                id="source-44",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested stroke count on physical page 2.",
                id="source-45",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested stroke count to verify the kit.",
                id="source-46",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested stroke count, and verify the kit.",
                id="source-47",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested stroke count that the vendor is reporting.",
                id="source-48",
            ),
        ],
    )
    def test_nominal_source_exposes_real_callable(self, monkeypatch, query):
        from ai.core.analysis.intent import classify_rules

        decision = classify_rules(query)
        _a1_lit(monkeypatch)
        try:
            selected = select_capabilities(
                query,
                profile=A1_ORDINARY_PROFILE,
                authenticated=True,
                task_intent=decision.intent.value if decision else None,
            )
            assert not selected.requires_specialist
            assert "search_attachment_docs" in selected.tool_ids
            offered = _a1_offered_callable(selected, "search_attachment_docs")
            assert offered is not None
            _a1_assert_sdk_strict_selector(offered)
        finally:
            capability_catalog.cache_clear()

    @pytest.mark.parametrize(
        "query",
        [
            pytest.param("Now count those records.", id="request-0"),
            pytest.param("Just count those claims.", id="request-1"),
            pytest.param("First count these lists.", id="request-2"),
            pytest.param("Please also count those notes.", id="request-3"),
            pytest.param("Start counting those records.", id="request-4"),
            pytest.param("Would it be possible to count those records?", id="request-5"),
            pytest.param("Please quickly count damaged records.", id="request-6"),
            pytest.param("Read the manual then quickly count damaged records.", id="request-7"),
            pytest.param("Quickly count damaged records.", id="request-8"),
            pytest.param("Quickly count existing records.", id="request-9"),
            pytest.param("Please carefully count pending records.", id="request-10"),
            pytest.param("Read the manual and carefully count modified records.", id="request-11"),
            pytest.param("Now count these records?", id="request-12"),
            pytest.param("Now count these lists?", id="request-13"),
            pytest.param("Now count these claims?", id="request-14"),
            pytest.param("Now count these notes?", id="request-15"),
            pytest.param("Now count those records?", id="request-16"),
            pytest.param("Now count those lists?", id="request-17"),
            pytest.param("Now count those claims?", id="request-18"),
            pytest.param("Now count those notes?", id="request-19"),
            pytest.param("Just count these records?", id="request-20"),
            pytest.param("Just count these lists?", id="request-21"),
            pytest.param("Just count these claims?", id="request-22"),
            pytest.param("Just count these notes?", id="request-23"),
            pytest.param("Just count those records?", id="request-24"),
            pytest.param("Just count those lists?", id="request-25"),
            pytest.param("Just count those claims?", id="request-26"),
            pytest.param("Just count those notes?", id="request-27"),
            pytest.param("First count these records?", id="request-28"),
            pytest.param("First count these lists?", id="request-29"),
            pytest.param("First count these claims?", id="request-30"),
            pytest.param("First count these notes?", id="request-31"),
            pytest.param("First count those records?", id="request-32"),
            pytest.param("First count those lists?", id="request-33"),
            pytest.param("First count those claims?", id="request-34"),
            pytest.param("First count those notes?", id="request-35"),
            pytest.param("Please also count these records?", id="request-36"),
            pytest.param("Please also count these lists?", id="request-37"),
            pytest.param("Please also count these claims?", id="request-38"),
            pytest.param("Please also count these notes?", id="request-39"),
            pytest.param("Please also count those records?", id="request-40"),
            pytest.param("Please also count those lists?", id="request-41"),
            pytest.param("Please also count those claims?", id="request-42"),
            pytest.param("Please also count those notes?", id="request-43"),
            pytest.param("Can you carefully count these records?", id="request-44"),
            pytest.param("Can you carefully count these lists?", id="request-45"),
            pytest.param("Can you carefully count these claims?", id="request-46"),
            pytest.param("Can you carefully count these notes?", id="request-47"),
            pytest.param("Can you carefully count those records?", id="request-48"),
            pytest.param("Can you carefully count those lists?", id="request-49"),
            pytest.param("Can you carefully count those claims?", id="request-50"),
            pytest.param("Can you carefully count those notes?", id="request-51"),
            pytest.param("Could you please quickly count these records?", id="request-52"),
            pytest.param("Could you please quickly count these lists?", id="request-53"),
            pytest.param("Could you please quickly count these claims?", id="request-54"),
            pytest.param("Could you please quickly count these notes?", id="request-55"),
            pytest.param("Could you please quickly count those records?", id="request-56"),
            pytest.param("Could you please quickly count those lists?", id="request-57"),
            pytest.param("Could you please quickly count those claims?", id="request-58"),
            pytest.param("Could you please quickly count those notes?", id="request-59"),
            pytest.param("Read the manual then quickly count these records?", id="request-60"),
            pytest.param("Read the manual then quickly count these lists?", id="request-61"),
            pytest.param("Read the manual then quickly count these claims?", id="request-62"),
            pytest.param("Read the manual then quickly count these notes?", id="request-63"),
            pytest.param("Read the manual then quickly count those records?", id="request-64"),
            pytest.param("Read the manual then quickly count those lists?", id="request-65"),
            pytest.param("Read the manual then quickly count those claims?", id="request-66"),
            pytest.param("Read the manual then quickly count those notes?", id="request-67"),
            pytest.param("Read the manual and carefully count these records?", id="request-68"),
            pytest.param("Read the manual and carefully count these lists?", id="request-69"),
            pytest.param("Read the manual and carefully count these claims?", id="request-70"),
            pytest.param("Read the manual and carefully count these notes?", id="request-71"),
            pytest.param("Read the manual and carefully count those records?", id="request-72"),
            pytest.param("Read the manual and carefully count those lists?", id="request-73"),
            pytest.param("Read the manual and carefully count those claims?", id="request-74"),
            pytest.param("Read the manual and carefully count those notes?", id="request-75"),
            pytest.param("Start counting these records?", id="request-76"),
            pytest.param("Start counting these lists?", id="request-77"),
            pytest.param("Start counting these claims?", id="request-78"),
            pytest.param("Start counting these notes?", id="request-79"),
            pytest.param("Start counting those records?", id="request-80"),
            pytest.param("Start counting those lists?", id="request-81"),
            pytest.param("Start counting those claims?", id="request-82"),
            pytest.param("Start counting those notes?", id="request-83"),
            pytest.param("Begin by counting these records?", id="request-84"),
            pytest.param("Begin by counting these lists?", id="request-85"),
            pytest.param("Begin by counting these claims?", id="request-86"),
            pytest.param("Begin by counting these notes?", id="request-87"),
            pytest.param("Begin by counting those records?", id="request-88"),
            pytest.param("Begin by counting those lists?", id="request-89"),
            pytest.param("Begin by counting those claims?", id="request-90"),
            pytest.param("Begin by counting those notes?", id="request-91"),
            pytest.param("Would it be possible to count these records?", id="request-92"),
            pytest.param("Would it be possible to count these lists?", id="request-93"),
            pytest.param("Would it be possible to count these claims?", id="request-94"),
            pytest.param("Would it be possible to count these notes?", id="request-95"),
            pytest.param("Would it be possible to count those lists?", id="request-96"),
            pytest.param("Would it be possible to count those claims?", id="request-97"),
            pytest.param("Would it be possible to count those notes?", id="request-98"),
            pytest.param("Quickly count damaged notes.", id="request-99"),
            pytest.param("Quickly count pending records.", id="request-100"),
            pytest.param("Quickly count pending notes.", id="request-101"),
            pytest.param("Please carefully count damaged records.", id="request-102"),
            pytest.param("Please carefully count damaged notes.", id="request-103"),
            pytest.param("Please carefully count pending notes.", id="request-104"),
            pytest.param("Can you quickly count damaged records.", id="request-105"),
            pytest.param("Can you quickly count damaged notes.", id="request-106"),
            pytest.param("Can you quickly count pending records.", id="request-107"),
            pytest.param("Can you quickly count pending notes.", id="request-108"),
            pytest.param("Read the manual then quickly count damaged notes.", id="request-109"),
            pytest.param("Read the manual then quickly count pending records.", id="request-110"),
            pytest.param("Read the manual then quickly count pending notes.", id="request-111"),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count for this unit then quickly count damaged records.",
                id="request-112",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count for this unit. Could you please count those notes.",
                id="request-113",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count to verify the kit then quickly count damaged records.",
                id="request-114",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count to verify the kit. Could you please count those notes.",
                id="request-115",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count, and verify the kit then quickly count damaged records.",
                id="request-116",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count, and verify the kit. Could you please count those notes.",
                id="request-117",
            ),
        ],
    )
    def test_actual_count_request_keeps_specialist(self, monkeypatch, query):
        test_a3_real_writes_remain_specialist(monkeypatch, query)

    @pytest.mark.parametrize(
        "query",
        [
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count to verify the kit.",
                id="source-0",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count, and verify the kit.",
                id="source-1",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count.", id="source-2"
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count for this unit.",
                id="source-3",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count in the datasheet.",
                id="source-4",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count on physical page 2.",
                id="source-5",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested cycle count that the vendor is reporting.",
                id="source-6",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested stroke count for this unit.",
                id="source-7",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested stroke count in the datasheet.",
                id="source-8",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested stroke count on physical page 2.",
                id="source-9",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested stroke count to verify the kit.",
                id="source-10",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested stroke count, and verify the kit.",
                id="source-11",
            ),
            pytest.param(
                "Read uploaded attachment 60 and report the tested stroke count that the vendor is reporting.",
                id="source-12",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested cycle count for this unit.",
                id="source-13",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested cycle count in the datasheet.",
                id="source-14",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested cycle count on physical page 2.",
                id="source-15",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested cycle count to verify the kit.",
                id="source-16",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested cycle count, and verify the kit.",
                id="source-17",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested cycle count that the vendor is reporting.",
                id="source-18",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested stroke count for this unit.",
                id="source-19",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested stroke count in the datasheet.",
                id="source-20",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested stroke count on physical page 2.",
                id="source-21",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested stroke count to verify the kit.",
                id="source-22",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested stroke count, and verify the kit.",
                id="source-23",
            ),
            pytest.param(
                "Search uploaded attachment 60 and report the tested stroke count that the vendor is reporting.",
                id="source-24",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested cycle count for this unit.",
                id="source-25",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested cycle count in the datasheet.",
                id="source-26",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested cycle count on physical page 2.",
                id="source-27",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested cycle count to verify the kit.",
                id="source-28",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested cycle count, and verify the kit.",
                id="source-29",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested cycle count that the vendor is reporting.",
                id="source-30",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested stroke count for this unit.",
                id="source-31",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested stroke count in the datasheet.",
                id="source-32",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested stroke count on physical page 2.",
                id="source-33",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested stroke count to verify the kit.",
                id="source-34",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested stroke count, and verify the kit.",
                id="source-35",
            ),
            pytest.param(
                "Can you read uploaded attachment 60 and report the tested stroke count that the vendor is reporting.",
                id="source-36",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested cycle count for this unit.",
                id="source-37",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested cycle count in the datasheet.",
                id="source-38",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested cycle count on physical page 2.",
                id="source-39",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested cycle count to verify the kit.",
                id="source-40",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested cycle count, and verify the kit.",
                id="source-41",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested cycle count that the vendor is reporting.",
                id="source-42",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested stroke count for this unit.",
                id="source-43",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested stroke count in the datasheet.",
                id="source-44",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested stroke count on physical page 2.",
                id="source-45",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested stroke count to verify the kit.",
                id="source-46",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested stroke count, and verify the kit.",
                id="source-47",
            ),
            pytest.param(
                "Could you please read uploaded attachment 60 and report the tested stroke count that the vendor is reporting.",
                id="source-48",
            ),
        ],
    )
    @pytest.mark.parametrize(
        ("authenticated", "profile"), [(True, frozenset()), (False, A1_ORDINARY_PROFILE)]
    )
    def test_nominal_source_denials_remain_closed(self, monkeypatch, query, authenticated, profile):
        from ai.core.analysis.intent import classify_rules

        decision = classify_rules(query)
        _a1_lit(monkeypatch)
        try:
            selected = select_capabilities(
                query,
                profile=profile,
                authenticated=authenticated,
                task_intent=decision.intent.value if decision else None,
            )
            assert "search_attachment_docs" not in selected.tool_ids
            assert _a1_offered_callable(selected, "search_attachment_docs") is None
        finally:
            capability_catalog.cache_clear()

    @pytest.mark.parametrize(
        "query",
        [
            pytest.param("How many documents were read last month?", id="event-0"),
            pytest.param("How many revisions were published since 2020?", id="event-1"),
            pytest.param("How many procedures were tested in the audit?", id="event-2"),
        ],
    )
    def test_document_event_aggregation_preserved(self, monkeypatch, query):
        from ai.core.analysis.intent import EffectIntent, TaskIntent, classify_rules

        decision = classify_rules(query)
        assert decision is not None
        assert decision.intent is TaskIntent.FLEET_AGGREGATE
        assert decision.effect is EffectIntent.READ_ONLY
        _a1_lit(monkeypatch)
        try:
            selected = select_capabilities(
                query,
                profile=A1_ORDINARY_PROFILE,
                authenticated=True,
                task_intent=decision.intent.value,
            )
            assert not selected.requires_specialist
            assert "search_attachment_docs" not in selected.tool_ids
        finally:
            capability_catalog.cache_clear()


class TestFinalSubordinateSourceOwnership:
    """Public intent/selection/SDK/grant boundary, not a helper-only property."""

    @pytest.mark.parametrize(
        ("query", "expected_intent"),
        [
            pytest.param(
                "How many pumps were repaired after the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-event-0-0-0",
            ),
            pytest.param(
                "How many pumps were repaired after the manual reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-0-0-1",
            ),
            pytest.param(
                "How many pumps were repaired after the datasheet noted the backlog?",
                "fleet_aggregate",
                id="s1-event-0-1-0",
            ),
            pytest.param(
                "How many pumps were repaired after the datasheet reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-0-1-1",
            ),
            pytest.param(
                "How many pumps were repaired after the procedure noted the backlog?",
                "fleet_aggregate",
                id="s1-event-0-2-0",
            ),
            pytest.param(
                "How many pumps were repaired after the procedure reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-0-2-1",
            ),
            pytest.param(
                "How many pumps were repaired under unusual operating conditions under unusual operating conditions under unusual operating conditions under unusual operating conditions after the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-long-event-0",
            ),
            pytest.param(
                "How many cycles does the manual report testing after the audit?",
                "manual_fact",
                id="s1-tail-source-0-0",
            ),
            pytest.param(
                "How many cycles does the datasheet report testing after the audit?",
                "manual_fact",
                id="s1-tail-source-0-1",
            ),
            pytest.param(
                "How many cycles does the procedure report testing after the audit?",
                "manual_fact",
                id="s1-tail-source-0-2",
            ),
            pytest.param(
                "How many pumps were repaired before the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-event-1-0-0",
            ),
            pytest.param(
                "How many pumps were repaired before the manual reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-1-0-1",
            ),
            pytest.param(
                "How many pumps were repaired before the datasheet noted the backlog?",
                "fleet_aggregate",
                id="s1-event-1-1-0",
            ),
            pytest.param(
                "How many pumps were repaired before the datasheet reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-1-1-1",
            ),
            pytest.param(
                "How many pumps were repaired before the procedure noted the backlog?",
                "fleet_aggregate",
                id="s1-event-1-2-0",
            ),
            pytest.param(
                "How many pumps were repaired before the procedure reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-1-2-1",
            ),
            pytest.param(
                "How many pumps were repaired under unusual operating conditions under unusual operating conditions under unusual operating conditions under unusual operating conditions before the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-long-event-1",
            ),
            pytest.param(
                "How many cycles does the manual report testing before the audit?",
                "manual_fact",
                id="s1-tail-source-1-0",
            ),
            pytest.param(
                "How many cycles does the datasheet report testing before the audit?",
                "manual_fact",
                id="s1-tail-source-1-1",
            ),
            pytest.param(
                "How many cycles does the procedure report testing before the audit?",
                "manual_fact",
                id="s1-tail-source-1-2",
            ),
            pytest.param(
                "How many pumps were repaired when the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-event-2-0-0",
            ),
            pytest.param(
                "How many pumps were repaired when the manual reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-2-0-1",
            ),
            pytest.param(
                "How many pumps were repaired when the datasheet noted the backlog?",
                "fleet_aggregate",
                id="s1-event-2-1-0",
            ),
            pytest.param(
                "How many pumps were repaired when the datasheet reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-2-1-1",
            ),
            pytest.param(
                "How many pumps were repaired when the procedure noted the backlog?",
                "fleet_aggregate",
                id="s1-event-2-2-0",
            ),
            pytest.param(
                "How many pumps were repaired when the procedure reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-2-2-1",
            ),
            pytest.param(
                "How many pumps were repaired under unusual operating conditions under unusual operating conditions under unusual operating conditions under unusual operating conditions when the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-long-event-2",
            ),
            pytest.param(
                "How many cycles does the manual report testing when the audit?",
                "manual_fact",
                id="s1-tail-source-2-0",
            ),
            pytest.param(
                "How many cycles does the datasheet report testing when the audit?",
                "manual_fact",
                id="s1-tail-source-2-1",
            ),
            pytest.param(
                "How many cycles does the procedure report testing when the audit?",
                "manual_fact",
                id="s1-tail-source-2-2",
            ),
            pytest.param(
                "How many pumps were repaired while the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-event-3-0-0",
            ),
            pytest.param(
                "How many pumps were repaired while the manual reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-3-0-1",
            ),
            pytest.param(
                "How many pumps were repaired while the datasheet noted the backlog?",
                "fleet_aggregate",
                id="s1-event-3-1-0",
            ),
            pytest.param(
                "How many pumps were repaired while the datasheet reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-3-1-1",
            ),
            pytest.param(
                "How many pumps were repaired while the procedure noted the backlog?",
                "fleet_aggregate",
                id="s1-event-3-2-0",
            ),
            pytest.param(
                "How many pumps were repaired while the procedure reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-3-2-1",
            ),
            pytest.param(
                "How many pumps were repaired under unusual operating conditions under unusual operating conditions under unusual operating conditions under unusual operating conditions while the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-long-event-3",
            ),
            pytest.param(
                "How many cycles does the manual report testing while the audit?",
                "manual_fact",
                id="s1-tail-source-3-0",
            ),
            pytest.param(
                "How many cycles does the datasheet report testing while the audit?",
                "manual_fact",
                id="s1-tail-source-3-1",
            ),
            pytest.param(
                "How many cycles does the procedure report testing while the audit?",
                "manual_fact",
                id="s1-tail-source-3-2",
            ),
            pytest.param(
                "How many pumps were repaired once the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-event-4-0-0",
            ),
            pytest.param(
                "How many pumps were repaired once the manual reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-4-0-1",
            ),
            pytest.param(
                "How many pumps were repaired once the datasheet noted the backlog?",
                "fleet_aggregate",
                id="s1-event-4-1-0",
            ),
            pytest.param(
                "How many pumps were repaired once the datasheet reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-4-1-1",
            ),
            pytest.param(
                "How many pumps were repaired once the procedure noted the backlog?",
                "fleet_aggregate",
                id="s1-event-4-2-0",
            ),
            pytest.param(
                "How many pumps were repaired once the procedure reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-4-2-1",
            ),
            pytest.param(
                "How many pumps were repaired under unusual operating conditions under unusual operating conditions under unusual operating conditions under unusual operating conditions once the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-long-event-4",
            ),
            pytest.param(
                "How many cycles does the manual report testing once the audit?",
                "manual_fact",
                id="s1-tail-source-4-0",
            ),
            pytest.param(
                "How many cycles does the datasheet report testing once the audit?",
                "manual_fact",
                id="s1-tail-source-4-1",
            ),
            pytest.param(
                "How many cycles does the procedure report testing once the audit?",
                "manual_fact",
                id="s1-tail-source-4-2",
            ),
            pytest.param(
                "How many pumps were repaired as the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-event-5-0-0",
            ),
            pytest.param(
                "How many pumps were repaired as the manual reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-5-0-1",
            ),
            pytest.param(
                "How many pumps were repaired as the datasheet noted the backlog?",
                "fleet_aggregate",
                id="s1-event-5-1-0",
            ),
            pytest.param(
                "How many pumps were repaired as the datasheet reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-5-1-1",
            ),
            pytest.param(
                "How many pumps were repaired as the procedure noted the backlog?",
                "fleet_aggregate",
                id="s1-event-5-2-0",
            ),
            pytest.param(
                "How many pumps were repaired as the procedure reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-5-2-1",
            ),
            pytest.param(
                "How many pumps were repaired under unusual operating conditions under unusual operating conditions under unusual operating conditions under unusual operating conditions as the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-long-event-5",
            ),
            pytest.param(
                "How many cycles does the manual report testing as the audit?",
                "manual_fact",
                id="s1-tail-source-5-0",
            ),
            pytest.param(
                "How many cycles does the datasheet report testing as the audit?",
                "manual_fact",
                id="s1-tail-source-5-1",
            ),
            pytest.param(
                "How many cycles does the procedure report testing as the audit?",
                "manual_fact",
                id="s1-tail-source-5-2",
            ),
            pytest.param(
                "How many pumps were repaired since the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-event-6-0-0",
            ),
            pytest.param(
                "How many pumps were repaired since the manual reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-6-0-1",
            ),
            pytest.param(
                "How many pumps were repaired since the datasheet noted the backlog?",
                "fleet_aggregate",
                id="s1-event-6-1-0",
            ),
            pytest.param(
                "How many pumps were repaired since the datasheet reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-6-1-1",
            ),
            pytest.param(
                "How many pumps were repaired since the procedure noted the backlog?",
                "fleet_aggregate",
                id="s1-event-6-2-0",
            ),
            pytest.param(
                "How many pumps were repaired since the procedure reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-6-2-1",
            ),
            pytest.param(
                "How many pumps were repaired under unusual operating conditions under unusual operating conditions under unusual operating conditions under unusual operating conditions since the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-long-event-6",
            ),
            pytest.param(
                "How many cycles does the manual report testing since the audit?",
                "manual_fact",
                id="s1-tail-source-6-0",
            ),
            pytest.param(
                "How many cycles does the datasheet report testing since the audit?",
                "manual_fact",
                id="s1-tail-source-6-1",
            ),
            pytest.param(
                "How many cycles does the procedure report testing since the audit?",
                "manual_fact",
                id="s1-tail-source-6-2",
            ),
            pytest.param(
                "How many pumps were repaired until the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-event-7-0-0",
            ),
            pytest.param(
                "How many pumps were repaired until the manual reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-7-0-1",
            ),
            pytest.param(
                "How many pumps were repaired until the datasheet noted the backlog?",
                "fleet_aggregate",
                id="s1-event-7-1-0",
            ),
            pytest.param(
                "How many pumps were repaired until the datasheet reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-7-1-1",
            ),
            pytest.param(
                "How many pumps were repaired until the procedure noted the backlog?",
                "fleet_aggregate",
                id="s1-event-7-2-0",
            ),
            pytest.param(
                "How many pumps were repaired until the procedure reported the tested cycle count?",
                "fleet_aggregate",
                id="s1-event-7-2-1",
            ),
            pytest.param(
                "How many pumps were repaired under unusual operating conditions under unusual operating conditions under unusual operating conditions under unusual operating conditions until the manual noted the backlog?",
                "fleet_aggregate",
                id="s1-long-event-7",
            ),
            pytest.param(
                "How many cycles does the manual report testing until the audit?",
                "manual_fact",
                id="s1-tail-source-7-0",
            ),
            pytest.param(
                "How many cycles does the datasheet report testing until the audit?",
                "manual_fact",
                id="s1-tail-source-7-1",
            ),
            pytest.param(
                "How many cycles does the procedure report testing until the audit?",
                "manual_fact",
                id="s1-tail-source-7-2",
            ),
            pytest.param(
                "Before the audit, how many cycles does the manual report testing?",
                "manual_fact",
                id="s1-prefix-source-0",
            ),
            pytest.param(
                "How many cycles does the manual that we uploaded report testing?",
                "manual_fact",
                id="s1-relative-source-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the datasheet report testing?",
                "manual_fact",
                id="s1-prefix-source-1",
            ),
            pytest.param(
                "How many cycles does the datasheet that we uploaded report testing?",
                "manual_fact",
                id="s1-relative-source-1",
            ),
            pytest.param(
                "Before the audit, how many cycles does the procedure report testing?",
                "manual_fact",
                id="s1-prefix-source-2",
            ),
            pytest.param(
                "How many cycles does the procedure that we uploaded report testing?",
                "manual_fact",
                id="s1-relative-source-2",
            ),
            pytest.param(
                "How many archived documents were not read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-0-0",
            ),
            pytest.param(
                "How many archived procedures were not read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-0-1",
            ),
            pytest.param(
                "How many archived revisions were not read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-0-2",
            ),
            pytest.param(
                "How many archived documents were once read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-1-0",
            ),
            pytest.param(
                "How many archived procedures were once read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-1-1",
            ),
            pytest.param(
                "How many archived revisions were once read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-1-2",
            ),
            pytest.param(
                "How many archived documents were hardly ever read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-2-0",
            ),
            pytest.param(
                "How many archived procedures were hardly ever read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-2-1",
            ),
            pytest.param(
                "How many archived revisions were hardly ever read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-2-2",
            ),
            pytest.param(
                "How many archived documents were actually read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-3-0",
            ),
            pytest.param(
                "How many archived procedures were actually read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-3-1",
            ),
            pytest.param(
                "How many archived revisions were actually read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-3-2",
            ),
            pytest.param(
                "How many archived documents were often read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-4-0",
            ),
            pytest.param(
                "How many archived procedures were often read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-4-1",
            ),
            pytest.param(
                "How many archived revisions were often read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-4-2",
            ),
            pytest.param(
                "How many archived documents were recently read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-5-0",
            ),
            pytest.param(
                "How many archived procedures were recently read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-5-1",
            ),
            pytest.param(
                "How many archived revisions were recently read last month?",
                "fleet_aggregate",
                id="s1-public-event-variant-5-2",
            ),
            pytest.param(
                "How many pumps were repaired after the audit was completed?",
                "fleet_aggregate",
                id="s1-event-no-source-0",
            ),
            pytest.param(
                "How many pumps were repaired before the audit was completed?",
                "fleet_aggregate",
                id="s1-event-no-source-1",
            ),
            pytest.param(
                "How many pumps were repaired when the audit was completed?",
                "fleet_aggregate",
                id="s1-event-no-source-2",
            ),
            pytest.param(
                "How many pumps were repaired while the audit was completed?",
                "fleet_aggregate",
                id="s1-event-no-source-3",
            ),
            pytest.param(
                "How many pumps were repaired once the audit was completed?",
                "fleet_aggregate",
                id="s1-event-no-source-4",
            ),
            pytest.param(
                "How many pumps were repaired as the audit was completed?",
                "fleet_aggregate",
                id="s1-event-no-source-5",
            ),
            pytest.param(
                "How many pumps were repaired since the audit was completed?",
                "fleet_aggregate",
                id="s1-event-no-source-6",
            ),
            pytest.param(
                "How many pumps were repaired until the audit was completed?",
                "fleet_aggregate",
                id="s1-event-no-source-7",
            ),
        ],
    )
    def test_subordinate_ownership_public_boundary(self, monkeypatch, query, expected_intent):
        from agent_framework import ai_function as sdk_ai_function
        from ai.core.analysis.intent import classify_rules
        from pydantic import ValidationError

        decision = classify_rules(query)
        _a1_lit(monkeypatch)
        try:
            selected = select_capabilities(
                query,
                profile=A1_ORDINARY_PROFILE,
                authenticated=True,
                task_intent=decision.intent.value if decision else None,
            )
            document_tool = _a1_offered_callable(selected, "search_attachment_docs")
            records_tool = _a1_offered_callable(selected, "query_database")
            if expected_intent == "manual_fact":
                assert document_tool is not None
                assert "search_attachment_docs" in selected.tool_ids
                _a1_assert_sdk_strict_selector(document_tool)
                model = sdk_ai_function(document_tool).input_model
                for bad in (True, 0, -1, 60.0, "60"):
                    with pytest.raises(ValidationError):
                        model.model_validate({"query": "source fact", "attachment_id": bad})
            else:
                assert document_tool is None
                assert "search_attachment_docs" not in selected.tool_ids
                assert records_tool is not None
                assert "query_database" in selected.tool_ids
            assert decision is not None and decision.intent.value == expected_intent
            assert decision.effect.value == "read_only"
            assert not selected.requires_specialist
            for authenticated, profile in ((True, frozenset()), (False, A1_ORDINARY_PROFILE)):
                denied = select_capabilities(
                    query,
                    profile=profile,
                    authenticated=authenticated,
                    task_intent=decision.intent.value,
                )
                assert "search_attachment_docs" not in denied.tool_ids
                assert "query_database" not in denied.tool_ids
                assert _a1_offered_callable(denied, "search_attachment_docs") is None
                assert _a1_offered_callable(denied, "query_database") is None
        finally:
            capability_catalog.cache_clear()


class TestFinalCompoundSourceOwnership:
    """Hyphenated source words are not subordinate-event ownership."""

    @pytest.mark.parametrize(
        ("query", "expected_intent"),
        [
            pytest.param(
                "How many cycles does the as-built manual report testing?",
                "manual_fact",
                id="h1-compound-0-0-0-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the as-built manual report testing?",
                "manual_fact",
                id="h1-compound-0-0-0-1",
            ),
            pytest.param(
                "How many cycles does the as-built datasheet report testing?",
                "manual_fact",
                id="h1-compound-0-0-1-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the as-built datasheet report testing?",
                "manual_fact",
                id="h1-compound-0-0-1-1",
            ),
            pytest.param(
                "How many cycles does the as\u2010built manual report testing?",
                "manual_fact",
                id="h1-compound-0-1-0-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the as\u2010built manual report testing?",
                "manual_fact",
                id="h1-compound-0-1-0-1",
            ),
            pytest.param(
                "How many cycles does the as\u2010built datasheet report testing?",
                "manual_fact",
                id="h1-compound-0-1-1-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the as\u2010built datasheet report testing?",
                "manual_fact",
                id="h1-compound-0-1-1-1",
            ),
            pytest.param(
                "How many cycles does the as\u2011built manual report testing?",
                "manual_fact",
                id="h1-compound-0-2-0-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the as\u2011built manual report testing?",
                "manual_fact",
                id="h1-compound-0-2-0-1",
            ),
            pytest.param(
                "How many cycles does the as\u2011built datasheet report testing?",
                "manual_fact",
                id="h1-compound-0-2-1-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the as\u2011built datasheet report testing?",
                "manual_fact",
                id="h1-compound-0-2-1-1",
            ),
            pytest.param(
                "How many cycles does the once-only manual report testing?",
                "manual_fact",
                id="h1-compound-1-0-0-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the once-only manual report testing?",
                "manual_fact",
                id="h1-compound-1-0-0-1",
            ),
            pytest.param(
                "How many cycles does the once-only datasheet report testing?",
                "manual_fact",
                id="h1-compound-1-0-1-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the once-only datasheet report testing?",
                "manual_fact",
                id="h1-compound-1-0-1-1",
            ),
            pytest.param(
                "How many cycles does the once\u2010only manual report testing?",
                "manual_fact",
                id="h1-compound-1-1-0-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the once\u2010only manual report testing?",
                "manual_fact",
                id="h1-compound-1-1-0-1",
            ),
            pytest.param(
                "How many cycles does the once\u2010only datasheet report testing?",
                "manual_fact",
                id="h1-compound-1-1-1-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the once\u2010only datasheet report testing?",
                "manual_fact",
                id="h1-compound-1-1-1-1",
            ),
            pytest.param(
                "How many cycles does the once\u2011only manual report testing?",
                "manual_fact",
                id="h1-compound-1-2-0-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the once\u2011only manual report testing?",
                "manual_fact",
                id="h1-compound-1-2-0-1",
            ),
            pytest.param(
                "How many cycles does the once\u2011only datasheet report testing?",
                "manual_fact",
                id="h1-compound-1-2-1-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the once\u2011only datasheet report testing?",
                "manual_fact",
                id="h1-compound-1-2-1-1",
            ),
            pytest.param(
                "How many cycles does the after-action manual report testing?",
                "manual_fact",
                id="h1-compound-2-0-0-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the after-action manual report testing?",
                "manual_fact",
                id="h1-compound-2-0-0-1",
            ),
            pytest.param(
                "How many cycles does the after-action datasheet report testing?",
                "manual_fact",
                id="h1-compound-2-0-1-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the after-action datasheet report testing?",
                "manual_fact",
                id="h1-compound-2-0-1-1",
            ),
            pytest.param(
                "How many cycles does the after\u2010action manual report testing?",
                "manual_fact",
                id="h1-compound-2-1-0-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the after\u2010action manual report testing?",
                "manual_fact",
                id="h1-compound-2-1-0-1",
            ),
            pytest.param(
                "How many cycles does the after\u2010action datasheet report testing?",
                "manual_fact",
                id="h1-compound-2-1-1-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the after\u2010action datasheet report testing?",
                "manual_fact",
                id="h1-compound-2-1-1-1",
            ),
            pytest.param(
                "How many cycles does the after\u2011action manual report testing?",
                "manual_fact",
                id="h1-compound-2-2-0-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the after\u2011action manual report testing?",
                "manual_fact",
                id="h1-compound-2-2-0-1",
            ),
            pytest.param(
                "How many cycles does the after\u2011action datasheet report testing?",
                "manual_fact",
                id="h1-compound-2-2-1-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the after\u2011action datasheet report testing?",
                "manual_fact",
                id="h1-compound-2-2-1-1",
            ),
            pytest.param(
                "How many cycles does the before-use manual report testing?",
                "manual_fact",
                id="h1-compound-3-0-0-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the before-use manual report testing?",
                "manual_fact",
                id="h1-compound-3-0-0-1",
            ),
            pytest.param(
                "How many cycles does the before-use datasheet report testing?",
                "manual_fact",
                id="h1-compound-3-0-1-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the before-use datasheet report testing?",
                "manual_fact",
                id="h1-compound-3-0-1-1",
            ),
            pytest.param(
                "How many cycles does the before\u2010use manual report testing?",
                "manual_fact",
                id="h1-compound-3-1-0-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the before\u2010use manual report testing?",
                "manual_fact",
                id="h1-compound-3-1-0-1",
            ),
            pytest.param(
                "How many cycles does the before\u2010use datasheet report testing?",
                "manual_fact",
                id="h1-compound-3-1-1-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the before\u2010use datasheet report testing?",
                "manual_fact",
                id="h1-compound-3-1-1-1",
            ),
            pytest.param(
                "How many cycles does the before\u2011use manual report testing?",
                "manual_fact",
                id="h1-compound-3-2-0-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the before\u2011use manual report testing?",
                "manual_fact",
                id="h1-compound-3-2-0-1",
            ),
            pytest.param(
                "How many cycles does the before\u2011use datasheet report testing?",
                "manual_fact",
                id="h1-compound-3-2-1-0",
            ),
            pytest.param(
                "Before the audit, how many cycles does the before\u2011use datasheet report testing?",
                "manual_fact",
                id="h1-compound-3-2-1-1",
            ),
        ],
    )
    def test_compound_source_public_boundary(self, monkeypatch, query, expected_intent):
        from agent_framework import ai_function as sdk_ai_function
        from ai.core.analysis.intent import classify_rules
        from pydantic import ValidationError

        decision = classify_rules(query)
        _a1_lit(monkeypatch)
        try:
            selected = select_capabilities(
                query,
                profile=A1_ORDINARY_PROFILE,
                authenticated=True,
                task_intent=decision.intent.value if decision else None,
            )
            document_tool = _a1_offered_callable(selected, "search_attachment_docs")
            records_tool = _a1_offered_callable(selected, "query_database")
            if expected_intent == "manual_fact":
                assert document_tool is not None
                assert "search_attachment_docs" in selected.tool_ids
                _a1_assert_sdk_strict_selector(document_tool)
                model = sdk_ai_function(document_tool).input_model
                for bad in (True, 0, -1, 60.0, "60"):
                    with pytest.raises(ValidationError):
                        model.model_validate({"query": "source fact", "attachment_id": bad})
            else:
                assert document_tool is None
                assert "search_attachment_docs" not in selected.tool_ids
                assert records_tool is not None
                assert "query_database" in selected.tool_ids
            assert decision is not None and decision.intent.value == expected_intent
            assert decision.effect.value == "read_only"
            assert not selected.requires_specialist
            for authenticated, profile in ((True, frozenset()), (False, A1_ORDINARY_PROFILE)):
                denied = select_capabilities(
                    query,
                    profile=profile,
                    authenticated=authenticated,
                    task_intent=decision.intent.value,
                )
                assert "search_attachment_docs" not in denied.tool_ids
                assert "query_database" not in denied.tool_ids
                assert _a1_offered_callable(denied, "search_attachment_docs") is None
                assert _a1_offered_callable(denied, "query_database") is None
        finally:
            capability_catalog.cache_clear()


class TestFinalPassiveSourceAttribution:
    """Real selection/SDK/grant plus preserved explicit-comparison boundaries."""

    @pytest.mark.parametrize(
        ("query", "expected_intent"),
        [
            pytest.param(
                "How many cycles are listed in the datasheet?",
                "manual_fact",
                id="p1-passive-0-0-0-0",
            ),
            pytest.param(
                "How many cycles are listed in the datasheet on physical page 2?",
                "manual_fact",
                id="p1-passive-0-0-0-1",
            ),
            pytest.param(
                "How many cycles are clearly listed in the datasheet?",
                "manual_fact",
                id="p1-passive-0-0-1-0",
            ),
            pytest.param(
                "How many cycles are clearly listed in the datasheet on physical page 2?",
                "manual_fact",
                id="p1-passive-0-0-1-1",
            ),
            pytest.param(
                "How many cycles does the datasheet list?", "manual_fact", id="p1-active-0-0"
            ),
            pytest.param(
                "How many cycles are listed in uploaded attachment 60?",
                "manual_fact",
                id="p1-passive-0-1-0-0",
            ),
            pytest.param(
                "How many cycles are listed in uploaded attachment 60 on physical page 2?",
                "manual_fact",
                id="p1-passive-0-1-0-1",
            ),
            pytest.param(
                "How many cycles are clearly listed in uploaded attachment 60?",
                "manual_fact",
                id="p1-passive-0-1-1-0",
            ),
            pytest.param(
                "How many cycles are clearly listed in uploaded attachment 60 on physical page 2?",
                "manual_fact",
                id="p1-passive-0-1-1-1",
            ),
            pytest.param(
                "How many cycles does uploaded attachment 60 list?",
                "manual_fact",
                id="p1-active-0-1",
            ),
            pytest.param(
                "How many documents were listed last month?",
                "fleet_aggregate",
                id="p1-patient-event-0",
            ),
            pytest.param(
                "How many figures are shown in the datasheet?",
                "manual_fact",
                id="p1-passive-1-0-0-0",
            ),
            pytest.param(
                "How many figures are shown in the datasheet on physical page 2?",
                "manual_fact",
                id="p1-passive-1-0-0-1",
            ),
            pytest.param(
                "How many figures are clearly shown in the datasheet?",
                "manual_fact",
                id="p1-passive-1-0-1-0",
            ),
            pytest.param(
                "How many figures are clearly shown in the datasheet on physical page 2?",
                "manual_fact",
                id="p1-passive-1-0-1-1",
            ),
            pytest.param(
                "How many figures does the datasheet show?", "manual_fact", id="p1-active-1-0"
            ),
            pytest.param(
                "How many figures are shown in uploaded attachment 60?",
                "manual_fact",
                id="p1-passive-1-1-0-0",
            ),
            pytest.param(
                "How many figures are shown in uploaded attachment 60 on physical page 2?",
                "manual_fact",
                id="p1-passive-1-1-0-1",
            ),
            pytest.param(
                "How many figures are clearly shown in uploaded attachment 60?",
                "manual_fact",
                id="p1-passive-1-1-1-0",
            ),
            pytest.param(
                "How many figures are clearly shown in uploaded attachment 60 on physical page 2?",
                "manual_fact",
                id="p1-passive-1-1-1-1",
            ),
            pytest.param(
                "How many figures does uploaded attachment 60 show?",
                "manual_fact",
                id="p1-active-1-1",
            ),
            pytest.param(
                "How many documents were shown last month?",
                "fleet_aggregate",
                id="p1-patient-event-1",
            ),
            pytest.param(
                "How many features are described in the datasheet?",
                "manual_fact",
                id="p1-passive-2-0-0-0",
            ),
            pytest.param(
                "How many features are described in the datasheet on physical page 2?",
                "manual_fact",
                id="p1-passive-2-0-0-1",
            ),
            pytest.param(
                "How many features are clearly described in the datasheet?",
                "manual_fact",
                id="p1-passive-2-0-1-0",
            ),
            pytest.param(
                "How many features are clearly described in the datasheet on physical page 2?",
                "manual_fact",
                id="p1-passive-2-0-1-1",
            ),
            pytest.param(
                "How many features does the datasheet describe?", "manual_fact", id="p1-active-2-0"
            ),
            pytest.param(
                "How many features are described in uploaded attachment 60?",
                "manual_fact",
                id="p1-passive-2-1-0-0",
            ),
            pytest.param(
                "How many features are described in uploaded attachment 60 on physical page 2?",
                "manual_fact",
                id="p1-passive-2-1-0-1",
            ),
            pytest.param(
                "How many features are clearly described in uploaded attachment 60?",
                "manual_fact",
                id="p1-passive-2-1-1-0",
            ),
            pytest.param(
                "How many features are clearly described in uploaded attachment 60 on physical page 2?",
                "manual_fact",
                id="p1-passive-2-1-1-1",
            ),
            pytest.param(
                "How many features does uploaded attachment 60 describe?",
                "manual_fact",
                id="p1-active-2-1",
            ),
            pytest.param(
                "How many documents were described last month?",
                "fleet_aggregate",
                id="p1-patient-event-2",
            ),
            pytest.param(
                "How many cycles are mentioned in the datasheet?",
                "manual_fact",
                id="p1-passive-3-0-0-0",
            ),
            pytest.param(
                "How many cycles are mentioned in the datasheet on physical page 2?",
                "manual_fact",
                id="p1-passive-3-0-0-1",
            ),
            pytest.param(
                "How many cycles are clearly mentioned in the datasheet?",
                "manual_fact",
                id="p1-passive-3-0-1-0",
            ),
            pytest.param(
                "How many cycles are clearly mentioned in the datasheet on physical page 2?",
                "manual_fact",
                id="p1-passive-3-0-1-1",
            ),
            pytest.param(
                "How many cycles does the datasheet mention?", "manual_fact", id="p1-active-3-0"
            ),
            pytest.param(
                "How many cycles are mentioned in uploaded attachment 60?",
                "manual_fact",
                id="p1-passive-3-1-0-0",
            ),
            pytest.param(
                "How many cycles are mentioned in uploaded attachment 60 on physical page 2?",
                "manual_fact",
                id="p1-passive-3-1-0-1",
            ),
            pytest.param(
                "How many cycles are clearly mentioned in uploaded attachment 60?",
                "manual_fact",
                id="p1-passive-3-1-1-0",
            ),
            pytest.param(
                "How many cycles are clearly mentioned in uploaded attachment 60 on physical page 2?",
                "manual_fact",
                id="p1-passive-3-1-1-1",
            ),
            pytest.param(
                "How many cycles does uploaded attachment 60 mention?",
                "manual_fact",
                id="p1-active-3-1",
            ),
            pytest.param(
                "How many documents were mentioned last month?",
                "fleet_aggregate",
                id="p1-patient-event-3",
            ),
            pytest.param(
                "How many procedures are covered in the datasheet?",
                "manual_fact",
                id="p1-passive-4-0-0-0",
            ),
            pytest.param(
                "How many procedures are covered in the datasheet on physical page 2?",
                "manual_fact",
                id="p1-passive-4-0-0-1",
            ),
            pytest.param(
                "How many procedures are clearly covered in the datasheet?",
                "manual_fact",
                id="p1-passive-4-0-1-0",
            ),
            pytest.param(
                "How many procedures are clearly covered in the datasheet on physical page 2?",
                "manual_fact",
                id="p1-passive-4-0-1-1",
            ),
            pytest.param(
                "How many procedures does the datasheet cover?", "manual_fact", id="p1-active-4-0"
            ),
            pytest.param(
                "How many procedures are covered in uploaded attachment 60?",
                "manual_fact",
                id="p1-passive-4-1-0-0",
            ),
            pytest.param(
                "How many procedures are covered in uploaded attachment 60 on physical page 2?",
                "manual_fact",
                id="p1-passive-4-1-0-1",
            ),
            pytest.param(
                "How many procedures are clearly covered in uploaded attachment 60?",
                "manual_fact",
                id="p1-passive-4-1-1-0",
            ),
            pytest.param(
                "How many procedures are clearly covered in uploaded attachment 60 on physical page 2?",
                "manual_fact",
                id="p1-passive-4-1-1-1",
            ),
            pytest.param(
                "How many procedures does uploaded attachment 60 cover?",
                "manual_fact",
                id="p1-active-4-1",
            ),
            pytest.param(
                "How many documents were covered last month?",
                "fleet_aggregate",
                id="p1-patient-event-4",
            ),
            pytest.param(
                "How many tests are reported in the datasheet?",
                "manual_fact",
                id="p1-passive-5-0-0-0",
            ),
            pytest.param(
                "How many tests are reported in the datasheet on physical page 2?",
                "manual_fact",
                id="p1-passive-5-0-0-1",
            ),
            pytest.param(
                "How many tests are clearly reported in the datasheet?",
                "manual_fact",
                id="p1-passive-5-0-1-0",
            ),
            pytest.param(
                "How many tests are clearly reported in the datasheet on physical page 2?",
                "manual_fact",
                id="p1-passive-5-0-1-1",
            ),
            pytest.param(
                "How many tests does the datasheet report?", "manual_fact", id="p1-active-5-0"
            ),
            pytest.param(
                "How many tests are reported in uploaded attachment 60?",
                "manual_fact",
                id="p1-passive-5-1-0-0",
            ),
            pytest.param(
                "How many tests are reported in uploaded attachment 60 on physical page 2?",
                "manual_fact",
                id="p1-passive-5-1-0-1",
            ),
            pytest.param(
                "How many tests are clearly reported in uploaded attachment 60?",
                "manual_fact",
                id="p1-passive-5-1-1-0",
            ),
            pytest.param(
                "How many tests are clearly reported in uploaded attachment 60 on physical page 2?",
                "manual_fact",
                id="p1-passive-5-1-1-1",
            ),
            pytest.param(
                "How many tests does uploaded attachment 60 report?",
                "manual_fact",
                id="p1-active-5-1",
            ),
            pytest.param(
                "How many documents were reported last month?",
                "fleet_aggregate",
                id="p1-patient-event-5",
            ),
            pytest.param(
                "How many pumps were repaired per the datasheet?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-0-0-0",
            ),
            pytest.param(
                "How many pumps were repaired according to the datasheet?",
                "manual_wo_comparison",
                id="p1-event-source-qualifier-0-0-1",
            ),
            pytest.param(
                "How many pumps were repaired per uploaded attachment 60?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-0-1-0",
            ),
            pytest.param(
                "How many pumps were repaired according to uploaded attachment 60?",
                "manual_wo_comparison",
                id="p1-event-source-qualifier-0-1-1",
            ),
            pytest.param(
                "How many pumps were inspected per the datasheet?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-1-0-0",
            ),
            pytest.param(
                "How many pumps were inspected according to the datasheet?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-1-0-1",
            ),
            pytest.param(
                "How many pumps were inspected per uploaded attachment 60?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-1-1-0",
            ),
            pytest.param(
                "How many pumps were inspected according to uploaded attachment 60?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-1-1-1",
            ),
            pytest.param(
                "How many documents were read per the datasheet?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-2-0-0",
            ),
            pytest.param(
                "How many documents were read according to the datasheet?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-2-0-1",
            ),
            pytest.param(
                "How many documents were read per uploaded attachment 60?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-2-1-0",
            ),
            pytest.param(
                "How many documents were read according to uploaded attachment 60?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-2-1-1",
            ),
            pytest.param(
                "How many procedures were tested per the datasheet?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-3-0-0",
            ),
            pytest.param(
                "How many procedures were tested according to the datasheet?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-3-0-1",
            ),
            pytest.param(
                "How many procedures were tested per uploaded attachment 60?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-3-1-0",
            ),
            pytest.param(
                "How many procedures were tested according to uploaded attachment 60?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-3-1-1",
            ),
            pytest.param(
                "How many revisions were published per the datasheet?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-4-0-0",
            ),
            pytest.param(
                "How many revisions were published according to the datasheet?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-4-0-1",
            ),
            pytest.param(
                "How many revisions were published per uploaded attachment 60?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-4-1-0",
            ),
            pytest.param(
                "How many revisions were published according to uploaded attachment 60?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-4-1-1",
            ),
            pytest.param(
                "How many work orders were closed per the datasheet?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-5-0-0",
            ),
            pytest.param(
                "How many work orders were closed according to the datasheet?",
                "manual_wo_comparison",
                id="p1-event-source-qualifier-5-0-1",
            ),
            pytest.param(
                "How many work orders were closed per uploaded attachment 60?",
                "fleet_aggregate",
                id="p1-event-source-qualifier-5-1-0",
            ),
            pytest.param(
                "How many work orders were closed according to uploaded attachment 60?",
                "manual_wo_comparison",
                id="p1-event-source-qualifier-5-1-1",
            ),
            pytest.param(
                "How many pumps were repaired after figures were listed in the manual?",
                "fleet_aggregate",
                id="p1-subordinate-passive-event-0",
            ),
            pytest.param(
                "How many pumps were repaired before figures were listed in the manual?",
                "fleet_aggregate",
                id="p1-subordinate-passive-event-1",
            ),
            pytest.param(
                "How many pumps were repaired when figures were listed in the manual?",
                "fleet_aggregate",
                id="p1-subordinate-passive-event-2",
            ),
            pytest.param(
                "How many pumps were repaired while figures were listed in the manual?",
                "fleet_aggregate",
                id="p1-subordinate-passive-event-3",
            ),
            pytest.param(
                "How many pumps were repaired once figures were listed in the manual?",
                "fleet_aggregate",
                id="p1-subordinate-passive-event-4",
            ),
            pytest.param(
                "How many pumps were repaired as figures were listed in the manual?",
                "fleet_aggregate",
                id="p1-subordinate-passive-event-5",
            ),
            pytest.param(
                "How many pumps were repaired since figures were listed in the manual?",
                "fleet_aggregate",
                id="p1-subordinate-passive-event-6",
            ),
            pytest.param(
                "How many pumps were repaired until figures were listed in the manual?",
                "fleet_aggregate",
                id="p1-subordinate-passive-event-7",
            ),
            pytest.param(
                "Before the audit, how many cycles are listed in the datasheet?",
                "manual_fact",
                id="p1-prefix-passive-source-0",
            ),
            pytest.param(
                "Before the audit, how many cycles are listed in uploaded attachment 60?",
                "manual_fact",
                id="p1-prefix-passive-source-1",
            ),
        ],
    )
    def test_passive_source_public_boundary(self, monkeypatch, query, expected_intent):
        from agent_framework import ai_function as sdk_ai_function
        from ai.core.analysis.intent import classify_rules
        from pydantic import ValidationError

        decision = classify_rules(query)
        _a1_lit(monkeypatch)
        try:
            selected = select_capabilities(
                query,
                profile=A1_ORDINARY_PROFILE,
                authenticated=True,
                task_intent=decision.intent.value if decision else None,
            )
            document_tool = _a1_offered_callable(selected, "search_attachment_docs")
            records_tool = _a1_offered_callable(selected, "query_database")
            if expected_intent == "manual_fact":
                assert document_tool is not None
                assert "search_attachment_docs" in selected.tool_ids
                _a1_assert_sdk_strict_selector(document_tool)
                model = sdk_ai_function(document_tool).input_model
                for bad in (True, 0, -1, 60.0, "60"):
                    with pytest.raises(ValidationError):
                        model.model_validate({"query": "source fact", "attachment_id": bad})
            elif expected_intent == "manual_wo_comparison":
                assert document_tool is None
                assert records_tool is None
                assert _a1_offered_callable(selected, "search_work_orders") is not None
                assert _a1_offered_callable(selected, "search_manuals") is not None
                assert {"search_work_orders", "search_manuals"} <= set(selected.tool_ids)
            else:
                assert document_tool is None
                assert "search_attachment_docs" not in selected.tool_ids
                assert records_tool is not None
                assert "query_database" in selected.tool_ids
            assert decision is not None and decision.intent.value == expected_intent
            assert decision.effect.value == "read_only"
            assert not selected.requires_specialist
            for authenticated, profile in ((True, frozenset()), (False, A1_ORDINARY_PROFILE)):
                denied = select_capabilities(
                    query,
                    profile=profile,
                    authenticated=authenticated,
                    task_intent=decision.intent.value,
                )
                assert "search_attachment_docs" not in denied.tool_ids
                assert "query_database" not in denied.tool_ids
                assert _a1_offered_callable(denied, "search_attachment_docs") is None
                assert _a1_offered_callable(denied, "query_database") is None
                assert "search_work_orders" not in denied.tool_ids
                assert "search_manuals" not in denied.tool_ids
                assert _a1_offered_callable(denied, "search_work_orders") is None
                assert _a1_offered_callable(denied, "search_manuals") is None
        finally:
            capability_catalog.cache_clear()


class TestFinalRelativeSourceOwnership:
    """Actual classifier/selection/SDK/grants; relative predicate ownership."""

    @pytest.mark.parametrize(
        ("query", "expected_intent"),
        [
            pytest.param(
                "How many pumps that are listed in the manual were repaired last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-0-0-0-0",
            ),
            pytest.param(
                "How many pumps that are listed in the datasheet were repaired last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-0-0-0-1",
            ),
            pytest.param(
                "How many pumps that are listed in the manual were inspected last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-0-0-1-0",
            ),
            pytest.param(
                "How many pumps that are listed in the datasheet were inspected last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-0-0-1-1",
            ),
            pytest.param(
                "How many pumps that are listed in the manual were tested last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-0-0-2-0",
            ),
            pytest.param(
                "How many pumps that are listed in the datasheet were tested last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-0-0-2-1",
            ),
            pytest.param(
                "How many pumps that were listed in the manual were repaired last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-0-1-0-0",
            ),
            pytest.param(
                "How many pumps that were listed in the datasheet were repaired last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-0-1-0-1",
            ),
            pytest.param(
                "How many pumps that were listed in the manual were inspected last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-0-1-1-0",
            ),
            pytest.param(
                "How many pumps that were listed in the datasheet were inspected last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-0-1-1-1",
            ),
            pytest.param(
                "How many pumps that were listed in the manual were tested last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-0-1-2-0",
            ),
            pytest.param(
                "How many pumps that were listed in the datasheet were tested last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-0-1-2-1",
            ),
            pytest.param(
                "How many pumps which are listed in the manual were repaired last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-1-0-0-0",
            ),
            pytest.param(
                "How many pumps which are listed in the datasheet were repaired last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-1-0-0-1",
            ),
            pytest.param(
                "How many pumps which are listed in the manual were inspected last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-1-0-1-0",
            ),
            pytest.param(
                "How many pumps which are listed in the datasheet were inspected last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-1-0-1-1",
            ),
            pytest.param(
                "How many pumps which are listed in the manual were tested last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-1-0-2-0",
            ),
            pytest.param(
                "How many pumps which are listed in the datasheet were tested last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-1-0-2-1",
            ),
            pytest.param(
                "How many pumps which were listed in the manual were repaired last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-1-1-0-0",
            ),
            pytest.param(
                "How many pumps which were listed in the datasheet were repaired last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-1-1-0-1",
            ),
            pytest.param(
                "How many pumps which were listed in the manual were inspected last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-1-1-1-0",
            ),
            pytest.param(
                "How many pumps which were listed in the datasheet were inspected last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-1-1-1-1",
            ),
            pytest.param(
                "How many pumps which were listed in the manual were tested last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-1-1-2-0",
            ),
            pytest.param(
                "How many pumps which were listed in the datasheet were tested last month?",
                "fleet_aggregate",
                id="r1-passive-relative-event-1-1-2-1",
            ),
            pytest.param(
                "How many pumps that the manual describes were repaired last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-0-0-0-0",
            ),
            pytest.param(
                "How many pumps that the datasheet describes were repaired last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-0-0-0-1",
            ),
            pytest.param(
                "How many pumps that the manual describes were inspected last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-0-0-1-0",
            ),
            pytest.param(
                "How many pumps that the datasheet describes were inspected last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-0-0-1-1",
            ),
            pytest.param(
                "How many pumps that the manual mentions were repaired last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-0-1-0-0",
            ),
            pytest.param(
                "How many pumps that the datasheet mentions were repaired last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-0-1-0-1",
            ),
            pytest.param(
                "How many pumps that the manual mentions were inspected last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-0-1-1-0",
            ),
            pytest.param(
                "How many pumps that the datasheet mentions were inspected last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-0-1-1-1",
            ),
            pytest.param(
                "How many pumps that the manual covers were repaired last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-0-2-0-0",
            ),
            pytest.param(
                "How many pumps that the datasheet covers were repaired last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-0-2-0-1",
            ),
            pytest.param(
                "How many pumps that the manual covers were inspected last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-0-2-1-0",
            ),
            pytest.param(
                "How many pumps that the datasheet covers were inspected last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-0-2-1-1",
            ),
            pytest.param(
                "How many pumps which the manual describes were repaired last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-1-0-0-0",
            ),
            pytest.param(
                "How many pumps which the datasheet describes were repaired last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-1-0-0-1",
            ),
            pytest.param(
                "How many pumps which the manual describes were inspected last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-1-0-1-0",
            ),
            pytest.param(
                "How many pumps which the datasheet describes were inspected last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-1-0-1-1",
            ),
            pytest.param(
                "How many pumps which the manual mentions were repaired last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-1-1-0-0",
            ),
            pytest.param(
                "How many pumps which the datasheet mentions were repaired last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-1-1-0-1",
            ),
            pytest.param(
                "How many pumps which the manual mentions were inspected last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-1-1-1-0",
            ),
            pytest.param(
                "How many pumps which the datasheet mentions were inspected last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-1-1-1-1",
            ),
            pytest.param(
                "How many pumps which the manual covers were repaired last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-1-2-0-0",
            ),
            pytest.param(
                "How many pumps which the datasheet covers were repaired last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-1-2-0-1",
            ),
            pytest.param(
                "How many pumps which the manual covers were inspected last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-1-2-1-0",
            ),
            pytest.param(
                "How many pumps which the datasheet covers were inspected last month?",
                "fleet_aggregate",
                id="r1-active-relative-event-1-2-1-1",
            ),
            pytest.param(
                "How many cycles are listed in the manual?", "manual_fact", id="r1-source-fact-0-0"
            ),
            pytest.param(
                "How many cycles are listed in the datasheet?",
                "manual_fact",
                id="r1-source-fact-0-1",
            ),
            pytest.param(
                "How many figures are shown in the manual?", "manual_fact", id="r1-source-fact-1-0"
            ),
            pytest.param(
                "How many figures are shown in the datasheet?",
                "manual_fact",
                id="r1-source-fact-1-1",
            ),
            pytest.param(
                "How many features are described in the manual?",
                "manual_fact",
                id="r1-source-fact-2-0",
            ),
            pytest.param(
                "How many features are described in the datasheet?",
                "manual_fact",
                id="r1-source-fact-2-1",
            ),
            pytest.param(
                "How many cycles are mentioned in the manual?",
                "manual_fact",
                id="r1-source-fact-3-0",
            ),
            pytest.param(
                "How many cycles are mentioned in the datasheet?",
                "manual_fact",
                id="r1-source-fact-3-1",
            ),
            pytest.param(
                "How many procedures are covered in the manual?",
                "manual_fact",
                id="r1-source-fact-4-0",
            ),
            pytest.param(
                "How many procedures are covered in the datasheet?",
                "manual_fact",
                id="r1-source-fact-4-1",
            ),
            pytest.param(
                "How many tests are reported in the manual?", "manual_fact", id="r1-source-fact-5-0"
            ),
            pytest.param(
                "How many tests are reported in the datasheet?",
                "manual_fact",
                id="r1-source-fact-5-1",
            ),
            pytest.param(
                "How many pumps that passed inspection are listed in the manual?",
                "manual_fact",
                id="r1-counted-head-relative-fact-0-0-0",
            ),
            pytest.param(
                "How many pumps that passed inspection are listed in the datasheet?",
                "manual_fact",
                id="r1-counted-head-relative-fact-0-0-1",
            ),
            pytest.param(
                "How many pumps that were pressure-tested are listed in the manual?",
                "manual_fact",
                id="r1-counted-head-relative-fact-0-1-0",
            ),
            pytest.param(
                "How many pumps that were pressure-tested are listed in the datasheet?",
                "manual_fact",
                id="r1-counted-head-relative-fact-0-1-1",
            ),
            pytest.param(
                "How many pumps which passed inspection are listed in the manual?",
                "manual_fact",
                id="r1-counted-head-relative-fact-1-0-0",
            ),
            pytest.param(
                "How many pumps which passed inspection are listed in the datasheet?",
                "manual_fact",
                id="r1-counted-head-relative-fact-1-0-1",
            ),
            pytest.param(
                "How many pumps which were pressure-tested are listed in the manual?",
                "manual_fact",
                id="r1-counted-head-relative-fact-1-1-0",
            ),
            pytest.param(
                "How many pumps which were pressure-tested are listed in the datasheet?",
                "manual_fact",
                id="r1-counted-head-relative-fact-1-1-1",
            ),
        ],
    )
    def test_relative_source_public_boundary(self, monkeypatch, query, expected_intent):
        from agent_framework import ai_function as sdk_ai_function
        from ai.core.analysis.intent import classify_rules
        from pydantic import ValidationError

        decision = classify_rules(query)
        assert decision is not None
        assert decision.intent.value == expected_intent
        _a1_lit(monkeypatch)
        try:
            selected = select_capabilities(
                query,
                profile=A1_ORDINARY_PROFILE,
                authenticated=True,
                task_intent=decision.intent.value if decision else None,
            )
            document_tool = _a1_offered_callable(selected, "search_attachment_docs")
            records_tool = _a1_offered_callable(selected, "query_database")
            if expected_intent == "manual_fact":
                assert document_tool is not None
                assert "search_attachment_docs" in selected.tool_ids
                _a1_assert_sdk_strict_selector(document_tool)
                model = sdk_ai_function(document_tool).input_model
                for bad in (True, 0, -1, 60.0, "60"):
                    with pytest.raises(ValidationError):
                        model.model_validate({"query": "source fact", "attachment_id": bad})
            elif expected_intent == "manual_wo_comparison":
                assert document_tool is None
                assert records_tool is None
                assert _a1_offered_callable(selected, "search_work_orders") is not None
                assert _a1_offered_callable(selected, "search_manuals") is not None
                assert {"search_work_orders", "search_manuals"} <= set(selected.tool_ids)
            else:
                assert document_tool is None
                assert "search_attachment_docs" not in selected.tool_ids
                assert records_tool is not None
                assert "query_database" in selected.tool_ids
            assert decision is not None and decision.intent.value == expected_intent
            assert decision.effect.value == "read_only"
            assert not selected.requires_specialist
            for authenticated, profile in ((True, frozenset()), (False, A1_ORDINARY_PROFILE)):
                denied = select_capabilities(
                    query,
                    profile=profile,
                    authenticated=authenticated,
                    task_intent=decision.intent.value,
                )
                assert "search_attachment_docs" not in denied.tool_ids
                assert "query_database" not in denied.tool_ids
                assert _a1_offered_callable(denied, "search_attachment_docs") is None
                assert _a1_offered_callable(denied, "query_database") is None
                assert "search_work_orders" not in denied.tool_ids
                assert "search_manuals" not in denied.tool_ids
                assert _a1_offered_callable(denied, "search_work_orders") is None
                assert _a1_offered_callable(denied, "search_manuals") is None
        finally:
            capability_catalog.cache_clear()


class TestFinalSourceModifierWidth:
    """Source-NP modifier width across fact licensing and relative ownership."""

    @pytest.mark.parametrize(
        ("query", "expected_intent"),
        [
            pytest.param(
                "How many cycles are listed in the manual?", "manual_fact", id="w1-fact-0-0-0"
            ),
            pytest.param(
                "How many cycles are listed in the updated manual?",
                "manual_fact",
                id="w1-fact-0-0-1",
            ),
            pytest.param(
                "How many cycles are listed in the newly updated manual?",
                "manual_fact",
                id="w1-fact-0-0-2",
            ),
            pytest.param(
                "How many cycles are listed in the very recently updated manual?",
                "manual_fact",
                id="w1-fact-0-0-3",
            ),
            pytest.param(
                "How many cycles are listed in the datasheet?", "manual_fact", id="w1-fact-0-1-0"
            ),
            pytest.param(
                "How many cycles are listed in the updated datasheet?",
                "manual_fact",
                id="w1-fact-0-1-1",
            ),
            pytest.param(
                "How many cycles are listed in the newly updated datasheet?",
                "manual_fact",
                id="w1-fact-0-1-2",
            ),
            pytest.param(
                "How many cycles are listed in the very recently updated datasheet?",
                "manual_fact",
                id="w1-fact-0-1-3",
            ),
            pytest.param(
                "How many figures are shown in the manual?", "manual_fact", id="w1-fact-1-0-0"
            ),
            pytest.param(
                "How many figures are shown in the updated manual?",
                "manual_fact",
                id="w1-fact-1-0-1",
            ),
            pytest.param(
                "How many figures are shown in the newly updated manual?",
                "manual_fact",
                id="w1-fact-1-0-2",
            ),
            pytest.param(
                "How many figures are shown in the very recently updated manual?",
                "manual_fact",
                id="w1-fact-1-0-3",
            ),
            pytest.param(
                "How many figures are shown in the datasheet?", "manual_fact", id="w1-fact-1-1-0"
            ),
            pytest.param(
                "How many figures are shown in the updated datasheet?",
                "manual_fact",
                id="w1-fact-1-1-1",
            ),
            pytest.param(
                "How many figures are shown in the newly updated datasheet?",
                "manual_fact",
                id="w1-fact-1-1-2",
            ),
            pytest.param(
                "How many figures are shown in the very recently updated datasheet?",
                "manual_fact",
                id="w1-fact-1-1-3",
            ),
            pytest.param(
                "How many features are described in the manual?", "manual_fact", id="w1-fact-2-0-0"
            ),
            pytest.param(
                "How many features are described in the updated manual?",
                "manual_fact",
                id="w1-fact-2-0-1",
            ),
            pytest.param(
                "How many features are described in the newly updated manual?",
                "manual_fact",
                id="w1-fact-2-0-2",
            ),
            pytest.param(
                "How many features are described in the very recently updated manual?",
                "manual_fact",
                id="w1-fact-2-0-3",
            ),
            pytest.param(
                "How many features are described in the datasheet?",
                "manual_fact",
                id="w1-fact-2-1-0",
            ),
            pytest.param(
                "How many features are described in the updated datasheet?",
                "manual_fact",
                id="w1-fact-2-1-1",
            ),
            pytest.param(
                "How many features are described in the newly updated datasheet?",
                "manual_fact",
                id="w1-fact-2-1-2",
            ),
            pytest.param(
                "How many features are described in the very recently updated datasheet?",
                "manual_fact",
                id="w1-fact-2-1-3",
            ),
            pytest.param(
                "How many cycles are mentioned in the manual?", "manual_fact", id="w1-fact-3-0-0"
            ),
            pytest.param(
                "How many cycles are mentioned in the updated manual?",
                "manual_fact",
                id="w1-fact-3-0-1",
            ),
            pytest.param(
                "How many cycles are mentioned in the newly updated manual?",
                "manual_fact",
                id="w1-fact-3-0-2",
            ),
            pytest.param(
                "How many cycles are mentioned in the very recently updated manual?",
                "manual_fact",
                id="w1-fact-3-0-3",
            ),
            pytest.param(
                "How many cycles are mentioned in the datasheet?", "manual_fact", id="w1-fact-3-1-0"
            ),
            pytest.param(
                "How many cycles are mentioned in the updated datasheet?",
                "manual_fact",
                id="w1-fact-3-1-1",
            ),
            pytest.param(
                "How many cycles are mentioned in the newly updated datasheet?",
                "manual_fact",
                id="w1-fact-3-1-2",
            ),
            pytest.param(
                "How many cycles are mentioned in the very recently updated datasheet?",
                "manual_fact",
                id="w1-fact-3-1-3",
            ),
            pytest.param(
                "How many procedures are covered in the manual?", "manual_fact", id="w1-fact-4-0-0"
            ),
            pytest.param(
                "How many procedures are covered in the updated manual?",
                "manual_fact",
                id="w1-fact-4-0-1",
            ),
            pytest.param(
                "How many procedures are covered in the newly updated manual?",
                "manual_fact",
                id="w1-fact-4-0-2",
            ),
            pytest.param(
                "How many procedures are covered in the very recently updated manual?",
                "manual_fact",
                id="w1-fact-4-0-3",
            ),
            pytest.param(
                "How many procedures are covered in the datasheet?",
                "manual_fact",
                id="w1-fact-4-1-0",
            ),
            pytest.param(
                "How many procedures are covered in the updated datasheet?",
                "manual_fact",
                id="w1-fact-4-1-1",
            ),
            pytest.param(
                "How many procedures are covered in the newly updated datasheet?",
                "manual_fact",
                id="w1-fact-4-1-2",
            ),
            pytest.param(
                "How many procedures are covered in the very recently updated datasheet?",
                "manual_fact",
                id="w1-fact-4-1-3",
            ),
            pytest.param(
                "How many tests are reported in the manual?", "manual_fact", id="w1-fact-5-0-0"
            ),
            pytest.param(
                "How many tests are reported in the updated manual?",
                "manual_fact",
                id="w1-fact-5-0-1",
            ),
            pytest.param(
                "How many tests are reported in the newly updated manual?",
                "manual_fact",
                id="w1-fact-5-0-2",
            ),
            pytest.param(
                "How many tests are reported in the very recently updated manual?",
                "manual_fact",
                id="w1-fact-5-0-3",
            ),
            pytest.param(
                "How many tests are reported in the datasheet?", "manual_fact", id="w1-fact-5-1-0"
            ),
            pytest.param(
                "How many tests are reported in the updated datasheet?",
                "manual_fact",
                id="w1-fact-5-1-1",
            ),
            pytest.param(
                "How many tests are reported in the newly updated datasheet?",
                "manual_fact",
                id="w1-fact-5-1-2",
            ),
            pytest.param(
                "How many tests are reported in the very recently updated datasheet?",
                "manual_fact",
                id="w1-fact-5-1-3",
            ),
            pytest.param(
                "How many pumps that are listed in the manual were repaired last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-0-0-0",
            ),
            pytest.param(
                "How many pumps that the manual describes were repaired last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-0-0-0",
            ),
            pytest.param(
                "How many pumps that are listed in the updated manual were repaired last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-0-0-1",
            ),
            pytest.param(
                "How many pumps that the updated manual describes were repaired last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-0-0-1",
            ),
            pytest.param(
                "How many pumps that are listed in the newly updated manual were repaired last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-0-0-2",
            ),
            pytest.param(
                "How many pumps that the newly updated manual describes were repaired last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-0-0-2",
            ),
            pytest.param(
                "How many pumps that are listed in the very recently updated manual were repaired last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-0-0-3",
            ),
            pytest.param(
                "How many pumps that the very recently updated manual describes were repaired last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-0-0-3",
            ),
            pytest.param(
                "How many pumps that are listed in the datasheet were repaired last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-0-1-0",
            ),
            pytest.param(
                "How many pumps that the datasheet describes were repaired last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-0-1-0",
            ),
            pytest.param(
                "How many pumps that are listed in the updated datasheet were repaired last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-0-1-1",
            ),
            pytest.param(
                "How many pumps that the updated datasheet describes were repaired last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-0-1-1",
            ),
            pytest.param(
                "How many pumps that are listed in the newly updated datasheet were repaired last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-0-1-2",
            ),
            pytest.param(
                "How many pumps that the newly updated datasheet describes were repaired last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-0-1-2",
            ),
            pytest.param(
                "How many pumps that are listed in the very recently updated datasheet were repaired last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-0-1-3",
            ),
            pytest.param(
                "How many pumps that the very recently updated datasheet describes were repaired last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-0-1-3",
            ),
            pytest.param(
                "How many pumps that are listed in the manual were inspected last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-1-0-0",
            ),
            pytest.param(
                "How many pumps that the manual describes were inspected last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-1-0-0",
            ),
            pytest.param(
                "How many pumps that are listed in the updated manual were inspected last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-1-0-1",
            ),
            pytest.param(
                "How many pumps that the updated manual describes were inspected last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-1-0-1",
            ),
            pytest.param(
                "How many pumps that are listed in the newly updated manual were inspected last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-1-0-2",
            ),
            pytest.param(
                "How many pumps that the newly updated manual describes were inspected last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-1-0-2",
            ),
            pytest.param(
                "How many pumps that are listed in the very recently updated manual were inspected last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-1-0-3",
            ),
            pytest.param(
                "How many pumps that the very recently updated manual describes were inspected last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-1-0-3",
            ),
            pytest.param(
                "How many pumps that are listed in the datasheet were inspected last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-1-1-0",
            ),
            pytest.param(
                "How many pumps that the datasheet describes were inspected last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-1-1-0",
            ),
            pytest.param(
                "How many pumps that are listed in the updated datasheet were inspected last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-1-1-1",
            ),
            pytest.param(
                "How many pumps that the updated datasheet describes were inspected last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-1-1-1",
            ),
            pytest.param(
                "How many pumps that are listed in the newly updated datasheet were inspected last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-1-1-2",
            ),
            pytest.param(
                "How many pumps that the newly updated datasheet describes were inspected last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-1-1-2",
            ),
            pytest.param(
                "How many pumps that are listed in the very recently updated datasheet were inspected last month?",
                "fleet_aggregate",
                id="w1-passive-relative-event-1-1-3",
            ),
            pytest.param(
                "How many pumps that the very recently updated datasheet describes were inspected last month?",
                "fleet_aggregate",
                id="w1-active-relative-event-1-1-3",
            ),
            pytest.param(
                "How many pumps that passed inspection are listed in the manual?",
                "manual_fact",
                id="w1-relative-head-fact-0-0",
            ),
            pytest.param(
                "How many pumps that passed inspection are listed in the updated manual?",
                "manual_fact",
                id="w1-relative-head-fact-0-1",
            ),
            pytest.param(
                "How many pumps that passed inspection are listed in the newly updated manual?",
                "manual_fact",
                id="w1-relative-head-fact-0-2",
            ),
            pytest.param(
                "How many pumps that passed inspection are listed in the very recently updated manual?",
                "manual_fact",
                id="w1-relative-head-fact-0-3",
            ),
            pytest.param(
                "How many pumps that passed inspection are listed in the datasheet?",
                "manual_fact",
                id="w1-relative-head-fact-1-0",
            ),
            pytest.param(
                "How many pumps that passed inspection are listed in the updated datasheet?",
                "manual_fact",
                id="w1-relative-head-fact-1-1",
            ),
            pytest.param(
                "How many pumps that passed inspection are listed in the newly updated datasheet?",
                "manual_fact",
                id="w1-relative-head-fact-1-2",
            ),
            pytest.param(
                "How many pumps that passed inspection are listed in the very recently updated datasheet?",
                "manual_fact",
                id="w1-relative-head-fact-1-3",
            ),
        ],
    )
    def test_source_width_public_boundary(self, monkeypatch, query, expected_intent):
        from agent_framework import ai_function as sdk_ai_function
        from ai.core.analysis.intent import classify_rules
        from pydantic import ValidationError

        decision = classify_rules(query)
        assert decision is not None
        assert decision.intent.value == expected_intent
        _a1_lit(monkeypatch)
        try:
            selected = select_capabilities(
                query,
                profile=A1_ORDINARY_PROFILE,
                authenticated=True,
                task_intent=decision.intent.value if decision else None,
            )
            document_tool = _a1_offered_callable(selected, "search_attachment_docs")
            records_tool = _a1_offered_callable(selected, "query_database")
            if expected_intent == "manual_fact":
                assert document_tool is not None
                assert "search_attachment_docs" in selected.tool_ids
                _a1_assert_sdk_strict_selector(document_tool)
                model = sdk_ai_function(document_tool).input_model
                for bad in (True, 0, -1, 60.0, "60"):
                    with pytest.raises(ValidationError):
                        model.model_validate({"query": "source fact", "attachment_id": bad})
            elif expected_intent == "manual_wo_comparison":
                assert document_tool is None
                assert records_tool is None
                assert _a1_offered_callable(selected, "search_work_orders") is not None
                assert _a1_offered_callable(selected, "search_manuals") is not None
                assert {"search_work_orders", "search_manuals"} <= set(selected.tool_ids)
            else:
                assert document_tool is None
                assert "search_attachment_docs" not in selected.tool_ids
                assert records_tool is not None
                assert "query_database" in selected.tool_ids
            assert decision is not None and decision.intent.value == expected_intent
            assert decision.effect.value == "read_only"
            assert not selected.requires_specialist
            for authenticated, profile in ((True, frozenset()), (False, A1_ORDINARY_PROFILE)):
                denied = select_capabilities(
                    query,
                    profile=profile,
                    authenticated=authenticated,
                    task_intent=decision.intent.value,
                )
                assert "search_attachment_docs" not in denied.tool_ids
                assert "query_database" not in denied.tool_ids
                assert _a1_offered_callable(denied, "search_attachment_docs") is None
                assert _a1_offered_callable(denied, "query_database") is None
                assert "search_work_orders" not in denied.tool_ids
                assert "search_manuals" not in denied.tool_ids
                assert _a1_offered_callable(denied, "search_work_orders") is None
                assert _a1_offered_callable(denied, "search_manuals") is None
        finally:
            capability_catalog.cache_clear()


class TestGenericSourceModifiers:
    @pytest.mark.parametrize(
        ("query", "expected_intent"),
        [
            pytest.param(
                "How many cycles are listed in the large blue current manual?",
                "manual_fact",
                id="w1g-fact-0",
            ),
            pytest.param(
                "How many pumps that the large blue current manual describes were repaired last month?",
                "fleet_aggregate",
                id="w1g-active-event-0",
            ),
            pytest.param(
                "How many pumps that are listed in the large blue current manual were repaired last month?",
                "fleet_aggregate",
                id="w1g-passive-event-0",
            ),
            pytest.param(
                "How many pumps that passed inspection are listed in the large blue current manual?",
                "manual_fact",
                id="w1g-head-fact-0",
            ),
            pytest.param(
                "How many cycles are listed in the large blue current datasheet?",
                "manual_fact",
                id="w1g-fact-1",
            ),
            pytest.param(
                "How many pumps that the large blue current datasheet describes were repaired last month?",
                "fleet_aggregate",
                id="w1g-active-event-1",
            ),
            pytest.param(
                "How many pumps that are listed in the large blue current datasheet were repaired last month?",
                "fleet_aggregate",
                id="w1g-passive-event-1",
            ),
            pytest.param(
                "How many pumps that passed inspection are listed in the large blue current datasheet?",
                "manual_fact",
                id="w1g-head-fact-1",
            ),
        ],
    )
    def test_generic_source_modifiers(self, monkeypatch, query, expected_intent):
        from agent_framework import ai_function as sdk_ai_function
        from ai.core.analysis.intent import classify_rules
        from pydantic import ValidationError

        decision = classify_rules(query)
        assert decision is not None
        assert decision.intent.value == expected_intent
        _a1_lit(monkeypatch)
        try:
            selected = select_capabilities(
                query,
                profile=A1_ORDINARY_PROFILE,
                authenticated=True,
                task_intent=decision.intent.value if decision else None,
            )
            document_tool = _a1_offered_callable(selected, "search_attachment_docs")
            records_tool = _a1_offered_callable(selected, "query_database")
            if expected_intent == "manual_fact":
                assert document_tool is not None
                assert "search_attachment_docs" in selected.tool_ids
                _a1_assert_sdk_strict_selector(document_tool)
                model = sdk_ai_function(document_tool).input_model
                for bad in (True, 0, -1, 60.0, "60"):
                    with pytest.raises(ValidationError):
                        model.model_validate({"query": "source fact", "attachment_id": bad})
            elif expected_intent == "manual_wo_comparison":
                assert document_tool is None
                assert records_tool is None
                assert _a1_offered_callable(selected, "search_work_orders") is not None
                assert _a1_offered_callable(selected, "search_manuals") is not None
                assert {"search_work_orders", "search_manuals"} <= set(selected.tool_ids)
            else:
                assert document_tool is None
                assert "search_attachment_docs" not in selected.tool_ids
                assert records_tool is not None
                assert "query_database" in selected.tool_ids
            assert decision is not None and decision.intent.value == expected_intent
            assert decision.effect.value == "read_only"
            assert not selected.requires_specialist
            for authenticated, profile in ((True, frozenset()), (False, A1_ORDINARY_PROFILE)):
                denied = select_capabilities(
                    query,
                    profile=profile,
                    authenticated=authenticated,
                    task_intent=decision.intent.value,
                )
                assert "search_attachment_docs" not in denied.tool_ids
                assert "query_database" not in denied.tool_ids
                assert _a1_offered_callable(denied, "search_attachment_docs") is None
                assert _a1_offered_callable(denied, "query_database") is None
                assert "search_work_orders" not in denied.tool_ids
                assert "search_manuals" not in denied.tool_ids
                assert _a1_offered_callable(denied, "search_work_orders") is None
                assert _a1_offered_callable(denied, "search_manuals") is None
        finally:
            capability_catalog.cache_clear()


def test_source_modifier_punctuation_work_bound():
    """A fixed short rejected modifier must not cause runaway backtracking."""
    import signal

    from ai.core.analysis.intent import classify_rules

    def exceeded(_signum, _frame):
        raise AssertionError("Short source-modifier punctuation exceeded one second")

    previous = signal.signal(signal.SIGALRM, exceeded)
    signal.setitimer(signal.ITIMER_REAL, 1.0)
    try:
        assert classify_rules("How many cycles are listed in " + "a" * 40 + ", manual?") is not None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.parametrize(
    "query,expected_intent",
    [
        ("How many pumps were repaired that are listed in the manual?", "fleet_aggregate"),
        ("How many pumps that were repaired are listed in the manual?", "manual_fact"),
        ("How many pumps were repaired that the manual lists?", "fleet_aggregate"),
        ("How many pumps that were repaired does the manual list?", "manual_fact"),
        ("How many pumps were repaired which are listed in the manual?", "fleet_aggregate"),
        ("How many pumps which were repaired are listed in the manual?", "manual_fact"),
        ("How many pumps were repaired which the manual lists?", "fleet_aggregate"),
        ("How many pumps which were repaired does the manual list?", "manual_fact"),
        ("How many pumps were inspected that are listed in the manual?", "fleet_aggregate"),
        ("How many pumps that were inspected are listed in the manual?", "manual_fact"),
        ("How many pumps were inspected that the manual lists?", "fleet_aggregate"),
        ("How many pumps that were inspected does the manual list?", "manual_fact"),
        ("How many pumps were inspected which are listed in the manual?", "fleet_aggregate"),
        ("How many pumps which were inspected are listed in the manual?", "manual_fact"),
        ("How many pumps were inspected which the manual lists?", "fleet_aggregate"),
        ("How many pumps which were inspected does the manual list?", "manual_fact"),
        ("How many pumps were replaced that are listed in the manual?", "fleet_aggregate"),
        ("How many pumps that were replaced are listed in the manual?", "manual_fact"),
        ("How many pumps were replaced that the manual lists?", "fleet_aggregate"),
        ("How many pumps that were replaced does the manual list?", "manual_fact"),
        ("How many pumps were replaced which are listed in the manual?", "fleet_aggregate"),
        ("How many pumps which were replaced are listed in the manual?", "manual_fact"),
        ("How many pumps were replaced which the manual lists?", "fleet_aggregate"),
        ("How many pumps which were replaced does the manual list?", "manual_fact"),
        ("How many pumps were recently repaired that are listed in the manual?", "fleet_aggregate"),
        ("How many pumps that were recently repaired are listed in the manual?", "manual_fact"),
        ("How many pumps were recently repaired that the manual lists?", "fleet_aggregate"),
        ("How many pumps that were recently repaired does the manual list?", "manual_fact"),
        (
            "How many pumps were recently repaired which are listed in the manual?",
            "fleet_aggregate",
        ),
        ("How many pumps which were recently repaired are listed in the manual?", "manual_fact"),
        ("How many pumps were recently repaired which the manual lists?", "fleet_aggregate"),
        ("How many pumps which were recently repaired does the manual list?", "manual_fact"),
        ("How many pumps had been repaired that are listed in the manual?", "fleet_aggregate"),
        ("How many pumps that had been repaired are listed in the manual?", "manual_fact"),
        ("How many pumps had been repaired that the manual lists?", "fleet_aggregate"),
        ("How many pumps that had been repaired does the manual list?", "manual_fact"),
        ("How many pumps had been repaired which are listed in the manual?", "fleet_aggregate"),
        ("How many pumps which had been repaired are listed in the manual?", "manual_fact"),
        ("How many pumps had been repaired which the manual lists?", "fleet_aggregate"),
        ("How many pumps which had been repaired does the manual list?", "manual_fact"),
        ("How many pumps were built that are listed in the manual?", "fleet_aggregate"),
        ("How many pumps that were built are listed in the manual?", "manual_fact"),
        ("How many pumps were built that the manual lists?", "fleet_aggregate"),
        ("How many pumps that were built does the manual list?", "manual_fact"),
        ("How many pumps were built which are listed in the manual?", "fleet_aggregate"),
        ("How many pumps which were built are listed in the manual?", "manual_fact"),
        ("How many pumps were built which the manual lists?", "fleet_aggregate"),
        ("How many pumps which were built does the manual list?", "manual_fact"),
        ("How many pumps that are listed in the manual were repaired?", "fleet_aggregate"),
        ("How many pumps that the manual describes were repaired?", "fleet_aggregate"),
        ("How many pumps which are listed in the manual were repaired?", "fleet_aggregate"),
        ("How many pumps which the manual describes were repaired?", "fleet_aggregate"),
        ("How many pumps that are listed in the manual were inspected?", "fleet_aggregate"),
        ("How many pumps that the manual describes were inspected?", "fleet_aggregate"),
        ("How many pumps which are listed in the manual were inspected?", "fleet_aggregate"),
        ("How many pumps which the manual describes were inspected?", "fleet_aggregate"),
        ("How many pumps that are listed in the manual were replaced?", "fleet_aggregate"),
        ("How many pumps that the manual describes were replaced?", "fleet_aggregate"),
        ("How many pumps which are listed in the manual were replaced?", "fleet_aggregate"),
        ("How many pumps which the manual describes were replaced?", "fleet_aggregate"),
        ("How many pumps that are listed in the manual were closed?", "fleet_aggregate"),
        ("How many pumps that the manual describes were closed?", "fleet_aggregate"),
        ("How many pumps which are listed in the manual were closed?", "fleet_aggregate"),
        ("How many pumps which the manual describes were closed?", "fleet_aggregate"),
    ],
    ids=[
        "e1-event-before-repaired-that-listed",
        "e1-fact-head-repaired-that-listed",
        "e1-event-before-repaired-that-source-subject",
        "e1-fact-head-repaired-that-source-subject",
        "e1-event-before-repaired-which-listed",
        "e1-fact-head-repaired-which-listed",
        "e1-event-before-repaired-which-source-subject",
        "e1-fact-head-repaired-which-source-subject",
        "e1-event-before-inspected-that-listed",
        "e1-fact-head-inspected-that-listed",
        "e1-event-before-inspected-that-source-subject",
        "e1-fact-head-inspected-that-source-subject",
        "e1-event-before-inspected-which-listed",
        "e1-fact-head-inspected-which-listed",
        "e1-event-before-inspected-which-source-subject",
        "e1-fact-head-inspected-which-source-subject",
        "e1-event-before-replaced-that-listed",
        "e1-fact-head-replaced-that-listed",
        "e1-event-before-replaced-that-source-subject",
        "e1-fact-head-replaced-that-source-subject",
        "e1-event-before-replaced-which-listed",
        "e1-fact-head-replaced-which-listed",
        "e1-event-before-replaced-which-source-subject",
        "e1-fact-head-replaced-which-source-subject",
        "e1-event-before-adverb-that-listed",
        "e1-fact-head-adverb-that-listed",
        "e1-event-before-adverb-that-source-subject",
        "e1-fact-head-adverb-that-source-subject",
        "e1-event-before-adverb-which-listed",
        "e1-fact-head-adverb-which-listed",
        "e1-event-before-adverb-which-source-subject",
        "e1-fact-head-adverb-which-source-subject",
        "e1-event-before-perfect-that-listed",
        "e1-fact-head-perfect-that-listed",
        "e1-event-before-perfect-that-source-subject",
        "e1-fact-head-perfect-that-source-subject",
        "e1-event-before-perfect-which-listed",
        "e1-fact-head-perfect-which-listed",
        "e1-event-before-perfect-which-source-subject",
        "e1-fact-head-perfect-which-source-subject",
        "e1-event-before-irregular-that-listed",
        "e1-fact-head-irregular-that-listed",
        "e1-event-before-irregular-that-source-subject",
        "e1-fact-head-irregular-that-source-subject",
        "e1-event-before-irregular-which-listed",
        "e1-fact-head-irregular-which-listed",
        "e1-event-before-irregular-which-source-subject",
        "e1-fact-head-irregular-which-source-subject",
        "e1-event-after-repaired-that-listed",
        "e1-event-after-repaired-that-source-subject",
        "e1-event-after-repaired-which-listed",
        "e1-event-after-repaired-which-source-subject",
        "e1-event-after-inspected-that-listed",
        "e1-event-after-inspected-that-source-subject",
        "e1-event-after-inspected-which-listed",
        "e1-event-after-inspected-which-source-subject",
        "e1-event-after-replaced-that-listed",
        "e1-event-after-replaced-that-source-subject",
        "e1-event-after-replaced-which-listed",
        "e1-event-after-replaced-which-source-subject",
        "e1-event-after-closed-that-listed",
        "e1-event-after-closed-that-source-subject",
        "e1-event-after-closed-which-listed",
        "e1-event-after-closed-which-source-subject",
    ],
)
def test_event_head_before_document_relative(monkeypatch, query, expected_intent):
    TestFinalRelativeSourceOwnership().test_relative_source_public_boundary(
        monkeypatch, query, expected_intent
    )


class TestBoundedClauseOwnershipAdjuncts:
    """A reporter in a subordinate clause must not become the source noun."""

    @pytest.mark.parametrize(
        ("query", "expected_intent"),
        [
            pytest.param(
                "Read the manual while the foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-while-foreman",
            ),
            pytest.param(
                "Read the manual while the technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-while-technician",
            ),
            pytest.param(
                "Read the manual while he reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-while-he",
            ),
            pytest.param(
                "Read the manual while she reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-while-she",
            ),
            pytest.param(
                "Read the manual while they report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-while-they",
            ),
            pytest.param(
                "Read the manual while Alex reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-while-proper-name",
            ),
            pytest.param(
                "Read the manual while I report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-while-i",
            ),
            pytest.param(
                "Read the manual while we report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-while-we",
            ),
            pytest.param(
                "Read the manual while you report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-while-you",
            ),
            pytest.param(
                "Read the manual when the foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-when-foreman",
            ),
            pytest.param(
                "Read the manual when the technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-when-technician",
            ),
            pytest.param(
                "Read the manual when he reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-when-he",
            ),
            pytest.param(
                "Read the manual when she reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-when-she",
            ),
            pytest.param(
                "Read the manual when they report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-when-they",
            ),
            pytest.param(
                "Read the manual when Alex reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-when-proper-name",
            ),
            pytest.param(
                "Read the manual when I report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-when-i",
            ),
            pytest.param(
                "Read the manual when we report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-when-we",
            ),
            pytest.param(
                "Read the manual when you report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-when-you",
            ),
            pytest.param(
                "Read the manual before the foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-before-foreman",
            ),
            pytest.param(
                "Read the manual before the technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-before-technician",
            ),
            pytest.param(
                "Read the manual before he reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-before-he",
            ),
            pytest.param(
                "Read the manual before she reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-before-she",
            ),
            pytest.param(
                "Read the manual before they report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-before-they",
            ),
            pytest.param(
                "Read the manual before Alex reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-before-proper-name",
            ),
            pytest.param(
                "Read the manual before I report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-before-i",
            ),
            pytest.param(
                "Read the manual before we report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-before-we",
            ),
            pytest.param(
                "Read the manual before you report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-before-you",
            ),
            pytest.param(
                "Read the manual after the foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-after-foreman",
            ),
            pytest.param(
                "Read the manual after the technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-after-technician",
            ),
            pytest.param(
                "Read the manual after he reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-after-he",
            ),
            pytest.param(
                "Read the manual after she reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-after-she",
            ),
            pytest.param(
                "Read the manual after they report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-after-they",
            ),
            pytest.param(
                "Read the manual after Alex reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-after-proper-name",
            ),
            pytest.param(
                "Read the manual after I report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-after-i",
            ),
            pytest.param(
                "Read the manual after we report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-after-we",
            ),
            pytest.param(
                "Read the manual after you report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-after-you",
            ),
            pytest.param(
                "Read the manual once the foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-once-foreman",
            ),
            pytest.param(
                "Read the manual once the technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-once-technician",
            ),
            pytest.param(
                "Read the manual once he reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-once-he",
            ),
            pytest.param(
                "Read the manual once she reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-once-she",
            ),
            pytest.param(
                "Read the manual once they report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-once-they",
            ),
            pytest.param(
                "Read the manual once Alex reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-once-proper-name",
            ),
            pytest.param(
                "Read the manual once I report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-once-i",
            ),
            pytest.param(
                "Read the manual once we report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-once-we",
            ),
            pytest.param(
                "Read the manual once you report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-once-you",
            ),
            pytest.param(
                "Read the manual as the foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-as-foreman",
            ),
            pytest.param(
                "Read the manual as the technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-as-technician",
            ),
            pytest.param(
                "Read the manual as he reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-as-he",
            ),
            pytest.param(
                "Read the manual as she reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-as-she",
            ),
            pytest.param(
                "Read the manual as they report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-as-they",
            ),
            pytest.param(
                "Read the manual as Alex reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-as-proper-name",
            ),
            pytest.param(
                "Read the manual as I report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-as-i",
            ),
            pytest.param(
                "Read the manual as we report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-as-we",
            ),
            pytest.param(
                "Read the manual as you report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-as-you",
            ),
            pytest.param(
                "Read the manual since the foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-since-foreman",
            ),
            pytest.param(
                "Read the manual since the technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-since-technician",
            ),
            pytest.param(
                "Read the manual since he reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-since-he",
            ),
            pytest.param(
                "Read the manual since she reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-since-she",
            ),
            pytest.param(
                "Read the manual since they report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-since-they",
            ),
            pytest.param(
                "Read the manual since Alex reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-since-proper-name",
            ),
            pytest.param(
                "Read the manual since I report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-since-i",
            ),
            pytest.param(
                "Read the manual since we report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-since-we",
            ),
            pytest.param(
                "Read the manual since you report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-since-you",
            ),
            pytest.param(
                "Read the manual until the foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-until-foreman",
            ),
            pytest.param(
                "Read the manual until the technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-until-technician",
            ),
            pytest.param(
                "Read the manual until he reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-until-he",
            ),
            pytest.param(
                "Read the manual until she reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-until-she",
            ),
            pytest.param(
                "Read the manual until they report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-until-they",
            ),
            pytest.param(
                "Read the manual until Alex reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-until-proper-name",
            ),
            pytest.param(
                "Read the manual until I report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-until-i",
            ),
            pytest.param(
                "Read the manual until we report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-until-we",
            ),
            pytest.param(
                "Read the manual until you report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-until-you",
            ),
            pytest.param(
                "How many cycles does the manual state?",
                "manual_fact",
                id="co1-source-fact-control",
            ),
        ],
    )
    def test_adjunct_reporter_source_public_boundary(self, monkeypatch, query, expected_intent):
        TestFinalRelativeSourceOwnership().test_relative_source_public_boundary(
            monkeypatch, query, expected_intent
        )


class TestClauseOwnershipDesignPreservation:
    """Preserve existing source content and caller boundaries during redesign."""

    @pytest.mark.parametrize(
        ("query", "expected_intent"),
        [
            pytest.param(
                "How many pumps that the manual describes were repaired last month?",
                "fleet_aggregate",
                id="co1-preserve-p1-relative-attributive-event",
            ),
            pytest.param(
                "How many pumps that the manual reports were repaired last month?",
                "manual_fact",
                id="co1-preserve-p1-relative-reporting-content",
            ),
            pytest.param(
                "How many pumps does the manual report as repaired?",
                "manual_fact",
                id="co1-preserve-p1-source-reported-event",
            ),
            pytest.param(
                "Read the manual and report how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co1-preserve-p2-independent-event-count",
            ),
            pytest.param(
                "Read the manual and report what it reports.",
                "manual_fact",
                id="co1-preserve-p2-quoted-source-content",
            ),
            pytest.param(
                "How many cycles do the manual and the datasheet report?",
                "manual_fact",
                id="co1-preserve-p3-coordinated-source-subject",
            ),
            pytest.param(
                "How many tested cycles does the manual report?",
                "manual_fact",
                id="co1-preserve-p4-source-counted-tested-cycles",
            ),
        ],
    )
    def test_preserved_source_content_boundary(self, monkeypatch, query, expected_intent):
        TestFinalRelativeSourceOwnership().test_relative_source_public_boundary(
            monkeypatch, query, expected_intent
        )


class TestBoundedClauseOwnershipMorphology:
    """A non-source NP does not become a source predicate by word suffix."""

    @pytest.mark.parametrize(
        ("query", "expected_intent"),
        [
            pytest.param(
                "Read the manual while the acting foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-while-acting",
            ),
            pytest.param(
                "Read the manual while the retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-while-retired",
            ),
            pytest.param(
                "Read the manual while Fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-while-name-ed",
            ),
            pytest.param(
                "Read the manual when the acting foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-when-acting",
            ),
            pytest.param(
                "Read the manual when the retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-when-retired",
            ),
            pytest.param(
                "Read the manual when Fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-when-name-ed",
            ),
            pytest.param(
                "Read the manual before the acting foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-before-acting",
            ),
            pytest.param(
                "Read the manual before the retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-before-retired",
            ),
            pytest.param(
                "Read the manual before Fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-before-name-ed",
            ),
            pytest.param(
                "Read the manual after the acting foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-after-acting",
            ),
            pytest.param(
                "Read the manual after the retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-after-retired",
            ),
            pytest.param(
                "Read the manual after Fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-after-name-ed",
            ),
            pytest.param(
                "Read the manual once the acting foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-once-acting",
            ),
            pytest.param(
                "Read the manual once the retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-once-retired",
            ),
            pytest.param(
                "Read the manual once Fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-once-name-ed",
            ),
            pytest.param(
                "Read the manual as the acting foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-as-acting",
            ),
            pytest.param(
                "Read the manual as the retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-as-retired",
            ),
            pytest.param(
                "Read the manual as Fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-as-name-ed",
            ),
            pytest.param(
                "Read the manual since the acting foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-since-acting",
            ),
            pytest.param(
                "Read the manual since the retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-since-retired",
            ),
            pytest.param(
                "Read the manual since Fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-since-name-ed",
            ),
            pytest.param(
                "Read the manual until the acting foreman reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-until-acting",
            ),
            pytest.param(
                "Read the manual until the retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-until-retired",
            ),
            pytest.param(
                "Read the manual until Fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co2-until-name-ed",
            ),
        ],
    )
    def test_subject_morphology_public_boundary(self, monkeypatch, query, expected_intent):
        TestFinalRelativeSourceOwnership().test_relative_source_public_boundary(
            monkeypatch, query, expected_intent
        )


class TestBoundedClauseOwnershipCaseAndModifiers:
    """Casing and harmless NP modifiers do not change grammatical ownership."""

    @pytest.mark.parametrize(
        ("query", "expected_intent"),
        [
            pytest.param(
                "Read the manual while fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-while-lowercase-name",
            ),
            pytest.param(
                "Read the manual while the senior retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-while-senior-retired",
            ),
            pytest.param(
                "Read the manual while the recently retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-while-recently-retired",
            ),
            pytest.param(
                "Read the manual when fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-when-lowercase-name",
            ),
            pytest.param(
                "Read the manual when the senior retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-when-senior-retired",
            ),
            pytest.param(
                "Read the manual when the recently retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-when-recently-retired",
            ),
            pytest.param(
                "Read the manual before fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-before-lowercase-name",
            ),
            pytest.param(
                "Read the manual before the senior retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-before-senior-retired",
            ),
            pytest.param(
                "Read the manual before the recently retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-before-recently-retired",
            ),
            pytest.param(
                "Read the manual after fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-after-lowercase-name",
            ),
            pytest.param(
                "Read the manual after the senior retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-after-senior-retired",
            ),
            pytest.param(
                "Read the manual after the recently retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-after-recently-retired",
            ),
            pytest.param(
                "Read the manual once fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-once-lowercase-name",
            ),
            pytest.param(
                "Read the manual once the senior retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-once-senior-retired",
            ),
            pytest.param(
                "Read the manual once the recently retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-once-recently-retired",
            ),
            pytest.param(
                "Read the manual as fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-as-lowercase-name",
            ),
            pytest.param(
                "Read the manual as the senior retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-as-senior-retired",
            ),
            pytest.param(
                "Read the manual as the recently retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-as-recently-retired",
            ),
            pytest.param(
                "Read the manual since fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-since-lowercase-name",
            ),
            pytest.param(
                "Read the manual since the senior retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-since-senior-retired",
            ),
            pytest.param(
                "Read the manual since the recently retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-since-recently-retired",
            ),
            pytest.param(
                "Read the manual until fred reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-until-lowercase-name",
            ),
            pytest.param(
                "Read the manual until the senior retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-until-senior-retired",
            ),
            pytest.param(
                "Read the manual until the recently retired technician reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co3-until-recently-retired",
            ),
        ],
    )
    def test_actor_np_case_and_modifiers_public_boundary(self, monkeypatch, query, expected_intent):
        TestFinalRelativeSourceOwnership().test_relative_source_public_boundary(
            monkeypatch, query, expected_intent
        )


class TestSourceOwnershipStructuralRegressions:
    """PP subjects and genuine source qualifiers need grammatical ownership."""

    @pytest.mark.parametrize(
        ("query", "expected_intent"),
        [
            pytest.param(
                "How many pumps does the manual after the revision list?",
                "manual_fact",
                id="co4-mi1-temporal-PP",
            ),
            pytest.param(
                "How many cycles does the datasheet while undergoing revision report?",
                "manual_fact",
                id="co4-mi2-bare-reduced",
            ),
            pytest.param(
                "How many cycles does the manual when recently updated state?",
                "manual_fact",
                id="co4-s3g-adverb-bare",
            ),
            pytest.param(
                "Read the manual after Fred by the pump reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co4-s3g-after-name-PP",
            ),
            pytest.param(
                "Read the manual as Fred by the pump reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co4-s3g-as-name-PP",
            ),
            pytest.param(
                "How many cycles does the manual when inspected state?",
                "manual_fact",
                id="co4-s3g-bare-inspected",
            ),
            pytest.param(
                "How many cycles does the manual once revised state?",
                "manual_fact",
                id="co4-s3g-bare-revised",
            ),
            pytest.param(
                "How many cycles does the manual when updated state?",
                "manual_fact",
                id="co4-s3g-bare-updated",
            ),
            pytest.param(
                "Read the manual before Fred by the pump reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co4-s3g-before-name-PP",
            ),
            pytest.param(
                "Read the manual once Fred by the pump reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co4-s3g-once-name-PP",
            ),
            pytest.param(
                "How many cycles does the manual once revised report?",
                "manual_fact",
                id="co4-s3g-revised-report",
            ),
            pytest.param(
                "Read the manual since Fred by the pump reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co4-s3g-since-name-PP",
            ),
            pytest.param(
                "Read the manual until Fred by the pump reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co4-s3g-until-name-PP",
            ),
            pytest.param(
                "Read the manual when Fred by the pump reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co4-s3g-when-name-PP",
            ),
            pytest.param(
                "Read the manual while Fred by the pump reports how many pumps were repaired last month.",
                "fleet_aggregate",
                id="co4-s3g-while-name-PP",
            ),
        ],
    )
    def test_structural_source_public_boundary(self, monkeypatch, query, expected_intent):
        TestFinalRelativeSourceOwnership().test_relative_source_public_boundary(
            monkeypatch, query, expected_intent
        )
