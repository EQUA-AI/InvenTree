"""Per-user tool loading driven by InvenTree's RBAC rulesets.

Agents receive only the tools the requesting user's roles permit, resolved
per run (the shared agent instances stay cached):

- Each RBAC-relevant tool maps to one ``(ruleset, permission)`` pair.
  Inventory/order tools use InvenTree's native vocabulary (``users.ruleset``);
  kanban and email use AIMMS-native capability permissions gated by a
  dedicated Django group (see ``_AIMMS_NATIVE_GROUPS``). Only the database
  tools (which enforce per-table RBAC themselves) stay unmapped/pass-through.
- A user's *permission profile* is the frozenset of granted pairs: InvenTree
  pairs from ``users.permissions.check_user_role`` plus AIMMS-native pairs
  from group membership (session-cached; superusers short-circuit to
  everything).
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
    from ai.core.integrations import controlled_document_corpus as cdt
    from ai.core.integrations import inventory_tools as it
    from ai.core.integrations import kanban_tools as kt
    from ai.core.tools.inventree.read import machines as mt
    from ai.core.tools.inventree.read import maintenance as wt
    from ai.core.tools.inventree.write import purchase_orders as po

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
        # InvenTree governs part_bomitem with the BOM ruleset (users/ruleset.py:103),
        # not the part ruleset. Mapping this to part:change let an account with
        # no BOM access at all add BOM items through the AI that it could not add
        # in the UI -- the AI must never be more permissive than the app it fronts.
        it.add_bom_item: ("bom", "add"),
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
        # Centralized purchase-order write tools (create_purchase_order and
        # add_po_line_item are already mapped above via inventory_tools).
        po.issue_purchase_order: ("purchase_order", "change"),
        po.receive_po_items: ("purchase_order", "change"),
        po.cancel_purchase_order: ("purchase_order", "change"),
        po.update_purchase_order: ("purchase_order", "change"),
        po.complete_purchase_order: ("purchase_order", "change"),
        po.delete_purchase_order: ("purchase_order", "delete"),
        po.delete_po_line_item: ("purchase_order", "delete"),
        # A kanban card is how a work order's tracked work shows on the board,
        # so touching one is exercising work-order authority. InvenTree governs
        # tasks_workorder and tasks_workorderpart with the WORK_ORDER ruleset
        # (users/ruleset.py), so that is the permission the AI must check -- not
        # an AIMMS-native group. Gating these on an invented "kanban" capability
        # meant a user holding work_order rights could manage cards in the UI but
        # not through the AI, and the aimms.kanban.* groups it looked for are
        # created by no migration, so only superusers ever reached these tools.
        kt.list_kanban_cards: ("work_order", "view"),
        kt.get_kanban_card: ("work_order", "view"),
        kt.get_kanban_summary: ("work_order", "view"),
        kt.check_kanban_card_stock: ("work_order", "view"),
        # The direct-ORM kanban write tools were deleted (S12 step 3): board
        # mutations go through the governed proposal rail and REST surface only.
        # Machines / assets. AssetMachine has no ruleset of its own, and an
        # asset is what a work order is raised against, so it borrows the
        # WORK_ORDER ruleset the same way kanban cards do. Role visibility is
        # explicitly not the boundary here: every one of these re-derives
        # customer/client scope per call in assets.ai_read, because a
        # work_order:view grant is global while asset rows belong to tenants.
        mt.search_machines: ("work_order", "view"),
        mt.get_machine_overview: ("work_order", "view"),
        mt.get_machine_health: ("work_order", "view"),
        mt.get_machine_signals: ("work_order", "view"),
        mt.get_machine_signal_trend: ("work_order", "view"),
        mt.get_machine_anomalies: ("work_order", "view"),
        mt.get_machine_parts: ("work_order", "view"),
        mt.get_machine_maintenance_history: ("work_order", "view"),
        mt.get_machine_attachments: ("work_order", "view"),
        # Maintenance work orders borrow the WORK_ORDER ruleset for the same
        # reason; tasks.ai_read re-derives scope per call.
        wt.search_work_orders: ("work_order", "view"),
        wt.get_work_order_overview: ("work_order", "view"),
        wt.get_work_order_readiness: ("work_order", "view"),
        wt.get_work_order_repair_state: ("work_order", "view"),
        wt.get_open_repairs_for_machine: ("work_order", "view"),
        # Controlled-document corpus search: readable by maintenance staff;
        # the site-key filter inside the tool is the content boundary.
        cdt.search_manuals: ("work_order", "view"),
        # Email stays AIMMS-native (_native_tool_map): Gmail is not an InvenTree
        # model and has no ruleset to map onto.
        # query_database / list_database_tables are intentionally unmapped:
        # they enforce per-table RBAC internally for the same user.
    }
    return mapping


def _native_tool_map() -> dict[Any, tuple[str, str]]:
    """Map email tools to their AIMMS-native ``(ruleset, permission)``.

    Kept separate from ``_tool_permission_map`` because these pairs are resolved
    by Django group membership rather than ``check_user_role``. Only email
    belongs here: it has no InvenTree model and therefore no ruleset. Kanban
    used to live here and does not -- its cards are work orders, governed by the
    WORK_ORDER ruleset, so it is mapped in ``_tool_permission_map`` instead.
    """
    from ai.core.integrations.email import tools as et

    return {
        et.list_emails: ("email", "view"),
        et.get_email_details: ("email", "view"),
        et.download_attachment: ("email", "view"),
        et.mark_email_processed: ("email", "send"),
        et.send_email: ("email", "send"),
        et.generate_and_send_document: ("email", "send"),
    }


@lru_cache(maxsize=1)
def _permission_map_cached() -> dict[Any, tuple[str, str]]:
    return _tool_permission_map()


@lru_cache(maxsize=1)
def _all_pairs() -> frozenset[tuple[str, str]]:
    return frozenset(_permission_map_cached().values())


# AIMMS-native capability permissions have no InvenTree RuleSet. Email (Gmail)
# is gated by membership in a dedicated Django group; superusers get all.
# Granting these to a user = adding them to the group.
#
# Kanban was here too, on aimms.kanban.view/change. It was wrong twice over: no
# migration creates those groups, so only superusers ever passed, and kanban
# cards are InvenTree work orders with a real ruleset of their own.
_AIMMS_NATIVE_GROUPS: dict[tuple[str, str], str] = {
    ("email", "view"): "aimms.email.view",
    ("email", "send"): "aimms.email.send",
}


def _native_pairs(user) -> frozenset[tuple[str, str]]:
    """AIMMS-native (non-InvenTree) permission pairs granted to a user.

    Fail-closed: inactive/None users and any group-lookup failure yield none.
    """
    if user is None or not getattr(user, "is_active", False):
        return frozenset()
    if getattr(user, "is_superuser", False):
        return frozenset(_AIMMS_NATIVE_GROUPS)
    try:
        group_names = set(user.groups.values_list("name", flat=True))
    except Exception:
        return frozenset()
    return frozenset(pair for pair, group in _AIMMS_NATIVE_GROUPS.items() if group in group_names)


def permission_profile(user) -> frozenset[tuple[str, str]]:
    """Return the granted ``(ruleset, permission)`` pairs for one user.

    Only pairs some tool actually requires are evaluated; superusers get
    everything without any lookups.
    """
    if user is None or not getattr(user, "is_active", False):
        return frozenset()
    if getattr(user, "is_superuser", False):
        return _all_pairs() | frozenset(_AIMMS_NATIVE_GROUPS)

    native = _native_pairs(user)

    from users.permissions import check_user_role

    # _all_pairs() is InvenTree-only; native (email/kanban) pairs are resolved by
    # group membership, not check_user_role.
    inventree = frozenset(pair for pair in _all_pairs() if check_user_role(user, pair[0], pair[1]))
    return inventree | native


def _permission_profile_for_user_pk_sync(user_pk: str) -> frozenset[tuple[str, str]]:
    """Resolve a fresh text-chat permission profile for one actor id."""
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    try:
        user = user_model.objects.get(pk=int(user_pk))
    except (OverflowError, TypeError, ValueError, user_model.DoesNotExist):
        return frozenset()
    return permission_profile(user)


async def permission_profile_for_user_pk(
    user_pk: str,
) -> frozenset[tuple[str, str]]:
    """Async-safe fresh profile used at voice proposal and execution time."""
    return await sync_to_async(
        _permission_profile_for_user_pk_sync,
        thread_sensitive=True,
    )(user_pk)


@lru_cache(maxsize=1)
def _filter_map_cached() -> dict[Any, tuple[str, str]]:
    """Combined map for list filtering: InvenTree + AIMMS-native (email/kanban)."""
    return {**_permission_map_cached(), **_native_tool_map()}


def tool_requirement(tool: Any) -> tuple[str, str] | None:
    """Return the same RBAC requirement used by text-chat tool filtering."""
    return _filter_map_cached().get(tool)


def is_action_tool(tool: Any) -> bool:
    """Whether a tool performs an effect and therefore requires confirmation."""
    requirement = tool_requirement(tool)
    if requirement is not None:
        return requirement[1] != "view"
    return bool(getattr(tool, "_requires_hitl", False))


def read_tools(tools: Sequence[Any]) -> tuple[Any, ...]:
    """Return the order-stable read projection of a text-chat toolset."""
    return tuple(tool for tool in tools if not is_action_tool(tool))


def action_tools(tools: Sequence[Any]) -> tuple[Any, ...]:
    """Return the order-stable confirmed-action projection of a toolset."""
    return tuple(tool for tool in tools if is_action_tool(tool))


@lru_cache(maxsize=64)
def _filter_cached(tools: tuple[Any, ...], profile: frozenset[tuple[str, str]]) -> tuple[Any, ...]:
    mapping = _filter_map_cached()
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
