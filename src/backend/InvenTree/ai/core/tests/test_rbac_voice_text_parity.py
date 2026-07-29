"""Voice and text chat must grant exactly the same writes to the same person.

The design intent is that RBAC -- not the modality -- decides what a user may do:
someone with write privileges can use them through either surface, and someone
without gets the same refusal on both. This pins that, because the two paths
reach the decision differently: text filters the tool LIST before the model sees
it, while voice checks a capability STRING at proposal and again at execution.
Both must resolve through ``permission_profile``.

Profiles here are synthetic on purpose -- the guarantee is about the mapping from
a permission set to an allowed action set, not about any particular user's roles.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.tools.capabilities import tool_name  # noqa: E402
from ai.core.tools.rbac import (  # noqa: E402
    action_tools,
    filter_tools,
    tool_requirement,
)
from ai.core.voice.tool_actions import (  # noqa: E402
    capability_for_tool,
    text_chat_action_tools,
    text_chat_tools,
)

#: A stockroom technician: may adjust stock, may not create parts or send mail.
TECHNICIAN = frozenset({
    ("stock", "view"),
    ("stock", "change"),
    ("part", "view"),
    ("stock_location", "view"),
})
#: A planner: may raise purchase orders, may not touch stock.
PLANNER = frozenset({
    ("part", "view"),
    ("purchase_order", "view"),
    ("purchase_order", "add"),
})
#: Read-only: the account used for lookups.
VIEWER = frozenset({("part", "view"), ("stock", "view")})
NOBODY: frozenset[tuple[str, str]] = frozenset()


def _voice_allows(tool, profile) -> bool:
    """The question the voice gate asks: does the profile hold this capability?

    ``TextToolRBACVoicePermission`` resolves the profile for the actor and then
    performs exactly this membership test, so the comparison below is between
    the two surfaces' *decisions*, not their plumbing.
    """
    requirement = tool_requirement(tool)
    return requirement is not None and requirement in profile


@pytest.mark.parametrize("profile", [TECHNICIAN, PLANNER, VIEWER, NOBODY])
def test_voice_and_text_grant_the_same_write_actions(profile):
    """The core guarantee: same person, same permissions, same writes."""
    text_allowed = {
        tool_name(tool) for tool in action_tools(filter_tools(text_chat_tools(), profile))
    }
    voice_allowed = {
        tool_name(tool) for tool in text_chat_action_tools() if _voice_allows(tool, profile)
    }

    assert text_allowed == voice_allowed


def test_write_privileges_are_actually_usable_by_voice():
    """A user WITH write rights can use them -- RBAC grants, it does not only deny."""
    voice_allowed = {
        tool_name(tool) for tool in text_chat_action_tools() if _voice_allows(tool, TECHNICIAN)
    }

    assert "count_stock" in voice_allowed
    assert "remove_stock" in voice_allowed
    assert "transfer_stock" in voice_allowed


def test_privileges_are_scoped_not_blanket():
    """Holding one write right must not confer the others."""
    voice_allowed = {
        tool_name(tool) for tool in text_chat_action_tools() if _voice_allows(tool, TECHNICIAN)
    }

    assert "create_part" not in voice_allowed  # needs part:add
    assert "send_email" not in voice_allowed  # needs email:send
    assert "archive_kanban_card" not in voice_allowed  # needs work_order:change


def test_a_planner_gets_procurement_writes_and_no_stock_writes():
    voice_allowed = {
        tool_name(tool) for tool in text_chat_action_tools() if _voice_allows(tool, PLANNER)
    }

    assert "create_purchase_order" in voice_allowed
    assert not {"add_stock", "remove_stock", "count_stock"} & voice_allowed


@pytest.mark.parametrize("profile", [VIEWER, NOBODY])
def test_read_only_accounts_get_no_writes_on_either_surface(profile):
    text_allowed = action_tools(filter_tools(text_chat_tools(), profile))
    voice_allowed = [t for t in text_chat_action_tools() if _voice_allows(t, profile)]

    assert text_allowed == ()
    assert voice_allowed == []


def test_the_ai_is_never_more_permissive_than_inventree():
    """A tool must require the ruleset InvenTree itself governs the model with.

    Found against the live deployment: the 'allaccess' account holds no BOM
    access at all (roles API returns bom: null) yet the AI allowed it to add BOM
    items, because add_bom_item was mapped to part:change. InvenTree governs
    part_bomitem with the BOM ruleset (users/ruleset.py), so the AI was more
    permissive than the UI it fronts.
    """
    from ai.core.integrations import inventory_tools

    assert tool_requirement(inventory_tools.add_bom_item) == ("bom", "add")


def test_a_part_editor_without_bom_rights_cannot_change_the_bom():
    """The live 'allaccess' shape: broad part/stock writes, no BOM ruleset."""
    part_editor = frozenset({
        ("part", "view"),
        ("part", "add"),
        ("part", "change"),
        ("stock", "view"),
        ("stock", "change"),
    })

    voice_allowed = {
        tool_name(tool) for tool in text_chat_action_tools() if _voice_allows(tool, part_editor)
    }

    assert "update_part" in voice_allowed  # they really can edit parts
    assert "add_bom_item" not in voice_allowed  # ...but not the BOM


#: The live 'reader' account: view on most rulesets, write only on work orders.
WORK_ORDER_CREW = frozenset({
    ("part", "view"),
    ("stock", "view"),
    ("work_order", "view"),
    ("work_order", "add"),
    ("work_order", "change"),
})


def test_a_work_order_role_reaches_the_kanban_tools():
    """Kanban cards are work orders, so the WORK_ORDER ruleset must open them.

    Found against the live deployment: 'reader' holds work_order add/change/
    delete in InvenTree and can manage cards in the UI, but the AI offered it
    zero kanban tools -- they were gated on an invented 'kanban' capability
    backed by aimms.kanban.* Django groups that no migration ever creates. Only
    superusers passed, so in practice the whole surface was unreachable.
    """
    text_allowed = {tool_name(tool) for tool in filter_tools(text_chat_tools(), WORK_ORDER_CREW)}

    assert "list_kanban_cards" in text_allowed  # view
    assert "create_kanban_card" in text_allowed  # add
    assert "move_kanban_card" in text_allowed  # change


#: The six reads that landed with the maintenance/manuals packs. All are
#: mapped to work_order:view in rbac.py, so both surfaces must key on it.
MAINTENANCE_READ_NAMES = frozenset({
    "search_work_orders",
    "get_work_order_overview",
    "get_work_order_readiness",
    "get_work_order_repair_state",
    "get_open_repairs_for_machine",
    "search_manuals",
})


def test_the_work_order_role_gates_the_maintenance_reads():
    """work_order:view opens all six new reads; without it, none appear.

    The exclusion half is the load-bearing pin: filter_tools passes UNMAPPED
    tools through, so a read that leaked from the rbac map would silently reach
    every profile -- exactly what this asserts cannot happen.
    """
    crew_allowed = {tool_name(tool) for tool in filter_tools(text_chat_tools(), WORK_ORDER_CREW)}
    viewer_allowed = {tool_name(tool) for tool in filter_tools(text_chat_tools(), VIEWER)}

    assert crew_allowed >= MAINTENANCE_READ_NAMES
    assert not MAINTENANCE_READ_NAMES & viewer_allowed


def test_maintenance_reads_are_never_confirmation_gated_actions():
    """A read must not be an action tool: no write confirmation, no voice gate."""
    action_names = {tool_name(tool) for tool in text_chat_action_tools()}

    assert not MAINTENANCE_READ_NAMES & action_names


def test_work_order_writes_are_granular_not_blanket():
    """view alone must not confer add or change on work orders."""
    viewer = frozenset({("part", "view"), ("work_order", "view")})

    voice_allowed = {
        tool_name(tool) for tool in text_chat_action_tools() if _voice_allows(tool, viewer)
    }

    assert voice_allowed == set()

    #: delete is its own permission -- holding change must not grant it.
    assert tool_requirement(_kanban_tool("delete_kanban_card")) == ("work_order", "delete")
    assert not _voice_allows(_kanban_tool("delete_kanban_card"), WORK_ORDER_CREW)


def _kanban_tool(name: str):
    from ai.core.integrations import kanban_tools

    return getattr(kanban_tools, name)


def test_every_action_tool_states_a_capability():
    """No write may be reachable without a permission to check against."""
    unmapped = []
    for tool in text_chat_action_tools():
        try:
            capability_for_tool(tool)
        except ValueError:
            unmapped.append(tool_name(tool))

    assert unmapped == [], f"action tools with no RBAC requirement: {unmapped}"


def test_capability_string_round_trips_to_the_same_requirement():
    """The gate's capability string must mean the same pair text filters on."""
    for tool in text_chat_action_tools():
        requirement = tool_requirement(tool)
        assert requirement is not None
        assert capability_for_tool(tool) == f"{requirement[0]}:{requirement[1]}"
