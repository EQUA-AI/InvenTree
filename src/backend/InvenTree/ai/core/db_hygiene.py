"""Django connection hygiene for the AI plane (M2 PR 6; plan of record §9 line 1685).

Why this module exists (E38): the estate's PostgreSQL server refuses new
sessions at roughly 33, and every AI web replica was idling about three
persistent sessions of its own. Two leaks explain them:

* The FastAPI mount at ``/api/ai`` is served by the same process as Django's
  ASGI handler, but it is NOT a Django request. Django wraps each of its own
  requests in ``asgiref.sync.ThreadSensitiveContext`` (one executor thread per
  request, torn down at the end) and closes connections through the
  ``request_finished`` signal. The AI mount had neither, so every
  ``sync_to_async(..., thread_sensitive=True)`` ORM hop (``turn_service._call_sync``,
  ``auth.AIBoundaryAuthMiddleware``) landed on asgiref's single process-wide
  executor thread, and that thread kept its Django connection forever.
* Several hops on the AI request path run ORM work on ``asyncio.to_thread``
  pooled threads: the two quota bridges in ``turn_service.process`` (durable
  ``AIQuotaReservation`` rows), the pilot-latch check next to them (a latch
  row read on every cache miss while the flag is armed), the budget check in
  ``RateLimitMiddleware`` (a policy-assignment read on every cache miss) and
  the ``quota_preflight`` read (an unconditional latch row read). Those
  threads are recycled by the default executor and never run
  ``close_old_connections``, so each one that ever touched the ORM kept a
  connection open for the life of the process.

The fix mirrors what Django does for itself:

* :class:`ConnectionReleaseMiddleware` gives every ``http`` request on the AI
  mount its own ``ThreadSensitiveContext`` and, once the inner app has returned
  (i.e. after the response was fully sent), closes the connections owned by
  that request's executor thread via ``connections.close_all()`` -- executed
  ON that thread through ``sync_to_async(thread_sensitive=True)`` before the
  context tears the executor down. ``lifespan`` and ``websocket`` scopes pass
  straight through: the lifespan is deliberately NOT wrapped so startup work
  keeps its process-wide executor.
* :func:`run_in_thread_releasing` is ``asyncio.to_thread`` plus a ``finally``
  on the worker thread that closes that thread's connections, for sync work
  that must stay off the event loop AND off the thread-sensitive executor.

Expected effect (measured in Phase 2 with ``pg_session_sample.py``): at most
one idle session per web replica instead of about three.

Value-free discipline: the only thing ever logged here is an exception class
name at DEBUG level. Django is imported lazily inside functions so the pytest
island (no configured settings) can import this module.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, MutableMapping

    ASGIScope = MutableMapping[str, Any]
    ASGIReceive = Callable[[], Awaitable[MutableMapping[str, Any]]]
    ASGISend = Callable[[MutableMapping[str, Any]], Awaitable[None]]
    ASGIApp = Callable[[ASGIScope, ASGIReceive, ASGISend], Awaitable[None]]

logger = logging.getLogger(__name__)


def _close_all_django_connections() -> None:
    """Close every Django database connection owned by the CURRENT thread.

    A no-op when Django is not importable (the pytest island, tooling that
    imports the AI plane without a settings module). Tests monkeypatch this
    callable to keep Django out of the picture.
    """
    try:
        from django.db import connections
    except Exception:  # pragma: no cover - Django absent or unconfigured
        return
    connections.close_all()


def _release_current_thread() -> None:
    """Release the current thread's Django connections; never raises.

    Hygiene must never fail a turn or a request, so any failure is swallowed
    and reported as an exception class name only.
    """
    try:
        _close_all_django_connections()
    except Exception as exc:
        logger.debug("db_hygiene.release_failed exc_class=%s", type(exc).__name__)


async def run_in_thread_releasing(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Run ``fn`` on the default executor and release its thread's connections.

    ``asyncio.to_thread`` semantics (the caller's context variables are
    copied to the worker), plus a ``finally`` that runs ON the worker thread
    so the pooled thread hands back its Django connection instead of keeping
    it open until the process exits. ``fn``'s return value is returned and
    its exception propagates after the release ran.
    """

    def _run() -> Any:
        try:
            return fn(*args, **kwargs)
        finally:
            _release_current_thread()

    return await asyncio.to_thread(_run)


class ConnectionReleaseMiddleware:
    """Pure-ASGI wrapper: per-request thread-sensitive context + connection release.

    For ``http`` scopes the inner app runs inside ``ThreadSensitiveContext``,
    so every ``sync_to_async(..., thread_sensitive=True)`` call made while
    serving the request lands on one executor thread dedicated to this
    request. After the inner app returns -- the response has been fully sent
    by then -- the connections that thread owns are closed on that same
    thread, and the context then retires the thread. Every other scope type
    (``lifespan``, ``websocket``) is passed through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        from asgiref.sync import ThreadSensitiveContext, sync_to_async

        async with ThreadSensitiveContext():
            try:
                await self.app(scope, receive, send)
            finally:
                try:
                    await sync_to_async(_release_current_thread, thread_sensitive=True)()
                except Exception as exc:  # pragma: no cover - hygiene never fails a request
                    logger.debug(
                        "db_hygiene.release_dispatch_failed exc_class=%s", type(exc).__name__
                    )
