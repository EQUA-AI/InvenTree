"""§2.6 set-equality pins for the memory vocabulary (GR-53: deprecate-and-add)."""

from __future__ import annotations

from ai.core.memory import vocabulary
from ai.core.memory.summary_body import ITEM_LISTS


def test_per_fact_enums_match_the_binding_table():
    assert {m.value for m in vocabulary.FactVerification} == {
        "user_confirmed",
        "tool_verified",
        "human_verified",
        "inferred",
    }
    assert {m.value for m in vocabulary.FactLifecycle} == {
        "proposed",
        "active",
        "resolved",
        "superseded",
        "expired",
        "forgotten",
        "withdrawn",
    }
    assert {m.value for m in vocabulary.FactOrigin} == {
        "user_explicit",
        "compaction",
        "mem0",
        "tool_read",
        "closeout",
        "incident",
    }


def test_memory_type_by_list_covers_every_item_list():
    assert set(vocabulary.MEMORY_TYPE_BY_LIST) == set(ITEM_LISTS)
    assert set(vocabulary.MEMORY_TYPE_BY_LIST.values()) <= set(vocabulary.MemoryType)
    assert vocabulary.MEMORY_TYPE_BY_LIST["open_questions"] == vocabulary.MemoryType.OPEN_ISSUE


def test_m1_item_level_enums_are_unchanged():
    assert {m.value for m in vocabulary.Lifecycle} == {"active", "superseded", "tombstoned"}
    assert {m.value for m in vocabulary.VerificationClass} == {
        "server_record",
        "compacted_summary",
        "user_authored",
        "unverified",
    }
    assert {m.value for m in vocabulary.MemoryType} == {
        "equipment_fact",
        "procedure_note",
        "site_convention",
        "schedule",
        "open_issue",
        "contact_role",
        "user_preference",
    }
    for name in ("FactVerification", "FactLifecycle", "FactOrigin", "MEMORY_TYPE_BY_LIST"):
        assert name in vocabulary.__all__
