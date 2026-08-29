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
    # S12: the wire-typed code. Distinct from "rate_limited" — the client
    # must not auto-retry a spent budget (reset is at UTC midnight).
    assert body["code"] == "token_budget_exhausted"
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


# --------------------------------------------------------------------------- #
# S12 (WP-B2): reservation/settlement engine + profile-resolved caps           #
# --------------------------------------------------------------------------- #
from ai.core.quota import reservation as resv
from ai.core.quota.assignment_source import PolicySnapshot

_SNAPSHOT = PolicySnapshot(
    profile="evaluation",
    version=2,
    user_cap=1_000,
    tenant_cap=2_500,
    global_cap=10_000,
    requests_per_minute=200,
    requests_per_hour=2_000,
)


def _quota_settings(**overrides) -> Settings:
    base = {
        "AI_USER_DAILY_TOKEN_BUDGET": 1000,
        "FEATURE_TOKEN_BUDGET_SHADOW": True,
        "FEATURE_TOKEN_BUDGET_ENFORCE": False,
        "FEATURE_AI_QUOTA_PROFILES": True,
        "AI_QUOTA_TURN_RESERVE_TOKENS": 500,
        "AI_DEPLOYMENT_ENV": "testenv",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


class TestReservation:
    def _reserve(self, key: str = "turn-1", user="7", tenant="site-a"):
        return resv.reserve_turn(
            user_pk=user, tenant_id=tenant, snapshot=_SNAPSHOT, idempotency_key=key
        )

    def test_reserve_then_settle_actuals(self, monkeypatch):
        monkeypatch.setattr("ai.core.config.get_settings", _quota_settings)
        reservation = self._reserve()
        assert reservation is not None and reservation.worst_case == 500
        usages = {
            u.level: u
            for u in resv.level_usage(user_pk="7", tenant_id="site-a", snapshot=_SNAPSHOT)
        }
        assert usages["user"].reserved == 500
        assert usages["tenant"].reserved == 500
        assert usages["global"].reserved == 500

        charged = resv.settle_turn(reservation, ledger_tokens=200, outcome="executed")
        assert charged == 200
        usages = {
            u.level: u
            for u in resv.level_usage(user_pk="7", tenant_id="site-a", snapshot=_SNAPSHOT)
        }
        assert usages["user"].used == 200
        assert usages["user"].reserved == 0

    def test_reserve_and_settle_are_idempotent(self, monkeypatch):
        monkeypatch.setattr("ai.core.config.get_settings", _quota_settings)
        first = self._reserve("turn-x")
        assert first is not None
        assert self._reserve("turn-x") is None, "same turn must not double-reserve"
        assert resv.settle_turn(first, ledger_tokens=100, outcome="executed") == 100
        assert resv.settle_turn(first, ledger_tokens=100, outcome="executed") == 0
        usages = {
            u.level: u
            for u in resv.level_usage(user_pk="7", tenant_id="site-a", snapshot=_SNAPSHOT)
        }
        assert usages["user"].used == 100

    def test_empty_ledger_executed_turn_charges_worst_case(self, monkeypatch):
        """The uninstrumented-rail bound: no ledger evidence => full envelope."""
        monkeypatch.setattr("ai.core.config.get_settings", _quota_settings)
        reservation = self._reserve("turn-legacy")
        assert resv.settle_turn(reservation, ledger_tokens=0, outcome="executed") == 500

    def test_replay_and_preemption_charge_zero(self, monkeypatch):
        monkeypatch.setattr("ai.core.config.get_settings", _quota_settings)
        for key, outcome in (("turn-r", "replayed"), ("turn-p", "preempted")):
            reservation = self._reserve(key)
            assert resv.settle_turn(reservation, ledger_tokens=0, outcome=outcome) == 0
        usages = {
            u.level: u
            for u in resv.level_usage(user_pk="7", tenant_id="site-a", snapshot=_SNAPSHOT)
        }
        assert usages["user"].used == 0
        assert usages["user"].reserved == 0

    def test_negative_reserved_counter_is_clamped(self, monkeypatch):
        monkeypatch.setattr("ai.core.config.get_settings", _quota_settings)
        reservation = self._reserve("turn-drift")
        from django.core.cache import cache

        cache.clear()  # simulate a flush between reserve and settle
        resv.settle_turn(reservation, ledger_tokens=50, outcome="executed")
        usages = {
            u.level: u
            for u in resv.level_usage(user_pk="7", tenant_id="site-a", snapshot=_SNAPSHOT)
        }
        assert usages["user"].reserved == 0, "drifted counter must clamp, not go negative"

    def test_concurrent_reservations_never_lose_updates(self, monkeypatch):
        monkeypatch.setattr("ai.core.config.get_settings", _quota_settings)
        import threading

        def worker(i: int) -> None:
            self._reserve(f"turn-c{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        usages = {
            u.level: u
            for u in resv.level_usage(user_pk="7", tenant_id="site-a", snapshot=_SNAPSHOT)
        }
        assert usages["user"].reserved == 8 * 500


class TestProfileBudget:
    def _patch_profile(self, monkeypatch, snapshot=_SNAPSHOT):
        monkeypatch.setattr("ai.core.quota.profiles.resolve_profile", lambda _pk: snapshot)

    def test_committed_usage_blocks_at_the_user_cap(self, monkeypatch):
        monkeypatch.setattr(
            "ai.core.config.get_settings",
            lambda: _quota_settings(FEATURE_TOKEN_BUDGET_ENFORCE=True),
        )
        self._patch_profile(monkeypatch)
        # Two reservations commit 1000 against the 1000-token user cap.
        resv.reserve_turn(
            user_pk="7", tenant_id="site-a", snapshot=_SNAPSHOT, idempotency_key="pb-1"
        )
        resv.reserve_turn(
            user_pk="7", tenant_id="site-a", snapshot=_SNAPSHOT, idempotency_key="pb-2"
        )
        decision = budget.check_budget("7", now=T0, tenant_id="site-a")
        assert decision.blocked
        assert not decision.store_unavailable

    def test_tenant_ceiling_blocks_an_under_cap_user(self, monkeypatch):
        """A13: the tenant level is real — user B is blocked by A's spend."""
        monkeypatch.setattr(
            "ai.core.config.get_settings",
            lambda: _quota_settings(FEATURE_TOKEN_BUDGET_ENFORCE=True),
        )
        self._patch_profile(monkeypatch)
        for i in range(5):  # 2500 committed against the 2500 tenant cap
            resv.reserve_turn(
                user_pk=f"a{i}",
                tenant_id="site-a",
                snapshot=_SNAPSHOT,
                idempotency_key=f"tc-{i}",
            )
        decision = budget.check_budget("fresh-user", now=T0, tenant_id="site-a")
        assert decision.blocked
        # A different tenant is unaffected.
        other = budget.check_budget("fresh-user", now=T0, tenant_id="site-b")
        assert not other.blocked

    def test_shadow_logs_would_block_by_level(self, monkeypatch, caplog):
        monkeypatch.setattr("ai.core.config.get_settings", _quota_settings)
        self._patch_profile(monkeypatch)
        resv.reserve_turn(
            user_pk="7", tenant_id="site-a", snapshot=_SNAPSHOT, idempotency_key="sw-1"
        )
        resv.reserve_turn(
            user_pk="7", tenant_id="site-a", snapshot=_SNAPSHOT, idempotency_key="sw-2"
        )
        with caplog.at_level(logging.WARNING, logger="ai.core.middleware.budget"):
            decision = budget.check_budget("7", now=T0, tenant_id="site-a")
        assert not decision.blocked
        assert any("quota.would_block level=user" in r.getMessage() for r in caplog.records)

    def test_enforce_fails_closed_when_the_source_is_down(self, monkeypatch):
        from ai.core.quota.profiles import QuotaSourceUnavailable

        monkeypatch.setattr(
            "ai.core.config.get_settings",
            lambda: _quota_settings(FEATURE_TOKEN_BUDGET_ENFORCE=True),
        )

        def broken(_pk):
            raise QuotaSourceUnavailable("db down")

        monkeypatch.setattr("ai.core.quota.profiles.resolve_profile", broken)
        decision = budget.check_budget("7", now=T0, tenant_id="site-a")
        assert decision.blocked
        assert decision.store_unavailable

    def test_shadow_fails_open_when_the_source_is_down(self, monkeypatch):
        from ai.core.quota.profiles import QuotaSourceUnavailable

        monkeypatch.setattr("ai.core.config.get_settings", _quota_settings)

        def broken(_pk):
            raise QuotaSourceUnavailable("db down")

        monkeypatch.setattr("ai.core.quota.profiles.resolve_profile", broken)
        decision = budget.check_budget("7", now=T0, tenant_id="site-a")
        assert not decision.blocked
        assert not decision.store_unavailable

    def test_middleware_returns_typed_503_when_store_unavailable(self, monkeypatch):
        from ai.core.quota.profiles import QuotaSourceUnavailable

        monkeypatch.setattr(
            "ai.core.config.get_settings",
            lambda: _quota_settings(
                FEATURE_TOKEN_BUDGET_ENFORCE=True,
                FEATURE_DISTRIBUTED_RATE_LIMIT_SHADOW=False,
                FEATURE_DISTRIBUTED_RATE_LIMIT_ENFORCE=False,
            ),
        )

        def broken(_pk):
            raise QuotaSourceUnavailable("db down")

        monkeypatch.setattr("ai.core.quota.profiles.resolve_profile", broken)

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
        assert status == 503
        body = json.loads(next(m["body"] for m in messages if m["type"] == "http.response.body"))
        assert body["code"] == "quota_store_unavailable"


class TestTurnSettleIntegration:
    def test_settle_runs_before_the_ledger_drain_and_both_count(self, monkeypatch):
        """_settle_turn_quota charges v2 actuals AND the v1 counter, once."""
        from types import SimpleNamespace

        from ai.core.turn_service import _settle_turn_quota

        monkeypatch.setattr("ai.core.config.get_settings", _quota_settings)
        reservation = resv.reserve_turn(
            user_pk="7", tenant_id="site-a", snapshot=_SNAPSHOT, idempotency_key="int-1"
        )
        ledger = TurnUsageLedger()
        ledger.record("wf8", {"total_tokens": 321})
        token = turn_usage_ledger.set(ledger)
        try:
            actor = SimpleNamespace(user_pk="7", scope="site-a")
            result = SimpleNamespace(replayed=False)
            _settle_turn_quota(actor, reservation, result, False)
        finally:
            turn_usage_ledger.reset(token)
        usages = {
            u.level: u
            for u in resv.level_usage(user_pk="7", tenant_id="site-a", snapshot=_SNAPSHOT)
        }
        assert usages["user"].used == 321
        assert usages["user"].reserved == 0
        assert budget.current_spend("7") == 321, "the v1 counter still runs in parallel"
        assert not ledger.events, "the drain must still clear the ledger"
