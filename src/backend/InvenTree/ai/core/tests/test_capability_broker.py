"""Contract tests for the RBAC-first capability broker."""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ai.core.integrations.attachment_corpus import ATTACHMENT_CORPUS_TOOLS
from ai.core.integrations.controlled_document_corpus import CONTROLLED_CORPUS_TOOLS
from ai.core.integrations.document_search import DOCUMENT_SEARCH_TOOLS
from ai.core.integrations.email.tools import EMAIL_TOOLS
from ai.core.integrations.inventory_tools import INVENTORY_READ_TOOLS
from ai.core.integrations.kanban_tools import KANBAN_READ_TOOLS, KANBAN_TOOLS
from ai.core.integrations.media_corpus import EVIDENCE_MEDIA_TOOLS
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
    wf8_tools = tuple(
        INVENTORY_READ_TOOLS
        + EMAIL_TOOLS
        + KANBAN_TOOLS
        + DOCUMENT_SEARCH_TOOLS
        + CONTROLLED_CORPUS_TOOLS
        + ATTACHMENT_CORPUS_TOOLS
        + EVIDENCE_MEDIA_TOOLS
        + SOURCE_INVENTORY_TOOLS
    )
    catalog = capability_catalog()

    # 61 wf8 tools (delete_kanban_card is withheld) + the specialist writes
    # wf2/wf3/wf4/wf6 carry: parts/stock/company/sales writes and the nine
    # purchase-order write tools. R2 added search_attachment_docs; R3 added
    # search_evidence_media; S8a added list_document_sources.
    assert len(wf8_tools) == 59
    assert len(catalog) == 95
    assert tuple(entry.tool for entry in catalog[:59]) == wf8_tools
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
        "parts.read": 5,
        "stock.read": 6,
        "bom.read": 2,
        # 3 since R2: search_attachment_docs joins the legacy pair; the pack
        # relaxes back to 2 when search_part_documents is unwired at R5.
        "documents.read": 3,
        "procurement.read": 5,
        "sales.read": 4,
        "build.read": 3,
        "analytics.read": 2,
        # Every machine-page tab, plus search (spoken-name resolution) and the
        # signal trend: the machine detail page is unreachable without them.
        "machines.read": 9,
        "email.read": 3,
        "email.write": 3,
        "kanban.read": 4,
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
        permission = "view" if tool in EMAIL_TOOLS[:3] else "send"
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
        "search_part_documents",
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
    """Dark by default; flag-on grants the any_of two-arm authorizer policy.

    The catalog is process-cached, so both directions clear it -- and the
    teardown clear keeps the flag-on catalog from leaking into other tests.
    """
    from ai.core import config as ai_config

    policies = {entry.tool_id: entry.authorization for entry in capability_catalog()}
    dark = policies["search_attachment_docs"]
    assert dark.kind is PolicyKind.DISABLED
    assert "FEATURE_ATTACHMENT_RAG_RETRIEVAL" in (dark.reason or "")

    real_settings = ai_config.get_settings()
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
    """Dark by default; flag-on grants the single-arm work_order authorizer.

    all_of, not any_of: one role grants the whole evidence corpus -- every
    owner type is an evidence surface under maintenance scope, and part-owned
    media never ingests, so there is no second arm.
    """
    from ai.core import config as ai_config

    policies = {entry.tool_id: entry.authorization for entry in capability_catalog()}
    dark = policies["search_evidence_media"]
    assert dark.kind is PolicyKind.DISABLED
    assert "FEATURE_MEDIA_RAG_RETRIEVAL" in (dark.reason or "")

    real_settings = ai_config.get_settings()
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
    # Matches the catalog pin: 93 entries (wf8 57 + specialist writes + packs).
    assert len(first) == 95
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
    # 17 = maintenance(7, +closeout S5b) + machines(9) + SQL(1) after the
    # manuals-rider adjacency change kept manuals reachable from a
    # maintenance primary.
    assert worst == 17


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
