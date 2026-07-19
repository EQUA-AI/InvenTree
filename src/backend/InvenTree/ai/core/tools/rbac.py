"""Per-user tool loading driven by InvenTree's RBAC rulesets.

Agents receive only the tools the requesting user's roles permit, resolved
per run (the shared agent instances stay cached):

- Each RBAC-relevant tool maps to one ``(ruleset, permission)`` pair using
  InvenTree's native vocabulary (``users.ruleset``). Tools without a
  mapping (email, kanban, document search, and the database tools that
  enforce per-table RBAC themselves) are available to any authenticated
  user.
- A user's *permission profile* is the frozenset of granted pairs, built
  from ``users.permissions.check_user_role`` (session-cached; superusers
  short-circuit to everything).
- Filtering is memoized per (toolset, profile) and preserves the base
  ordering, so each profile presents a byte-stable tool schema — which
  keeps provider-side prompt caching effective.

Visibility filtering is UX and efficiency, not the security boundary:
runtime enforcement (the voice read-only fence, per-table checks in the
database tools, and eventually per-user API identity) stays authoritative.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from asgiref.sync import sync_to_async

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


def _tool_permission_map() -> dict[Any, tuple[str, str]]:
    """Map tool objects to their required ``(ruleset, permission)`` pair."""
    from ai.core.integrations import inventory_tools as it

    mapping: dict[Any, tuple[str, str]] = {
        # Parts
        it.search_parts: ("part", "view"),
        it.get_part_details: ("part", "view"),
        it.check_low_stock: ("part", "view"),
        it.get_part_parameters: ("part", "view"),
        it.get_part_attachments: ("part", "view"),
        it.get_part_pricing: ("part", "view"),
        it.create_part: ("part", "add"),
        it.update_part: ("part", "change"),
        it.set_part_parameter: ("part", "change"),
        it.deactivate_part: ("part", "change"),
        # Categories / locations
        it.list_categories: ("part_category", "view"),
        it.create_part_category: ("part_category", "add"),
        it.create_stock_location: ("stock_location", "add"),
        it.list_locations: ("stock_location", "view"),
        # Stock
        it.get_stock_levels: ("stock", "view"),
        it.get_stock_quantity: ("stock", "view"),
        it.get_stock_item: ("stock", "view"),
        it.get_stock_at_location: ("stock", "view"),
        it.add_stock: ("stock", "add"),
        it.remove_stock: ("stock", "change"),
        it.transfer_stock: ("stock", "change"),
        it.count_stock: ("stock", "change"),
        it.merge_stock: ("stock", "change"),
        it.update_stock_location: ("stock", "change"),
        it.change_stock_status: ("stock", "change"),
        it.split_stock: ("stock", "change"),
        it.convert_stock: ("stock", "change"),
        it.add_stock_test_result: ("stock", "change"),
        it.serialize_stock: ("stock", "change"),
        it.install_stock: ("stock", "change"),
        it.uninstall_stock: ("stock", "change"),
        it.assign_stock: ("stock", "change"),
        it.return_stock: ("stock", "change"),
        # BOM
        it.get_bom: ("part", "view"),
        it.get_where_used: ("part", "view"),
        it.add_bom_item: ("part", "change"),
        # Purchasing / companies / suppliers
        it.list_suppliers: ("purchase_order", "view"),
        it.get_supplier_parts: ("purchase_order", "view"),
        it.list_purchase_orders: ("purchase_order", "view"),
        it.get_purchase_order: ("purchase_order", "view"),
        it.get_purchase_order_lines: ("purchase_order", "view"),
        it.create_purchase_order: ("purchase_order", "add"),
        it.add_po_line_item: ("purchase_order", "change"),
        it.create_company: ("purchase_order", "add"),
        it.create_supplier_part: ("purchase_order", "add"),
        it.create_manufacturer_part: ("purchase_order", "add"),
        # Sales
        it.list_sales_orders: ("sales_order", "view"),
        it.get_sales_order: ("sales_order", "view"),
        it.get_sales_order_lines: ("sales_order", "view"),
        it.get_customers: ("sales_order", "view"),
        it.create_sales_order: ("sales_order", "add"),
        it.add_so_line_item: ("sales_order", "change"),
        # Builds
        it.list_build_orders: ("build", "view"),
        it.get_build_order: ("build", "view"),
        it.get_build_order_lines: ("build", "view"),
        # query_database / list_database_tables are intentionally unmapped:
        # they enforce per-table RBAC internally for the same user.
    }
    return mapping


@lru_cache(maxsize=1)
def _permission_map_cached() -> dict[Any, tuple[str, str]]:
    return _tool_permission_map()


@lru_cache(maxsize=1)
def _all_pairs() -> frozenset[tuple[str, str]]:
    return frozenset(_permission_map_cached().values())


def permission_profile(user) -> frozenset[tuple[str, str]]:
    """Return the granted ``(ruleset, permission)`` pairs for one user.

    Only pairs some tool actually requires are evaluated; superusers get
    everything without any lookups.
    """
    if user is None or not getattr(user, "is_active", False):
        return frozenset()
    if getattr(user, "is_superuser", False):
        return _all_pairs()

    from users.permissions import check_user_role

    return frozenset(pair for pair in _all_pairs() if check_user_role(user, pair[0], pair[1]))


@lru_cache(maxsize=64)
def _filter_cached(tools: tuple[Any, ...], profile: frozenset[tuple[str, str]]) -> tuple[Any, ...]:
    mapping = _permission_map_cached()
    return tuple(
        tool
        for tool in tools
        if (requirement := mapping.get(tool)) is None or requirement in profile
    )


def filter_tools(tools: Sequence[Any], profile: frozenset[tuple[str, str]]) -> list[Any]:
    """Return the profile's tool list, order-stable and memoized."""
    return list(_filter_cached(tuple(tools), profile))


def _tools_for_current_user_sync(tools: tuple[Any, ...]) -> list[Any]:
    from ai.core.auth import get_current_principal
    from django.contrib.auth import get_user_model

    principal = get_current_principal()
    user = None
    if principal is not None:
        try:
            user = get_user_model().objects.get(pk=int(principal.user_pk))
        except Exception:
            user = None
    profile = permission_profile(user)
    selected = filter_tools(tools, profile)
    if len(selected) != len(tools):
        logger.debug(
            "RBAC tool filtering applied",
            extra={"offered": len(selected), "base": len(tools)},
        )
    return selected


async def tools_for_current_user(tools: Sequence[Any]) -> list[Any]:
    """Async-safe: resolve the boundary principal and filter the toolset.

    Fail-closed: without a resolvable user, only unmapped (non-RBAC) tools
    are offered.
    """
    return await sync_to_async(_tools_for_current_user_sync, thread_sensitive=True)(tuple(tools))
