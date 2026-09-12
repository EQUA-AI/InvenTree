"""Single-slot pending-decision stores with guarded compare-and-set updates."""

from __future__ import annotations

from threading import Lock
from typing import Any, Protocol
from uuid import uuid4

from ai.core.decisions.models import PendingDecision

DEFAULT_PENDING_DECISION_CACHE_TIMEOUT_SECONDS = 300 + 60
_MUTATION_LOCK_SECONDS = 5


class DecisionStoreUnavailable(RuntimeError):
    """Contention or malformed state must never fall through to a legacy write."""


def _read(record: Any) -> PendingDecision | None:
    if record is None:
        return None
    decision = _decode(record)
    if decision is None:
        raise DecisionStoreUnavailable("Decision state is unavailable; nothing was confirmed.")
    return decision


def _matches(current, expected) -> bool:
    if current is None or expected is None:
        return current is expected
    return (current.decision_id, current.sequence) == (expected.decision_id, expected.sequence)


_CAS_IMMUTABLE_FIELDS = (
    "decision_id",
    "kind",
    "source_id",
    "revision",
    "target_label",
    "sections",
    "required_review_sections",
    "allowed_responses",
    "required_phrase",
    "locale",
    "voice_eligible",
    "voice_ineligible_reason",
    "preview_hash",
    "actor_user_pk",
    "session_id",
    "thread_id",
    "scope_hash",
    "nonce",
    "executable",
    "source_content",
    "armed_at",
)


def _decode(record: Any) -> PendingDecision | None:
    try:
        return PendingDecision.from_record(record)
    except (TypeError, ValueError):
        return None


def _check_binding(thread_id: Any, decision: PendingDecision) -> str:
    key = str(thread_id)
    if decision.thread_id != key:
        raise ValueError("pending decision thread binding does not match its store key")
    return key


def _same_cas_identity(current: PendingDecision, replacement: PendingDecision) -> bool:
    """A CAS may advance state, never substitute a different authority/source."""

    return all(
        getattr(current, field) == getattr(replacement, field) for field in _CAS_IMMUTABLE_FIELDS
    )


def _timing_advances(current: PendingDecision, replacement: PendingDecision) -> bool:
    """Reject replay-window counters or playback completion moving backward."""

    if replacement.review_turns < current.review_turns:
        return False
    return not (
        current.playback_completed_at is not None
        and (
            replacement.playback_completed_at is None
            or replacement.playback_completed_at < current.playback_completed_at
        )
    )


class PendingDecisionStore(Protocol):
    """One pending decision per thread, with optimistic sequence updates."""

    def save(self, thread_id: Any, decision: PendingDecision) -> bool: ...

    def read(self, thread_id: Any) -> PendingDecision | None: ...

    def install(self, decision: PendingDecision, expected: PendingDecision | None) -> bool: ...

    def peek(self, thread_id: Any) -> PendingDecision | None: ...

    def take(self, thread_id: Any) -> PendingDecision | None: ...

    def replace_if(
        self, thread_id: Any, expected_sequence: int, replacement: PendingDecision
    ) -> bool: ...


class InMemoryPendingDecisionStore:
    """Process-local test store with the same guarded mutation semantics."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._mutation_lock = Lock()

    def save(self, thread_id: Any, decision: PendingDecision) -> bool:
        key = _check_binding(thread_id, decision)
        if not self._mutation_lock.acquire(blocking=False):
            return False
        try:
            self._records[key] = decision.to_record()
            return True
        finally:
            self._mutation_lock.release()

    def peek(self, thread_id: Any) -> PendingDecision | None:
        with self._mutation_lock:
            return _decode(self._records.get(str(thread_id)))

    def read(self, thread_id: Any) -> PendingDecision | None:
        """Read without confusing corruption or contention with an empty slot."""
        if not self._mutation_lock.acquire(blocking=False):
            raise DecisionStoreUnavailable("Decision state is busy.")
        try:
            return _read(self._records.get(str(thread_id)))
        finally:
            self._mutation_lock.release()

    def install(self, decision: PendingDecision, expected: PendingDecision | None) -> bool:
        """Install only if the pre-resolution slot is still unchanged."""
        if not self._mutation_lock.acquire(blocking=False):
            return False
        try:
            if not _matches(_read(self._records.get(decision.thread_id)), expected):
                return False
            self._records[decision.thread_id] = decision.to_record()
            return True
        finally:
            self._mutation_lock.release()

    def take(self, thread_id: Any) -> PendingDecision | None:
        if not self._mutation_lock.acquire(blocking=False):
            return None
        try:
            return _decode(self._records.pop(str(thread_id), None))
        finally:
            self._mutation_lock.release()

    def replace_if(
        self, thread_id: Any, expected_sequence: int, replacement: PendingDecision
    ) -> bool:
        key = _check_binding(thread_id, replacement)
        if replacement.sequence != expected_sequence + 1:
            return False
        if not self._mutation_lock.acquire(blocking=False):
            return False
        try:
            current = _decode(self._records.get(key))
            if (
                current is None
                or current.sequence != expected_sequence
                or not _same_cas_identity(current, replacement)
                or not _timing_advances(current, replacement)
            ):
                return False
            self._records[key] = replacement.to_record()
            return True
        finally:
            self._mutation_lock.release()


class CachedPendingDecisionStore:
    """Django-cache store shared across workers when global Redis is enabled."""

    def __init__(
        self, timeout_seconds: int = DEFAULT_PENDING_DECISION_CACHE_TIMEOUT_SECONDS
    ) -> None:
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _key(thread_id: Any) -> str:
        return f"aimms:pending-decision:{thread_id}"

    @classmethod
    def _lock_key(cls, thread_id: Any) -> str:
        return f"{cls._key(thread_id)}:mutate"

    def _acquire(self, thread_id: Any) -> str | None:
        from django.core.cache import cache

        token = uuid4().hex
        if cache.add(self._lock_key(thread_id), token, timeout=_MUTATION_LOCK_SECONDS):
            return token
        return None

    def _release(self, thread_id: Any, token: str) -> None:
        from django.core.cache import cache

        if cache.get(self._lock_key(thread_id)) == token:
            cache.delete(self._lock_key(thread_id))

    def _redis_mutate(self, decision, expected, *, installing):
        """WATCH/MULTI makes production CAS atomic without relying on a lock lease."""
        from django.core.cache import cache
        from redis.exceptions import WatchError

        client = getattr(cache, "client", None)
        if client is None:
            return None  # LocMem test backend uses the matching locked fallback.
        connection = client.get_client(write=True)
        key = cache.make_key(self._key(decision.thread_id))
        try:
            with connection.pipeline() as pipe:
                pipe.watch(key)
                raw = pipe.get(key)
                current = _read(client.decode(raw) if raw is not None else None)
                if installing:
                    valid = _matches(current, expected)
                else:
                    valid = (
                        current is not None
                        and current.sequence == expected
                        and decision.sequence == expected + 1
                        and _same_cas_identity(current, decision)
                        and _timing_advances(current, decision)
                    )
                if not valid:
                    return False
                pipe.multi()
                pipe.set(key, client.encode(decision.to_record()), ex=self.timeout_seconds)
                pipe.execute()
                return True
        except WatchError:
            return False
        except Exception as exc:
            raise DecisionStoreUnavailable("Decision storage is unavailable.") from exc

    def save(self, thread_id: Any, decision: PendingDecision) -> bool:
        _check_binding(thread_id, decision)
        token = self._acquire(thread_id)
        if token is None:
            return False
        try:
            from django.core.cache import cache

            cache.set(self._key(thread_id), decision.to_record(), timeout=self.timeout_seconds)
            return True
        finally:
            self._release(thread_id, token)

    def peek(self, thread_id: Any) -> PendingDecision | None:
        from django.core.cache import cache

        return _decode(cache.get(self._key(thread_id)))

    def read(self, thread_id: Any) -> PendingDecision | None:
        """Read the authoritative slot, failing closed on cache errors."""
        from django.core.cache import cache

        try:
            if cache.get(self._lock_key(thread_id)) is not None:
                raise DecisionStoreUnavailable("Decision state is busy.")
            return _read(cache.get(self._key(thread_id)))
        except Exception as exc:
            raise DecisionStoreUnavailable("Decision state is unavailable.") from exc

    def install(self, decision: PendingDecision, expected: PendingDecision | None) -> bool:
        """A late resolver cannot replace a newer interaction."""
        from django.core.cache import cache

        if (result := self._redis_mutate(decision, expected, installing=True)) is not None:
            return result

        token = self._acquire(decision.thread_id)
        if token is None:
            return False
        try:
            key = self._key(decision.thread_id)
            if not _matches(_read(cache.get(key)), expected):
                return False
            cache.set(key, decision.to_record(), timeout=self.timeout_seconds)
            return True
        finally:
            self._release(decision.thread_id, token)

    def take(self, thread_id: Any) -> PendingDecision | None:
        token = self._acquire(thread_id)
        if token is None:
            return None
        try:
            from django.core.cache import cache

            key = self._key(thread_id)
            record = cache.get(key)
            cache.delete(key)
            return _decode(record)
        finally:
            self._release(thread_id, token)

    def replace_if(
        self, thread_id: Any, expected_sequence: int, replacement: PendingDecision
    ) -> bool:
        _check_binding(thread_id, replacement)
        if replacement.sequence != expected_sequence + 1:
            return False
        if (
            result := self._redis_mutate(replacement, expected_sequence, installing=False)
        ) is not None:
            return result
        token = self._acquire(thread_id)
        if token is None:
            return False
        try:
            from django.core.cache import cache

            key = self._key(thread_id)
            current = _decode(cache.get(key))
            if (
                current is None
                or current.sequence != expected_sequence
                or not _same_cas_identity(current, replacement)
                or not _timing_advances(current, replacement)
            ):
                return False
            cache.set(key, replacement.to_record(), timeout=self.timeout_seconds)
            return True
        finally:
            self._release(thread_id, token)


__all__ = [
    "DEFAULT_PENDING_DECISION_CACHE_TIMEOUT_SECONDS",
    "CachedPendingDecisionStore",
    "InMemoryPendingDecisionStore",
    "PendingDecisionStore",
]
