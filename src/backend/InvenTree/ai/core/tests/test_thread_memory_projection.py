"""M2 PR 5: the pure projections behind the inspection routes (ai.core.app).

``summary_label`` is the only part of a stored summary the list/get routes
ship (plan of record §8.6 item 1, GR-49); ``thread_memory_projection``
parses the body for the owner-only memory endpoint (§8.6 item 2). Both are
pure — no ORM, a thread is any object with the three columns.
"""

# ruff: noqa: E402

from __future__ import annotations

import json
import os
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.app import (
    ThreadMemoryCorrectionRequest,
    ThreadMemoryItem,
    ThreadMemoryResponse,
    summary_label,
    thread_memory_projection,
)
from ai.core.memory.summary_body import ITEM_LISTS, upgrade_body
from pydantic import ValidationError

FACT = "the motor is 5.5 kW"


def _thread(summary: str | None, *, through: int = 4, next_sequence: int = 9):
    return SimpleNamespace(
        pk="thread_1",
        summary=summary,
        summary_through_sequence=through,
        next_sequence=next_sequence,
    )


def _stored(label: str, body: dict) -> str:
    return label + "\n" + json.dumps(body)


# --------------------------------------------------------------------------- #
# summary_label                                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("Pump 3", "Pump 3"),
        ("  Pump 3  \n" + json.dumps({"machine_facts": [FACT]}), "Pump 3"),
        ("first\nsecond\nthird", "first"),
        ("\n" + json.dumps({"machine_facts": [FACT]}), ""),
    ],
)
def test_summary_label_is_the_stripped_first_line(summary, expected):
    label = summary_label(summary)
    assert label == expected
    assert "{" not in label
    assert FACT not in label


# --------------------------------------------------------------------------- #
# thread_memory_projection                                                     #
# --------------------------------------------------------------------------- #
def test_empty_summary_projects_empty_lists_and_zero_version():
    payload = thread_memory_projection(_thread("", through=0, next_sequence=1))
    assert isinstance(payload, ThreadMemoryResponse)
    assert payload.thread_id == "thread_1"
    assert payload.label == ""
    assert (payload.through_sequence, payload.latest_sequence) == (0, 0)
    assert payload.body_version == 0
    assert all(getattr(payload, field) == [] for field in ITEM_LISTS)
    assert payload.citation_keys == []
    assert payload.narrative == ""
    assert payload.exclusions_count == 0


def test_label_only_summary_projects_the_label_and_nothing_else():
    payload = thread_memory_projection(_thread("Pump 3"))
    assert payload.label == "Pump 3"
    assert payload.body_version == 0
    assert all(getattr(payload, field) == [] for field in ITEM_LISTS)


def test_legacy_string_body_reads_as_objects():
    legacy = {
        "label": "Pump 3",
        "open_questions": ["which breaker?"],
        "pending_proposals": [],
        "machine_facts": [FACT, ""],
        "corrections": ["not 4 kW"],
        "citation_keys": ["WO-12", "WO-12"],
        "narrative": "The motor is 5.5 kW.",
    }
    payload = thread_memory_projection(_thread(_stored("Pump 3", legacy)))
    assert payload.body_version == 2
    assert payload.latest_sequence == 8
    assert payload.through_sequence == 4
    (question,) = payload.open_questions
    assert isinstance(question, ThreadMemoryItem)
    assert (question.id, question.text, question.lifecycle) == ("oq1", "which breaker?", "active")
    assert question.created_seq == 0  # legacy items carry no sequence
    assert question.superseded_by is None
    assert question.directive_flags == []
    # Blank legacy strings are not items; ids keep counting across lists.
    assert [(i.id, i.text) for i in payload.machine_facts] == [("mf2", FACT)]
    assert [(i.id, i.text) for i in payload.corrections] == [("co3", "not 4 kW")]
    assert payload.citation_keys == ["WO-12"]
    assert payload.narrative == "The motor is 5.5 kW."


def test_history_is_projected_with_lifecycle_flags_and_exclusions():
    body = upgrade_body(
        {"label": "Pump 3", "machine_facts": ["the motor is 4 kW", FACT]}, created_seq=6
    )
    body["machine_facts"][0]["lifecycle"] = "superseded"
    body["machine_facts"][0]["superseded_by"] = "mf2"
    body["machine_facts"][1]["directive_flags"] = ["nl_directive"]
    body["exclusions"] = [
        {"fingerprint": "abc", "item_id": "oq9", "created_seq": 6, "reason": "wrong"}
    ]
    payload = thread_memory_projection(_thread(_stored("Pump 3", body)))
    old, new = payload.machine_facts
    assert (old.id, old.lifecycle, old.superseded_by, old.created_seq) == (
        "mf1",
        "superseded",
        "mf2",
        6,
    )
    assert (new.id, new.lifecycle, new.superseded_by) == ("mf2", "active", None)
    assert new.directive_flags == ["nl_directive"]
    assert new.memory_type == "equipment_fact"
    assert payload.exclusions_count == 1


def test_unparsable_body_projects_the_label_alone():
    payload = thread_memory_projection(_thread("Pump 3\n{not json"))
    assert payload.label == "Pump 3"
    assert payload.body_version == 0
    assert all(getattr(payload, field) == [] for field in ITEM_LISTS)


def test_latest_sequence_never_goes_negative():
    assert thread_memory_projection(_thread("", next_sequence=0)).latest_sequence == 0


# --------------------------------------------------------------------------- #
# The correction request contract (FastAPI maps a ValidationError to 422)     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("item_id", ["", "mf", "zz1", "mf1 ", "[mf1]", "mf" + "1" * 31])
def test_correction_request_rejects_malformed_item_ids(item_id):
    with pytest.raises(ValidationError):
        ThreadMemoryCorrectionRequest(item_id=item_id, action="wrong")


def test_correction_request_rejects_unknown_actions():
    with pytest.raises(ValidationError):
        ThreadMemoryCorrectionRequest(item_id="mf1", action="edit")
    for action in ("wrong", "forget"):
        assert ThreadMemoryCorrectionRequest(item_id="pp12", action=action).action == action


def test_correction_request_has_no_idempotency_key_field():
    """The item id is the idempotency key; a client key would be a dead field.

    ``forget_item`` makes a repeat ``applied`` with no write, and the route
    implements none of ``/chat``'s key semantics (correlation-id minting,
    409 on reuse), so the request model carries exactly two fields and a
    legacy ``idempotency_key`` is ignored rather than retained.
    """
    assert set(ThreadMemoryCorrectionRequest.model_fields) == {"item_id", "action"}
    legacy = ThreadMemoryCorrectionRequest(item_id="mf1", action="wrong", idempotency_key="k")
    assert not hasattr(legacy, "idempotency_key")
    assert legacy.model_dump() == {"item_id": "mf1", "action": "wrong"}
