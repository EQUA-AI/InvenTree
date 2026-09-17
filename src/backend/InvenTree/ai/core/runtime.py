"""Process-local AI readiness and a single, non-replaying startup supervisor.

Only cached, content-free state is exposed. A failed initialization never admits
business requests; Django liveness is independent of the optional AI plane.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
RuntimeState = Literal[
    "starting", "ready", "transiently_unavailable", "permanently_failed", "stopping"
]
FailureReason = Literal[
    "provider_throttled",
    "provider_unreachable",
    "authentication",
    "configuration",
    "model_pin",
    "initialization",
]


class RuntimeAvailability(BaseModel):
    """Authenticated capability/health detail, with no provider exception text."""

    state: RuntimeState
    available: bool
    reason: FailureReason | None = None
    retry_after_s: int | None = None


@dataclass(frozen=True)
class Failure:
    """A normalized error; deliberately does not retain the source exception."""

    transient: bool
    reason: FailureReason
    status: int | None = None
    retry_after: float = 0


def classify_failure(error: Exception) -> Failure:
    """Walk bounded adapter causes. Explicit fatal faults always win."""
    chain = []
    current = error
    while current is not None and len(chain) < 8 and all(current is not e for e in chain):
        chain.append(current)
        current = current.__cause__ or current.__context__
    transient = None
    for item in chain:
        code = getattr(item, "code", "")
        code = code if isinstance(code, str) else ""
        status = getattr(item, "status_code", None) or getattr(item, "code", None)
        status = status if type(status) is int and 100 <= status <= 599 else None
        if status in (401, 403):
            return Failure(False, "authentication", status)
        if any(marker in code for marker in ("DIMENSION_DRIFT", "MODEL_PIN", "MODEL_MISMATCH")):
            return Failure(False, "model_pin", status)
        if "CONFIG_INVALID" in code or isinstance(item, (ValueError, ImportError)):
            return Failure(False, "configuration", status)
        if status is not None and 400 <= status < 500 and status not in (408, 429):
            return Failure(False, "configuration", status)
        retry_after = getattr(item, "retry_after", None)
        if retry_after is None:
            response = getattr(item, "response", None)
            headers = getattr(response, "headers", {})
            retry_after = headers.get("retry-after", 0) if hasattr(headers, "get") else 0
        try:
            retry_after = min(30.0, max(0.0, float(retry_after or 0)))
        except (TypeError, ValueError, OverflowError):
            retry_after = 0
        if status == 429:
            transient = Failure(True, "provider_throttled", status, retry_after)
        elif status == 408 or (status is not None and 500 <= status <= 599):
            transient = transient or Failure(True, "provider_unreachable", status, retry_after)
        elif isinstance(item, (TimeoutError, ConnectionError)) or type(item).__name__ in {
            "APITimeoutError",
            "APIConnectionError",
            "ConnectTimeout",
            "ReadTimeout",
            "WriteTimeout",
            "PoolTimeout",
            "ConnectError",
            "ReadError",
            "ServiceRequestError",
            "ServiceResponseError",
            "TransportError",
        }:
            transient = transient or Failure(True, "provider_unreachable")
    return transient or Failure(False, "initialization")


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded startup policy, not business-request retry configuration."""

    attempts: int = 3
    cycle_s: float = 60
    cooldown_s: float = 60
    max_cooldown_s: float = 300


class RuntimeSupervisor:
    """One initializer at a time, including while its probe thread drains.

    Stopping signals the owner rather than canceling a to_thread await: request
    timeouts bound provider I/O and no second initializer may race that thread.
    """

    def __init__(self, policy: RetryPolicy = RetryPolicy()):
        self.policy = policy
        self.state: RuntimeState = "starting"
        self.failure: Failure | None = None
        self.retry_at: float | None = None
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None
        self._initial_result: asyncio.Event | None = None
        self._initial_deadline: float = 0

    def snapshot(self) -> RuntimeAvailability:
        return RuntimeAvailability(
            state=self.state,
            available=self.state == "ready",
            reason=self.failure.reason if self.failure else None,
            retry_after_s=max(1, int(self.retry_at - time.monotonic()) + 1)
            if self.retry_at
            else None,
        )

    def start(self, initialize) -> asyncio.Task:
        """Idempotent within a lifespan; a new lifespan gets fresh probe state."""
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._initial_result = asyncio.Event()
            self._initial_deadline = time.monotonic() + self.policy.cycle_s
            self.state, self.failure, self.retry_at = "starting", None, None
            self._task = asyncio.create_task(self._run(initialize), name="ai-runtime-startup")
        return self._task

    async def wait_for_initial_result(self) -> None:
        """Wait for the existing initializer, within its original startup budget.

        A recycled ASGI worker can accept a request before its background
        initializer finishes. Admission waits before any business work; it
        never starts another initializer or replays the request. A failure,
        shutdown, cancellation or expired budget still fails closed.
        """
        if self.state != "starting" or self._initial_result is None:
            return
        remaining = self._initial_deadline - time.monotonic()
        if remaining <= 0:
            return
        with suppress(TimeoutError):
            await asyncio.wait_for(self._initial_result.wait(), timeout=remaining)

    async def close(self) -> None:
        """Wait for the single in-flight attempt's structured cleanup."""
        self.state, self.retry_at = "stopping", None
        if self._initial_result is not None:
            self._initial_result.set()
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            await self._task

    async def _wait(self, seconds: float) -> None:
        self.retry_at = time.monotonic() + seconds
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass
        finally:
            self.retry_at = None

    async def _run(self, initialize) -> None:
        cooldown = self.policy.cooldown_s
        while not self._stop.is_set():
            deadline = time.monotonic() + self.policy.cycle_s
            for attempt in range(self.policy.attempts):
                if self._stop.is_set():
                    return
                try:
                    async with initialize(deadline):
                        if not self._stop.is_set():
                            self.state, self.failure = "ready", None
                        self._initial_result.set()
                        await self._stop.wait()
                    return
                except Exception as error:
                    if self._stop.is_set():
                        return
                    self.failure = classify_failure(error)
                    self.state = (
                        "transiently_unavailable"
                        if self.failure.transient
                        else "permanently_failed"
                    )
                    self._initial_result.set()
                    logger.warning(
                        "ai.runtime state=%s reason=%s status=%s attempt=%s",
                        self.state,
                        self.failure.reason,
                        self.failure.status,
                        attempt + 1,
                    )
                    if not self.failure.transient:
                        await self._stop.wait()
                        return
                    delay = max(
                        self.failure.retry_after, min(8, 2**attempt) + random.uniform(0, 0.5)
                    )
                    if attempt + 1 >= self.policy.attempts or time.monotonic() + delay >= deadline:
                        break
                    await self._wait(delay)
            if not self._stop.is_set():
                await self._wait(cooldown)
                cooldown = min(self.policy.max_cooldown_s, cooldown * 2)


runtime = RuntimeSupervisor()


async def require_ai_runtime(request: Request) -> None:
    """Gate before endpoint parsing/writes, after the principal dependency.

    Health and capability are authenticated read-only diagnostics. No other
    bypass, including session creation, SDP or turns, is allowed.
    """
    from starlette._utils import get_route_path

    if request.method == "GET" and get_route_path(request.scope) in {
        "/health",
        "/voice/capability",
    }:
        return
    await runtime.wait_for_initial_result()
    status = runtime.snapshot()
    if not status.available:
        raise HTTPException(
            status_code=503,
            detail={"code": "AI_RUNTIME_UNAVAILABLE", "runtime": status.model_dump()},
            headers={"Retry-After": str(status.retry_after_s or 5)},
        )


async def liveness(request: Request):  # noqa: RUF029 - cheap ASGI probe, no threadpool
    """Base process liveness: no database, auth or provider work."""
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "alive"})


async def readiness(request: Request):  # noqa: RUF029 - event-loop-owned state
    """Public probe discloses only ready/not-ready, never configuration."""
    from starlette.responses import JSONResponse

    ready = runtime.snapshot().available
    return JSONResponse(
        {"status": "ready" if ready else "not_ready"}, status_code=200 if ready else 503
    )
