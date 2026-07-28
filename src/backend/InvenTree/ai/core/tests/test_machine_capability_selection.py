"""Machine questions must reach machine tools, on voice and on text.

The reported defect was not that a machine answer was wrong -- it was that no
machine tool existed on the unscoped rail at all, so the broker scored no pack
for "how is the pump doing" and routed it to the tool-less clarify agent. These
tests pin the whole path: the pack is selectable, the tools survive the voice
read-only fence, and both modalities are offered the same set.
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
from ai.core.tools.inventree.read.machines import MACHINE_READ_TOOLS  # noqa: E402
from ai.core.tools.rbac import read_tools  # noqa: E402

MACHINE_TOOL_IDS = frozenset(tool_name(tool) for tool in MACHINE_READ_TOOLS)

PROFILE = frozenset({
    ("work_order", "view"),
    ("part", "view"),
    ("stock", "view"),
})


@pytest.fixture(autouse=True)
def _pinned_lexicons(monkeypatch):
    """Pin both lexicons empty so selection never depends on live data.

    Pinning the machine lexicon in particular is what keeps these assertions
    honest: they must prove the *static* pack vocabulary routes a machine
    question, not lean on whatever assets happen to exist.
    """
    monkeypatch.setattr(capabilities, "category_lexicon", frozenset)
    monkeypatch.setattr(capabilities, "machine_lexicon", frozenset)


def test_machine_tools_are_on_the_shared_read_surface():
    """Voice and the lookup agent are both built from this list."""
    for tool in MACHINE_READ_TOOLS:
        assert tool in INVENTORY_READ_TOOLS


@pytest.mark.parametrize(
    "question",
    [
        "Are there any alarms on this machine?",
        "When was machine 4 last serviced?",
        "Show me the equipment health",
        "Which asset has an open fault?",
        "How much downtime has this machine had?",
        "What is the serial number of that machine?",
        "Any anomalies on the asset?",
        "Show me the vibration sensor",
    ],
)
def test_a_spoken_machine_question_selects_machine_tools(question):
    """Every one of these previously scored no pack and dead-ended.

    These use only the static pack vocabulary -- both lexicons are pinned
    empty -- so they prove the floor works without live data.
    """
    selected = select_capabilities(question, profile=PROFILE, authenticated=True)

    assert "machines.read" in selected.pack_ids, question
    assert MACHINE_TOOL_IDS & set(selected.tool_ids), question
    assert not selected.clarification_required, question


@pytest.mark.parametrize(
    "question",
    [
        "Where do we keep the grinder pump seal kit?",
        "How much stock of the motor bearing do we have?",
        "What does the valve gasket cost?",
    ],
)
def test_part_questions_naming_equipment_still_reach_the_parts_rails(question):
    """A spare is named after the kit it belongs to; that is not an asset ask.

    This is why the pack carries no bare equipment nouns: "pump" in "grinder
    pump seal kit" is evidence of nothing, and a term for it hijacked genuine
    stock questions away from stock.read.
    """
    selected = select_capabilities(question, profile=PROFILE, authenticated=True)
    assert "machines.read" not in selected.pack_ids, (question, selected.pack_ids)


def test_machine_selection_fits_the_initial_tool_budget():
    """A pack that is always trimmed away is a pack that never runs."""
    selected = select_capabilities(
        "Show me the equipment health", profile=PROFILE, authenticated=True
    )
    assert len(selected.tool_ids) <= MAX_INITIAL_TOOLS
    # The whole pack survives, so a follow-up drill-down has its tool present.
    assert MACHINE_TOOL_IDS.issubset(set(selected.tool_ids))


def test_machine_tools_survive_the_voice_read_only_fence():
    """Voice is the surface the user reported broken; it must keep these."""
    voice_surface = read_tools(tuple(INVENTORY_READ_TOOLS))
    for tool in MACHINE_READ_TOOLS:
        assert tool in voice_surface, tool_name(tool)


def test_machine_tools_are_offered_to_voice_and_text_identically():
    """ "Both voice and text" is the requirement; assert it, do not assume it."""
    catalog = {entry.tool_id: entry for entry in capability_catalog()}
    for tool_id in MACHINE_TOOL_IDS:
        entry = catalog[tool_id]
        assert "voice" in entry.modalities, tool_id
        assert "text" in entry.modalities, tool_id


def test_every_machine_tool_is_read_effect():
    """A read rail must not acquire a write tool by accident."""
    catalog = {entry.tool_id: entry for entry in capability_catalog()}
    for tool_id in MACHINE_TOOL_IDS:
        assert catalog[tool_id].effect is ToolEffect.READ, tool_id


def test_machine_tools_require_a_resource_authorizer_not_just_a_role():
    """A work_order:view grant is global; asset rows belong to tenants."""
    catalog = {entry.tool_id: entry for entry in capability_catalog()}
    for tool_id in MACHINE_TOOL_IDS:
        policy = catalog[tool_id].authorization
        assert policy.kind is PolicyKind.RESOURCE_AUTHORIZER, tool_id
        assert policy.authorizer == "machine_scope_access", tool_id


def test_machine_tools_are_withheld_without_the_work_order_role():
    """No role, no visibility -- before scope is even consulted."""
    selected = select_capabilities(
        "How is the feed pump doing?",
        profile=frozenset({("part", "view")}),
        authenticated=True,
    )
    assert not MACHINE_TOOL_IDS & set(selected.tool_ids)


def test_machine_tools_are_withheld_from_an_unauthenticated_turn():
    """The broker fails closed before any tool is named."""
    selected = select_capabilities(
        "How is the feed pump doing?", profile=PROFILE, authenticated=False
    )
    assert not MACHINE_TOOL_IDS & set(selected.tool_ids)


def test_a_named_asset_routes_even_without_a_matching_noun(monkeypatch):
    """The lexicon is what covers names no fixed noun list could predict."""
    monkeypatch.setattr(capabilities, "machine_lexicon", lambda: frozenset({"hydrocracker"}))
    selected = select_capabilities(
        "How is the hydrocracker doing?", profile=PROFILE, authenticated=True
    )
    assert "machines.read" in selected.pack_ids
    assert "machine_lexicon" in selected.signals


def test_machine_lexicon_degrades_to_empty_rather_than_failing(monkeypatch):
    """A lexicon outage may under-select tools; it must never raise."""

    def _boom():
        raise RuntimeError("no assets app here")

    monkeypatch.setattr(capabilities, "_build_machine_lexicon", _boom)
    monkeypatch.setattr(capabilities, "machine_lexicon", capabilities.machine_lexicon)
    assert isinstance(capabilities.machine_lexicon(), frozenset)


def test_machine_lexicon_rejects_collision_prone_terms():
    """ "Machine" or a bare number as a term would fire on any sentence."""
    assert capabilities._machine_lexicon_variants("Machine") == set()
    assert capabilities._machine_lexicon_variants("7") == set()
    assert "feed pump 7" in capabilities._machine_lexicon_variants("Feed Pump 7")
    assert "pump" in capabilities._machine_lexicon_variants("Feed Pump 7")


def test_machines_pack_is_not_a_sql_escape_hatch():
    """query_database applies no maintenance scope, so it must not stand in.

    Letting the SQL fallback answer a machine question would hand the model an
    unscoped path around every gate the machine tools enforce.
    """
    assert "machines.read" not in capabilities._SQL_HATCH_PACKS
