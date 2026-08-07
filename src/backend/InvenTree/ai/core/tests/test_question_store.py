"""S22: the pending-question store — single slot, consume-on-read, TTL."""

# ruff: noqa: E402

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.questions.pending import (
    PENDING_QUESTION_SCHEMA_VERSION,
    CachedPendingQuestionStore,
    InMemoryPendingQuestionStore,
)


def _record(**overrides) -> dict:
    record = {
        "schema_version": PENDING_QUESTION_SCHEMA_VERSION,
        "interrupt_id": "q-1",
        "options": [{"id": "machine:1", "label": "Pump 1"}],
    }
    record.update(overrides)
    return record


@pytest.fixture(params=["memory", "cached"])
def store(request):
    if request.param == "memory":
        return InMemoryPendingQuestionStore()
    from django.core.cache import cache

    cache.clear()
    return CachedPendingQuestionStore()


def test_take_consumes_exactly_once(store):
    """An answer can never be replayed: the second take sees nothing."""
    store.save(7, _record())
    assert store.take(7)["interrupt_id"] == "q-1"
    assert store.take(7) is None


def test_single_slot_overwrites(store):
    """A newer question replaces the old one entirely."""
    store.save(7, _record(interrupt_id="q-old"))
    store.save(7, _record(interrupt_id="q-new"))
    assert store.take(7)["interrupt_id"] == "q-new"
    assert store.take(7) is None


def test_threads_are_isolated(store):
    store.save(1, _record(interrupt_id="q-a"))
    store.save(2, _record(interrupt_id="q-b"))
    assert store.take(2)["interrupt_id"] == "q-b"
    assert store.take(1)["interrupt_id"] == "q-a"


def test_wrong_schema_version_reads_as_nothing(store):
    """A stale or foreign record must never surface as a question."""
    store.save(7, _record(schema_version="question-card-v0"))
    assert store.take(7) is None


def test_non_dict_record_reads_as_nothing():
    from django.core.cache import cache

    cache.clear()
    store = CachedPendingQuestionStore()
    cache.set("aimms:pending-question:7", "not-a-dict")
    assert store.take(7) is None


def test_ttl_expiry_reads_as_nothing():
    """No auto-selected default on timeout — expiry is silence."""
    from django.core.cache import cache

    cache.clear()
    store = CachedPendingQuestionStore(timeout_seconds=0)
    store.save(7, _record())
    assert store.take(7) is None


def test_take_lock_contention_fails_closed():
    """Two racing turns must never both act on one question."""
    from django.core.cache import cache

    cache.clear()
    store = CachedPendingQuestionStore()
    store.save(7, _record())
    cache.add("aimms:pending-question:7:take", True, timeout=5)
    assert store.take(7) is None
    # The record is still there for the lock holder; after the lock clears,
    # a later take succeeds (contention loses, it does not destroy).
    cache.delete("aimms:pending-question:7:take")
    assert store.take(7)["interrupt_id"] == "q-1"
