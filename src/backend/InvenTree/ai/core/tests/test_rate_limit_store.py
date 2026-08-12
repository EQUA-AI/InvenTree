"""S35: fixed-window rate limiting — store counters and windowed limiter.

Behavioral tests only: everything goes through the public store protocol or
``WindowedRateLimiter.check_rate_limit``; nothing reaches into counters.
Parametrized over both stores so the cached store and the in-memory test
store stay contract-equal (the ``test_question_store`` idiom).
"""

# ruff: noqa: E402

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.middleware.rate_limit import (
    RateLimitConfig,
    WindowedRateLimiter,
)
from ai.core.middleware.rate_limit_store import (
    CacheRateLimitStore,
    InMemoryRateLimitStore,
    seconds_to_window_end,
    window_start,
)

T0 = 1_700_000_000.0  # an arbitrary fixed instant, aligned nowhere special


@pytest.fixture(params=["memory", "cached"])
def store(request):
    if request.param == "memory":
        return InMemoryRateLimitStore()
    from django.core.cache import cache

    cache.clear()
    return CacheRateLimitStore()


def _config(**overrides) -> RateLimitConfig:
    values = {
        "max_requests_per_minute": 3,
        "max_requests_per_hour": 5,
        "global_max_requests_per_minute": 100,
        "endpoint_limits": {},
    }
    values.update(overrides)
    return RateLimitConfig(**values)


def test_store_counts_within_a_window(store):
    counts = [
        store.increment(scope="user", endpoint="/chat", key="u1", window_seconds=60, now=T0 + i)
        for i in range(3)
    ]
    assert counts == [1, 2, 3]


def test_store_windows_reset_at_the_boundary(store):
    base = float(window_start(T0, 60))
    assert (
        store.increment(scope="user", endpoint="/chat", key="u1", window_seconds=60, now=base + 59)
        == 1
    )
    # Next window: the counter starts over.
    assert (
        store.increment(scope="user", endpoint="/chat", key="u1", window_seconds=60, now=base + 61)
        == 1
    )


def test_store_isolates_scopes_endpoints_and_keys(store):
    assert store.increment(scope="user", endpoint="/chat", key="u1", window_seconds=60, now=T0) == 1
    assert store.increment(scope="user", endpoint="/chat", key="u2", window_seconds=60, now=T0) == 1
    assert (
        store.increment(scope="user", endpoint="/voice", key="u1", window_seconds=60, now=T0) == 1
    )
    assert (
        store.increment(scope="global", endpoint="/chat", key="-", window_seconds=60, now=T0) == 1
    )


def test_minute_limit_rejects_with_window_retry_after(store):
    limiter = WindowedRateLimiter(_config(), store=store)
    base = float(window_start(T0, 3600))  # align so the hour window never splits

    for _ in range(3):
        assert limiter.check_rate_limit("u1", "/chat", now=base).allowed

    result = limiter.check_rate_limit("u1", "/chat", now=base)
    assert not result.allowed
    assert result.reason == "user_limit_exceeded"
    assert result.limit_type == "user"
    assert 0 < result.retry_after <= 60
    assert result.retry_after == seconds_to_window_end(base, 60)


def test_hour_limit_rejects_after_minute_windows_rotate(store):
    limiter = WindowedRateLimiter(_config(), store=store)
    base = float(window_start(T0, 3600))

    # 5 allowed requests spread over distinct minute windows fill the hour.
    for i in range(5):
        assert limiter.check_rate_limit("u1", "/chat", now=base + i * 60).allowed

    result = limiter.check_rate_limit("u1", "/chat", now=base + 5 * 60)
    assert not result.allowed
    assert result.reason == "user_hourly_limit_exceeded"
    assert 0 < result.retry_after <= 3600


def test_global_limit_rejects_across_users(store):
    limiter = WindowedRateLimiter(
        _config(global_max_requests_per_minute=2, max_requests_per_minute=10),
        store=store,
    )
    base = float(window_start(T0, 3600))

    assert limiter.check_rate_limit("u1", "/chat", now=base).allowed
    assert limiter.check_rate_limit("u2", "/chat", now=base).allowed
    result = limiter.check_rate_limit("u3", "/chat", now=base)
    assert not result.allowed
    assert result.reason == "global_limit_exceeded"
    assert result.limit_type == "global"


def test_user_over_own_limit_never_consumes_the_global_window(store):
    limiter = WindowedRateLimiter(
        _config(max_requests_per_minute=1, global_max_requests_per_minute=2),
        store=store,
    )
    base = float(window_start(T0, 3600))

    assert limiter.check_rate_limit("hammer", "/chat", now=base).allowed
    for _ in range(10):
        assert not limiter.check_rate_limit("hammer", "/chat", now=base).allowed

    # The hammering user consumed exactly one global slot; another user fits.
    assert limiter.check_rate_limit("victim", "/chat", now=base).allowed


def test_endpoint_overrides_apply(store):
    limiter = WindowedRateLimiter(
        _config(endpoint_limits={"/chat": {"per_minute": 1, "per_hour": 100}}),
        store=store,
    )
    base = float(window_start(T0, 3600))

    assert limiter.check_rate_limit("u1", "/chat", now=base).allowed
    assert not limiter.check_rate_limit("u1", "/chat", now=base).allowed
    # Other endpoints keep the default limit.
    assert limiter.check_rate_limit("u1", "/other", now=base).allowed


def test_exempt_user_is_never_limited(store):
    limiter = WindowedRateLimiter(
        _config(max_requests_per_minute=1, exempt_user_ids={"svc"}), store=store
    )
    for _ in range(5):
        assert limiter.check_rate_limit("svc", "/chat", now=T0).allowed


def test_remaining_counts_down_on_success(store):
    limiter = WindowedRateLimiter(_config(), store=store)
    base = float(window_start(T0, 3600))

    assert limiter.check_rate_limit("u1", "/chat", now=base).remaining == 2
    assert limiter.check_rate_limit("u1", "/chat", now=base).remaining == 1
    assert limiter.check_rate_limit("u1", "/chat", now=base).remaining == 0


def test_global_rejection_never_consumes_user_windows(store):
    """A stranger's traffic storm must not eat the victim's own quotas."""
    limiter = WindowedRateLimiter(
        _config(
            global_max_requests_per_minute=1,
            max_requests_per_minute=5,
            max_requests_per_hour=5,
        ),
        store=store,
    )
    base = float(window_start(T0, 3600))

    assert limiter.check_rate_limit("noisy", "/chat", now=base).allowed
    for _ in range(10):
        result = limiter.check_rate_limit("victim", "/chat", now=base)
        assert not result.allowed
        assert result.reason == "global_limit_exceeded"

    # The storm window rolls over; the victim was never charged.
    next_minute = base + 60
    assert limiter.check_rate_limit("victim", "/chat", now=next_minute).allowed
    hour_count = store.peek(
        scope="user", endpoint="/chat", key="victim", window_seconds=3600, now=next_minute
    )
    assert hour_count == 1


def test_store_peek_reads_without_counting(store):
    assert store.peek(scope="user", endpoint="/chat", key="u1", window_seconds=60, now=T0) == 0
    store.increment(scope="user", endpoint="/chat", key="u1", window_seconds=60, now=T0)
    assert store.peek(scope="user", endpoint="/chat", key="u1", window_seconds=60, now=T0) == 1
    assert store.peek(scope="user", endpoint="/chat", key="u1", window_seconds=60, now=T0) == 1


class _BrokenStore:
    def increment(self, **kwargs):
        return None

    def peek(self, **kwargs):
        return None


def test_store_failure_fails_open():
    """ADR: an unavailable store must never turn into a 429 outage."""
    limiter = WindowedRateLimiter(_config(max_requests_per_minute=0), store=_BrokenStore())
    result = limiter.check_rate_limit("u1", "/chat", now=T0)
    assert result.allowed


def test_cached_store_fails_open_on_backend_error(monkeypatch, caplog):
    """A raising cache backend reads as None (fail-open), loudly logged."""
    import logging

    store = CacheRateLimitStore()

    class _ExplodingCache:
        def add(self, *a, **k):
            raise RuntimeError("cache down")

        def incr(self, *a, **k):  # pragma: no cover - add already raised
            raise RuntimeError("cache down")

    monkeypatch.setattr("django.core.cache.cache.add", _ExplodingCache().add)
    with caplog.at_level(logging.ERROR, logger="ai.core.middleware.rate_limit_store"):
        count = store.increment(scope="user", endpoint="/chat", key="u1", window_seconds=60, now=T0)
    assert count is None
    assert any("fail-open" in record.getMessage() for record in caplog.records)
