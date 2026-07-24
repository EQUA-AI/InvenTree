"""Canonical capability catalog and deterministic per-run tool selection."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

logger = logging.getLogger(__name__)

CATALOG_VERSION = "1"
#: Ceiling on one run's model-visible schema. A primary pack plus two adjacent
#: packs plus the SQL escape hatch is 15 tools at worst (parts 5 + stock 6 +
#: bom-or-documents 2 + analytics 2), so this leaves headroom while staying
#: well under the full read surface (28 tools).
MAX_INITIAL_TOOLS = 16


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
    #: Which widening inputs fired ("shape", "lexicon", "sql_escape_hatch").
    #: Logged per turn so a future mis-selection is diagnosable from logs alone,
    #: which is what this class of bug previously lacked.
    signals: tuple[str, ...] = ()


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

#: Write verbs that are also ordinary read vocabulary. "count the fasteners over
#: 2000" is a question; "count stock in bin A-3" is a stocktake. Only these verbs
#: are eligible to be re-read as questions, and only on an explicit read signal
#: that is not the verb itself.
_AMBIGUOUS_WRITE_VERBS = frozenset({"count"})

#: A verb immediately followed by "of" is being used as a noun ("count of parts").
_NOUN_USE_RE = re.compile(r"\s+of\b", re.IGNORECASE)

#: Compound nouns whose head word is also a write verb. "Show build order lines"
#: and "List supplier purchase orders" are read requests, but "order" and
#: "purchase" match the write pattern, so both currently route to a specialist
#: and reach no tools at all. Masking the phrase leaves a genuine imperative
#: ("create a purchase order") still matching on its own verb.
_NOUN_PHRASE_RE = re.compile(
    r"\b(?:purchase|sales|build|work|return)\s+orders?\b"
    r"|\border\s+(?:lines?|status|number)\b",
    re.IGNORECASE,
)


def _mask_noun_phrases(text: str) -> str:
    """Blank compound nouns, preserving offsets so match positions stay valid."""
    return _NOUN_PHRASE_RE.sub(lambda match: " " * (match.end() - match.start()), text)


#: The grammar of an aggregate, threshold, ranking, or grouping question,
#: independent of which nouns it happens to use. Users vary domain nouns far more
#: than they vary question shape, so this generalizes where a term whitelist
#: cannot: "over 2000", "how many", "per location" all read as analytics work.
_AGGREGATION_SHAPE_RE = re.compile(
    r"\b(?:how many|how much|number of|count|total|sum|average|mean|highest|lowest|"
    r"most|least|top|bottom|rank|ranking|compare|comparison|breakdown|each|per|every|all)\b"
    r"|\b(?:over|above|under|below|more than|greater than|less than|fewer than|"
    r"at least|at most|exceeds?|exceeding|between)\b"
    r"|[<>]=?\s*\d",
    re.IGNORECASE,
)

#: The subset of read shapes strong enough to overrule an ambiguous write verb.
#: Bare determiners ("all", "each", "any") are deliberately excluded: "count
#: stock in every bin" is still a stocktake.
_STRICT_READ_SHAPE_RE = re.compile(
    r"\b(?:how many|how much|number of|total|sum|average|mean|highest|lowest|"
    r"most|least|top|bottom|rank|ranking|compare|comparison|breakdown)\b"
    r"|\b(?:over|above|under|below|more than|greater than|less than|fewer than|"
    r"at least|at most|exceeds?|exceeding|between)\b"
    r"|[<>]=?\s*\d",
    re.IGNORECASE,
)

#: Weight for a shape match. Above a single term hit so an unmistakably
#: analytical question outranks an incidental noun, below two so a question that
#: names its domain still leads with the domain pack.
_SHAPE_SCORE = 2

#: Packs paired with the SQL escape hatch when nothing else scores, so the model
#: is never handed `query_database` as its only way to reach inventory data.
_DEFAULT_READ_PACKS = ("parts.read", "stock.read")


def _write_intent(text: str) -> str | None:
    """Return the write verb routing this query to a specialist, else ``None``.

    Misclassification here is asymmetric. Selection already restricts to
    READ-effect packs, so a write mistaken for a read merely offers read tools,
    while a read mistaken for a write offers nothing at all and the turn dies.
    Ambiguous verbs therefore resolve toward read -- but only on evidence.
    """
    text = _mask_noun_phrases(text)
    matches = list(_WRITE_PATTERN.finditer(text))
    if not matches:
        return None
    decisive = [match for match in matches if match.group(0).lower() not in _AMBIGUOUS_WRITE_VERBS]
    if decisive:
        return decisive[0].group(0).lower()
    for match in matches:
        trailing = text[match.end() :]
        residual = f"{text[: match.start()]} {trailing}"
        if _NOUN_USE_RE.match(trailing) or _STRICT_READ_SHAPE_RE.search(residual):
            return None
    return matches[0].group(0).lower()


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


def governed_kanban_writes_enabled() -> bool:
    """Public accessor for the governance flag (voice gate consults it too)."""
    return _governed_kanban_writes_enabled()


def governed_kanban_write_tool_ids() -> frozenset[str]:
    """The direct-ORM kanban write tools retired by the governance flag."""
    return _GOVERNED_KANBAN_WRITE_TOOLS


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


def _ai_setting(name: str, default: Any) -> Any:
    """Read one AI setting, falling back when AI configuration is absent.

    Selection must keep working in deployments (and tests) that never configure
    the AI settings object, so a missing config yields the shipped default rather
    than failing the turn.
    """
    try:
        from ai.core.config import get_settings

        return getattr(get_settings(), name, default)
    except Exception:
        return default


def selection_v2_enabled() -> bool:
    """Whether shape-based selection and the always-on SQL pack are active."""
    return bool(_ai_setting("feature_capability_selection_v2", True))


def category_lexicon_enabled() -> bool:
    """Whether live part category names contribute selection terms."""
    return bool(_ai_setting("feature_category_lexicon", True))


#: Category names below this length, or in this stop list, are too collision-prone
#: to be evidence: a category called "Misc" or "Parts" would fire on any sentence.
_LEXICON_MIN_LENGTH = 4
_LEXICON_STOPWORDS = frozenset(
    {
        "assemblies",
        "assembly",
        "component",
        "components",
        "general",
        "item",
        "items",
        "material",
        "materials",
        "misc",
        "other",
        "part",
        "parts",
        "product",
        "products",
        "spare",
        "spares",
        "stock",
        "supplies",
        "tools",
    }
)
_CATEGORY_LEXICON_CACHE_KEY = "aimms:capability:category_lexicon:v1"
_CATEGORY_LEXICON_TTL_SECONDS = 600


def _lexicon_variants(name: str) -> set[str]:
    """Return the casefolded singular/plural forms of one category name."""
    cleaned = " ".join(str(name or "").casefold().split())
    # Reject on the name itself, before deriving forms. Filtering only the
    # derived set would let a rejected "Misc" back in as "miscs".
    if len(cleaned) < _LEXICON_MIN_LENGTH or cleaned in _LEXICON_STOPWORDS:
        return set()
    variants = {cleaned}
    if cleaned.endswith("ies") and len(cleaned) > 4:
        variants.add(f"{cleaned[:-3]}y")
    elif cleaned.endswith("es") and len(cleaned) > 3:
        variants.add(cleaned[:-2])
    elif cleaned.endswith("s") and not cleaned.endswith(("ss", "is")):
        variants.add(cleaned[:-1])
    elif not cleaned.endswith("s"):
        # Names already ending in -s ("glass", "analysis") gain no bogus +s form.
        variants.add(f"{cleaned}s")
    return {
        variant
        for variant in variants
        if len(variant) >= _LEXICON_MIN_LENGTH and variant not in _LEXICON_STOPWORDS
    }


def _build_category_lexicon() -> frozenset[str]:
    from part.models import PartCategory

    terms: set[str] = set()
    for name in PartCategory.objects.values_list("name", flat=True):
        terms |= _lexicon_variants(name)
    return frozenset(terms)


def category_lexicon() -> frozenset[str]:
    """Return selection terms derived from live part category names.

    This is a routing hint, never a source of truth. Category names are resolved
    against live data at answer time by ``get_categories`` and ``query_database``,
    so a stale lexicon can only under-select tools -- it can never produce a wrong
    answer. Any failure degrades to an empty lexicon, which is safe because these
    terms only ever add score.
    """
    try:
        from django.core.cache import cache

        cached = cache.get(_CATEGORY_LEXICON_CACHE_KEY)
        if cached is not None:
            return frozenset(cached)
        terms = _build_category_lexicon()
        cache.set(_CATEGORY_LEXICON_CACHE_KEY, sorted(terms), _CATEGORY_LEXICON_TTL_SECONDS)
        return terms
    except Exception as exc:
        logger.info("Category lexicon unavailable", extra={"error_type": type(exc).__name__})
        return frozenset()


def invalidate_category_lexicon(**_kwargs: Any) -> None:
    """Drop the cached lexicon so a category change is picked up on the next turn."""
    try:
        from django.core.cache import cache

        cache.delete(_CATEGORY_LEXICON_CACHE_KEY)
    except Exception as exc:
        logger.info(
            "Category lexicon invalidation failed",
            extra={"error_type": type(exc).__name__},
        )


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


def _matches_lexicon(text: str, lexicon: Iterable[str]) -> bool:
    """Whether any live category name appears in the query.

    A single point regardless of how many variants match: "fasteners" also hits
    the stored singular form and must not score twice.
    """
    return any(_contains_term(text, term) for term in lexicon)


def _ordered_pack_ids(
    primary: str,
    scores: Mapping[str, int],
    *,
    max_adjacent: int = 1,
) -> tuple[str, ...]:
    candidates = [
        pack_id
        for pack_id in _ADJACENT_PACKS.get(primary, frozenset())
        if scores.get(pack_id, 0) > 0
    ]
    candidates.sort(key=lambda pack_id: (-scores[pack_id], pack_id))
    return (primary, *candidates[:max_adjacent])


def _with_sql_escape_hatch(pack_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Attach the read-only SQL pack to an InvenTree data selection.

    Keyword and shape matching cannot anticipate every phrasing, so the two SQL
    tools ride along as the universal fallback for questions the specific tools
    cannot express. Packs outside the InvenTree data graph (email, kanban) are
    left alone: SQL is not a fallback for a mailbox. This widens exposure, not
    access -- ``exposure_authorized`` still requires a view permission and
    ``_run_query`` re-checks every relation the plan touches per invocation.
    """
    if pack_ids[0] not in _ADJACENT_PACKS:
        return pack_ids
    if "analytics.read" not in pack_ids:
        pack_ids = (*pack_ids, "analytics.read")
    if pack_ids == ("analytics.read",):
        # SQL alone would force every answer through hand-written aggregates when
        # a cheaper, better-typed read tool exists.
        pack_ids = (*pack_ids, *_DEFAULT_READ_PACKS)
    return pack_ids


def _authorized_read_entries(
    pack_ids: Iterable[str],
    profile: frozenset[tuple[str, str]],
    *,
    authenticated: bool,
) -> tuple[CapabilityEntry, ...]:
    return tuple(
        entry
        for entry in entries_for_packs(pack_ids)
        if entry.effect is ToolEffect.READ
        and exposure_authorized(entry, profile, authenticated=authenticated)
    )


def select_capabilities(
    query: str,
    *,
    lookup_type: str | None = None,
    context: Mapping[str, Any] | None = None,
    profile: frozenset[tuple[str, str]] = frozenset(),
    authenticated: bool = False,
) -> CapabilitySelection:
    """Select one stable read pack plus the reviewed adjacent packs.

    Scoring reads the current message only. Follow-ups are resolved from the
    replayed transcript inside the agent turn rather than here, so a message
    carrying no signal of its own still asks for clarification instead of
    guessing at a subject.
    """
    normalized = " ".join(query.casefold().split())
    if _write_intent(normalized) is not None:
        return CapabilitySelection(
            pack_ids=(),
            tool_ids=(),
            tools=(),
            reason="write_or_external_effect_requires_specialist",
            requires_specialist=True,
        )

    widened = selection_v2_enabled()
    lexicon = category_lexicon() if widened and category_lexicon_enabled() else frozenset()
    signals: list[str] = []
    scores = _pack_scores(normalized)
    if _matches_lexicon(normalized, lexicon):
        scores["parts.read"] = scores.get("parts.read", 0) + 1
        signals.append("lexicon")
    if widened and _AGGREGATION_SHAPE_RE.search(normalized):
        scores["analytics.read"] = scores.get("analytics.read", 0) + _SHAPE_SCORE
        signals.append("shape")

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
            signals=tuple(signals),
        )

    pack_ids = _ordered_pack_ids(primary, scores, max_adjacent=2 if widened else 1)
    if widened:
        with_hatch = _with_sql_escape_hatch(pack_ids)
        if with_hatch != pack_ids:
            signals.append("sql_escape_hatch")
        pack_ids = with_hatch

    entries = _authorized_read_entries(pack_ids, profile, authenticated=authenticated)
    # Trim the weakest adjacent pack rather than collapsing to the primary: the
    # old collapse silently discarded exactly the pack that made a question
    # answerable, and the old ValueError surfaced as a failed turn.
    while len(entries) > MAX_INITIAL_TOOLS and len(pack_ids) > 1:
        dropped = min(pack_ids[1:], key=lambda pack_id: (scores.get(pack_id, 0), pack_id))
        pack_ids = tuple(pack_id for pack_id in pack_ids if pack_id != dropped)
        entries = _authorized_read_entries(pack_ids, profile, authenticated=authenticated)
        logger.info(
            "Capability selection trimmed to fit the tool budget",
            extra={"dropped_pack": dropped, "pack_ids": pack_ids, "limit": MAX_INITIAL_TOOLS},
        )
    if len(entries) > MAX_INITIAL_TOOLS:
        # A single pack outgrew the budget. Answering with an oversized schema
        # beats failing the turn, but the drift needs to be visible.
        logger.warning(
            "Capability pack exceeds the tool budget on its own",
            extra={"pack_ids": pack_ids, "tool_count": len(entries), "limit": MAX_INITIAL_TOOLS},
        )

    return CapabilitySelection(
        pack_ids=pack_ids,
        tool_ids=tuple(entry.tool_id for entry in entries),
        tools=tuple(entry.tool for entry in entries),
        reason="selected",
        signals=tuple(signals),
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
    "category_lexicon",
    "category_lexicon_enabled",
    "contract_digest",
    "entries_for_packs",
    "exposure_authorized",
    "invalidate_category_lexicon",
    "manifest_json",
    "select_capabilities",
    "selection_v2_enabled",
    "serialized_contract_bytes",
    "tool_contract",
    "tool_name",
]
