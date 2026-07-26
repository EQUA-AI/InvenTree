"""Regression: the AI agent cannot hard-delete a work order.

``delete_kanban_card`` performs a bare ``card.delete()``. ``KanbanCard`` cascades to
``WorkOrderEvent``, ``WorkOrderCommand``, ``WorkOrderCloseout``, ``WorkOrderDeviation``,
``CloseoutPartUsage`` and ``CloseoutReading``, so a single call destroys the governance
and closeout history of completed work. It also applies no customer scope, unlike the
REST work-order surface which uses ``scope_for_actor``.

The tool stays defined -- admin and ORM deletion are unaffected, and deletion returns
later as a governed command -- but it must not be reachable by the agent. Two
independent mechanisms enforce that, and both are asserted here so neither can be
removed silently:

1. it is absent from ``KANBAN_TOOLS``, so no workflow offers it; and
2. ``_WITHHELD_TOOLS`` keeps it ``DISABLED`` in the capability catalog, which denies
   both schema exposure and invocation if it is ever re-added to that list.
"""

from __future__ import annotations

import ai.core.integrations.kanban_tools as kanban_tools
import pytest
from ai.core.integrations.kanban_tools import KANBAN_READ_TOOLS, KANBAN_TOOLS
from ai.core.tools.capabilities import (
    PolicyKind,
    _authorization_policy,
    _pack_index,
    capability_catalog,
    tool_name,
)
from ai.core.tools.rbac import _filter_map_cached, filter_tools

DELETE_TOOL_ID = "delete_kanban_card"

#: A profile holding every work-order permission -- the most privileged
#: non-superuser case the list filter can see. Kanban cards are work orders, so
#: these are the InvenTree WORK_ORDER ruleset pairs, not an AIMMS-native group.
FULL_KANBAN_PROFILE = frozenset({
    ("work_order", "view"),
    ("work_order", "add"),
    ("work_order", "change"),
    ("work_order", "delete"),
})


def test_delete_kanban_card_is_withheld_from_the_agent():
    """The primary control: the tool is in no list any workflow offers."""
    assert DELETE_TOOL_ID not in {tool_name(tool) for tool in KANBAN_TOOLS}
    assert DELETE_TOOL_ID not in {tool_name(tool) for tool in KANBAN_READ_TOOLS}


def test_delete_kanban_card_is_absent_from_the_capability_catalog():
    assert DELETE_TOOL_ID not in {entry.tool_id for entry in capability_catalog()}


def test_full_kanban_permissions_still_do_not_offer_delete():
    """Even the most privileged profile is not offered the tool."""
    offered = filter_tools(KANBAN_TOOLS, FULL_KANBAN_PROFILE)

    assert DELETE_TOOL_ID not in {tool_name(tool) for tool in offered}
    # The filter is permissive by default -- an unmapped tool passes through -- so
    # this assertion is only meaningful because absence from the list is the control.
    assert offered, "expected the profile to be offered the remaining kanban tools"


def test_soft_delete_remains_available():
    """Withholding hard delete must not remove the legitimate alternative."""
    offered = {tool_name(tool) for tool in filter_tools(KANBAN_TOOLS, FULL_KANBAN_PROFILE)}

    assert "archive_kanban_card" in offered
    assert "restore_kanban_card" in offered


def test_readding_the_tool_would_still_be_denied():
    """The backstop: re-adding it to KANBAN_TOOLS does not re-expose it.

    ``PolicyKind.DISABLED`` is refused by both ``_is_exposed`` (schema) and the
    invocation guard (``policy_disabled``), so the failure mode of someone restoring
    the list entry is a denied tool rather than a live hard delete.
    """
    policy = _authorization_policy(kanban_tools.delete_kanban_card, DELETE_TOOL_ID)

    assert policy.kind is PolicyKind.DISABLED
    assert policy.reason
    assert "governed" in policy.reason


def test_pack_spec_does_not_reference_the_withheld_tool():
    """Catalog construction rejects packs naming tools it does not register.

    So withholding the tool requires removing its pack entry too. Restoring exposure
    means restoring *both* the ``KANBAN_TOOLS`` entry and the pack entry -- and even
    then ``_WITHHELD_TOOLS`` keeps the policy ``DISABLED``. Partially restoring one
    of the two raises at catalog build, which is loud rather than silent.
    """
    assert DELETE_TOOL_ID not in _pack_index()


def test_rbac_mapping_is_retained():
    """The permission mapping stays, so the tool is not silently unmapped later."""
    assert _filter_map_cached()[kanban_tools.delete_kanban_card] == ("work_order", "delete")


def test_the_agent_prompt_does_not_advertise_delete():
    """A tool the agent cannot call must not appear in its instructions."""
    from ai.core.workflows import wf8_lookup

    source = wf8_lookup.__doc__ or ""
    prompts = [
        value
        for name, value in vars(wf8_lookup).items()
        if isinstance(value, str) and not name.startswith("__")
    ]

    assert all(DELETE_TOOL_ID not in text for text in [*prompts, source])


@pytest.mark.parametrize(
    "tool_id",
    ["create_kanban_card", "update_kanban_card", "move_kanban_card"],
)
def test_other_write_tools_are_unaffected(tool_id):
    """Scope check: this change withholds one tool, not the write surface."""
    assert tool_id in {tool_name(tool) for tool in KANBAN_TOOLS}
