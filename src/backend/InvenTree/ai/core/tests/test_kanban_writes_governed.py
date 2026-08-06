"""Regression: the direct-ORM kanban write path no longer exists at all.

S12 step 3 (after the governed-flag soak) DELETED the seven write tools and
their ``kanban.write`` capability pack: board mutations from any AI surface go
through the governed proposal rail and the REST surface only. These tests pin
the invariant by absence — stronger than the flag-driven DISABLED policy they
replace, because there is no configuration in which the bypass can return.
"""

# ruff: noqa: E402

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import ai.core.integrations.kanban_tools as kanban_tools
from ai.core.tools.capabilities import capability_catalog

#: Every tool id the retired bypass exposed, including the withheld delete.
RETIRED_WRITE_TOOL_IDS = (
    "create_kanban_card",
    "update_kanban_card",
    "move_kanban_card",
    "archive_kanban_card",
    "restore_kanban_card",
    "add_parts_to_kanban_card",
    "remove_part_from_kanban_card",
    "delete_kanban_card",
)

READ_TOOL_IDS = (
    "list_kanban_cards",
    "get_kanban_card",
    "get_kanban_summary",
    "check_kanban_card_stock",
)


def _tool_name(tool) -> str:
    return getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))


def test_kanban_module_exports_reads_only() -> None:
    """KANBAN_TOOLS is exactly the read set; no write function exists."""
    names = {_tool_name(tool) for tool in kanban_tools.KANBAN_TOOLS}
    assert names == set(READ_TOOL_IDS)
    for retired in RETIRED_WRITE_TOOL_IDS:
        assert not hasattr(kanban_tools, retired), (
            f"{retired} exists again; board writes must go through the "
            "governed proposal rail, never a direct-ORM tool"
        )


def test_no_capability_pack_carries_a_kanban_write() -> None:
    """The kanban.write pack is gone and no entry smuggles the ids back in."""
    for entry in capability_catalog():
        assert entry.pack_id != "kanban.write"
        assert entry.tool_id not in RETIRED_WRITE_TOOL_IDS, (
            f"{entry.tool_id} reappeared in pack {entry.pack_id}"
        )


def test_text_chat_union_has_no_kanban_writes() -> None:
    """The voice gate builds from this union; it must be structurally clean."""
    from ai.core.voice.tool_actions import text_chat_tools

    names = {_tool_name(tool) for tool in text_chat_tools()}
    for tool_id in RETIRED_WRITE_TOOL_IDS:
        assert tool_id not in names
    for read_id in READ_TOOL_IDS:
        assert read_id in names
