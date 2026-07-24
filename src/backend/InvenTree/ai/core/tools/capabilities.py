"""Canonical capability catalog and deterministic per-run tool selection."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

CATALOG_VERSION = "1"
MAX_INITIAL_TOOLS = 12


class ToolEffect(StrEnum):
    """Externally observable effect of invoking a tool."""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    EXTERNAL_EFFECT = "external_effect"


class PolicyKind(StrEnum):
    """Supported authorization strategies."""

    NATIVE_PERMISSION = "native_permission"
    RESOURCE_AUTHORIZER = "resource_authorizer"
    DISABLED = "disabled"


@dataclass(frozen=True)
class AuthorizationPolicy:
    """Explicit exposure and invocation policy for one canonical tool."""

    kind: PolicyKind
    all_of: tuple[tuple[str, str], ...] = ()
    any_of: tuple[tuple[str, str], ...] = ()
    authorizer: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class CapabilityEntry:
    """Immutable catalog entry referencing the original callable object."""

    tool_id: str
    tool: Any
    pack_id: str
    effect: ToolEffect
    authorization: AuthorizationPolicy
    workflows: frozenset[str]
    modalities: frozenset[str]
    selection_terms: tuple[str, ...]
    contract_digest: str


@dataclass(frozen=True)
class CapabilitySelection:
    """Deterministic capability decision for one run."""

    pack_ids: tuple[str, ...]
    tool_ids: tuple[str, ...]
    tools: tuple[Any, ...]
    reason: str
    clarification_required: bool = False
    requires_specialist: bool = False


_PACK_SPECS: dict[str, tuple[ToolEffect, tuple[str, ...], tuple[str, ...]]] = {
    "parts.read": (
        ToolEffect.READ,
        (
            "search_parts",
            "get_part",
            "get_part_parameters",
            "get_part_pricing",
            "get_categories",
        ),
        ("part", "parts", "component", "item", "category", "parameter", "price"),
    ),
    "stock.read": (
        ToolEffect.READ,
        (
            "check_low_stock",
            "get_stock_levels",
            "get_stock_quantity",
            "get_stock_item",
            "get_stock_at_location",
            "get_stock_locations",
        ),
        ("stock", "inventory", "quantity", "available", "location", "warehouse"),
    ),
    "bom.read": (
        ToolEffect.READ,
        ("get_bom", "get_where_used"),
        ("bom", "bill of materials", "assembly", "where used"),
    ),
    "documents.read": (
        ToolEffect.READ,
        ("get_part_attachments", "search_part_documents"),
        ("attachment", "document", "drawing", "datasheet", "pdf", "file"),
    ),
    "procurement.read": (
        ToolEffect.READ,
        (
            "get_suppliers",
            "get_supplier_parts",
            "get_purchase_orders",
            "get_purchase_order",
            "get_purchase_order_lines",
        ),
        ("supplier", "vendor", "purchase order", "procurement", "po"),
    ),
    "sales.read": (
        ToolEffect.READ,
        (
            "get_customers",
            "get_sales_orders",
            "get_sales_order",
            "get_sales_order_lines",
        ),
        ("customer", "sales order", "sale", "so"),
    ),
    "build.read": (
        ToolEffect.READ,
        ("get_build_orders", "get_build_order", "get_build_order_lines"),
        ("build", "build order", "work order", "manufacturing order"),
    ),
    "analytics.read": (
        ToolEffect.READ,
        ("list_database_tables", "query_database"),
        ("highest", "lowest", "most", "least", "total", "average", "rank", "compare"),
    ),
    "email.read": (
        ToolEffect.READ,
        ("list_emails", "get_email_details", "download_attachment"),
        ("email", "emails", "inbox", "message", "mail attachment"),
    ),
    "email.write": (
        ToolEffect.EXTERNAL_EFFECT,
        ("mark_email_processed", "send_email", "generate_and_send_document"),
        ("send email", "mark email", "email document"),
    ),
    "kanban.read": (
        ToolEffect.READ,
        (
            "list_kanban_cards",
            "get_kanban_card",
            "get_kanban_summary",
            "check_kanban_card_stock",
        ),
        ("kanban", "board", "card", "backlog"),
    ),
    "kanban.write": (
        ToolEffect.WRITE,
        (
            "create_kanban_card",
            "update_kanban_card",
            "move_kanban_card",
            "archive_kanban_card",
            "restore_kanban_card",
            # "delete_kanban_card" is withheld -- see _WITHHELD_TOOLS. The catalog
            # rejects packs referencing unregistered tools, so this entry must stay
            # removed for as long as the tool is absent from KANBAN_TOOLS.
            "add_parts_to_kanban_card",
            "remove_part_from_kanban_card",
        ),
        ("create card", "update card", "move card", "archive card", "delete card"),
    ),
}

_LOOKUP_PACKS = {
    "stock_check": "stock.read",
    "part_details": "parts.read",
    "part_location": "stock.read",
    "bom_query": "bom.read",
    "category_list": "parts.read",
    "supplier_list": "procurement.read",
    "low_stock_alert": "stock.read",
}

_ADJACENT_PACKS: dict[str, frozenset[str]] = {
    "parts.read": frozenset({"stock.read", "bom.read", "documents.read", "analytics.read"}),
    "stock.read": frozenset({"parts.read", "analytics.read"}),
    "bom.read": frozenset({"parts.read", "analytics.read"}),
    "documents.read": frozenset({"parts.read"}),
    "procurement.read": frozenset({"parts.read", "analytics.read"}),
    "sales.read": frozenset({"parts.read", "analytics.read"}),
    "build.read": frozenset({"parts.read", "analytics.read"}),
    "analytics.read": frozenset({
        "parts.read",
        "stock.read",
        "bom.read",
        "procurement.read",
        "sales.read",
        "build.read",
    }),
}

_WRITE_PATTERN = re.compile(
    r"\b(add|allocate|approve|archive|assign|attach|cancel|change|complete|convert|"
    r"count|create|deactivate|delete|email|generate|install|issue|mark|merge|move|"
    r"order|purchase|receive|remove|restore|return|send|serialize|set|split|transfer|"
    r"uninstall|update)\b",
    re.IGNORECASE,
)


def tool_name(tool: Any) -> str:
    """Return a stable model-visible identifier for a tool."""
    name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
    if not isinstance(name, str) or not name:
        raise ValueError(f"AI tool has no stable name: {tool!r}")
    return name


def tool_contract(tool: Any) -> dict[str, Any]:
    """Return normalized local contract metadata for drift detection."""
    try:
        signature = str(inspect.signature(tool))
    except (TypeError, ValueError):
        signature = ""

    schemas: dict[str, Any] = {}
    for attribute in (
        "parameters",
        "parameters_json_schema",
        "json_schema",
        "schema",
    ):
        value = getattr(tool, attribute, None)
        if callable(value):
            try:
                value = value()
            except TypeError:
                continue
        if value is not None:
            schemas[attribute] = value

    return {
        "description": inspect.getdoc(tool) or getattr(tool, "description", "") or "",
        "module": getattr(tool, "__module__", ""),
        "name": tool_name(tool),
        "qualname": getattr(tool, "__qualname__", ""),
        "schemas": schemas,
        "signature": signature,
    }


def contract_digest(tool: Any) -> str:
    """Hash a normalized callable contract without invoking the tool."""
    encoded = json.dumps(
        tool_contract(tool),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@lru_cache(maxsize=64)
def _serialized_contract_bytes(tools: tuple[Any, ...]) -> int:
    """Return comparable local contract bytes, not provider token usage."""
    payload = json.dumps(
        tuple(tool_contract(tool) for tool in tools),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return len(payload.encode("utf-8"))


def serialized_contract_bytes(tools: Iterable[Any]) -> int:
    """Return memoized local contract bytes for a stable tool shape."""
    return _serialized_contract_bytes(tuple(tools))


def _pack_index() -> dict[str, tuple[str, ToolEffect, tuple[str, ...]]]:
    index: dict[str, tuple[str, ToolEffect, tuple[str, ...]]] = {}
    for pack_id, (effect, tool_ids, terms) in _PACK_SPECS.items():
        for tool_id in tool_ids:
            if tool_id in index:
                raise ValueError(f"Tool appears in multiple capability packs: {tool_id}")
            index[tool_id] = (pack_id, effect, terms)
    return index


#: Tools that remain defined and RBAC-mapped but are withheld from the model-visible
#: catalog and denied at invocation. Each entry needs an explicit reason: a disabled
#: tool is a deliberate policy decision, not an oversight.
_WITHHELD_TOOLS: dict[str, str] = {
    # ``delete_kanban_card`` hard-deletes a work order. ``KanbanCard`` cascades to
    # ``WorkOrderEvent``, ``WorkOrderCommand``, ``WorkOrderCloseout``,
    # ``WorkOrderDeviation``, ``CloseoutPartUsage`` and ``CloseoutReading``, so a
    # single call destroys the governance and closeout history of completed work.
    # The tool is additionally scope-free: ``aimms.kanban.change`` is a flat group,
    # unlike the REST work-order surface which applies ``scope_for_actor``.
    # Deletion returns as a governed command (permission, customer scope, expected
    # version, strict confirmation, durable audit record); until then it is withheld.
    # ``archive_kanban_card`` remains available and is the correct soft-delete.
    "delete_kanban_card": (
        "Hard delete cascades away work-order audit and closeout history, and the "
        "tool applies no customer scope; withheld pending a governed delete command"
    ),
}


#: The direct-ORM kanban write tools. When governed writes are enabled these are
#: withheld from the agent so the ONLY AI path that mutates a board card is the
#: governed proposal rail (``aichat.services.proposals``): the model proposes, a
#: deterministic command computes the effect, and a separate authenticated
#: confirmation dispatches the canonical ``tasks.services`` command (permission,
#: customer scope, expected-version, audit event, exactly-once receipt). Off by
#: default, so a deployment that has not adopted the proposal rail is unchanged.
_GOVERNED_KANBAN_WRITE_TOOLS: frozenset[str] = frozenset({
    "create_kanban_card",
    "update_kanban_card",
    "move_kanban_card",
    "archive_kanban_card",
    "restore_kanban_card",
    "add_parts_to_kanban_card",
    "remove_part_from_kanban_card",
})

#: Reason surfaced when a governed-write tool is denied. Kept short and stable so
#: the catalog manifest digest is deterministic under the flag.
_GOVERNED_WRITE_REASON = (
    "Direct AI board writes are governed: propose the change through the chat "
    "proposal rail, which dispatches the canonical work-order command on confirm"
)


def _governed_kanban_writes_enabled() -> bool:
    """Whether the direct-ORM kanban write bypass is retired (deploy setting).

    Read at catalog-build time; ``capability_catalog.cache_clear()`` re-reads it.
    """
    from django.conf import settings

    return bool(getattr(settings, "AIMMS_GOVERNED_KANBAN_WRITES", False))


def _authorization_policy(tool: Any, tool_id: str) -> AuthorizationPolicy:
    from ai.core.tools.rbac import tool_requirement

    requirement = tool_requirement(tool)
    withheld_reason = _WITHHELD_TOOLS.get(tool_id)
    if withheld_reason is not None:
        # Checked before the RBAC map so a mapped-but-withheld tool cannot fall
        # through to NATIVE_PERMISSION and become exposed again.
        return AuthorizationPolicy(kind=PolicyKind.DISABLED, reason=withheld_reason)
    if tool_id in _GOVERNED_KANBAN_WRITE_TOOLS and _governed_kanban_writes_enabled():
        # Same guarantee as _WITHHELD_TOOLS, but policy-driven: the direct-ORM
        # write is retired in favour of the proposal rail when governance is on.
        return AuthorizationPolicy(kind=PolicyKind.DISABLED, reason=_GOVERNED_WRITE_REASON)
    if tool_id == "get_part_attachments":
        return AuthorizationPolicy(
            kind=PolicyKind.RESOURCE_AUTHORIZER,
            all_of=(("part", "view"),),
            authorizer="part_attachment_access",
        )
    if requirement is not None:
        return AuthorizationPolicy(
            kind=PolicyKind.NATIVE_PERMISSION,
            all_of=(requirement,),
        )
    if tool_id in {"list_database_tables", "query_database"}:
        return AuthorizationPolicy(
            kind=PolicyKind.RESOURCE_AUTHORIZER,
            authorizer="database_relation_access",
        )
    if tool_id == "search_part_documents":
        return AuthorizationPolicy(
            kind=PolicyKind.RESOURCE_AUTHORIZER,
            all_of=(("part", "view"),),
            authorizer="part_document_access",
        )
    return AuthorizationPolicy(
        kind=PolicyKind.DISABLED,
        reason="No explicit InvenTree-managed AI capability permission exists",
    )


def _wf8_tools() -> tuple[Any, ...]:
    from ai.core.integrations.document_search import DOCUMENT_SEARCH_TOOLS
    from ai.core.integrations.email.tools import EMAIL_TOOLS
    from ai.core.integrations.inventory_tools import INVENTORY_READ_TOOLS
    from ai.core.integrations.kanban_tools import KANBAN_TOOLS

    return tuple(INVENTORY_READ_TOOLS + EMAIL_TOOLS + KANBAN_TOOLS + DOCUMENT_SEARCH_TOOLS)


@lru_cache(maxsize=1)
def capability_catalog() -> tuple[CapabilityEntry, ...]:
    """Return the ordered, immutable WF8 capability catalog."""
    pack_index = _pack_index()
    entries: list[CapabilityEntry] = []
    seen_ids: set[str] = set()

    for tool in _wf8_tools():
        tool_id = tool_name(tool)
        if tool_id in seen_ids:
            raise ValueError(f"Duplicate tool name in capability catalog: {tool_id}")
        try:
            pack_id, effect, terms = pack_index[tool_id]
        except KeyError as exc:
            raise ValueError(f"Tool has no capability pack: {tool_id}") from exc
        seen_ids.add(tool_id)
        entries.append(
            CapabilityEntry(
                tool_id=tool_id,
                tool=tool,
                pack_id=pack_id,
                effect=effect,
                authorization=_authorization_policy(tool, tool_id),
                workflows=frozenset({"wf8"}),
                modalities=frozenset({"text", "voice"}),
                selection_terms=terms,
                contract_digest=contract_digest(tool),
            )
        )

    missing = set(pack_index).difference(seen_ids)
    if missing:
        raise ValueError(
            "Capability packs reference unregistered tools: " + ", ".join(sorted(missing))
        )
    return tuple(entries)


def catalog_manifest() -> tuple[dict[str, Any], ...]:
    """Return stable contract records suitable for baseline serialization."""
    return tuple(
        {
            **tool_contract(entry.tool),
            "authorization": entry.authorization.kind.value,
            "contract_digest": entry.contract_digest,
            "effect": entry.effect.value,
            "pack_id": entry.pack_id,
        }
        for entry in capability_catalog()
    )


def entries_for_packs(pack_ids: Iterable[str]) -> tuple[CapabilityEntry, ...]:
    """Return entries in canonical catalog order for the requested packs."""
    selected = frozenset(pack_ids)
    return tuple(entry for entry in capability_catalog() if entry.pack_id in selected)


def exposure_authorized(
    entry: CapabilityEntry,
    profile: frozenset[tuple[str, str]],
    *,
    authenticated: bool,
) -> bool:
    """Return whether an entry may be included in a model-visible schema."""
    policy = entry.authorization
    if not authenticated or policy.kind is PolicyKind.DISABLED:
        return False
    if policy.all_of and not frozenset(policy.all_of).issubset(profile):
        return False
    if policy.any_of and not frozenset(policy.any_of).intersection(profile):
        return False
    if policy.authorizer == "database_relation_access":
        return any(permission == "view" for _, permission in profile)
    return True


def _contains_term(text: str, term: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None


def _pack_scores(text: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    for pack_id, (effect, _, terms) in _PACK_SPECS.items():
        if effect is not ToolEffect.READ:
            continue
        score = sum(1 for term in terms if _contains_term(text, term))
        if score:
            scores[pack_id] = score
    return scores


def _ordered_pack_ids(primary: str, scores: Mapping[str, int]) -> tuple[str, ...]:
    candidates = [
        pack_id
        for pack_id in _ADJACENT_PACKS.get(primary, frozenset())
        if scores.get(pack_id, 0) > 0
    ]
    candidates.sort(key=lambda pack_id: (-scores[pack_id], pack_id))
    if not candidates:
        return (primary,)
    return (primary, candidates[0])


def select_capabilities(
    query: str,
    *,
    lookup_type: str | None = None,
    context: Mapping[str, Any] | None = None,
    profile: frozenset[tuple[str, str]] = frozenset(),
    authenticated: bool = False,
) -> CapabilitySelection:
    """Select one stable read pack and at most one reviewed adjacent pack."""
    normalized = " ".join(query.casefold().split())
    if _WRITE_PATTERN.search(normalized):
        return CapabilitySelection(
            pack_ids=(),
            tool_ids=(),
            tools=(),
            reason="write_or_external_effect_requires_specialist",
            requires_specialist=True,
        )

    scores = _pack_scores(normalized)
    primary = _LOOKUP_PACKS.get(lookup_type or "")
    if primary is None and scores:
        primary = sorted(
            scores,
            key=lambda pack_id: (pack_id == "analytics.read", -scores[pack_id], pack_id),
        )[0]
    if primary is None:
        return CapabilitySelection(
            pack_ids=(),
            tool_ids=(),
            tools=(),
            reason="no_capability_match",
            clarification_required=True,
        )

    pack_ids = _ordered_pack_ids(primary, scores)
    entries = tuple(
        entry
        for entry in entries_for_packs(pack_ids)
        if entry.effect is ToolEffect.READ
        and exposure_authorized(entry, profile, authenticated=authenticated)
    )
    if len(entries) > MAX_INITIAL_TOOLS and len(pack_ids) > 1:
        pack_ids = (primary,)
        entries = tuple(
            entry
            for entry in entries_for_packs(pack_ids)
            if entry.effect is ToolEffect.READ
            and exposure_authorized(entry, profile, authenticated=authenticated)
        )
    if len(entries) > MAX_INITIAL_TOOLS:
        raise ValueError(f"Capability selection exceeds {MAX_INITIAL_TOOLS} tools")

    return CapabilitySelection(
        pack_ids=pack_ids,
        tool_ids=tuple(entry.tool_id for entry in entries),
        tools=tuple(entry.tool for entry in entries),
        reason="selected",
    )


def manifest_json() -> str:
    """Serialize the contract manifest with deterministic ordering."""
    return json.dumps(catalog_manifest(), sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "CATALOG_VERSION",
    "MAX_INITIAL_TOOLS",
    "AuthorizationPolicy",
    "CapabilityEntry",
    "CapabilitySelection",
    "PolicyKind",
    "ToolEffect",
    "capability_catalog",
    "catalog_manifest",
    "contract_digest",
    "entries_for_packs",
    "exposure_authorized",
    "manifest_json",
    "select_capabilities",
    "serialized_contract_bytes",
    "tool_contract",
    "tool_name",
]
