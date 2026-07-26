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
    assert "archive_kanban_card" not in voice_allowed  # needs kanban:change


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
