"""S37: per-user daily token budgets — counters, ladder, fail-open, 429."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import json
import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.auth import AI_PRINCIPAL_SCOPE_KEY, AIPrincipal
from ai.core.config import Settings
from ai.core.middleware import budget
from ai.core.middleware.rate_limit import RateLimiter, RateLimitMiddleware, WindowedRateLimiter
from ai.core.middleware.rate_limit_store import InMemoryRateLimitStore
from ai.core.usage import TurnUsageLedger, turn_usage_ledger

T0 = 1_700_000_000.0


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def _settings(**overrides) -> Settings:
    base = {
        "AI_USER_DAILY_TOKEN_BUDGET": 1000,
        "FEATURE_TOKEN_BUDGET_SHADOW": True,
        "FEATURE_TOKEN_BUDGET_ENFORCE": False,
        "AI_BUDGET_EXEMPT_USER_IDS": "",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_spend_accumulates_within_a_utc_day():
    budget.add_spend("7", 300, now=T0)
    budget.add_spend("7", 200, now=T0 + 60)
    assert budget.current_spend("7", now=T0 + 120) == 500


def test_spend_resets_on_the_next_utc_day():
    budget.add_spend("7", 300, now=T0)
    next_day = T0 + 24 * 3600
    assert budget.current_spend("7", now=next_day) == 0


def test_users_are_isolated():
    budget.add_spend("7", 300, now=T0)
    assert budget.current_spend("8", now=T0) == 0


def test_shadow_logs_would_block_but_never_blocks(monkeypatch, caplog):
    monkeypatch.setattr("ai.core.config.get_settings", _settings)
    budget.add_spend("7", 1500, now=T0)
    with caplog.at_level(logging.WARNING, logger="ai.core.middleware.budget"):
        decision = budget.check_budget("7", now=T0)
    assert not decision.blocked
    assert decision.used == 1500
    assert any("budget.would_block" in r.getMessage() for r in caplog.records)


def test_enforce_blocks_with_retry_to_utc_midnight(monkeypatch):
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(FEATURE_TOKEN_BUDGET_ENFORCE=True),
    )
    budget.add_spend("7", 1500, now=T0)
    decision = budget.check_budget("7", now=T0)
    assert decision.blocked
    assert 0 < decision.retry_after <= 24 * 3600
    assert decision.retry_after == budget.seconds_to_utc_midnight(T0)


def test_under_cap_is_never_blocked(monkeypatch):
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(FEATURE_TOKEN_BUDGET_ENFORCE=True),
    )
    budget.add_spend("7", 900, now=T0)
    assert not budget.check_budget("7", now=T0).blocked


def test_exempt_user_is_never_blocked(monkeypatch):
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(FEATURE_TOKEN_BUDGET_ENFORCE=True, AI_BUDGET_EXEMPT_USER_IDS="3, 7"),
    )
    budget.add_spend("7", 5000, now=T0)
    assert not budget.check_budget("7", now=T0).blocked


def test_zero_cap_means_unlimited(monkeypatch):
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(AI_USER_DAILY_TOKEN_BUDGET=0, FEATURE_TOKEN_BUDGET_ENFORCE=True),
    )
    budget.add_spend("7", 10_000_000, now=T0)
    assert not budget.check_budget("7", now=T0).blocked


def test_cache_failure_fails_open(monkeypatch, caplog):
    """ADR: a cache blip must never become an AI outage."""
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(FEATURE_TOKEN_BUDGET_ENFORCE=True),
    )

    def _explode(*a, **k):
        raise RuntimeError("cache down")

    monkeypatch.setattr("django.core.cache.cache.get", _explode)
    with caplog.at_level(logging.ERROR, logger="ai.core.middleware.budget"):
        decision = budget.check_budget("7", now=T0)
    assert not decision.blocked
    assert any("fail-open" in r.getMessage() for r in caplog.records)


def test_record_turn_spend_uses_canonical_totals_and_clears():
    ledger = TurnUsageLedger()
    ledger.record("wf8_lookup", {"input_token_count": 100, "total_token_count": 120})
    token = turn_usage_ledger.set(ledger)
    try:
        budget.record_turn_spend("7")
    finally:
        turn_usage_ledger.reset(token)
    today = budget.current_spend("7")
    assert today == 120
    # A second call in the same context must not double-count.
    token = turn_usage_ledger.set(ledger)
    try:
        budget.record_turn_spend("7")
    finally:
        turn_usage_ledger.reset(token)
    assert budget.current_spend("7") == 120


def _principal(subject: str = "user:7") -> AIPrincipal:
    return AIPrincipal(
        subject=subject,
        actor=subject,
        user_pk=subject.removeprefix("user:"),
        username="budget-user",
        authentication_method="django_session",
        scope="pilot-site",
        policy_version="policy-v1",
        is_staff=False,
        is_superuser=False,
    )


async def _run(middleware, principal, path: str = "/chat") -> list[dict]:
    messages: list[dict] = []
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "root_path": "/api/ai",
        "headers": [],
        AI_PRINCIPAL_SCOPE_KEY: principal,
    }

    async def receive():  # noqa: RUF029
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):  # noqa: RUF029
        messages.append(message)

    await middleware(scope, receive, send)
    return messages


def test_middleware_returns_typed_429_when_enforced(monkeypatch):
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(
            FEATURE_TOKEN_BUDGET_ENFORCE=True,
            FEATURE_DISTRIBUTED_RATE_LIMIT_SHADOW=False,
            FEATURE_DISTRIBUTED_RATE_LIMIT_ENFORCE=False,
        ),
    )
    budget.add_spend("7", 5000)

    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    limiter = RateLimiter()
    middleware = RateLimitMiddleware(
        app,
        limiter=limiter,
        exempt_paths=set(),
        windowed=WindowedRateLimiter(limiter.config, store=InMemoryRateLimitStore()),
    )
    messages = asyncio.run(_run(middleware, _principal()))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    assert status == 429
    body = json.loads(next(m["body"] for m in messages if m["type"] == "http.response.body"))
    assert body["error"] == "token_budget_exhausted"
    assert body["code"] == "rate_limited"
    assert body["retry_after"] > 0
    headers = dict(next(m["headers"] for m in messages if m["type"] == "http.response.start"))
    assert b"Retry-After" in headers

    # Voice turn submissions are budgeted too...
    voice = asyncio.run(_run(middleware, _principal(), path="/voice/sessions/abc123/turns"))
    assert next(m["status"] for m in voice if m["type"] == "http.response.start") == 429

    # ...but read endpoints never are: an over-cap user keeps access to
    # their own threads and capability probes.
    reads = asyncio.run(_run(middleware, _principal(), path="/threads"))
    assert next(m["status"] for m in reads if m["type"] == "http.response.start") == 200
