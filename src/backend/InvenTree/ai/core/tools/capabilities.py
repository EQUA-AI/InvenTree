"""Canonical capability catalog and deterministic per-run tool selection."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
from bisect import bisect_right
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

logger = logging.getLogger(__name__)

CATALOG_VERSION = "1"
#: Ceiling on one run's model-visible schema. A primary pack plus two adjacent
#: packs plus the SQL escape hatch is 16 tools at worst (parts 5 + stock 6 +
#: documents 3 + analytics 2 -- documents.read grew to 3 with the R2
#: attachment tool; it relaxes back to 2 when search_part_documents is
#: unwired at R5), exactly at the ceiling while staying well under the full
#: read surface.
#: Raised 16 -> 17 with S5b: the maintenance.read pack gained the
#: owner-approved get_work_order_closeout (A16/Q14), growing the worst-case
#: stack (maintenance 7 + machines 9 + SQL 1). A one-tool schema increase is
#: a deliberate decision, not drift — the budget test pins the new worst case.
MAX_INITIAL_TOOLS = 17


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
        (
            "stock",
            "inventory",
            "quantity",
            "available",
            "warehouse",
            # "where is X located" scored nothing without these, so the
            # question dead-ended on the clarify agent even after the
            # location_detail fix made the answer available.
            "location",
            "located",
            "locate",
            "where",
            "bin",
            "shelf",
            "rack",
            "aisle",
            "stored",
            "on hand",
        ),
    ),
    "bom.read": (
        ToolEffect.READ,
        ("get_bom", "get_where_used"),
        ("bom", "bill of materials", "assembly", "where used"),
    ),
    "documents.read": (
        ToolEffect.READ,
        # search_attachment_docs (R2 attachment corpus) lives here, not in
        # manuals.read: the manuals worst-case stack is exactly at
        # MAX_INITIAL_TOOLS and the explicit-manuals rider always re-appends
        # that pack, so a second manuals tool would force the trim loop.
        # This pack's terms are the attachment corpus's own doc_type
        # vocabulary. Worst case here: parts 5 + stock 6 + documents 3 +
        # analytics 2 = 16 <= MAX_INITIAL_TOOLS. search_part_documents is
        # deprecated (R2) and unwired at R5, relaxing the pack back to 2.
        ("get_part_attachments", "search_part_documents", "search_attachment_docs"),
        # "uploaded"/"documents": how operators actually name the attachment
        # corpus ("the uploaded datasheet", "what do the uploaded documents
        # say") — without them a spec question phrased that way selected no
        # documents pack at all (live golden finding, 2026-08-20). Terms
        # match on word boundaries, so the plural needs its own entry.
        ("attachment", "document", "documents", "drawing", "datasheet", "pdf", "file", "uploaded"),
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
        # "work order" deliberately absent: on this fork a work order is a
        # MAINTENANCE job (maintenance.read); a manufacturing job is a build.
        ("build", "build order", "manufacturing order"),
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
    "machines.read": (
        ToolEffect.READ,
        (
            "search_machines",
            "get_machine_overview",
            "get_machine_health",
            "get_machine_signals",
            "get_machine_signal_trend",
            "get_machine_anomalies",
            "get_machine_parts",
            "get_machine_maintenance_history",
            "get_machine_attachments",
        ),
        # Floor vocabulary, not schema vocabulary. Without these terms a
        # machine question scored no pack at all and dead-ended on the clarify
        # agent -- the assistant asking what was meant while standing on the
        # machine's own page. "asset"/"equipment" are what the records are
        # called; the rest is what an operator actually says out loud.
        (
            # Deliberately NO bare equipment nouns ("pump", "motor", "valve").
            # Spares are named after the kit they belong to, so "grinder pump
            # seal kit" is a stock question that a "pump" term would hijack --
            # the noun is evidence of neither pack. Real asset names are
            # covered by machine_lexicon() instead, which is the only mechanism
            # that can know what a given plant actually calls its equipment.
            "machine",
            "machines",
            "asset",
            "assets",
            "equipment",
            "health",
            "alarm",
            "alarms",
            "anomaly",
            "anomalies",
            "fault",
            "faults",
            "downtime",
            # "breakdown" is deliberately absent: "give me a breakdown of stock
            # by category" is an analytics question, not a failed asset.
            "serviced",
            "commissioned",
            "sensor",
            "telemetry",
            "vibration",
            "serial number",
        ),
    ),
    "kanban.read": (
        ToolEffect.READ,
        (
            "list_kanban_cards",
            "get_kanban_card",
            "get_kanban_summary",
            "check_kanban_card_stock",
        ),
        # "job"/"task" are what the floor calls these; without them "Check for
        # all jobs" scored no kanban pack and the assistant claimed it could not
        # access job details -- right after summarising the board.
        ("kanban", "board", "card", "backlog", "job", "jobs", "task", "tasks"),
    ),
    "maintenance.read": (
        ToolEffect.READ,
        (
            "search_work_orders",
            "get_work_order_overview",
            "get_work_order_readiness",
            "get_work_order_repair_state",
            "get_open_repairs_for_machine",
            "get_work_order_history",
            "get_work_order_closeout",
        ),
        # A work order here is a MAINTENANCE job. Terms deliberately not shared
        # with any other pack: "job"/"task" stay on kanban (board phrasing),
        # "fault"/"downtime" stay on machines (asset phrasing), and "breakdown"
        # is absent for the same analytics reason machines.read documents.
        (
            "work order",
            "work orders",
            "maintenance",
            "repair",
            "repairs",
            "repair packet",
            "finding",
            "findings",
            "approved scope",
            "repair plan",
            "readiness",
            "ready to start",
            "blocker",
            "blockers",
            "loto",
            "lockout",
            "tagout",
            "permit",
            "corrective",
            "preventive",
            "preventative",
        ),
    ),
    "manuals.read": (
        ToolEffect.READ,
        ("search_manuals",),
        # Controlled documentation phrasing. "procedure"/"spec" stay with their
        # owning packs; "document"/"file" stay on documents.read.
        (
            "manual",
            "manuals",
            "o&m",
            "technical manual",
            "handbook",
            "knowledge base",
            "documentation",
        ),
    ),
    "sources.read": (
        ToolEffect.READ,
        ("list_document_sources",),
        # S8a: registry-inventory phrasing. Deliberately NARROW — the
        # sources-primary rider in select_capabilities (keyed on the shared
        # is_source_inventory_question shape) is what makes inventory
        # questions reach this pack; term sprawl here would hijack content
        # questions that belong to manuals/documents.
        (
            "revisions",
            "revision",
            "on file",
            "indexed",
        ),
    ),
    "evidence.read": (
        ToolEffect.READ,
        ("search_evidence_media",),
        # Evidence-media phrasing (R3): how operators name captured evidence
        # ("the photo of the nameplate", "what evidence was recorded on the
        # pump job"). Word-boundary matched; plurals need their own entries.
        # "image"/"images" deliberately included — no other pack claims them.
        (
            "photo",
            "photos",
            "picture",
            "pictures",
            "photograph",
            "photographed",
            "evidence",
            "image",
            "images",
            "recording",
            "recordings",
            "snapshot",
            "snapshots",
        ),
    ),
    # --- Specialist write packs (execution-plan S11) -------------------------
    #
    # wf2/wf3/wf4/wf6 already carry these tools at runtime; before S11 the
    # catalog covered wf8 only, so ``authorize_invocation`` would have denied
    # every one of them with ``workflow_not_allowed`` the moment the middleware
    # was attached. Cataloguing them is what makes the middleware attachable —
    # each is NATIVE_PERMISSION-mapped through the same RBAC requirement the
    # list filter already uses, so exposure is unchanged and enforcement is new.
    #
    # Selection terms are empty on purpose: ``_pack_scores`` only scores READ
    # packs, and a write pack must never be selectable from a user sentence —
    # the specialist workflow decides, not the phrasing.
    "parts.write": (
        ToolEffect.WRITE,
        (
            "create_part",
            "update_part",
            "set_part_parameter",
            "deactivate_part",
            "create_part_category",
            "add_bom_item",
        ),
        (),
    ),
    "stock.write": (
        ToolEffect.WRITE,
        (
            "create_stock_location",
            "add_stock",
            "remove_stock",
            "transfer_stock",
            "count_stock",
            "merge_stock",
            "update_stock_location",
            "change_stock_status",
            "split_stock",
            "convert_stock",
            "add_stock_test_result",
            "serialize_stock",
            "install_stock",
            "uninstall_stock",
            "assign_stock",
            "return_stock",
        ),
        (),
    ),
    "company.write": (
        ToolEffect.WRITE,
        ("create_company", "create_supplier_part", "create_manufacturer_part"),
        (),
    ),
    "procurement.write": (
        ToolEffect.WRITE,
        (
            "create_purchase_order",
            "add_po_line_item",
            "issue_purchase_order",
            "receive_po_items",
            "cancel_purchase_order",
            "update_purchase_order",
            "complete_purchase_order",
            "delete_purchase_order",
            "delete_po_line_item",
        ),
        (),
    ),
    "sales.write": (
        ToolEffect.WRITE,
        ("create_sales_order", "add_so_line_item"),
        (),
    ),
}

#: Which workflows may invoke each effectful pack. ``authorize_invocation``
#: denies a call whose bound workflow is absent here, so omission must fail
#: closed rather than grant a newly added or misspelled write pack to all rails.
_DEFAULT_PACK_WORKFLOWS = frozenset({"wf8", "general"})
_SPECIALIST_WORKFLOWS = frozenset({"wf2", "wf3", "wf4", "wf6"})
_ALL_PACK_WORKFLOWS = _DEFAULT_PACK_WORKFLOWS | _SPECIALIST_WORKFLOWS
_PACK_WORKFLOWS: dict[str, frozenset[str]] = {
    # Email is part of the research rail as well as the everyday assistant.
    "email.write": frozenset({"wf3", "wf8", "general"}),
    "parts.write": _SPECIALIST_WORKFLOWS,
    "stock.write": _SPECIALIST_WORKFLOWS,
    "company.write": _SPECIALIST_WORKFLOWS,
    "sales.write": _SPECIALIST_WORKFLOWS,
    # Procurement writes belong to wf4 alone: it is the HITL-gated rail.
    "procurement.write": frozenset({"wf4"}),
}


def pack_workflows(pack_id: str) -> frozenset[str]:
    """Return one pack's rails, sharing only known read packs by default."""
    explicit = _PACK_WORKFLOWS.get(pack_id)
    if explicit is not None:
        return explicit
    spec = _PACK_SPECS.get(pack_id)
    if spec is not None and spec[0] is ToolEffect.READ:
        return _ALL_PACK_WORKFLOWS
    return frozenset()


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
    # Without an adjacency entry a kanban primary is the ONLY pack selected, so
    # "which job needs the M8 bolts?" would lose every stock and parts tool.
    # maintenance.read joins so "is the pump job ready to start" keeps the
    # readiness tools (worst pick-2: 4 + 6 + 5 = 15 <= MAX_INITIAL_TOOLS).
    "kanban.read": frozenset({"parts.read", "stock.read", "maintenance.read"}),
    # A maintenance question usually names the asset, and a machine question
    # often leads to its open repairs and manual. manuals.read is adjacent to
    # BOTH: "what does the manual say about the pump's repair boundaries"
    # makes maintenance the primary, and without this edge the pack the user
    # named was unreachable. Worst stack: 9 + 5 + manuals 1 + SQL 1 = 16
    # <= MAX_INITIAL_TOOLS.
    "maintenance.read": frozenset({"machines.read", "manuals.read"}),
    "machines.read": frozenset({"maintenance.read", "manuals.read"}),
    "manuals.read": frozenset({"machines.read"}),
    # Evidence questions almost always name the job or the asset ("what did
    # the tech photograph on WO-104"), so evidence-primary pulls both
    # maintenance and machines. Worst evidence-primary stack:
    # evidence 1 + maintenance 6 + machines 9 = 16 <= MAX_INITIAL_TOOLS.
    # Deliberately NO reverse edges: maintenance- and machines-primary worst
    # stacks already sit at exactly 16, and a 17th tool trips the trim loop.
    # If live goldens show maintenance-primary photo questions losing the
    # tool, the fix is an explicit-evidence rider (manuals-rider clone), not
    # an adjacency edge.
    "evidence.read": frozenset({"maintenance.read", "machines.read"}),
    # S8a: an inventory-primary turn keeps machine-name resolution and the
    # content search beside the registry listing. Worst sources-primary
    # stack: sources 1 + machines 9 + manuals 1 = 11 <= MAX_INITIAL_TOOLS.
    # Deliberately NO reverse edges — manuals/maintenance/machines worst
    # stacks already sit at the budget; inventory reachability comes from
    # the sources-primary rider, not from adjacency.
    "sources.read": frozenset({"machines.read", "manuals.read"}),
}

#: Packs inside the InvenTree data graph, where a read-only SQL fallback makes
#: sense. Deliberately NOT the same as ``_ADJACENT_PACKS``: kanban has
#: neighbours (a job question often needs stock) but SQL is not a fallback for a
#: board, and email is outside the graph entirely.
_SQL_HATCH_PACKS: frozenset[str] = frozenset({
    "parts.read",
    "stock.read",
    "bom.read",
    "documents.read",
    "procurement.read",
    "sales.read",
    "build.read",
    "analytics.read",
})

#: Base verb forms plus gerunds: request shells take gerund complements
#: ("would you mind archiving card 12?"), which carry the same write intent.
#: Plural/-ed forms are deliberately absent -- "-s" forms are usually nouns
#: ("returns", "orders") and "-ed" forms are usually participles in questions
#: ("was the stock count updated?").
_WRITE_PATTERN = re.compile(
    r"\b(add|allocate|approve|archive|assign|attach|cancel|change|complete|convert|"
    r"count|create|deactivate|delete|email|generate|install|issue|mark|merge|move|"
    r"order|purchase|receive|remove|restore|return|send|serialize|set|split|transfer|"
    r"uninstall|update|"
    r"adding|allocating|approving|archiving|assigning|attaching|cancelling|canceling|changing|"
    r"completing|converting|counting|creating|deactivating|deleting|emailing|"
    r"generating|installing|issuing|marking|merging|moving|ordering|purchasing|"
    r"receiving|removing|restoring|returning|sending|serializing|setting|splitting|"
    r"transferring|uninstalling|updating)\b",
    re.IGNORECASE,
)

#: Write verbs that are also ordinary read vocabulary. "count the fasteners over
#: 2000" is a question; "count stock in bin A-3" is a stocktake. Only these verbs
#: are eligible to be re-read as questions, and only on an explicit read signal
#: that is not the verb itself.
_AMBIGUOUS_WRITE_VERBS = frozenset({"count", "counting"})

#: A verb immediately followed by "of" is being used as a noun ("count of parts").
_NOUN_USE_RE = re.compile(r"\s+of\b", re.IGNORECASE)

#: Compound nouns whose head word is also a write verb. "Show build order lines"
#: and "List supplier purchase orders" are read requests, but "order" and
#: "purchase" match the write pattern, so both would route to a specialist and
#: misdirect the question. Whether a phrase is nominal is decided positionally
#: in ``_mask_noun_phrases``: a clause-initial compound is verbal ("Return
#: orders 4512 and 4513 to the supplier"), and an order-reference is nominal
#: only at clause start or after a noun-context token ("show order so-100" vs
#: "place order po-100").
_NOUN_PHRASE_RE = re.compile(
    r"\b(?:purchase|sales|build|work|return)\s+orders?\b"
    r"|\border\s+(?:lines?|status|number)\b"
    r"|\borders?\s+(?:#\d+|(?:po|so|wo|bo|mo|rma)-?\d+)\b",
    re.IGNORECASE,
)
_ORDER_REF_RE = re.compile(r"\borders?\s+(?:#\d+|(?:po|so|wo|bo|mo|rma)-?\d+)\b", re.IGNORECASE)
_ORDER_PART_RE = re.compile(r"\border\s+(?:lines?|status|number)\b", re.IGNORECASE)

#: Light-verb writes whose action word is not itself in ``_WRITE_PATTERN``:
#: "place an order", "process a return", "an order placed". Treated as write
#: matches subject to question/request protection, so "was the order placed
#: yesterday?" still reads.
_LIGHT_VERB_WRITE_RE = re.compile(
    r"\b(?:place|put\s+in|raise|submit|process|book|log|file|start|begin|initiate)"
    r"\s+(?:an?\s+|the\s+)?(?:purchase\s+|sales\s+)?(?:orders?|returns?|purchases?)\b"
    r"|\bput\s+(?:an?\s+|the\s+)?(?:purchase\s+|sales\s+)?(?:orders?|returns?)\s+in\b"
    r"|\b(?:orders?|returns?|purchases?)\s+(?:placed|raised|submitted|processed|booked|logged|filed)\b",
    re.IGNORECASE,
)

#: R1 noun-context: an English imperative verb can never directly follow these
#: tokens, so a write-pattern word right after one is a noun, adjective, or
#: participle ("the return", "was marked", "show the transfer history").
#: 'get' is deliberately absent (causative "get the order cancelled" is a write
#: request) and so are 'first'/'next' (sentence-initial imperative adverbs:
#: "first order 50 units" is an instruction).
_NOUN_CONTEXT_TOKENS = frozenset(
    "the a an any some no this that these those each every "  # noqa: SIM905 - grouped, format-stable
    "my our your their its his her "
    "of in on for from per about regarding "
    "is are was were been being be "
    "marked flagged "
    "last latest recent previous open pending current "
    "average total sum highest lowest maximum minimum "
    "show list display view find check see".split()
)
#: 'as' is noun-context only after a state participle: "is po-100 marked as
#: complete?" reads, while "record po-100 as complete" stays an instruction.
_AS_PARTICIPLES = frozenset({"marked", "flagged", "listed", "shown", "recorded", "set"})
_READ_LOOKUP_VERBS = frozenset({"show", "list", "display", "view", "find", "check", "see"})
_SUBJECT_PRONOUNS = frozenset({"we", "i", "you", "they", "anyone", "anybody", "someone"})

#: R3 clause machinery. Clauses split on sentence punctuation, dashes, and the
#: comma-splice shapes that jam an imperative onto a question ("what's low,
#: just order 50 more"); an appositive comma never splits because participles
#: like 'marked' cannot match the base-form verb list.
_CLAUSE_SPLIT_RE = re.compile(
    r"[.!?;:]"
    r"|\s+[-–—]+\s+|[–—]"  # noqa: RUF001 - literal en/em dashes are clause boundaries
    r"|,\s*(?=(?:and|then|but|so|also|please)\b)"
    r"|,\s*(?=(?:[\w']+\s+){0,2}(?:add|allocate|approve|archive|assign|attach|cancel|"
    r"change|complete|convert|count|create|deactivate|delete|email|generate|install|"
    r"issue|mark|merge|move|order|purchase|receive|remove|restore|return|send|"
    r"serialize|set|split|transfer|uninstall|update)\b)"
    # A bare 'and'/'then' directly before a write verb or verbal compound starts
    # a new clause ("check stock then return orders 4512 to acme").
    r"|\s+(?:and|then)\s+(?=(?:add|allocate|approve|archive|assign|attach|cancel|"
    r"change|complete|convert|create|deactivate|delete|email|generate|install|"
    r"issue|mark|merge|move|order|purchase|receive|remove|restore|return|send|"
    r"serialize|set|split|transfer|uninstall|update)\b)",
    re.IGNORECASE,
)
#: A clause is interrogative when it opens with a question shape, tolerating a
#: short greeting/discourse prefix ("hey claude, did we receive ...").
_INTERROGATIVE_RE = re.compile(
    r"^\s*(?:(?:hi|hey|hello|ok|okay|hmm|so|and|but|also|btw|sorry|thanks|right|claude)\b[,\s]*){0,3}"
    r"(?:"
    r"what|which|who|whose|whom|"
    r"was|were|did|does|is|are|am|has|"
    r"(?:do|have)\s+(?:we|i|you|they|there|anyone|anybody)|"
    r"how\s+(?:many|much|long|often|old|far|do|does|did|is|are|was|were|has|have|can|could|should|would|will)|"
    r"(?:when|where|why)\s+(?:is|are|was|were|do|does|did|has|have|had|will|would|can|could|should)"
    r")\b",
    re.IGNORECASE,
)
#: Direct and indirect request forms. A request phrased as a question is still
#: a request ("can you...", "is it possible to..."), so question protection is
#: disabled for the whole message when one is present.
_REQUEST_FORM_RE = re.compile(
    r"\bplease\b|\b(?:can|could|will|would)\s+(?:you|u|someone|somebody)\b"
    r"|\bare\s+you\s+able\b|\bis\s+it\s+possible\b|\bis\s+there\s+(?:a|any)\s+way\b"
    r"|\bwould\s+it\s+be\s+possible\b|\bany\s+chance\b",
    re.IGNORECASE,
)
#: An order reference followed by a quantity or urgency tail is a reorder or
#: submit request, not an elliptical lookup ("order po-100 again", "order
#: po-100 asap").
_ORDER_REF_ACTION_TAIL_RE = re.compile(
    r"\s+\d|\s+(?:more|another|again|now|asap|today|immediately|urgently)\b",
    re.IGNORECASE,
)
#: Do-support and modal auxiliaries demand a following bare verb, so inside a
#: question "did <subject> <verb>" the verb belongs to the question ("did the
#: warehouse transfer 200 units?"). Be-forms do not: "which bins are empty move
#: stock there" completes at 'empty', making 'move' a spliced imperative.
_DO_MODAL_AUX = frozenset(
    "do does did will would can could should shall must may might".split()  # noqa: SIM905
)
#: Tokens that read as verbs when standing between an auxiliary and a match --
#: if one intervenes, the auxiliary's verb slot is already taken and the match
#: is a spliced imperative ("how many m3 screws do we have order 100 more").
_INTERVENING_VERB_TOKENS = frozenset(
    "have has had be been being get got make made take took is are was were".split()  # noqa: SIM905
)
#: A tail beginning with a preposition/particle or a temporal adverb marks a
#: participle or question continuation ("was stock split INTO batches?", "was
#: the order placed YESTERDAY?") rather than an imperative object.
_PROTECT_TAIL_RE = re.compile(
    r"\s+(?:into|to|in|on|at|from|by|with|as|of|off|up|out|back|over|under|per|for|"
    r"aside|down|away|"
    r"yesterday|today|tomorrow|this|last|earlier|recently|already|then|"
    r"correctly|properly|successfully|"
    r"\w+ed)\b",  # a participle tail marks a noun use: "was the stock count updated?"
    re.IGNORECASE,
)
#: "any chance OF cancelling po-100" is a request: 'of' after these heads does
#: not shield a gerund the way a plain preposition shields a noun.
_REQUEST_OF_HEADS = frozenset({"chance", "mind", "way", "possibility", "instead"})


def _preceding_token(text: str, pos: int) -> str | None:
    """The whitespace-separated token immediately before ``pos``, or ``None``."""
    end = pos
    while end > 0 and text[end - 1].isspace():
        end -= 1
    if end == pos:  # no whitespace between the token and the match position
        return None
    begin = end
    while begin > 0 and not text[begin - 1].isspace():
        begin -= 1
    return text[begin:end] if begin < end else None


def _noun_context_before(text: str, pos: int) -> bool:
    """R1: whether grammar forbids an imperative at ``pos`` (O(1) per match)."""
    token = _preceding_token(text, pos)
    if token is None:
        return False
    if token in _NOUN_CONTEXT_TOKENS:
        return True
    if token == "as":
        idx = text.rfind(token, 0, pos)
        return _preceding_token(text, idx) in _AS_PARTICIPLES
    return False


def _clause_spans(text: str) -> list[tuple[int, int]]:
    """Non-empty clause spans of ``text`` in order."""
    spans: list[tuple[int, int]] = []
    start = 0
    for boundary in _CLAUSE_SPLIT_RE.finditer(text):
        if boundary.start() > start:
            spans.append((start, boundary.start()))
        start = boundary.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans or [(0, len(text))]


def _first_token(text: str, span: tuple[int, int]) -> str:
    clause = text[span[0] : span[1]].lstrip()
    head = clause.split(" ", 1)[0] if clause else ""
    return head.strip(",")


def _mask_noun_phrases(text: str, clauses: list[tuple[int, int]] | None = None) -> str:
    """Blank nominal compounds only, preserving offsets.

    Verbal uses keep their write signal: a clause-initial compound ("Return
    orders 4512 ... to the supplier") and an order-reference in verb position
    ("place order po-100", "ship order so-100") are not masked.
    """
    if clauses is None:
        clauses = _clause_spans(text)
    starts = [span[0] for span in clauses]
    question = [bool(_INTERROGATIVE_RE.match(text[a:b])) for a, b in clauses]
    read_led = [_first_token(text, span) in _READ_LOOKUP_VERBS for span in clauses]

    def clause_index(pos: int) -> int:
        return max(0, bisect_right(starts, pos) - 1)

    def replace(match: re.Match) -> str:
        blank = " " * (match.end() - match.start())
        idx = clause_index(match.start())
        phrase = match.group(0)
        if _ORDER_PART_RE.fullmatch(phrase):
            return blank  # 'order lines/status/number' is always nominal
        if _ORDER_REF_RE.fullmatch(phrase):
            tail = text[match.end() :]
            if _ORDER_REF_ACTION_TAIL_RE.match(tail):
                return phrase  # reorder/submit request: "order po-100 again/asap"
            lead = text[clauses[idx][0] : match.start()]
            if not lead.strip() or _noun_context_before(text, match.start()):
                return blank  # elliptical lookup: "order so-100", "show order so-100"
            return phrase
        if question[idx] or read_led[idx] or _noun_context_before(text, match.start()):
            return blank
        # A verbal compound needs an object tail within its clause ("Return
        # orders 4512 ... to the supplier"); a clause-final compound is a plain
        # noun ("list parts and purchase orders").
        clause_tail = text[match.end() : clauses[idx][1]]
        if not clause_tail.strip():
            return blank
        return phrase

    return _NOUN_PHRASE_RE.sub(replace, text)


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

#: A maintenance work-order or repair-packet reference. Requires at least one
#: character after the prefix and permits hyphenated site schemes
#: ("WO-000104", "WO-WW-R-001", "RP-9"). Deliberately excludes po/so/bo/mo
#: prefixes -- those are order documents with their own packs.
_WORKORDER_REF_RE = re.compile(r"\b(?:wo|rp)-[a-z0-9][a-z0-9-]*\b", re.IGNORECASE)

#: Packs paired with the SQL escape hatch when nothing else scores, so the model
#: is never handed `query_database` as its only way to reach inventory data.
_DEFAULT_READ_PACKS = ("parts.read", "stock.read")


def _write_intent(text: str) -> str | None:
    """Return the write verb routing this query to a specialist, else ``None``.

    Misclassification here is asymmetric. Selection already restricts to
    READ-effect packs, so a write mistaken for a read merely offers read tools,
    while a read mistaken for a write is misrouted to a specialist (under wf8
    enforce, a leaked imperative with no scoring domain term additionally lands
    on a zero-tool clarify agent rather than the full-toolset specialist).
    Ambiguous and grammar-context matches therefore resolve toward read -- but
    only on evidence: a noun context (R1), a nominal compound (the positional
    mask), or an interrogative clause that is not a request form (R3).
    """
    clauses = _clause_spans(text)
    masked = _mask_noun_phrases(text, clauses)

    matches = list(_WRITE_PATTERN.finditer(masked))
    # Light verbs match the unmasked text: "put a purchase order in" masks its
    # compound, but the surrounding construction is still a write request.
    # Offsets stay valid because masking only blanks in place.
    light = list(_LIGHT_VERB_WRITE_RE.finditer(text))
    if not matches and not light:
        return None

    if _REQUEST_FORM_RE.search(masked) is not None:
        question_spans: list[tuple[int, int]] = []
    else:
        question_spans = [
            span for span in clauses if _INTERROGATIVE_RE.match(masked[span[0] : span[1]])
        ]
    question_starts = [span[0] for span in question_spans]

    def in_question_span(pos: int) -> int:
        idx = bisect_right(question_starts, pos) - 1
        if idx >= 0 and pos < question_spans[idx][1]:
            return idx
        return -1

    def question_protects(match: re.Match) -> bool:
        """Whether the question owns this verb, or it is a spliced imperative.

        The verb belongs to the question when (a) a subject pronoun precedes it
        ("did we order 500 units?"), (b) the nearest do/modal auxiliary in the
        span still owes a bare verb ("did the warehouse transfer 200 units?"),
        or (c) its tail opens with a preposition/temporal ("was stock split
        into batches?") or the clause ends ("is the build complete?"). Anything
        else after a complete predicate is a splice ("which bins are empty move
        stock there").
        """
        idx = in_question_span(match.start())
        if idx < 0:
            return False
        if _preceding_token(masked, match.start()) in _SUBJECT_PRONOUNS:
            return True
        span_start = question_spans[idx][0]
        between = masked[span_start : match.start()].split()
        for position in range(len(between) - 1, -1, -1):
            token = between[position].strip(",.?!").lower()
            if token in _DO_MODAL_AUX:
                after_aux = between[position + 1 :]
                return not any(
                    _WRITE_PATTERN.fullmatch(candidate.strip(",.?!"))
                    or candidate.strip(",.?!").lower() in _INTERVENING_VERB_TOKENS
                    for candidate in after_aux
                )
        tail = masked[match.end() : question_spans[idx][1]]
        return not tail.strip() or _PROTECT_TAIL_RE.match(tail) is not None

    def protected(match: re.Match, *, noun_context: bool = True) -> bool:
        if noun_context and _noun_context_before(masked, match.start()):  # R1
            # Exception: "any chance of cancelling po-100" -- 'of' after a
            # request head does not make a gerund nominal.
            token = _preceding_token(masked, match.start())
            if token == "of" and match.group(0).lower().endswith("ing"):
                of_idx = masked.rfind(token, 0, match.start())
                if _preceding_token(masked, of_idx) in _REQUEST_OF_HEADS:
                    return question_protects(match)
            return True
        return question_protects(match)  # R3

    # Light-verb matches skip R1: the reversed form ("an order placed") is
    # inherently determiner-preceded; only a question protects it.
    surviving_light = [m for m in light if not protected(m, noun_context=False)]
    if surviving_light:
        return surviving_light[0].group(0).lower().split()[0]

    verb_matches = [m for m in matches if not protected(m)]
    if not verb_matches:
        return None
    decisive = [m for m in verb_matches if m.group(0).lower() not in _AMBIGUOUS_WRITE_VERBS]
    if decisive:
        return decisive[0].group(0).lower()
    # Ambiguous verbs yield to one explicit read signal. The strict-shape search
    # runs once over the whole masked text: its vocabulary is disjoint from the
    # verb list, so excluding the match span is unnecessary (the old per-match
    # residual rebuild was quadratic on verb-dense input).
    strict_shape = _STRICT_READ_SHAPE_RE.search(masked) is not None
    for m in verb_matches:
        if _NOUN_USE_RE.match(masked[m.end() :]) or strict_shape:
            return None
    return verb_matches[0].group(0).lower()


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
    # ``delete_kanban_card`` hard-deletes a work order. ``WorkOrder`` cascades to
    # ``WorkOrderEvent``, ``WorkOrderCommand``, ``WorkOrderCloseout``,
    # ``WorkOrderDeviation``, ``CloseoutPartUsage`` and ``CloseoutReading``, so a
    # single call destroys the governance and closeout history of completed work.
    # The tool is additionally scope-free: ``aimms.kanban.change`` is a flat group,
    # unlike the REST work-order surface which applies ``scope_for_actor``.
    # Deletion returns as a governed command (permission, customer scope, expected
    # version, strict confirmation, durable audit record); until then it is withheld.
    # The function itself was deleted with the S12 write-tool retirement; this
    # entry remains as the fail-closed backstop should it ever be re-added.
    "delete_kanban_card": (
        "Hard delete cascades away work-order audit and closeout history, and the "
        "tool applies no customer scope; withheld pending a governed delete command"
    ),
}


# The direct-ORM kanban write tools were DELETED (execution-plan S12 step 3,
# after the governed-flag soak): board mutations from chat/voice go through
# the governed proposal rail (aichat.services.proposals) and the REST surface
# only. The retirement is enforced by absence — the tools, their kanban.write
# pack, and this module's disable-branch are gone. Do not re-add a direct
# write tool; add a governed command instead.


#: Tools whose authority is tenant-scoped rather than role-scoped. Kept as a
#: literal id set so the policy branch does not depend on import order.
_MACHINE_READ_TOOL_IDS = frozenset({
    "search_machines",
    "get_machine_overview",
    "get_machine_health",
    "get_machine_signals",
    "get_machine_signal_trend",
    "get_machine_anomalies",
    "get_machine_parts",
    "get_machine_maintenance_history",
    "get_machine_attachments",
})

#: The maintenance pack shares the machines rationale and authorizer: a
#: work_order:view grant says "may read jobs", never "may read *this* job",
#: and tasks.ai_read re-checks the row itself on every call.
_MAINTENANCE_READ_TOOL_IDS = frozenset({
    "search_work_orders",
    "get_work_order_overview",
    "get_work_order_readiness",
    "get_work_order_repair_state",
    "get_open_repairs_for_machine",
    "get_work_order_history",
    "get_work_order_closeout",
})


def _authorization_policy(tool: Any, tool_id: str) -> AuthorizationPolicy:
    from ai.core.tools.rbac import tool_requirement

    requirement = tool_requirement(tool)
    withheld_reason = _WITHHELD_TOOLS.get(tool_id)
    if withheld_reason is not None:
        # Checked before the RBAC map so a mapped-but-withheld tool cannot fall
        # through to NATIVE_PERMISSION and become exposed again.
        return AuthorizationPolicy(kind=PolicyKind.DISABLED, reason=withheld_reason)
    if tool_id == "get_part_attachments":
        return AuthorizationPolicy(
            kind=PolicyKind.RESOURCE_AUTHORIZER,
            all_of=(("part", "view"),),
            authorizer="part_attachment_access",
        )
    if tool_id in _MACHINE_READ_TOOL_IDS or tool_id in _MAINTENANCE_READ_TOOL_IDS:
        # Deliberately NOT plain NATIVE_PERMISSION. A work_order:view grant is
        # global, while asset rows belong to a customer or a client -- so the
        # role says "may read assets", never "may read *this* asset". The
        # resource authorizer additionally requires a resolvable maintenance
        # scope, and the shared readers re-check the row itself on every call.
        # The authorizer string is reused for both packs on purpose: the guard
        # branch is the maintenance-scope check, and an unknown string denies
        # everything.
        return AuthorizationPolicy(
            kind=PolicyKind.RESOURCE_AUTHORIZER,
            all_of=(("work_order", "view"),),
            authorizer="machine_scope_access",
        )
    if tool_id == "search_manuals":
        # Site-scoped controlled-document retrieval. Deliberately NOT
        # machine_scope_access: the corpus filter is built from deployment
        # constants server-side, and machine narrowing degrades rather than
        # gates -- the manual must stay readable before a scope resolver is
        # configured.
        return AuthorizationPolicy(
            kind=PolicyKind.RESOURCE_AUTHORIZER,
            all_of=(("work_order", "view"),),
            authorizer="controlled_corpus_access",
        )
    if tool_id == "search_attachment_docs":
        # R2 attachment-corpus retrieval, dark behind its flag. The DISABLED
        # branch is cached by capability_catalog() -- acceptable because env
        # flags are process-stable; the guard arm and the tool itself re-check
        # per call. any_of, not all_of: either role exposes the tool, and the
        # tool always restricts its model_type filter to the granted arms
        # (part:view -> part docs, work_order:view -> machine docs), so a
        # single-role user can never receive the other arm's documents.
        from ai.core.config import get_settings

        if not get_settings().feature_attachment_rag_retrieval:
            return AuthorizationPolicy(
                kind=PolicyKind.DISABLED,
                reason="FEATURE_ATTACHMENT_RAG_RETRIEVAL is off",
            )
        return AuthorizationPolicy(
            kind=PolicyKind.RESOURCE_AUTHORIZER,
            any_of=(("part", "view"), ("work_order", "view")),
            authorizer="attachment_corpus_access",
        )
    if tool_id == "search_evidence_media":
        # R3 evidence-media retrieval, dark behind its flag. Same caching
        # caveat as the attachment branch (env flags are process-stable; the
        # guard arm and the tool re-check per call). all_of, not any_of: one
        # role grants the whole corpus — every owner type (workorder / step /
        # assetmachine media) is an evidence surface under maintenance scope,
        # and part-owned media never ingests, so there is no second arm.
        # This branch must sit BEFORE the requirement fallthrough: the rbac
        # map row (text-chat filtering) would otherwise claim the tool as
        # NATIVE_PERMISSION and lose the flag gate + authorizer.
        from ai.core.config import get_settings

        if not get_settings().feature_media_rag_retrieval:
            return AuthorizationPolicy(
                kind=PolicyKind.DISABLED,
                reason="FEATURE_MEDIA_RAG_RETRIEVAL is off",
            )
        return AuthorizationPolicy(
            kind=PolicyKind.RESOURCE_AUTHORIZER,
            all_of=(("work_order", "view"),),
            authorizer="evidence_media_access",
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


def _catalog_tools() -> tuple[Any, ...]:
    """Every tool any registered workflow can dispatch, in stable order.

    Before S11 this was wf8's toolset alone, which is why attaching the
    invocation middleware to wf2/wf3/wf4/wf6 would have denied their every
    call: an uncatalogued tool is an unknown tool. The union is deduplicated
    by name (wf2-wf6 share the inventory toolset) and each entry's authorized
    workflows come from its pack, not from membership in this list.
    """
    from ai.core.integrations.attachment_corpus import ATTACHMENT_CORPUS_TOOLS
    from ai.core.integrations.controlled_document_corpus import CONTROLLED_CORPUS_TOOLS
    from ai.core.integrations.document_search import DOCUMENT_SEARCH_TOOLS
    from ai.core.integrations.email.tools import EMAIL_TOOLS
    from ai.core.integrations.inventory_tools import INVENTORY_READ_TOOLS, INVENTORY_TOOLS
    from ai.core.integrations.kanban_tools import KANBAN_TOOLS
    from ai.core.integrations.media_corpus import EVIDENCE_MEDIA_TOOLS
    from ai.core.integrations.source_inventory_tools import SOURCE_INVENTORY_TOOLS
    from ai.core.tools.inventree.write.purchase_orders import PURCHASE_ORDER_WRITE_TOOLS

    ordered: list[Any] = []
    seen: set[str] = set()
    for tool in (
        *INVENTORY_READ_TOOLS,
        *EMAIL_TOOLS,
        *KANBAN_TOOLS,
        *DOCUMENT_SEARCH_TOOLS,
        *CONTROLLED_CORPUS_TOOLS,
        *ATTACHMENT_CORPUS_TOOLS,
        *EVIDENCE_MEDIA_TOOLS,
        *SOURCE_INVENTORY_TOOLS,
        # Specialist rails (wf2/wf3/wf4/wf6) below; the wf8 prefix above keeps
        # the existing catalog order stable for the manifest.
        *INVENTORY_TOOLS,
        *PURCHASE_ORDER_WRITE_TOOLS,
    ):
        name = tool_name(tool)
        if name in seen:
            continue
        seen.add(name)
        ordered.append(tool)
    return tuple(ordered)


@lru_cache(maxsize=1)
def capability_catalog() -> tuple[CapabilityEntry, ...]:
    """Return the ordered, immutable WF8 capability catalog."""
    pack_index = _pack_index()
    entries: list[CapabilityEntry] = []
    seen_ids: set[str] = set()

    for tool in _catalog_tools():
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
                workflows=pack_workflows(pack_id),
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
_LEXICON_STOPWORDS = frozenset({
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
})
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


_MACHINE_LEXICON_CACHE_KEY = "aimms:capability:machine_lexicon:v1"
#: Machine names churn far less than part categories, but a newly commissioned
#: asset should still become askable within a shift rather than a deploy.
_MACHINE_LEXICON_TTL_SECONDS = 600
#: Asset names are frequently bare model numbers, so the length floor is lower
#: than the category one; the stop list still rejects the generic ones.
_MACHINE_LEXICON_MIN_LENGTH = 3
_MACHINE_LEXICON_STOPWORDS = frozenset({
    "asset",
    "assets",
    "line",
    "machine",
    "machines",
    "plant",
    "spare",
    "test",
    "unit",
    "units",
})


def machine_lexicon_enabled() -> bool:
    """Whether live machine names contribute selection terms."""
    return bool(_ai_setting("feature_machine_lexicon", True))


def _machine_lexicon_variants(name: str) -> set[str]:
    """Return the casefolded terms one machine name contributes.

    Both the whole name and its individual words are offered, because an
    operator says "how is the feed pump doing" far more often than they say the
    asset's full registered name.
    """
    cleaned = " ".join(str(name or "").casefold().split())
    if not cleaned:
        return set()
    candidates = {cleaned}
    candidates.update(cleaned.split())
    return {
        term
        for term in candidates
        if len(term) >= _MACHINE_LEXICON_MIN_LENGTH
        and term not in _MACHINE_LEXICON_STOPWORDS
        and not term.isdigit()
    }


def _build_machine_lexicon() -> frozenset[str]:
    from assets.models import AssetMachine

    terms: set[str] = set()
    rows = AssetMachine.objects.values_list("name", "manufacturer", "model")
    for name, manufacturer, model in rows[:500]:
        terms |= _machine_lexicon_variants(name)
        terms |= _machine_lexicon_variants(manufacturer)
        terms |= _machine_lexicon_variants(model)
    return frozenset(terms)


def machine_lexicon() -> frozenset[str]:
    """Return selection terms derived from live machine names.

    A routing hint only, exactly like ``category_lexicon``: it decides which
    tools are offered, never what any of them return. Every machine tool
    re-derives the actor's scope per call, so a name here that the asker cannot
    reach still yields nothing -- the lexicon is deliberately unscoped because
    scoping it would make tool selection itself an asset-existence oracle.

    A hard-coded noun list can never cover what a site actually calls its
    equipment; this is why "is there a manual for the chiller" routes correctly
    without "chiller" appearing anywhere in the pack spec.
    """
    try:
        from django.core.cache import cache

        cached = cache.get(_MACHINE_LEXICON_CACHE_KEY)
        if cached is not None:
            return frozenset(cached)
        terms = _build_machine_lexicon()
        cache.set(_MACHINE_LEXICON_CACHE_KEY, sorted(terms), _MACHINE_LEXICON_TTL_SECONDS)
        return terms
    except Exception as exc:
        logger.info("Machine lexicon unavailable", extra={"error_type": type(exc).__name__})
        return frozenset()


def invalidate_machine_lexicon(**_kwargs: Any) -> None:
    """Drop the cached machine lexicon so a new asset is askable next turn."""
    try:
        from django.core.cache import cache

        cache.delete(_MACHINE_LEXICON_CACHE_KEY)
    except Exception as exc:
        logger.info(
            "Machine lexicon invalidation failed",
            extra={"error_type": type(exc).__name__},
        )


#: S7 A2 actor-scoped ASR hints: per-user cache key + provider-safe cap.
_ACTOR_HINTS_CACHE_PREFIX = "aimms:capability:actor_hints:v1"
_ACTOR_HINTS_TTL_SECONDS = 600
_ACTOR_HINTS_CAP = 64


def actor_phrase_hints(user_id) -> list[str]:
    """ASR phrase hints scoped to ONE actor: their machines and open jobs.

    The reverted first build (S7 A2) exported the deliberately UNSCOPED
    ``machine_lexicon`` into per-user provider sessions — cross-tenant name
    egress, because that lexicon exists to route tools, not to leave the
    server. This lexicon is derived through the same authorization the tools
    themselves use (``assets.ai_read.machines_in_scope`` and the tasks scope
    filter under the acting user), so a session's hints can never carry
    another actor's machine names or work-order references.

    Failure degrades to an empty list: hints only improve transcription, so
    absence is safe.
    """
    if user_id is None:
        return []
    try:
        from django.contrib.auth import get_user_model
        from django.core.cache import cache

        key = f"{_ACTOR_HINTS_CACHE_PREFIX}:{user_id}"
        cached = cache.get(key)
        if cached is not None:
            return list(cached)

        user = get_user_model().objects.filter(pk=user_id, is_active=True).first()
        if user is None:
            return []

        terms: set[str] = set()
        from assets import ai_read

        for row in ai_read.machines_in_scope(user, limit=_ACTOR_HINTS_CAP):
            name = str(getattr(row, "name", "") or "").strip()
            if name:
                terms.add(name)

        try:
            from tasks.models import WorkOrder
            from tasks.scope import work_order_scope_filter

            references = (
                WorkOrder.objects
                .filter(work_order_scope_filter(user))
                .exclude(status=WorkOrder.STATUS_DONE)
                .exclude(reference="")
                .order_by("-updated_at")
                .values_list("reference", flat=True)[:16]
            )
            terms.update(str(ref) for ref in references)
        except Exception:  # hints are best-effort; scope errors mean none
            pass

        hints = sorted(terms)[:_ACTOR_HINTS_CAP]
        cache.set(key, hints, _ACTOR_HINTS_TTL_SECONDS)
        return hints
    except Exception as exc:
        logger.info("Actor phrase hints unavailable", extra={"error_type": type(exc).__name__})
        return []


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
    the stored singular form and must not score twice. Uses the compiled
    alternation -- the per-term scan cost 400ms/turn at 5000 categories.
    """
    pattern = _lexicon_pattern(frozenset(lexicon))
    return pattern is not None and pattern.search(text) is not None


@lru_cache(maxsize=8)
def _lexicon_pattern(lexicon: frozenset[str]) -> re.Pattern[str] | None:
    """One compiled alternation per lexicon; the naive per-term scan is O(n*m)."""
    if not lexicon:
        return None
    alternation = "|".join(re.escape(term) for term in sorted(lexicon, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)")


#: Per-message cap on hint scanning. The transcript window is already bounded,
#: but a single pasted wall of text must not stall the turn.
_HINT_CONTENT_CAP = 2000


def matched_machine_terms(
    query: str,
    machines: Iterable[Mapping[str, Any]],
    history: Iterable[Any] | None = None,
) -> tuple[dict, ...]:
    """RBAC-scoped machine descriptors mentioned in this turn or its history.

    The machine twin of :func:`matched_category_terms`, and the gap S22
    closes: the clarify path could observe *which category* a sentence named
    but never *which machine*. Pure matcher — the caller fetches descriptors
    (``{machine_id, name, serial}``) through the same authorized resolver the
    corpus search uses, so anything returned here is already in the actor's
    scope. Ranked by match strength (serial hit outranks full-name hit
    outranks token overlap), capped at three.

    USER history rows only, deliberately: an assistant row that asked a
    question card literally contains every option label, so matching against
    assistant text made each asked card guarantee the same matches forever —
    the self-reinforcing loop observed live 2026-08-08. The user's own words
    are the only signal for "which machine did you mean".
    """
    if not isinstance(history, (list, tuple)):
        history = ()
    texts = [" ".join(str(query or "").casefold().split())[:_HINT_CONTENT_CAP]]
    for entry in history:
        if isinstance(entry, dict) and entry.get("role") == "user":
            content = str(entry.get("content") or "")
            texts.append(" ".join(content.casefold().split())[:_HINT_CONTENT_CAP])
    haystack = " \n ".join(texts)
    haystack_tokens = set(re.findall(r"[a-z0-9][a-z0-9-]+", haystack))

    scored: list[tuple[int, int, dict]] = []
    for position, descriptor in enumerate(machines):
        name = str(descriptor.get("name") or "").strip()
        serial = str(descriptor.get("serial") or "").strip()
        if not name:
            continue
        name_folded = " ".join(name.casefold().split())
        score = 0
        if serial and serial.casefold() in haystack:
            score = 3
        elif name_folded and name_folded in haystack:
            score = 2
        else:
            name_tokens = {
                token for token in re.findall(r"[a-z0-9][a-z0-9-]+", name_folded) if len(token) >= 3
            }
            overlap = name_tokens & haystack_tokens
            if name_tokens and len(overlap) >= max(1, len(name_tokens) // 2):
                score = 1
        if score:
            scored.append((score, position, dict(descriptor)))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(descriptor for _, _, descriptor in scored[:3])


def matched_category_terms(query: str, history: Iterable[Any] | None = None) -> tuple[str, ...]:
    """Live category names mentioned in this turn or its replayed transcript.

    Powers the deterministic category hint on lookup turns: on a follow-up
    ("just the ones over 2000") the noun lives in the *history*, where the
    selection lexicon signal cannot fire because scoring reads the current
    message only. Purely observational -- never contributes to pack scoring or
    write-intent -- and fail-open: no lexicon means no terms. Singular/plural
    families collapse to one reported term, capped at three.
    """
    lexicon = category_lexicon() if category_lexicon_enabled() else frozenset()
    pattern = _lexicon_pattern(lexicon)
    if pattern is None:
        return ()

    if not isinstance(history, (list, tuple)):
        history = ()
    texts = [" ".join(str(query or "").casefold().split())[:_HINT_CONTENT_CAP]]
    for entry in history:
        # Only conversational rows feed the hint, mirroring _run_input: a tool
        # result mentioning a category must not steer the next turn.
        if isinstance(entry, dict) and entry.get("role") in ("user", "assistant"):
            content = str(entry.get("content") or "")
            texts.append(" ".join(content.casefold().split())[:_HINT_CONTENT_CAP])

    found: set[str] = set()
    for text in texts:
        for match in pattern.finditer(text):
            found.add(match.group(0))
    collapsed = {
        term
        for term in found
        if not (term.endswith("s") and term[:-1] in found)
        and not (term.endswith("ies") and f"{term[:-3]}y" in found)
    }
    return tuple(sorted(collapsed))[:3]


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
    selected = [primary, *candidates[:max_adjacent]]
    # An explicit documentation request must never lose the single-tool
    # manuals pack to adjacency scoring. "What does the manual say about the
    # pump's repair boundaries" scores maintenance/machines above manuals, and
    # ``max_adjacent`` then silently dropped the pack the user NAMED — the
    # model answered "I don't have a documentation search tool" (live,
    # 2026-08-06). The rider costs one tool and only fires when the sentence
    # itself scored a manuals term and the topology already permits the pair.
    if (
        "manuals.read" not in selected
        and scores.get("manuals.read", 0) > 0
        and "manuals.read" in _ADJACENT_PACKS.get(primary, frozenset())
    ):
        selected.append("manuals.read")
    # Same rider for the single-tool evidence pack, WITHOUT the topology
    # gate: maintenance/machines deliberately carry no edge into
    # evidence.read (their worst stacks already sit at MAX_INITIAL_TOOLS),
    # so "the evidence photos on work order WO-104" scores maintenance as
    # primary and the pack the user NAMED was unreachable (live golden,
    # 2026-08-21 — media-nameplate-grounded abstained). The rider costs one
    # tool; maintenance 6 + machines 9 + evidence 1 lands exactly on the
    # budget, and any rarer overflow falls to the trim loop, which drops
    # the lowest-scoring adjacent, never the explicitly-named pack a higher
    # score protects.
    if "evidence.read" not in selected and scores.get("evidence.read", 0) > 0:
        selected.append("evidence.read")
    return tuple(selected)


def _with_sql_escape_hatch(pack_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Attach the read-only SQL pack to an InvenTree data selection.

    Keyword and shape matching cannot anticipate every phrasing, so the two SQL
    tools ride along as the universal fallback for questions the specific tools
    cannot express. Packs outside the InvenTree data graph (email, kanban) are
    left alone: SQL is not a fallback for a mailbox. This widens exposure, not
    access -- ``exposure_authorized`` still requires a view permission and
    ``_run_query`` re-checks every relation the plan touches per invocation.
    """
    if pack_ids[0] not in _SQL_HATCH_PACKS:
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


#: How many prior messages an anaphoric follow-up may inherit its subject from.
#: Short on purpose: the subject of "where are those?" is the turn just before.
_HISTORY_SUBJECT_MESSAGES = 4

#: A follow-up must actually refer back before it may inherit a subject. Without
#: this, ANY message that scores nothing -- a greeting, a thank-you, an off-topic
#: aside -- would silently acquire the previous turn's read tools, including raw
#: SQL, and social turns would stop being social after the first question.
_ANAPHORA_RE = re.compile(
    r"\b(?:those|these|them|they|their|it|its|that|this|there|"
    r"the ones?|same|above|previous|earlier|first one|last one)\b"
    r"|^\s*(?:and|also|what about|how about|just|only)\b",
    re.IGNORECASE,
)


def _carried_scores(query: str, context: Mapping[str, Any] | None) -> dict[str, int]:
    """Pack scores inherited from the recent transcript, for anaphoric turns.

    Consulted only when the current message scores nothing at all AND actually
    refers back to something. Returns the scores of the most recent message that
    did score, so the follow-up inherits one subject rather than a blend of the
    whole conversation.
    """
    if not _ANAPHORA_RE.search(query or ""):
        return {}
    history = (context or {}).get("conversation_history")
    if not isinstance(history, (list, tuple)):
        return {}
    recent = [
        entry
        for entry in history
        if isinstance(entry, dict) and entry.get("role") in ("user", "assistant")
    ][-_HISTORY_SUBJECT_MESSAGES:]
    for entry in reversed(recent):
        text = " ".join(str(entry.get("content") or "").casefold().split())
        if not text:
            continue
        scores = _pack_scores(text)
        if scores:
            return scores
    return {}


#: S3: typed analysis task intent → the packs that answer it (primary
#: first). Deliberately bypasses ``_ADJACENT_PACKS`` topology: analytics
#: and maintenance are not lexical neighbours, but an aggregate question
#: needs both. Only ANALYSIS_INTENTS appear here — everything else keeps
#: lexical selection.
_INTENT_PACKS: dict[str, tuple[str, ...]] = {
    "fleet_aggregate": ("analytics.read", "maintenance.read"),
    "trend_analysis": ("analytics.read", "maintenance.read"),
    "record_retrieval": ("maintenance.read", "machines.read"),
    "manual_wo_comparison": ("maintenance.read", "manuals.read"),
    # S8a: inventory questions get the registry tool FIRST; manuals rides
    # along for the follow-up content question.
    "source_inventory": ("sources.read", "machines.read", "manuals.read"),
    "manual_fact": ("manuals.read", "machines.read"),
}


def select_capabilities(
    query: str,
    *,
    lookup_type: str | None = None,
    context: Mapping[str, Any] | None = None,
    profile: frozenset[tuple[str, str]] = frozenset(),
    authenticated: bool = False,
    task_intent: str | None = None,
) -> CapabilitySelection:
    """Select one stable read pack plus the reviewed adjacent packs.

    Scoring reads the current message. Only when that message names no subject
    at all does selection fall back to the replayed transcript, so an anaphoric
    follow-up ("and where are those located?") inherits the subject of the turn
    it refers to instead of dead-ending on a tool-less clarification.

    ``task_intent`` (S3, server-derived) outranks both the lexical scores and
    the ``lookup_type`` hint for analysis intents — these are exactly the
    questions the lexical signals misroute — and the history-subject
    carryover never runs for them, so a prior off-domain turn cannot pull an
    analysis question with it.
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
    machine_terms = machine_lexicon() if widened and machine_lexicon_enabled() else frozenset()
    if _matches_lexicon(normalized, machine_terms):
        # Naming an actual asset is strong evidence, and it is the case a fixed
        # noun list cannot cover: sites call their equipment whatever they like.
        scores["machines.read"] = scores.get("machines.read", 0) + 1
        signals.append("machine_lexicon")
    if _WORKORDER_REF_RE.search(normalized):
        # A spoken work-order or repair-packet reference ("wo-000104",
        # "WO-WW-R-001", "rp-9") is patterned, unlike machine names, so a
        # static regex routes it without a DB lexicon. Purchase/sales/build
        # refs (po-/so-/bo-) deliberately do not match.
        scores["maintenance.read"] = scores.get("maintenance.read", 0) + _SHAPE_SCORE
        signals.append("workorder_reference")
    if widened and _AGGREGATION_SHAPE_RE.search(normalized):
        scores["analytics.read"] = scores.get("analytics.read", 0) + _SHAPE_SCORE
        signals.append("shape")

    intent_packs = _INTENT_PACKS.get(task_intent or "")
    if intent_packs:
        # S3: typed-intent selection. Seeds the scores so the trim loop can
        # still rank, but the pack tuple itself is direct — no lexical
        # primary, no adjacency walk, and (critically) no history-subject
        # carryover for analysis turns.
        signals.append("task_intent")
        for pack_id in intent_packs:
            scores[pack_id] = scores.get(pack_id, 0) + _SHAPE_SCORE
        pack_ids: tuple[str, ...] = intent_packs
    else:
        primary = _LOOKUP_PACKS.get(lookup_type or "")
        # S8a sources-primary rider: an inventory-shaped sentence's best tool
        # IS the inventory tool, and position 0 is structurally protected
        # from the trim loop (it drops from pack_ids[1:] only) — an appended
        # score-0 pack would be the first casualty on exactly the stacks
        # that need it. The shape is shared with the intent classifier so
        # routing and selection cannot drift.
        from ai.core.analysis.intent import is_source_inventory_question

        if primary is None and is_source_inventory_question(normalized):
            primary = "sources.read"
            scores["sources.read"] = scores.get("sources.read", 0) + _SHAPE_SCORE
            signals.append("source_inventory_shape")
        if primary is None and scores:
            primary = sorted(
                scores,
                key=lambda pack_id: (pack_id == "analytics.read", -scores[pack_id], pack_id),
            )[0]
        if primary is None:
            # Nothing in this message names a subject. Before giving up, inherit the
            # subject from the replayed transcript: "and where are those located?"
            # is a real question whose noun lives in the previous turn, and handing
            # it a tool-less clarification turn is how a follow-up dead-ends. Only
            # this otherwise-empty path consults history, so an ordinary turn still
            # scores on its own words.
            carried = _carried_scores(normalized, context)
            if carried:
                scores = carried
                signals.append("history_subject")
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
    "matched_category_terms",
    "matched_machine_terms",
    "select_capabilities",
    "selection_v2_enabled",
    "serialized_contract_bytes",
    "tool_contract",
    "tool_name",
]
