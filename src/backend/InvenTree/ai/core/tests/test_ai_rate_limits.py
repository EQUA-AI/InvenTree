"""Principal-derived and mounted-path tests for AI rate limiting."""

from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.auth import AI_PRINCIPAL_SCOPE_KEY, AIPrincipal  # noqa: E402
from ai.core.middleware.rate_limit import (  # noqa: E402
    RateLimitConfig,
    RateLimiter,
    RateLimitMiddleware,
    normalized_route_path,
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

    async def receive():  # noqa: RUF029
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):  # noqa: RUF029
        messages.append(message)

    await middleware(scope, receive, send)
    return messages


def _status(messages: list[dict]) -> int:
    return next(
        message["status"] for message in messages if message["type"] == "http.response.start"
    )


def _middleware(limiter: RateLimiter) -> RateLimitMiddleware:
    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return RateLimitMiddleware(app, limiter=limiter, exempt_paths=set())


def test_normalized_route_path_handles_both_mounted_scope_shapes() -> None:
    assert normalized_route_path({"path": "/chat", "root_path": "/api/ai"}) == "/chat"
    assert normalized_route_path({"path": "/api/ai/chat", "root_path": "/api/ai"}) == "/chat"


def test_spoofed_header_cannot_change_principal_bucket() -> None:
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

    assert _status(first) == 200
    assert _status(second) == 429
    assert set(limiter._user_buckets) == {principal.rate_limit_key}


def test_mounted_endpoint_rule_applies_to_api_ai_chat() -> None:
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
    assert json.loads(body)["error"] == "rate_limit_exceeded"
    headers = dict(
        next(message["headers"] for message in messages if message["type"] == "http.response.start")
    )
    assert b"Retry-After" in headers


def test_rate_limit_middleware_never_falls_back_to_attacker_identity() -> None:
    middleware = _middleware(RateLimiter())
    messages = asyncio.run(_run(middleware, headers=[(b"x-user-id", b"chosen-bucket")]))
    assert _status(messages) == 401
