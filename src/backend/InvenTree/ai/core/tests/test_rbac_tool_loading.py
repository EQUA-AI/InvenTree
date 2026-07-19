"""Tests for per-user RBAC tool loading.

Filtering must be fail-closed for RBAC-mapped tools, order-stable and
memoized per permission profile, and must always retain unmapped tools
(email/kanban/document/database) for authenticated users.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

from unittest.mock import patch  # noqa: E402

from ai.core.integrations import inventory_tools as it  # noqa: E402
from ai.core.tools import rbac  # noqa: E402


class _User:
    def __init__(self, superuser=False, active=True):
        self.pk = 7
        self.is_superuser = superuser
        self.is_active = active


def _profile_for(granted: set[tuple[str, str]], user=None):
    def _check(_user, role, permission):
        return (role, permission) in granted

    with patch("users.permissions.check_user_role", side_effect=_check):
        return rbac.permission_profile(user or _User())


def test_every_inventory_tool_is_mapped_or_deliberately_open():
    mapping = rbac._permission_map_cached()
    deliberately_open = {it.query_database, it.list_database_tables}
    unmapped = [
        getattr(tool, "__name__", str(tool))
        for tool in it.INVENTORY_TOOLS
        if tool not in mapping and tool not in deliberately_open
    ]
    assert unmapped == [], f"tools without an RBAC mapping: {unmapped}"


def test_superuser_gets_the_full_toolset_without_role_lookups():
    with patch("users.permissions.check_user_role") as check:
        profile = rbac.permission_profile(_User(superuser=True))
        selected = rbac.filter_tools(it.INVENTORY_TOOLS, profile)
    check.assert_not_called()
    assert selected == list(it.INVENTORY_TOOLS)


def test_part_viewer_sees_part_reads_but_not_stock_or_writes():
    profile = _profile_for({("part", "view")})
    selected = rbac.filter_tools(it.INVENTORY_TOOLS, profile)
    assert it.search_parts in selected
    assert it.get_bom in selected
    assert it.get_stock_levels not in selected
    assert it.create_part not in selected
    assert it.add_stock not in selected


def test_unmapped_tools_survive_every_profile():
    empty = _profile_for(set())
    selected = rbac.filter_tools(it.INVENTORY_TOOLS, empty)
    assert it.query_database in selected
    assert it.list_database_tables in selected
    assert all(tool in (it.query_database, it.list_database_tables) for tool in selected)


def test_inactive_or_missing_user_fails_closed():
    assert rbac.permission_profile(None) == frozenset()
    assert rbac.permission_profile(_User(active=False)) == frozenset()


def test_filtering_preserves_base_ordering():
    profile = _profile_for({("part", "view"), ("stock", "view")})
    selected = rbac.filter_tools(it.INVENTORY_READ_TOOLS, profile)
    base_positions = {tool: i for i, tool in enumerate(it.INVENTORY_READ_TOOLS)}
    positions = [base_positions[tool] for tool in selected]
    assert positions == sorted(positions)


def test_filtering_is_memoized_per_profile():
    profile = _profile_for({("part", "view")})
    first = rbac._filter_cached(tuple(it.INVENTORY_READ_TOOLS), profile)
    second = rbac._filter_cached(tuple(it.INVENTORY_READ_TOOLS), profile)
    assert first is second
