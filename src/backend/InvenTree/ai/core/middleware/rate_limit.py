"""
Rate Limiting Middleware for FastAPI

Provides rate limiting to protect Azure OpenAI quotas and prevent abuse.

Two limiter implementations coexist during the S35 rollout:

- ``RateLimiter`` — the legacy per-process token buckets. Wrong once the API
  runs more than one replica: each replica grants the full limit.
- ``WindowedRateLimiter`` — fixed-window counters in the shared Django cache
  (see ``rate_limit_store``), correct across replicas. It also enforces the
  per-hour endpoint limits the bucket limiter silently ignored.

Rollout ladder (flags in ``ai.core.config``):
``FEATURE_DISTRIBUTED_RATE_LIMIT_SHADOW`` runs the windowed limiter next to
the buckets and logs any divergence; ``FEATURE_DISTRIBUTED_RATE_LIMIT_ENFORCE``
hands the decision to the windowed limiter. Both off = legacy buckets only.
The bucket machinery is deleted once enforce has soaked.

Usage:
    app.add_middleware(RateLimitMiddleware, limiter=RateLimiter())
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
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

    # Default limits. The hourly limits are enforced only by the
    # WindowedRateLimiter; the legacy bucket limiter never read them.
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


# ===== Windowed Rate Limiter (S35) =====


class WindowedRateLimiter:
    """Cross-replica fixed-window limiter over a shared ``RateLimitStore``.

    Enforces, per principal: the endpoint's per-minute limit, the endpoint's
    per-hour limit (configured since the beginning but never enforced by the
    bucket limiter), and the shared global per-minute limit. Check order:
    a read-only PEEK of the global window first (a saturated global window
    rejects without charging the user's own minute/hour quotas — otherwise
    someone else's traffic storm plus client auto-retries could lock a user
    out of their hour window), then user-minute and user-hour increments,
    then the global increment (which closes the peek's race window). A
    request over its own user limit never consumes the shared global window
    (the bucket limiter achieved the same with a refund).

    Store failures fail OPEN with a fault log — see the ADR note in
    ``rate_limit_store``.
    """

    def __init__(self, config: RateLimitConfig | None = None, store: Any = None):
        """Initialize with limits config and a counter store."""
        from ai.core.middleware.rate_limit_store import CacheRateLimitStore

        self.config = config or RateLimitConfig()
        self.store = store or CacheRateLimitStore()
        # Per-process observability only; the counters that decide live in
        # the shared store.
        self._stats = RateLimitStats()

    def _report_enforce_fail_open(self) -> None:
        """Report a store fail-open under enforce; never raises."""
        try:
            from ai.core.config import get_settings

            if not getattr(get_settings(), "feature_distributed_rate_limit_enforce", False):
                return
            from ai.core.pilot_latch import report_critical_event

            report_critical_event("enforce_fail_open", "rate-limit store unreadable under enforce")
        except Exception:  # pragma: no cover - reporting is best-effort
            pass

    def check_rate_limit(
        self, user_id: str, endpoint: str, now: float | None = None
    ) -> RateLimitResult:
        """Count this request in every applicable window and decide."""
        from ai.core.middleware.rate_limit_store import seconds_to_window_end

        now = time.time() if now is None else now
        if user_id in self.config.exempt_user_ids:
            return RateLimitResult(allowed=True, reason="exempt")

        global_limit = self.config.global_max_requests_per_minute
        global_seen = self.store.peek(
            scope="global", endpoint=endpoint, key="-", window_seconds=60, now=now
        )
        if global_seen is not None and global_seen >= global_limit:
            self._stats.record_rejected("global", endpoint)
            return RateLimitResult(
                allowed=False,
                reason="global_limit_exceeded",
                retry_after=seconds_to_window_end(now, 60),
                limit_type="global",
            )

        minute_limit = self.config.get_limit_for_endpoint(endpoint, "minute")
        minute_count = self.store.increment(
            scope="user", endpoint=endpoint, key=user_id, window_seconds=60, now=now
        )
        if minute_count is None:
            # S15/Q50(b): a store outage while distributed limiting is in
            # ENFORCE silently fails open — report the critical event
            # (admission control's own store_error stays exempt: its
            # fail-open is a recorded availability ADR, and Q50 names the
            # budget/rate stores only). Request behavior unchanged here.
            self._report_enforce_fail_open()
        remaining = -1 if minute_count is None else max(0, minute_limit - minute_count)
        if minute_count is not None and minute_count > minute_limit:
            self._stats.record_rejected("user", endpoint)
            return RateLimitResult(
                allowed=False,
                reason="user_limit_exceeded",
                retry_after=seconds_to_window_end(now, 60),
                limit_type="user",
                remaining=0,
            )

        hour_limit = self.config.get_limit_for_endpoint(endpoint, "hour")
        hour_count = self.store.increment(
            scope="user", endpoint=endpoint, key=user_id, window_seconds=3600, now=now
        )
        if hour_count is not None and hour_count > hour_limit:
            self._stats.record_rejected("user_hour", endpoint)
            return RateLimitResult(
                allowed=False,
                reason="user_hourly_limit_exceeded",
                retry_after=seconds_to_window_end(now, 3600),
                limit_type="user",
                remaining=0,
            )

        global_count = self.store.increment(
            scope="global", endpoint=endpoint, key="-", window_seconds=60, now=now
        )
        if global_count is not None and global_count > global_limit:
            self._stats.record_rejected("global", endpoint)
            return RateLimitResult(
                allowed=False,
                reason="global_limit_exceeded",
                retry_after=seconds_to_window_end(now, 60),
                limit_type="global",
            )

        self._stats.record_allowed(endpoint)
        return RateLimitResult(allowed=True, remaining=remaining)

    def get_stats(self) -> dict[str, Any]:
        """Get this process's rate limiting statistics."""
        return self._stats.to_dict()


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

#: The endpoints that actually spend tokens (normalized paths). The S37
#: budget gate applies only here — see the middleware comment. Matched with
#: fullmatch: a new spending route MUST be added as a literal alternative
#: (S49 added /agui) or it silently bypasses the budget.


def _admission_peek_saturated(user_pk) -> int | None:
    """S13 pre-stream peek: retry-after seconds when clearly saturated, else None.

    Read-only (no slot taken, nothing to release) and enforce-only — shadow
    saturation is the authoritative acquire's business. Fail-open on any
    store or config error, like admission itself.
    """
    try:
        from ai.core.config import get_settings
        from ai.core.quota.admission import _keys, _retry_after

        settings = get_settings()
        if not getattr(settings, "feature_ai_admission_control_enforce", False):
            return None
        user_cap = int(getattr(settings, "ai_admission_max_active_per_user", 0) or 0)
        global_cap = int(getattr(settings, "ai_admission_max_active_global", 0) or 0)
        from django.core.cache import cache

        user_key, global_key = _keys(user_pk)
        if user_cap and int(cache.get(user_key) or 0) >= user_cap:
            return _retry_after()
        if global_cap and int(cache.get(global_key) or 0) >= global_cap:
            return _retry_after()
        return None
    except Exception:
        return None


_BUDGETED_ENDPOINTS = re.compile(r"/chat|/chat/stream|/voice/sessions/[^/]+/turns|/agui")


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
        windowed: WindowedRateLimiter | None = None,
    ):
        """Initialize middleware."""
        self.app = app
        self.limiter = limiter or RateLimiter()
        # Retained only for constructor compatibility. This header is always
        # ignored as authority and never supplies a rate-limit key.
        self.user_id_header = user_id_header
        self.exempt_paths = exempt_paths or {"/health", "/docs", "/openapi.json"}
        self.windowed = windowed or get_windowed_rate_limiter(self.limiter.config)

    async def _check(self, user_key: str, endpoint: str) -> RateLimitResult:
        """Bucket path, windowed path, or both, per the S35 rollout flags.

        Enforce hands the decision to the shared-cache windowed limiter.
        Shadow keeps the bucket decision but runs the windowed limiter too
        and logs any divergence — the soak signal for the enforce flip.
        """
        try:
            from ai.core.config import get_settings

            settings = get_settings()
            shadow = settings.feature_distributed_rate_limit_shadow
            enforce = settings.feature_distributed_rate_limit_enforce
        except Exception:  # pragma: no cover - config absent in minimal envs
            shadow, enforce = False, False

        # The windowed limiter does real cache I/O; asyncio.to_thread keeps a
        # slow or stalling cache off the event loop (a blocked loop would
        # stall every concurrent SSE stream and voice turn on this worker —
        # exactly the outage the fail-open posture exists to prevent).
        if enforce:
            return await asyncio.to_thread(self.windowed.check_rate_limit, user_key, endpoint)

        bucket_result = await self.limiter.check_rate_limit(user_id=user_key, endpoint=endpoint)
        if shadow:
            windowed_result = await asyncio.to_thread(
                self.windowed.check_rate_limit, user_key, endpoint
            )
            if windowed_result.allowed != bucket_result.allowed:
                logger.warning(
                    "rate_limit.shadow divergence endpoint=%s bucket_allowed=%s "
                    "windowed_allowed=%s windowed_reason=%s",
                    endpoint,
                    bucket_result.allowed,
                    windowed_result.allowed,
                    windowed_result.reason,
                )
        return bucket_result

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

        # S37: pre-turn daily token budget, one cache GET off-loop. Only
        # turn submissions spend tokens, so only they are gated — an
        # over-cap user must keep read access to their own threads, voice
        # capability probes, and uploads.
        if _BUDGETED_ENDPOINTS.fullmatch(endpoint):
            from ai.core.middleware.budget import check_budget

            budget = await asyncio.to_thread(
                check_budget,
                getattr(principal, "user_pk", None),
                None,
                getattr(principal, "scope", None),
            )
            if budget.store_unavailable:
                # S12 enforce fails CLOSED: the quota store is down, so the
                # ceiling cannot be evaluated — a typed 503, never a pass.
                await _send_json(
                    send,
                    status=503,
                    content={
                        "error": "quota_store_unavailable",
                        "code": "quota_store_unavailable",
                        "retry_after": budget.retry_after,
                    },
                    headers={"Retry-After": str(budget.retry_after)},
                )
                return
            if budget.blocked:
                await _send_json(
                    send,
                    status=429,
                    content={
                        "error": "token_budget_exhausted",
                        # S12: the wire-typed code (QUOTA_ERROR_CODES). The
                        # frontend must NOT auto-retry this one — the reset
                        # is at UTC midnight, not seconds away.
                        "code": "token_budget_exhausted",
                        "retry_after": budget.retry_after,
                    },
                    headers={"Retry-After": str(budget.retry_after)},
                )
                return

            # S13: a READ-ONLY admission peek so a clearly saturated stream
            # gets its 503 before the SSE handshake. GET only, no incr — the
            # authoritative acquire (and its release) lives in
            # turn_service.process(); the peek->acquire race is benign.
            saturated = await asyncio.to_thread(
                _admission_peek_saturated, getattr(principal, "user_pk", None)
            )
            if saturated is not None:
                await _send_json(
                    send,
                    status=503,
                    content={
                        "error": "ai_capacity_busy",
                        "code": "ai_capacity_busy",
                        "retry_after": saturated,
                    },
                    headers={"Retry-After": str(saturated)},
                )
                return

        # Check rate limit
        result = await self._check(principal.rate_limit_key, endpoint)

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
                    # S12: every limiter response carries a machine-readable
                    # code (A13). Bounded retry honoring Retry-After is the
                    # correct client behavior for this one.
                    "code": "rate_limited",
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

    Legacy surface with no current callers; kept for API compatibility. It
    consults only the bucket limiter it is given and retires with the bucket
    machinery once S35 enforce has soaked.

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
_windowed_limiter: WindowedRateLimiter | None = None


def get_rate_limiter(config: RateLimitConfig | None = None) -> RateLimiter:
    """Get or create global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(config)
    return _rate_limiter


def get_windowed_rate_limiter(config: RateLimitConfig | None = None) -> WindowedRateLimiter:
    """Get or create the global windowed limiter (counters live in the cache)."""
    global _windowed_limiter
    if _windowed_limiter is None:
        _windowed_limiter = WindowedRateLimiter(config)
    return _windowed_limiter


# ===== Export =====

__all__ = [
    "RateLimitConfig",
    "RateLimitMiddleware",
    "RateLimitResult",
    "RateLimitStats",
    "RateLimiter",
    "TokenBucket",
    "WindowedRateLimiter",
    "get_rate_limiter",
    "get_windowed_rate_limiter",
    "normalized_route_path",
    "rate_limit",
]
