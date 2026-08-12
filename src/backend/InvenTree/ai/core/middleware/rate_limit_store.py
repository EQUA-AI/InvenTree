"""Fixed-window rate-limit counters over the shared Django cache (S35).

Why fixed windows and not a token bucket in the cache: bucket state needs a
read-modify-write under a cross-worker lock on every request, so a slow or
failing cache turns into serialized requests or spurious 429s. A fixed-window
counter is one atomic increment — ``cache.add`` to seed, then ``cache.incr``
— lock-free and cross-replica correct. The cost is the standard
window-boundary burst (at most 2x a window's limit straddling a boundary),
acceptable for an abuse control.

ADR note — fail-OPEN, a deliberate deviation from the fail-closed house
default: rate limiting is an abuse control, not a safety control. Failing
closed would make the cache a hard dependency of every AI request and turn a
cache blip into a full AI outage. A store error therefore reads as "allowed",
loudly logged with fault coordinates. The S37 budget counters share this
posture.

With the LocMem cache backend the counters degrade to per-process — the same
semantics as the in-process limiter this store replaces, which is correct for
single-replica dev and for tests. No special casing.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

from ai.core.faults import fault_location

logger = logging.getLogger(__name__)

#: Bump when the key layout or counting semantics change; stale keys expire
#: on their own.
RATE_LIMIT_SCHEMA_VERSION = "v1"


def window_start(now: float, window_seconds: int) -> int:
    """The UTC-epoch second this window began."""
    return int(now // window_seconds) * window_seconds


def seconds_to_window_end(now: float, window_seconds: int) -> float:
    """How long until the current window resets (the honest Retry-After)."""
    return window_start(now, window_seconds) + window_seconds - now


def counter_key(*, scope: str, endpoint: str, key: str, window_seconds: int, now: float) -> str:
    """Schema-versioned counter key, unique per window instance."""
    start = window_start(now, window_seconds)
    return f"aimms:rl:{RATE_LIMIT_SCHEMA_VERSION}:{scope}:{endpoint}:{key}:{window_seconds}:{start}"


class RateLimitStore(Protocol):
    """Atomic fixed-window counters, shared across workers."""

    def increment(
        self,
        *,
        scope: str,
        endpoint: str,
        key: str,
        window_seconds: int,
        now: float | None = None,
    ) -> int | None:
        """Count one request; return the window's new total, or None on store failure."""

    def peek(
        self,
        *,
        scope: str,
        endpoint: str,
        key: str,
        window_seconds: int,
        now: float | None = None,
    ) -> int | None:
        """Read the window's total without counting; None on store failure."""


class CacheRateLimitStore:
    """Django-cache counters: one ``add`` + one ``incr`` per check."""

    def increment(
        self,
        *,
        scope: str,
        endpoint: str,
        key: str,
        window_seconds: int,
        now: float | None = None,
    ) -> int | None:
        """Atomically count one request in the window; None means fail-open."""
        now = time.time() if now is None else now
        cache_key = counter_key(
            scope=scope, endpoint=endpoint, key=key, window_seconds=window_seconds, now=now
        )
        try:
            from django.core.cache import cache

            # Seed the window with a TTL, then count atomically. Double the
            # window keeps a boundary-straddling check from watching its own
            # counter expire mid-flight; the extra lifetime is inert because
            # the key embeds the window start.
            cache.add(cache_key, 0, timeout=window_seconds * 2)
            return int(cache.incr(cache_key))
        except Exception as exc:
            logger.error("rate limit store failed (fail-open) %s", fault_location(exc))
            return None

    def peek(
        self,
        *,
        scope: str,
        endpoint: str,
        key: str,
        window_seconds: int,
        now: float | None = None,
    ) -> int | None:
        """Read the window's total without counting; None means fail-open."""
        now = time.time() if now is None else now
        cache_key = counter_key(
            scope=scope, endpoint=endpoint, key=key, window_seconds=window_seconds, now=now
        )
        try:
            from django.core.cache import cache

            value = cache.get(cache_key)
            return int(value) if value is not None else 0
        except Exception as exc:
            logger.error("rate limit store failed (fail-open) %s", fault_location(exc))
            return None


class InMemoryRateLimitStore:
    """Process-local counters for tests; the cached store is the deployment seam."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._expiry: dict[str, float] = {}

    def increment(
        self,
        *,
        scope: str,
        endpoint: str,
        key: str,
        window_seconds: int,
        now: float | None = None,
    ) -> int | None:
        """Count one request; expired windows are pruned lazily."""
        now = time.time() if now is None else now
        self._prune(now)
        cache_key = counter_key(
            scope=scope, endpoint=endpoint, key=key, window_seconds=window_seconds, now=now
        )
        self._counters[cache_key] = self._counters.get(cache_key, 0) + 1
        self._expiry.setdefault(cache_key, now + window_seconds * 2)
        return self._counters[cache_key]

    def peek(
        self,
        *,
        scope: str,
        endpoint: str,
        key: str,
        window_seconds: int,
        now: float | None = None,
    ) -> int | None:
        """Read the window's total without counting."""
        now = time.time() if now is None else now
        cache_key = counter_key(
            scope=scope, endpoint=endpoint, key=key, window_seconds=window_seconds, now=now
        )
        return self._counters.get(cache_key, 0)

    def _prune(self, now: float) -> None:
        for cache_key, expires_at in list(self._expiry.items()):
            if expires_at <= now:
                self._expiry.pop(cache_key, None)
                self._counters.pop(cache_key, None)


__all__ = [
    "RATE_LIMIT_SCHEMA_VERSION",
    "CacheRateLimitStore",
    "InMemoryRateLimitStore",
    "RateLimitStore",
    "counter_key",
    "seconds_to_window_end",
    "window_start",
]
