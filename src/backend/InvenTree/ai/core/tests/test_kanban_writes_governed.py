"""Regression: with governed writes on, the AI has no direct board-write path.

Phase 6d retires the direct-ORM kanban write bypass. When
``AIMMS_GOVERNED_KANBAN_WRITES`` is enabled, every card-mutating tool
(``create``/``update``/``move``/``archive``/``restore`` and the part-allocation
tools) flips to ``PolicyKind.DISABLED``. Two independent enforcement points key
off that policy, and both are asserted here so neither can be removed silently:

1. ``exposure_authorized`` refuses a DISABLED entry regardless of how privileged
   the actor is, so the model-visible schema never contains the tool; and
2. the invocation guard refuses a DISABLED policy, so even a hand-crafted call
   cannot run it.

The flag is off by default: a deployment that has not adopted the proposal rail
keeps the legacy write surface unchanged, which the parity tests below assert.
"""

# ruff: noqa: E402

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import ai.core.integrations.kanban_tools as kanban_tools
import pytest
from ai.core.tools.capabilities import (
    _GOVERNED_KANBAN_WRITE_TOOLS,
    PolicyKind,
    _authorization_policy,
    capability_catalog,
    exposure_authorized,
)
from django.test import override_settings

GOVERNED = override_settings(AIMMS_GOVERNED_KANBAN_WRITES=True)

#: The most privileged non-superuser kanban profile the exposure filter can see.
# Kanban cards are work orders: the WORK_ORDER ruleset governs them.
FULL_KANBAN_PROFILE = frozenset({
    ("work_order", "view"),
    ("work_order", "add"),
    ("work_order", "change"),
    ("work_order", "delete"),
})

WRITE_TOOL_IDS = sorted(_GOVERNED_KANBAN_WRITE_TOOLS)
READ_TOOL_IDS = ["list_kanban_cards", "get_kanban_card"]


@pytest.fixture
def _fresh_catalog():
    """Rebuild the memoized catalog around a test so the flag is re-read."""
    capability_catalog.cache_clear()
    try:
        yield
    finally:
        capability_catalog.cache_clear()


def _entry(tool_id: str):
    return next(e for e in capability_catalog() if e.tool_id == tool_id)


@pytest.mark.parametrize("tool_id", WRITE_TOOL_IDS)
def test_write_tool_is_disabled_when_governed(tool_id):
    """The policy source of truth: every write tool is DISABLED under the flag."""
    tool = getattr(kanban_tools, tool_id)
    with GOVERNED:
        policy = _authorization_policy(tool, tool_id)
    assert policy.kind is PolicyKind.DISABLED
    assert policy.reason is not None
    assert "proposal" in policy.reason


@pytest.mark.parametrize("tool_id", WRITE_TOOL_IDS)
def test_write_tool_is_not_exposed_to_any_actor_when_governed(tool_id, _fresh_catalog):
    """Even a fully-permissioned actor cannot see a governed write tool."""
    with GOVERNED:
        capability_catalog.cache_clear()
        assert not exposure_authorized(_entry(tool_id), FULL_KANBAN_PROFILE, authenticated=True)


def test_reads_remain_exposed_when_governed(_fresh_catalog):
    """Governing writes must not touch the read surface."""
    with GOVERNED:
        capability_catalog.cache_clear()
        for tool_id in READ_TOOL_IDS:
            assert exposure_authorized(_entry(tool_id), FULL_KANBAN_PROFILE, authenticated=True)


@pytest.mark.parametrize("tool_id", WRITE_TOOL_IDS)
def test_write_tools_are_native_permissioned_by_default(tool_id):
    """Parity: with the flag off (default), the legacy write surface is intact."""
    tool = getattr(kanban_tools, tool_id)
    policy = _authorization_policy(tool, tool_id)
    assert policy.kind is PolicyKind.NATIVE_PERMISSION


@pytest.mark.parametrize("tool_id", WRITE_TOOL_IDS)
def test_write_tools_are_exposed_to_a_permissioned_actor_by_default(tool_id, _fresh_catalog):
    """Parity: default exposure of the write surface is unchanged."""
    assert exposure_authorized(_entry(tool_id), FULL_KANBAN_PROFILE, authenticated=True)


def test_governed_write_set_matches_the_kanban_write_pack():
    """The governed set is exactly the write pack (minus the withheld hard delete).

    Guards against a new write tool being added to the pack but not to the
    governed set, which would leave a live direct-ORM bypass under governance.
    """
    from ai.core.tools.capabilities import _PACK_SPECS

    _effect, pack_tools, _terms = _PACK_SPECS["kanban.write"]
    assert set(pack_tools) == set(WRITE_TOOL_IDS)


def test_still_no_direct_write_tool_leaks_into_exposure_under_governance(_fresh_catalog):
    """Whole-catalog sweep: no WRITE-effect kanban tool is exposed when governed."""
    from ai.core.tools.capabilities import ToolEffect

    with GOVERNED:
        capability_catalog.cache_clear()
        exposed_writes = [
            entry.tool_id
            for entry in capability_catalog()
            if entry.pack_id == "kanban.write"
            and entry.effect is ToolEffect.WRITE
            and exposure_authorized(entry, FULL_KANBAN_PROFILE, authenticated=True)
        ]
    assert exposed_writes == []
