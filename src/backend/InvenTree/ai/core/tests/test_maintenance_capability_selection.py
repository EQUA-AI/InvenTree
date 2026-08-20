"""Work-order questions must reach maintenance tools, on voice and on text.

The maintenance twin of ``test_machine_capability_selection.py``: the pack is
selectable from static vocabulary alone, a spoken work-order reference routes
without a lexicon, the tools survive the voice read-only fence, and both
modalities are offered the same set. The manuals pack rides along because a
repair question routinely ends at "what does the manual say".
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest  # noqa: E402
from ai.core.integrations.inventory_tools import INVENTORY_READ_TOOLS  # noqa: E402
from ai.core.tools import capabilities  # noqa: E402
from ai.core.tools.capabilities import (  # noqa: E402
    MAX_INITIAL_TOOLS,
    PolicyKind,
    ToolEffect,
    capability_catalog,
    select_capabilities,
    tool_name,
)
from ai.core.tools.inventree.read.maintenance import MAINTENANCE_READ_TOOLS  # noqa: E402
from ai.core.tools.rbac import read_tools  # noqa: E402

MAINTENANCE_TOOL_IDS = frozenset(tool_name(tool) for tool in MAINTENANCE_READ_TOOLS)

#: The five maintenance read tools the catalog must carry, by contract.
EXPECTED_MAINTENANCE_TOOL_IDS = frozenset({
    "search_work_orders",
    "get_work_order_overview",
    "get_work_order_readiness",
    "get_work_order_repair_state",
    "get_open_repairs_for_machine",
    "get_work_order_history",
})

PROFILE = frozenset({
    ("work_order", "view"),
    ("part", "view"),
    ("stock", "view"),
})


@pytest.fixture(autouse=True)
def _pinned_lexicons(monkeypatch):
    """Pin both lexicons empty so selection never depends on live data.

    The maintenance assertions must prove the *static* pack vocabulary and the
    work-order reference regex route a job question, not lean on whatever
    machines or categories happen to exist.
    """
    monkeypatch.setattr(capabilities, "category_lexicon", frozenset)
    monkeypatch.setattr(capabilities, "machine_lexicon", frozenset)


def test_maintenance_tools_are_on_the_shared_read_surface():
    """Voice and the lookup agent are both built from this list."""
    for tool in MAINTENANCE_READ_TOOLS:
        assert tool in INVENTORY_READ_TOOLS


def test_catalog_carries_exactly_the_five_maintenance_tools():
    """The pack is the six ai_read delegates -- no more, no fewer."""
    pack_tool_ids = {
        entry.tool_id for entry in capability_catalog() if entry.pack_id == "maintenance.read"
    }
    assert pack_tool_ids == EXPECTED_MAINTENANCE_TOOL_IDS
    assert MAINTENANCE_TOOL_IDS == EXPECTED_MAINTENANCE_TOOL_IDS


def test_every_maintenance_tool_is_read_effect():
    """A read rail must not acquire a write tool by accident."""
    catalog = {entry.tool_id: entry for entry in capability_catalog()}
    for tool_id in MAINTENANCE_TOOL_IDS:
        assert catalog[tool_id].effect is ToolEffect.READ, tool_id


def test_maintenance_tools_require_a_resource_authorizer_not_just_a_role():
    """A work_order:view grant is global; work-order rows belong to tenants."""
    catalog = {entry.tool_id: entry for entry in capability_catalog()}
    for tool_id in MAINTENANCE_TOOL_IDS:
        policy = catalog[tool_id].authorization
        assert policy.kind is PolicyKind.RESOURCE_AUTHORIZER, tool_id
        assert policy.authorizer == "machine_scope_access", tool_id
        assert ("work_order", "view") in policy.all_of, tool_id


@pytest.mark.parametrize(
    "question",
    [
        "what is the status of work order 12",
        "is wo-104 ready to start?",
        "what are the findings for the repair?",
        "is WO-WW-R-001 ready to start?",
    ],
)
def test_a_spoken_work_order_question_selects_maintenance_tools(question):
    """Static vocabulary plus the reference regex -- no lexicon involved."""
    selected = select_capabilities(question, profile=PROFILE, authenticated=True)

    assert selected.pack_ids, question
    assert selected.pack_ids[0] == "maintenance.read", (question, selected.pack_ids)
    assert MAINTENANCE_TOOL_IDS & set(selected.tool_ids), question
    assert not selected.clarification_required, question


@pytest.mark.parametrize(
    "question",
    [
        # Both the plain scheme and a hyphenated site scheme must match: the
        # regex is the only router a reference has once lexicons are empty.
        "is wo-104 ready to start?",
        "is WO-WW-R-001 ready to start?",
    ],
)
def test_a_work_order_reference_fires_the_reference_signal(question):
    """A patterned reference routes by regex and says so in the signals."""
    selected = select_capabilities(question, profile=PROFILE, authenticated=True)
    assert "maintenance.read" in selected.pack_ids, question
    assert "workorder_reference" in selected.signals, (question, selected.signals)


def test_build_orders_stay_on_the_build_pack():
    """On this fork a work order is maintenance; a build order is not."""
    selected = select_capabilities(
        "show me the open build orders", profile=PROFILE, authenticated=True
    )
    assert selected.pack_ids
    assert selected.pack_ids[0] == "build.read", selected.pack_ids
    assert "maintenance.read" not in selected.pack_ids, selected.pack_ids


def test_completing_a_work_order_requires_a_specialist():
    """The read broker must hand writes to the governed rail, not a tool."""
    selected = select_capabilities("complete the work order", profile=PROFILE, authenticated=True)
    assert selected.requires_specialist
    assert not selected.tool_ids


def test_job_questions_stay_on_the_kanban_pack():
    """ "Jobs" is board phrasing; the board pack leads even with adjacency."""
    selected = select_capabilities("check all jobs", profile=PROFILE, authenticated=True)
    assert selected.pack_ids
    assert selected.pack_ids[0] == "kanban.read", selected.pack_ids


def test_a_stock_breakdown_is_not_a_maintenance_question():
    """ "Breakdown" is analytics phrasing, deliberately absent from the pack."""
    selected = select_capabilities(
        "give me a breakdown of stock by category", profile=PROFILE, authenticated=True
    )
    assert "maintenance.read" not in selected.pack_ids, selected.pack_ids


def test_maintenance_selection_fits_the_initial_tool_budget():
    """A pack that is always trimmed away is a pack that never runs."""
    selected = select_capabilities(
        "what is the status of work order 12", profile=PROFILE, authenticated=True
    )
    assert selected.pack_ids[0] == "maintenance.read"
    assert len(selected.tool_ids) <= MAX_INITIAL_TOOLS
    # The whole pack survives, so a follow-up drill-down has its tool present.
    assert MAINTENANCE_TOOL_IDS.issubset(set(selected.tool_ids))


def test_maintenance_tools_survive_the_voice_read_only_fence():
    """Voice is a first-class surface for job questions; it must keep these."""
    voice_surface = read_tools(tuple(INVENTORY_READ_TOOLS))
    for tool in MAINTENANCE_READ_TOOLS:
        assert tool in voice_surface, tool_name(tool)


def test_maintenance_tools_are_offered_to_voice_and_text_identically():
    """ "Both voice and text" is the requirement; assert it, do not assume it."""
    catalog = {entry.tool_id: entry for entry in capability_catalog()}
    for tool_id in MAINTENANCE_TOOL_IDS:
        entry = catalog[tool_id]
        assert "voice" in entry.modalities, tool_id
        assert "text" in entry.modalities, tool_id


def test_maintenance_tools_are_withheld_without_the_work_order_role():
    """No role, no visibility -- before scope is even consulted."""
    selected = select_capabilities(
        "is wo-104 ready to start?",
        profile=frozenset({("part", "view")}),
        authenticated=True,
    )
    assert not MAINTENANCE_TOOL_IDS & set(selected.tool_ids)


def test_maintenance_tools_are_withheld_from_an_unauthenticated_turn():
    """The broker fails closed before any tool is named."""
    selected = select_capabilities(
        "is wo-104 ready to start?", profile=PROFILE, authenticated=False
    )
    assert not MAINTENANCE_TOOL_IDS & set(selected.tool_ids)


def test_a_manual_question_selects_the_manuals_pack():
    """Controlled documentation is its own pack, led by "manual" phrasing."""
    selected = select_capabilities(
        "what does the manual say about seal replacement?",
        profile=PROFILE,
        authenticated=True,
    )
    assert selected.pack_ids
    assert selected.pack_ids[0] == "manuals.read", selected.pack_ids
    assert "search_manuals" in selected.tool_ids


def test_search_manuals_uses_the_controlled_corpus_authorizer():
    """The corpus gate is its own branch, not the machine-scope check."""
    catalog = {entry.tool_id: entry for entry in capability_catalog()}
    policy = catalog["search_manuals"].authorization
    assert policy.kind is PolicyKind.RESOURCE_AUTHORIZER
    assert policy.authorizer == "controlled_corpus_access"
    assert ("work_order", "view") in policy.all_of


def test_maintenance_pack_is_not_a_sql_escape_hatch():
    """query_database applies no maintenance scope, so it must not stand in.

    Letting the SQL fallback answer a work-order question would hand the model
    an unscoped path around every gate tasks.ai_read enforces.
    """
    assert "maintenance.read" not in capabilities._SQL_HATCH_PACKS
    assert "manuals.read" not in capabilities._SQL_HATCH_PACKS


@pytest.mark.parametrize(
    "question",
    [
        # The flagship phrasing: a manuals term plus repair/maintenance terms.
        # maintenance.read wins the primary and max_adjacent used to drop the
        # single-tool manuals pack the user NAMED — the model then claimed it
        # had no documentation search at all (live, 2026-08-06).
        "What does the manual say about the influent pump station's repair boundaries?",
        "Search the manuals for repair boundaries",
        "what does the maintenance manual say about seal replacement?",
    ],
)
def test_an_explicit_manuals_term_always_carries_search_manuals(question):
    """Naming the manuals guarantees the manuals pack rides along."""
    selected = select_capabilities(question, profile=PROFILE, authenticated=True)

    assert "search_manuals" in set(selected.tool_ids), (question, selected.pack_ids)


# ---------------------------------------------------------------------------
# R2: search_attachment_docs joins documents.read behind its retrieval flag
# ---------------------------------------------------------------------------


def test_manuals_pack_stays_single_tool():
    """The manuals stack is budgeted exactly at MAX_INITIAL_TOOLS with ONE
    tool; the attachment tool must live in documents.read, never here."""
    _effect, tool_ids, _terms = capabilities._PACK_SPECS["manuals.read"]
    assert tool_ids == ("search_manuals",)
    assert "search_attachment_docs" in capabilities._PACK_SPECS["documents.read"][1]


def test_attachment_docs_is_dark_by_default():
    """Flag off: the catalog policy is DISABLED, so the tool is never
    model-visible regardless of pack selection."""
    from ai.core.tools.capabilities import exposure_authorized

    catalog = {entry.tool_id: entry for entry in capability_catalog()}
    entry = catalog["search_attachment_docs"]
    assert entry.pack_id == "documents.read"
    assert entry.authorization.kind is PolicyKind.DISABLED
    assert not exposure_authorized(entry, PROFILE, authenticated=True)


def test_datasheet_phrasing_carries_the_attachment_tool_when_lit(monkeypatch):
    """Flag on: documents.read selection includes the new tool for a profile
    holding either arm role."""
    from types import SimpleNamespace

    from ai.core import config as ai_config
    from ai.core.tools.capabilities import exposure_authorized

    monkeypatch.setattr(
        ai_config,
        "get_settings",
        lambda: SimpleNamespace(
            feature_attachment_rag_retrieval=True,
            single_site_policy_key="site-a",
        ),
    )
    capability_catalog.cache_clear()
    try:
        selected = select_capabilities(
            "what do the uploaded documents say about the HX-200 gasket?",
            profile=PROFILE,
            authenticated=True,
        )
        assert "documents.read" in selected.pack_ids
        assert "search_attachment_docs" in selected.tool_ids
        catalog = {entry.tool_id: entry for entry in capability_catalog()}
        assert exposure_authorized(catalog["search_attachment_docs"], PROFILE, authenticated=True)
        # any_of: a single arm role suffices for exposure.
        assert exposure_authorized(
            catalog["search_attachment_docs"],
            frozenset({("part", "view")}),
            authenticated=True,
        )
        assert not exposure_authorized(
            catalog["search_attachment_docs"],
            frozenset({("stock", "view")}),
            authenticated=True,
        )
    finally:
        capability_catalog.cache_clear()
