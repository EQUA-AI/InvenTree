"""
Rate Limiting Middleware for FastAPI

Provides rate limiting to protect Azure OpenAI quotas and prevent abuse.

Features:
- Token bucket algorithm for smooth rate limiting
- Per-user and global rate limits
- Configurable limits by endpoint
- In-memory storage (Redis adapter available)
- Proper 429 responses with Retry-After headers

Usage:
    from ai.core.middleware.rate_limit import RateLimiter, rate_limit

    # Global limiter
    limiter = RateLimiter()

    @app.post("/chat")
    @rate_limit(limiter, max_requests=10, window_seconds=60)
    async def chat(request: Request):
        ...

    # Or use middleware for all routes
    app.add_middleware(RateLimitMiddleware, limiter=limiter)
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from ai.core.auth import (
    AI_PRINCIPAL_SCOPE_KEY,
    AIPrincipal,
    record_identity_anomaly,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ===== Rate Limit Configuration =====


@dataclass
class RateLimitConfig:
    """Configuration for rate limits."""

    # Default limits
    max_requests_per_minute: int = 20  # Per user
    max_requests_per_hour: int = 200  # Per user
    global_max_requests_per_minute: int = 100  # Total across all users

    # Endpoint-specific overrides
    endpoint_limits: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            "/chat": {"per_minute": 10, "per_hour": 100},
            "/chat/stream": {"per_minute": 10, "per_hour": 100},
        }
    )

    # Burst allowance (token bucket refill)
    burst_multiplier: float = 1.5  # Allow 50% burst above limit

    # Exempt users (e.g., internal services)
    exempt_user_ids: set[str] = field(default_factory=set)

    def get_limit_for_endpoint(self, endpoint: str, window: str = "minute") -> int:
        """Get rate limit for specific endpoint."""
        key = f"per_{window}"
        if endpoint in self.endpoint_limits:
            return self.endpoint_limits[endpoint].get(
                key,
                self.max_requests_per_minute if window == "minute" else self.max_requests_per_hour,
            )
        return self.max_requests_per_minute if window == "minute" else self.max_requests_per_hour


# ===== Token Bucket Implementation =====


@dataclass
class TokenBucket:
    """
    Token bucket for rate limiting.

    Provides smooth rate limiting with burst capability.
    Tokens are consumed per request and refill over time.
    """

    capacity: float  # Maximum tokens
    tokens: float  # Current token count
    refill_rate: float  # Tokens per second
    last_update: float = field(default_factory=time.time)

    def consume(self, tokens: float = 1.0) -> tuple[bool, float]:
        """
        Try to consume tokens.

        Returns:
            Tuple of (success, wait_time_seconds)
            If success is False, wait_time is how long to wait for tokens
        """
        now = time.time()
        elapsed = now - self.last_update

        # Refill tokens based on elapsed time
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_update = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True, 0.0
        else:
            # Calculate wait time until enough tokens
            tokens_needed = tokens - self.tokens
            wait_time = tokens_needed / self.refill_rate
            return False, wait_time

    def refund(self, tokens: float = 1.0) -> None:
        """Return previously consumed tokens (used when a later check rejects)."""
        self.tokens = min(self.capacity, self.tokens + tokens)

    @classmethod
    def create_for_rate(
        cls, requests_per_period: int, period_seconds: int, burst_multiplier: float = 1.5
    ) -> TokenBucket:
        """Create a bucket for a specific rate limit."""
        refill_rate = requests_per_period / period_seconds
        capacity = requests_per_period * burst_multiplier
        return cls(capacity=capacity, tokens=capacity, refill_rate=refill_rate)


# ===== Rate Limiter =====


class RateLimiter:
    """
    In-memory rate limiter using token buckets.

    Tracks rate limits per user and globally.
    Thread-safe for async usage.
    """

    def __init__(self, config: RateLimitConfig | None = None):
        """Initialize rate limiter."""
        self.config = config or RateLimitConfig()

        # Per-user buckets: {user_id: {endpoint: TokenBucket}}
        self._user_buckets: dict[str, dict[str, TokenBucket]] = {}

        # Global bucket per endpoint
        self._global_buckets: dict[str, TokenBucket] = {}

        # Lock for thread-safe bucket access
        self._lock = asyncio.Lock()

        # Statistics
        self._stats = RateLimitStats()

    async def check_rate_limit(
        self,
        user_id: str,
        endpoint: str,
        tokens: float = 1.0,
    ) -> RateLimitResult:
        """
        Check if request is allowed under rate limits.

        Args:
            user_id: User identifier
            endpoint: API endpoint path
            tokens: Number of tokens to consume (default 1)

        Returns:
            RateLimitResult with allowed status and metadata
        """
        # Check if user is exempt
        if user_id in self.config.exempt_user_ids:
            return RateLimitResult(allowed=True, reason="exempt")

        async with self._lock:
            # Check global limit first
            global_result = await self._check_global_limit(endpoint, tokens)
            if not global_result.allowed:
                self._stats.record_rejected("global", endpoint)
                return global_result

            # Check per-user limit
            user_result = await self._check_user_limit(user_id, endpoint, tokens)
            if not user_result.allowed:
                # Refund the already-consumed global token: a single user
                # hammering past their own limit must not drain the shared
                # global bucket for everyone else.
                self._global_buckets[endpoint].refund(tokens)
                self._stats.record_rejected("user", endpoint)
                return user_result

            self._stats.record_allowed(endpoint)
            return RateLimitResult(allowed=True)

    async def _check_global_limit(self, endpoint: str, tokens: float) -> RateLimitResult:
        """Check global rate limit for endpoint."""
        if endpoint not in self._global_buckets:
            limit = self.config.global_max_requests_per_minute
            self._global_buckets[endpoint] = TokenBucket.create_for_rate(
                limit, 60, self.config.burst_multiplier
            )

        bucket = self._global_buckets[endpoint]
        allowed, wait_time = bucket.consume(tokens)

        if not allowed:
            return RateLimitResult(
                allowed=False,
                reason="global_limit_exceeded",
                retry_after=wait_time,
                limit_type="global",
            )
        return RateLimitResult(allowed=True)

    async def _check_user_limit(
        self, user_id: str, endpoint: str, tokens: float
    ) -> RateLimitResult:
        """Check per-user rate limit."""
        if user_id not in self._user_buckets:
            self._user_buckets[user_id] = {}

        if endpoint not in self._user_buckets[user_id]:
            limit = self.config.get_limit_for_endpoint(endpoint, "minute")
            self._user_buckets[user_id][endpoint] = TokenBucket.create_for_rate(
                limit, 60, self.config.burst_multiplier
            )

        bucket = self._user_buckets[user_id][endpoint]
        allowed, wait_time = bucket.consume(tokens)

        if not allowed:
            return RateLimitResult(
                allowed=False,
                reason="user_limit_exceeded",
                retry_after=wait_time,
                limit_type="user",
                remaining=int(bucket.tokens),
            )

        return RateLimitResult(
            allowed=True,
            remaining=int(bucket.tokens),
        )

    def get_stats(self) -> dict[str, Any]:
        """Get rate limiting statistics."""
        return self._stats.to_dict()

    async def cleanup_stale_buckets(self, max_age_seconds: float = 3600) -> int:
        """Remove buckets that haven't been used recently."""
        now = time.time()
        removed = 0

        async with self._lock:
            stale_users = []
            for user_id, endpoints in self._user_buckets.items():
                stale_endpoints = [
                    ep
                    for ep, bucket in endpoints.items()
                    if now - bucket.last_update > max_age_seconds
                ]
                for ep in stale_endpoints:
                    del endpoints[ep]
                    removed += 1
                if not endpoints:
                    stale_users.append(user_id)

            for user_id in stale_users:
                del self._user_buckets[user_id]

        return removed


# ===== Rate Limit Result =====


@dataclass
class RateLimitResult:
    """Result of rate limit check."""

    allowed: bool
    reason: str = ""
    retry_after: float = 0.0  # seconds
    limit_type: str = ""  # "user" or "global"
    remaining: int = -1  # Remaining requests if known

    def to_headers(self) -> dict[str, str]:
        """Generate response headers for rate limit info."""
        headers = {}
        if self.retry_after > 0:
            headers["Retry-After"] = str(int(self.retry_after) + 1)
        if self.remaining >= 0:
            headers["X-RateLimit-Remaining"] = str(self.remaining)
        return headers


# ===== Rate Limit Statistics =====


@dataclass
class RateLimitStats:
    """Statistics about rate limiting."""

    total_requests: int = 0
    allowed_requests: int = 0
    rejected_requests: int = 0
    rejections_by_type: dict[str, int] = field(default_factory=dict)
    rejections_by_endpoint: dict[str, int] = field(default_factory=dict)
    requests_by_endpoint: dict[str, int] = field(default_factory=dict)

    def record_allowed(self, endpoint: str) -> None:
        """Record an allowed request."""
        self.total_requests += 1
        self.allowed_requests += 1
        self.requests_by_endpoint[endpoint] = self.requests_by_endpoint.get(endpoint, 0) + 1

    def record_rejected(self, limit_type: str, endpoint: str) -> None:
        """Record a rejected request."""
        self.total_requests += 1
        self.rejected_requests += 1
        self.rejections_by_type[limit_type] = self.rejections_by_type.get(limit_type, 0) + 1
        self.rejections_by_endpoint[endpoint] = self.rejections_by_endpoint.get(endpoint, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_requests": self.total_requests,
            "allowed_requests": self.allowed_requests,
            "rejected_requests": self.rejected_requests,
            "rejection_rate": round(self.rejected_requests / max(1, self.total_requests), 4),
            "rejections_by_type": self.rejections_by_type,
            "rejections_by_endpoint": self.rejections_by_endpoint,
            "requests_by_endpoint": self.requests_by_endpoint,
        }


# ===== FastAPI Middleware =====


def normalized_route_path(scope: dict[str, Any]) -> str:
    """Return an endpoint path independent of a Starlette mount root.

    Starlette versions and test clients may expose a mounted request either as
    ``path=/chat, root_path=/api/ai`` or as
    ``path=/api/ai/chat, root_path=/api/ai``. Both normalize to ``/chat``.
    """
    path = str(scope.get("path") or "/")
    root_path = str(scope.get("root_path") or "")
    if root_path and path == root_path:
        path = "/"
    elif root_path and path.startswith(f"{root_path.rstrip('/')}/"):
        path = path[len(root_path.rstrip("/")) :]
    if not path.startswith("/"):
        path = f"/{path}"
    if len(path) > 1:
        path = path.rstrip("/")
    return path


async def _send_json(
    send: Any,
    *,
    status: int,
    content: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> None:
    """Send a small JSON response from pure ASGI middleware."""
    body = json.dumps(content, separators=(",", ":")).encode("utf-8")
    raw_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    raw_headers.extend(
        (name.encode("latin-1"), value.encode("latin-1")) for name, value in (headers or {}).items()
    )
    await send({"type": "http.response.start", "status": status, "headers": raw_headers})
    await send({"type": "http.response.body", "body": body})


class RateLimitMiddleware:
    """
    FastAPI middleware for rate limiting.

    Applies rate limits to all requests based on user ID and endpoint.
    """

    def __init__(
        self,
        app: Any,
        limiter: RateLimiter | None = None,
        user_id_header: str = "X-User-ID",
        exempt_paths: set[str] | None = None,
    ):
        """Initialize middleware."""
        self.app = app
        self.limiter = limiter or RateLimiter()
        # Retained only for constructor compatibility. This header is always
        # ignored as authority and never supplies a rate-limit key.
        self.user_id_header = user_id_header
        self.exempt_paths = exempt_paths or {"/health", "/docs", "/openapi.json"}

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Rate limit HTTP calls using only the boundary-derived principal."""
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        header_name = self.user_id_header.lower().encode("latin-1")
        if any(name.lower() == header_name for name, _ in scope.get("headers", [])):
            record_identity_anomaly("legacy_header_user_id")

        endpoint = normalized_route_path(scope)

        # Skip exempt paths
        if endpoint in self.exempt_paths or str(scope.get("method", "GET")).upper() == "OPTIONS":
            await self.app(scope, receive, send)
            return

        principal = scope.get(AI_PRINCIPAL_SCOPE_KEY)
        if not isinstance(principal, AIPrincipal):
            await _send_json(
                send,
                status=401,
                content={
                    "error": "authentication_required",
                    "message": "AI authentication is required",
                },
            )
            return

        # Check rate limit
        result = await self.limiter.check_rate_limit(
            user_id=principal.rate_limit_key,
            endpoint=endpoint,
        )

        if not result.allowed:
            logger.warning(
                "AI rate limit exceeded (endpoint=%s, reason=%s)",
                endpoint,
                result.reason,
            )
            await _send_json(
                send,
                status=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": f"Too many requests. Please retry after {int(result.retry_after)} seconds.",
                    "retry_after": int(result.retry_after) + 1,
                },
                headers=result.to_headers(),
            )
            return

        # Add rate limit headers to response
        async def send_with_rate_headers(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                message = dict(message)
                response_headers = list(message.get("headers", []))
                response_headers.extend(
                    (key.encode("latin-1"), value.encode("latin-1"))
                    for key, value in result.to_headers().items()
                )
                message["headers"] = response_headers
            await send(message)

        await self.app(scope, receive, send_with_rate_headers)


# ===== Decorator for Individual Endpoints =====


def rate_limit(
    limiter: RateLimiter,
    max_requests: int | None = None,
    window_seconds: int = 60,
    user_id_param: str = "user_id",
) -> Callable[[Callable[..., Coroutine[Any, Any, T]]], Callable[..., Coroutine[Any, Any, T]]]:
    """
    Decorator to add rate limiting to individual endpoints.

    Usage:
        limiter = RateLimiter()

        @app.post("/chat")
        @rate_limit(limiter, max_requests=10, window_seconds=60)
        async def chat(request: ChatRequest):
            ...
    """

    def decorator(
        func: Callable[..., Coroutine[Any, Any, T]],
    ) -> Callable[..., Coroutine[Any, Any, T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            # Check for Request object in args
            request_obj = next((arg for arg in args if isinstance(arg, Request)), None)

            # Also check kwargs for request
            if request_obj is None:
                request_obj = kwargs.get("request")

            # Get endpoint from function name or request
            endpoint = f"/{func.__name__}"
            principal = None
            if request_obj is not None:
                endpoint = normalized_route_path(request_obj.scope)
                principal = request_obj.scope.get(AI_PRINCIPAL_SCOPE_KEY)
                if request_obj.headers.get("X-User-ID") is not None:
                    record_identity_anomaly("legacy_header_user_id")
            if not isinstance(principal, AIPrincipal):
                raise HTTPException(status_code=401, detail="AI authentication required")

            # Check rate limit
            result = await limiter.check_rate_limit(
                user_id=principal.rate_limit_key,
                endpoint=endpoint,
            )

            if not result.allowed:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "rate_limit_exceeded",
                        "message": f"Too many requests. Please retry after {int(result.retry_after)} seconds.",
                        "retry_after": int(result.retry_after) + 1,
                    },
                    headers=result.to_headers(),
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


# ===== Global Rate Limiter Instance =====

_rate_limiter: RateLimiter | None = None


def get_rate_limiter(config: RateLimitConfig | None = None) -> RateLimiter:
    """Get or create global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(config)
    return _rate_limiter


# ===== Export =====

__all__ = [
    "RateLimitConfig",
    "RateLimitMiddleware",
    "RateLimitResult",
    "RateLimitStats",
    "RateLimiter",
    "TokenBucket",
    "get_rate_limiter",
    "normalized_route_path",
    "rate_limit",
]
