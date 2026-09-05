"""Closed vocabularies of the memory layer (plan §5.2, §9.4, §10.9, §13.8).

Every value here is an enum the telemetry allowlist may carry (GR-40) and
a page view may render; free text never enters these fields. Add values
the way registry rows are added (GR-53): a written consumer, a golden, and
the domain reviewer's sign-off — deprecate-and-add, never rename.
"""

from __future__ import annotations

from enum import StrEnum


class Slot(StrEnum):
    """The nine §5.2 context slots the builder declares from M1."""

    RECENT_TURNS = "recent_turns"
    THREAD_SUMMARY = "thread_summary"
    USER_PREFERENCES = "user_preferences"
    VERIFIED_ENTITY_FACTS = "verified_entity_facts"
    RECALLED_EPISODES = "recalled_episodes"
    CONTROLLED_DOCUMENT_EVIDENCE = "controlled_document_evidence"
    ATTACHMENT_DOCUMENT_EVIDENCE = "attachment_document_evidence"
    MEDIA_OBSERVATIONS = "media_observations"
    TOPOLOGY_CONTEXT = "topology_context"


#: Why a slot is empty (§9.4 "empty-with-reason"); enum codes, never prose.
class EmptyReason(StrEnum):
    POPULATED = "populated"
    NO_HISTORY_LIMIT = "no_history_limit"
    NO_MESSAGES = "no_messages"
    NO_WATERMARK_YET = "no_watermark_yet"
    COMPACTION_OFF = "compaction_off"
    NO_PREFERENCE_STORE = "no_preference_store"
    NO_VERIFIED_FACTS = "no_verified_facts"
    NOT_A_DIAGNOSTIC_ROUTE = "not_a_diagnostic_route"
    TOOL_NOT_INVOKED_ON_THIS_ROUTE = "tool_not_invoked_on_this_route"
    GRAPH_NOT_YET_AVAILABLE = "graph_not_yet_available"
    BUDGET_TIMEOUT = "budget_timeout"
    RECALL_ERROR = "recall_error"


class DegradeReason(StrEnum):
    """``aimms.memory_degrade_reason`` (§9.5): counts and enums only."""

    NONE = "none"
    BUDGET_TIMEOUT = "budget_timeout"
    RECALL_ERROR = "recall_error"
    NO_HISTORY_LIMIT = "no_history_limit"


class ContentTrust(StrEnum):
    """§9.9: the citation label on every emitted item, never a prompt role.

    ``TRANSCRIPT`` marks the user's own replayed turns (their words in
    their roles); ``UNTRUSTED_UNFENCED`` is the M1 PR B interim for the
    compaction note until PR D wraps it in the marker fence.
    """

    TRUSTED_RECORD = "trusted_record"
    UNTRUSTED_FENCED = "untrusted_fenced"
    UNTRUSTED_UNFENCED = "untrusted_unfenced"
    TRANSCRIPT = "transcript"


class VerificationClass(StrEnum):
    SERVER_RECORD = "server_record"
    COMPACTED_SUMMARY = "compacted_summary"
    USER_AUTHORED = "user_authored"
    UNVERIFIED = "unverified"


class Sensitivity(StrEnum):
    OPERATIONAL = "operational"
    PERSONAL = "personal"
    PREFERENCE = "preference"


class Lifecycle(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    TOMBSTONED = "tombstoned"


class LedgerState(StrEnum):
    """Per-corpus retrieval ledger states (§9.4 rule 1, §9.11 rows)."""

    NOT_CONSULTED = "not_consulted"
    CONSULTED_NONE = "consulted_none"
    USED = "used"


class Corpus(StrEnum):
    CONTROLLED = "controlled"
    UPLOADED = "uploaded"
    MEDIA = "media"
    REPAIR_HISTORY = "repair_history"


class MemoryType(StrEnum):
    """§10.9 ``memory_type``: the subject of a fact (M3a store; M1 filter seam)."""

    EQUIPMENT_FACT = "equipment_fact"
    PROCEDURE_NOTE = "procedure_note"
    SITE_CONVENTION = "site_convention"
    SCHEDULE = "schedule"
    OPEN_ISSUE = "open_issue"
    CONTACT_ROLE = "contact_role"
    USER_PREFERENCE = "user_preference"


#: The six operational types (everything but ``user_preference``).
OPERATIONAL_MEMORY_TYPES: frozenset[str] = frozenset(
    member.value for member in MemoryType if member is not MemoryType.USER_PREFERENCE
)


class Topic(StrEnum):
    """§10.9 ``topics``: the seven energy disciplines plus five work topics."""

    ELECTRICAL = "electrical"
    HYDRAULIC = "hydraulic"
    PNEUMATIC = "pneumatic"
    MECHANICAL = "mechanical"
    THERMAL = "thermal"
    CHEMICAL = "chemical"
    GRAVITY = "gravity"
    CONTROLS = "controls"
    SAFETY = "safety"
    PARTS = "parts"
    PLANNING = "planning"
    DOCUMENTATION = "documentation"


#: The energy-discipline subset, asserted equal to LockoutPoint.EnergySource
#: minus ``other`` where that model is importable (GR-53 set-equality idiom).
ENERGY_TOPICS: frozenset[str] = frozenset({
    Topic.ELECTRICAL,
    Topic.HYDRAULIC,
    Topic.PNEUMATIC,
    Topic.MECHANICAL,
    Topic.THERMAL,
    Topic.CHEMICAL,
    Topic.GRAVITY,
})

MAX_TOPICS = 3

__all__ = [
    "ENERGY_TOPICS",
    "MAX_TOPICS",
    "OPERATIONAL_MEMORY_TYPES",
    "ContentTrust",
    "Corpus",
    "DegradeReason",
    "EmptyReason",
    "LedgerState",
    "Lifecycle",
    "MemoryType",
    "Sensitivity",
    "Slot",
    "Topic",
    "VerificationClass",
]
