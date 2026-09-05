"""§10.9 read-time tags: the deterministic ``RecallFilter`` per routed intent.

M1 declares the seam — the builder derives the filter from the typed
``TaskIntent`` and records it in ``retrieval_plan`` — with no fact store to
narrow; it first acts in M3a as a ``WHERE memory_type = ANY(%s)`` clause
inside the single recall statement (GR-31). ``general``, an unknown intent
or the kill switch (``AIMMS_MEMORY_TYPE_FILTER=off``) mean "all six
operational types", identical to recall without tags: a mis-routed turn
loses nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ai.core.memory.vocabulary import OPERATIONAL_MEMORY_TYPES, MemoryType, Topic

_ALL = OPERATIONAL_MEMORY_TYPES

#: The committed §10.9 table. ``test_memory_context_builder`` fails when
#: ``TaskIntent`` gains a member without a row here.
INTENT_MEMORY_TYPES: dict[str, frozenset[str]] = {
    "diagnostic": frozenset({
        MemoryType.EQUIPMENT_FACT,
        MemoryType.PROCEDURE_NOTE,
        MemoryType.OPEN_ISSUE,
    }),
    "safety_lookup": frozenset({
        MemoryType.PROCEDURE_NOTE,
        MemoryType.SITE_CONVENTION,
        MemoryType.EQUIPMENT_FACT,
        MemoryType.CONTACT_ROLE,
        MemoryType.SCHEDULE,
    }),
    "governed_action": frozenset({
        MemoryType.PROCEDURE_NOTE,
        MemoryType.SITE_CONVENTION,
        MemoryType.EQUIPMENT_FACT,
        MemoryType.CONTACT_ROLE,
        MemoryType.SCHEDULE,
    }),
    "part_advice": frozenset({MemoryType.EQUIPMENT_FACT, MemoryType.SITE_CONVENTION}),
    "manual_fact": frozenset({
        MemoryType.EQUIPMENT_FACT,
        MemoryType.PROCEDURE_NOTE,
        MemoryType.SITE_CONVENTION,
    }),
    "manual_wo_comparison": frozenset({
        MemoryType.EQUIPMENT_FACT,
        MemoryType.PROCEDURE_NOTE,
        MemoryType.SITE_CONVENTION,
    }),
    "source_inventory": frozenset({
        MemoryType.EQUIPMENT_FACT,
        MemoryType.PROCEDURE_NOTE,
        MemoryType.SITE_CONVENTION,
    }),
    "record_retrieval": frozenset({
        MemoryType.EQUIPMENT_FACT,
        MemoryType.OPEN_ISSUE,
        MemoryType.SCHEDULE,
        MemoryType.SITE_CONVENTION,
    }),
    "fleet_aggregate": frozenset({
        MemoryType.EQUIPMENT_FACT,
        MemoryType.OPEN_ISSUE,
        MemoryType.SCHEDULE,
        MemoryType.SITE_CONVENTION,
    }),
    "trend_analysis": frozenset({
        MemoryType.EQUIPMENT_FACT,
        MemoryType.OPEN_ISSUE,
        MemoryType.SCHEDULE,
        MemoryType.SITE_CONVENTION,
    }),
    "general": _ALL,
}

INTENT_BOOST_TOPICS: dict[str, frozenset[str]] = {
    "safety_lookup": frozenset({Topic.SAFETY}),
    "governed_action": frozenset({Topic.SAFETY}),
    "part_advice": frozenset({Topic.PARTS}),
    "manual_fact": frozenset({Topic.DOCUMENTATION}),
    "manual_wo_comparison": frozenset({Topic.DOCUMENTATION}),
    "source_inventory": frozenset({Topic.DOCUMENTATION}),
    "record_retrieval": frozenset({Topic.PLANNING}),
    "fleet_aggregate": frozenset({Topic.PLANNING}),
    "trend_analysis": frozenset({Topic.PLANNING}),
}


@dataclass(frozen=True, slots=True)
class RecallFilter:
    """What the recall statement may narrow and re-order by (§10.9)."""

    task_intent: str
    memory_types: frozenset[str]
    boost_topics: frozenset[str] = field(default_factory=frozenset)
    type_filter_enabled: bool = True

    def reauthorize(self, candidate_ids: tuple[str, ...]) -> tuple[str, ...]:
        """Round trip (2) of GR-31.

        M1: nothing to reauthorize (no fact store), zero queries — the
        posture is recorded on ``ScopeLabel.source == "policy_key"``. M3a
        re-derives ``client_codes_for_actor`` here and fails closed (GR-11).
        """
        return tuple(candidate_ids)

    def as_plan_fields(self) -> dict[str, object]:
        """Content-free projection for ``retrieval_plan`` and telemetry."""
        return {
            "task_intent": self.task_intent,
            "memory_types": sorted(self.memory_types),
            "boost_topics": sorted(self.boost_topics),
            "type_filter_enabled": self.type_filter_enabled,
        }


def recall_filter_for(task_intent: str | None, *, type_filter_enabled: bool = True) -> RecallFilter:
    """The filter for a routed intent; ``general``/unknown/off -> all six types."""
    intent = str(task_intent or "general")
    if not type_filter_enabled:
        return RecallFilter(intent, _ALL, frozenset(), False)
    return RecallFilter(
        intent,
        INTENT_MEMORY_TYPES.get(intent, _ALL),
        INTENT_BOOST_TOPICS.get(intent, frozenset()),
        True,
    )


__all__ = ["INTENT_BOOST_TOPICS", "INTENT_MEMORY_TYPES", "RecallFilter", "recall_filter_for"]
