"""Single-slot pending-question store (S22).

A modality-agnostic clone of the voice Tier-3 pending-write store
(``CachedPendingVoiceWriteStore``): Django cache, one slot per thread, TTL,
and locked consume-on-read ``take()``. Two deliberate differences:

* Payloads are **JSON-safe dicts**, never pickled dataclasses — the record
  is audit data and must survive cache-backend changes.
* Every record carries ``schema_version``; a mismatched or malformed record
  reads as "nothing pending" rather than as a question.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: A question the user has not answered within this window simply expires;
#: there is NO auto-selected default on timeout, ever.
PENDING_QUESTION_TTL_SECONDS = 15 * 60

PENDING_QUESTION_SCHEMA_VERSION = "question-card-v1"


class PendingQuestionStore(Protocol):
    """Single-slot, consume-on-read pending question store, keyed by thread."""

    def save(self, thread_id: Any, record: dict) -> None:
        """Persist the pending question, replacing any existing slot."""

    def take(self, thread_id: Any) -> dict | None:
        """Consume and return the pending question exactly once, or None."""


class InMemoryPendingQuestionStore:
    """Process-local store for tests; the cached store is the deployment seam."""

    def __init__(self) -> None:
        self._records: dict[str, dict] = {}

    def save(self, thread_id: Any, record: dict) -> None:
        """Replace the thread's slot."""
        self._records[str(thread_id)] = record

    def take(self, thread_id: Any) -> dict | None:
        """Pop the thread's slot."""
        record = self._records.pop(str(thread_id), None)
        if not _valid_record(record):
            return None
        return record


class CachedPendingQuestionStore:
    """Django-cache store: cross-worker safe, TTL-bounded, locked take."""

    def __init__(self, timeout_seconds: int = PENDING_QUESTION_TTL_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _key(thread_id: Any) -> str:
        return f"aimms:pending-question:{thread_id}"

    def save(self, thread_id: Any, record: dict) -> None:
        """Replace the thread's slot (single-slot: a new question wins)."""
        from django.core.cache import cache

        cache.set(self._key(thread_id), record, timeout=self.timeout_seconds)

    def take(self, thread_id: Any) -> dict | None:
        """Consume-on-read under a short cross-worker lock.

        Lock contention reads as "nothing pending" — fail closed: two racing
        turns must never both act on one question.
        """
        from django.core.cache import cache

        key = self._key(thread_id)
        lock_key = f"{key}:take"
        if not cache.add(lock_key, True, timeout=5):
            return None
        try:
            record = cache.get(key)
            cache.delete(key)
            if not _valid_record(record):
                return None
            return record
        finally:
            cache.delete(lock_key)


def _valid_record(record: Any) -> bool:
    """A record is only a question if it is a dict of the current schema."""
    return (
        isinstance(record, dict) and record.get("schema_version") == PENDING_QUESTION_SCHEMA_VERSION
    )


__all__ = [
    "PENDING_QUESTION_SCHEMA_VERSION",
    "PENDING_QUESTION_TTL_SECONDS",
    "CachedPendingQuestionStore",
    "InMemoryPendingQuestionStore",
    "PendingQuestionStore",
]
