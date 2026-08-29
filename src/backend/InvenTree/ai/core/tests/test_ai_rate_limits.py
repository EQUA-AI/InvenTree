"""Principal-derived and mounted-path tests for AI rate limiting."""

from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.auth import AI_PRINCIPAL_SCOPE_KEY, AIPrincipal  # noqa: E402
from ai.core.config import Settings  # noqa: E402
from ai.core.middleware.rate_limit import (  # noqa: E402
    RateLimitConfig,
    RateLimiter,
    RateLimitMiddleware,
    WindowedRateLimiter,
    normalized_route_path,
)
from ai.core.middleware.rate_limit_store import InMemoryRateLimitStore  # noqa: E402


def _flag_settings(*, shadow: bool = False, enforce: bool = False) -> Settings:
    return Settings(
        _env_file=None,
        FEATURE_DISTRIBUTED_RATE_LIMIT_SHADOW=shadow,
        FEATURE_DISTRIBUTED_RATE_LIMIT_ENFORCE=enforce,
    )


def _principal(subject: str = "user:7") -> AIPrincipal:
    return AIPrincipal(
        subject=subject,
        actor=subject,
        user_pk=subject.removeprefix("user:"),
        username="rate-user",
        authentication_method="django_session",
        scope="pilot-site",
        policy_version="policy-v1",
        is_staff=False,
        is_superuser=False,
    )


async def _run(
    middleware: RateLimitMiddleware,
    *,
    path: str = "/chat",
    root_path: str = "/api/ai",
    headers: list[tuple[bytes, bytes]] | None = None,
    principal: AIPrincipal | None = None,
) -> list[dict]:
    messages: list[dict] = []
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "root_path": root_path,
        "headers": headers or [],
    }
    if principal is not None:
        scope[AI_PRINCIPAL_SCOPE_KEY] = principal

    async def receive():  # noqa: RUF029 - ASGI receive contract is async
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):  # noqa: RUF029 - ASGI send contract is async
        messages.append(message)

    await middleware(scope, receive, send)
    return messages


def _status(messages: list[dict]) -> int:
    return next(
        message["status"] for message in messages if message["type"] == "http.response.start"
    )


def _middleware(
    limiter: RateLimiter, windowed: WindowedRateLimiter | None = None
) -> RateLimitMiddleware:
    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return RateLimitMiddleware(
        app,
        limiter=limiter,
        exempt_paths=set(),
        windowed=windowed or WindowedRateLimiter(limiter.config, store=InMemoryRateLimitStore()),
    )


def test_normalized_route_path_handles_both_mounted_scope_shapes() -> None:
    assert normalized_route_path({"path": "/chat", "root_path": "/api/ai"}) == "/chat"
    assert normalized_route_path({"path": "/api/ai/chat", "root_path": "/api/ai"}) == "/chat"


def test_spoofed_header_cannot_change_principal_bucket(monkeypatch) -> None:
    monkeypatch.setattr("ai.core.config.get_settings", _flag_settings)
    limiter = RateLimiter(
        RateLimitConfig(
            max_requests_per_minute=1,
            global_max_requests_per_minute=100,
            endpoint_limits={"/chat": {"per_minute": 1}},
            burst_multiplier=1,
        )
    )
    middleware = _middleware(limiter)
    principal = _principal()

    first = asyncio.run(
        _run(
            middleware,
            principal=principal,
            headers=[(b"x-user-id", b"attacker-choice-one")],
        )
    )
    second = asyncio.run(
        _run(
            middleware,
            principal=principal,
            headers=[(b"x-user-id", b"attacker-choice-two")],
        )
    )
    # A different real principal still has its own budget — the header
    # never chose the bucket.
    other = asyncio.run(
        _run(
            middleware,
            principal=_principal("user:8"),
            headers=[(b"x-user-id", b"attacker-choice-one")],
        )
    )

    assert _status(first) == 200
    assert _status(second) == 429
    assert _status(other) == 200


def test_mounted_endpoint_rule_applies_to_api_ai_chat(monkeypatch) -> None:
    monkeypatch.setattr("ai.core.config.get_settings", _flag_settings)
    limiter = RateLimiter(
        RateLimitConfig(
            max_requests_per_minute=20,
            global_max_requests_per_minute=100,
            endpoint_limits={"/chat": {"per_minute": 1}},
            burst_multiplier=1,
        )
    )
    middleware = _middleware(limiter)
    principal = _principal()

    assert _status(asyncio.run(_run(middleware, path="/api/ai/chat", principal=principal))) == 200
    messages = asyncio.run(_run(middleware, path="/api/ai/chat", principal=principal))
    assert _status(messages) == 429
    body = next(message["body"] for message in messages if message["type"] == "http.response.body")
    payload = json.loads(body)
    assert payload["error"] == "rate_limit_exceeded"
    # S12/A13: every limiter response carries a machine-readable code.
    assert payload["code"] == "rate_limited"
    headers = dict(
        next(message["headers"] for message in messages if message["type"] == "http.response.start")
    )
    assert b"Retry-After" in headers


def test_rate_limit_middleware_never_falls_back_to_attacker_identity(monkeypatch) -> None:
    monkeypatch.setattr("ai.core.config.get_settings", _flag_settings)
    middleware = _middleware(RateLimiter())
    messages = asyncio.run(_run(middleware, headers=[(b"x-user-id", b"chosen-bucket")]))
    assert _status(messages) == 401


def test_enforce_hands_the_decision_to_the_windowed_limiter(monkeypatch) -> None:
    """S35: with enforce on, the shared-cache windows decide, not the buckets."""
    monkeypatch.setattr("ai.core.config.get_settings", lambda: _flag_settings(enforce=True))
    # Buckets would allow 100/min; the windowed limiter allows 1.
    limiter = RateLimiter(RateLimitConfig(max_requests_per_minute=100))
    windowed = WindowedRateLimiter(
        RateLimitConfig(
            max_requests_per_minute=1,
            max_requests_per_hour=100,
            global_max_requests_per_minute=100,
            endpoint_limits={},
        ),
        store=InMemoryRateLimitStore(),
    )
    middleware = _middleware(limiter, windowed=windowed)
    principal = _principal()

    assert _status(asyncio.run(_run(middleware, principal=principal))) == 200
    messages = asyncio.run(_run(middleware, principal=principal))
    assert _status(messages) == 429
    body = next(message["body"] for message in messages if message["type"] == "http.response.body")
    assert json.loads(body)["error"] == "rate_limit_exceeded"


def test_shadow_logs_divergence_but_keeps_the_bucket_decision(monkeypatch, caplog) -> None:
    """S35 soak signal: divergence is logged, the legacy verdict stands."""
    import logging

    monkeypatch.setattr("ai.core.config.get_settings", lambda: _flag_settings(shadow=True))
    limiter = RateLimiter(RateLimitConfig(max_requests_per_minute=100))
    windowed = WindowedRateLimiter(
        RateLimitConfig(
            max_requests_per_minute=1,
            max_requests_per_hour=100,
            global_max_requests_per_minute=100,
            endpoint_limits={},
        ),
        store=InMemoryRateLimitStore(),
    )
    middleware = _middleware(limiter, windowed=windowed)
    principal = _principal()

    with caplog.at_level(logging.WARNING, logger="ai.core.middleware.rate_limit"):
        assert _status(asyncio.run(_run(middleware, principal=principal))) == 200
        # Second request: buckets allow, windows reject — divergence, still 200.
        assert _status(asyncio.run(_run(middleware, principal=principal))) == 200

    assert any("rate_limit.shadow divergence" in r.getMessage() for r in caplog.records)


def test_cors_is_outermost_so_limiter_responses_carry_cors_headers() -> None:
    """S12 ordering invariant (app.py): Starlette's add_middleware inserts at
    the top of the stack, so user_middleware[0] is the OUTERMOST layer. CORS
    must be outermost — a 429/503 the rate limiter writes itself must still
    traverse CORSMiddleware, or a cross-origin frontend can never read
    Retry-After off it."""
    from ai.core.app import app
    from starlette.middleware.cors import CORSMiddleware

    classes = [entry.cls for entry in app.user_middleware]
    assert CORSMiddleware in classes
    assert RateLimitMiddleware in classes
    assert classes.index(CORSMiddleware) < classes.index(RateLimitMiddleware), (
        "CORSMiddleware must be outermost (added last), wrapping RateLimitMiddleware"
    )


def test_quota_preflight_is_rate_limit_exempt_but_never_budgeted() -> None:
    """An over-cap user must be able to READ why (exempt from the limiter),
    and the preflight itself must never spend budget."""
    from ai.core.app import app
    from ai.core.middleware.rate_limit import _BUDGETED_ENDPOINTS

    exempt = next(
        entry.kwargs["exempt_paths"]
        for entry in app.user_middleware
        if entry.cls is RateLimitMiddleware
    )
    assert "/quota/preflight" in exempt
    assert _BUDGETED_ENDPOINTS.fullmatch("/quota/preflight") is None


def test_rate_limits_are_settings_knobs() -> None:
    """WP-B3: the historic literals are env-tunable without code churn."""
    from ai.core.app import rate_limit_config
    from ai.core.config import Settings

    settings = Settings(_env_file=None, AI_RATE_CHAT_PER_MINUTE=30, AI_RATE_CHAT_PER_HOUR=600)
    assert settings.ai_rate_chat_per_minute == 30
    assert settings.ai_rate_chat_per_hour == 600
    # The app singleton was built from the default knobs.
    assert rate_limit_config.endpoint_limits["/chat"]["per_minute"] == 10
    assert rate_limit_config.max_requests_per_hour == 200
