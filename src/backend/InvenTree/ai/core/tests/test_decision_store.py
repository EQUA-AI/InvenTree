"""B1: immutable pending-decision model and guarded stores."""

# ruff: noqa: E402

from __future__ import annotations

import os
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.decisions.models import (
    PENDING_DECISION_SCHEMA_VERSION,
    PUBLIC_DECISION_FIELDS,
    SERVER_DECISION_FIELDS,
    DecisionDeliveryState,
    DecisionKind,
    DecisionState,
    PendingDecision,
)
from ai.core.decisions.store import (
    CachedPendingDecisionStore,
    InMemoryPendingDecisionStore,
)

T0 = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


def _decision(**overrides) -> PendingDecision:
    values = {
        "decision_id": "decision-1",
        "kind": DecisionKind.ACTION,
        "source_id": "proposal-1",
        "revision": 1,
        "state": DecisionState.PRESENTED,
        "target_label": "WO-104 Pump service",
        "sections": ({"id": "change", "text": "Put WO-104 on hold"},),
        "required_review_sections": ("change",),
        "allowed_responses": ("confirm hold", "change that", "cancel"),
        "required_phrase": "confirm hold",
        "locale": "en-US",
        "voice_eligible": True,
        "preview_hash": "a" * 64,
        "expires_at": T0 + timedelta(seconds=300),
        "sequence": 1,
        "utterance_id": "utterance-1",
        "delivery_state": DecisionDeliveryState.REQUESTED,
        "review_acknowledged": False,
        "actor_user_pk": "7",
        "session_id": "session-1",
        "thread_id": "7",
        "scope_hash": "b" * 64,
        "nonce": "nonce-1",
        "executable": {"adapter": "proposal", "proposal_id": "proposal-1"},
        "source_content": "Put work order 104 on hold for maintenance",
        "armed_at": T0,
    }
    values.update(overrides)
    return PendingDecision(**values)


@pytest.fixture(params=["memory", "cached"])
def store(request):
    if request.param == "memory":
        return InMemoryPendingDecisionStore()
    from django.core.cache import cache

    cache.clear()
    return CachedPendingDecisionStore()


def test_public_projection_excludes_every_private_binding():
    decision = _decision()
    public = decision.to_public_dict()
    record = decision.to_record()

    assert set(public) == set(PUBLIC_DECISION_FIELDS)
    assert not set(SERVER_DECISION_FIELDS) & set(public)
    assert record["schema_version"] == PENDING_DECISION_SCHEMA_VERSION
    assert set(SERVER_DECISION_FIELDS) <= set(record)
    assert PendingDecision.from_record(record) == decision


def test_model_is_frozen_and_detaches_json_inputs():
    section = {"id": "change", "text": "original"}
    executable = {"args": {"reason": "maintenance"}}
    decision = _decision(sections=(section,), executable=executable)
    section["text"] = "tampered"
    executable["args"]["reason"] = "tampered"

    assert decision.sections[0]["text"] == "original"
    assert decision.executable is not None
    assert decision.executable["args"]["reason"] == "maintenance"
    with pytest.raises(FrozenInstanceError):
        decision.sequence = 2
    with pytest.raises(TypeError):
        cast("Any", decision.sections[0])["text"] = "tampered"
    with pytest.raises(TypeError):
        cast("Any", decision.executable)["args"]["reason"] = "tampered"


def test_voice_ineligible_decision_requires_a_reason():
    with pytest.raises(ValueError, match="voice_ineligible_reason is required"):
        _decision(voice_eligible=False)

    decision = _decision(
        voice_eligible=False,
        voice_ineligible_reason="This action requires a screen.",
    )
    assert decision.voice_eligible is False


def test_naive_timing_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        _decision(armed_at=datetime(2026, 9, 12, 10, 0))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("voice_eligible", "false"),
        ("review_acknowledged", 1),
        ("sections", ("not-an-object",)),
        ("executable", ["not-an-object"]),
        ("kind", "unknown"),
        ("state", "armed"),
        ("delivery_state", "completed"),
    ],
)
def test_malformed_typed_values_are_rejected(field, value):
    with pytest.raises(ValueError):
        _decision(**{field: value})


def test_review_sections_must_have_unique_known_ids():
    with pytest.raises(ValueError, match="non-empty string id"):
        _decision(sections=({"text": "missing id"},), required_review_sections=())
    with pytest.raises(ValueError, match="section ids must be unique"):
        _decision(
            sections=({"id": "change"}, {"id": "change"}),
            required_review_sections=("change",),
        )
    with pytest.raises(ValueError, match="unknown sections"):
        _decision(required_review_sections=("missing",))


def test_peek_never_consumes_and_take_consumes_once(store):
    assert store.save(7, _decision()) is True
    assert store.peek(7).decision_id == "decision-1"
    assert store.peek(7).decision_id == "decision-1"
    assert store.take(7).decision_id == "decision-1"
    assert store.take(7) is None


def test_single_slot_overwrites_and_threads_are_isolated(store):
    assert store.save(7, _decision(decision_id="old")) is True
    assert store.save(7, _decision(decision_id="new")) is True
    assert store.save(8, _decision(decision_id="other", thread_id="8")) is True

    assert store.peek(7).decision_id == "new"
    assert store.peek(8).decision_id == "other"


def test_compare_and_set_requires_current_sequence_and_an_advance(store):
    original = _decision()
    updated = replace(original, sequence=2, state=DecisionState.EXECUTING)
    assert store.save(7, original) is True
    assert store.replace_if(7, 1, updated) is True
    assert store.peek(7) == updated

    stale = replace(updated, sequence=3, state=DecisionState.RESOLVED)
    assert store.replace_if(7, 1, stale) is False
    assert store.replace_if(7, 2, replace(updated, state=DecisionState.RESOLVED)) is False
    assert store.replace_if(7, 2, replace(updated, sequence=4)) is False
    assert store.peek(7) == updated


def test_compare_and_set_rejects_timing_rollbacks(store):
    playback_completed = T0 + timedelta(seconds=12)
    original = _decision(
        playback_completed_at=playback_completed,
        review_turns=2,
    )
    assert store.save(7, original) is True

    assert (
        store.replace_if(
            7,
            1,
            replace(original, sequence=2, playback_completed_at=None),
        )
        is False
    )
    assert (
        store.replace_if(
            7,
            1,
            replace(
                original,
                sequence=2,
                playback_completed_at=playback_completed - timedelta(seconds=1),
            ),
        )
        is False
    )
    assert store.replace_if(7, 1, replace(original, sequence=2, review_turns=1)) is False
    assert store.peek(7) == original


def test_compare_and_set_preserves_timing_fields(store):
    original = _decision()
    playback_completed = T0 + timedelta(seconds=12)
    updated = replace(
        original,
        sequence=2,
        playback_completed_at=playback_completed,
        review_turns=1,
        expires_at=playback_completed + timedelta(seconds=120),
    )
    assert store.save(7, original) is True
    assert store.replace_if(7, 1, updated) is True

    loaded = store.peek(7)
    assert loaded.armed_at == T0
    assert loaded.playback_completed_at == playback_completed
    assert loaded.review_turns == 1
    assert loaded.expires_at == playback_completed + timedelta(seconds=120)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision_id", "decision-2"),
        ("kind", DecisionKind.SELECTION),
        ("source_id", "proposal-2"),
        ("revision", 2),
        ("actor_user_pk", "8"),
        ("session_id", "session-2"),
        ("thread_id", "8"),
        ("scope_hash", "c" * 64),
        ("nonce", "nonce-2"),
        ("armed_at", T0 + timedelta(seconds=1)),
    ],
)
def test_compare_and_set_cannot_swap_authority_or_source_binding(store, field, value):
    original = _decision()
    assert store.save(7, original) is True
    replacement = replace(original, sequence=2, **{field: value})

    if field == "thread_id":
        with pytest.raises(ValueError, match="thread binding"):
            store.replace_if(7, 1, replacement)
    else:
        assert store.replace_if(7, 1, replacement) is False
    assert store.peek(7) == original


def test_thread_binding_mismatch_is_rejected(store):
    with pytest.raises(ValueError, match="thread binding"):
        store.save(8, _decision())


def test_wrong_schema_and_malformed_records_fail_closed():
    from django.core.cache import cache

    cache.clear()
    store = CachedPendingDecisionStore()
    record = _decision().to_record()
    record["schema_version"] = "pending-decision-v0"
    cache.set("aimms:pending-decision:7", record)
    assert store.peek(7) is None

    cache.set("aimms:pending-decision:7", {"schema_version": PENDING_DECISION_SCHEMA_VERSION})
    assert store.peek(7) is None

    wrong_container = _decision().to_record()
    wrong_container["sections"] = {}
    cache.set("aimms:pending-decision:7", wrong_container)
    assert store.peek(7) is None

    unknown = _decision().to_record()
    unknown["future_field"] = True
    cache.set("aimms:pending-decision:7", unknown)
    assert store.peek(7) is None


def test_cache_expiry_reads_as_nothing():
    from django.core.cache import cache

    cache.clear()
    store = CachedPendingDecisionStore(timeout_seconds=0)
    assert store.save(7, _decision()) is True
    assert store.peek(7) is None


def test_cached_lock_contention_preserves_record():
    from django.core.cache import cache

    cache.clear()
    store = CachedPendingDecisionStore()
    original = _decision()
    assert store.save(7, original) is True
    cache.add("aimms:pending-decision:7:mutate", True, timeout=5)

    assert store.take(7) is None
    assert store.replace_if(7, 1, replace(original, sequence=2)) is False
    assert store.peek(7) == original

    cache.delete("aimms:pending-decision:7:mutate")
    assert store.take(7) == original


def test_cached_lock_release_does_not_delete_a_new_owner():
    from django.core.cache import cache

    cache.clear()
    store = CachedPendingDecisionStore()
    old_token = store._acquire(7)
    assert old_token is not None
    cache.delete("aimms:pending-decision:7:mutate")
    cache.set("aimms:pending-decision:7:mutate", "new-owner", timeout=5)

    store._release(7, old_token)

    assert cache.get("aimms:pending-decision:7:mutate") == "new-owner"


def test_in_memory_lock_contention_preserves_record():
    store = InMemoryPendingDecisionStore()
    original = _decision()
    assert store.save(7, original) is True
    assert store._mutation_lock.acquire(blocking=False) is True
    try:
        assert store.take(7) is None
        assert store.replace_if(7, 1, replace(original, sequence=2)) is False
    finally:
        store._mutation_lock.release()
    assert store.peek(7) == original
