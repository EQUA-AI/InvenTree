"""M2 PR 6: Django connection hygiene for the AI plane (ai/core/db_hygiene.py).

Island tests: no database, no Django. The module-level close callable is
monkeypatched to a recorder so the contract under test is the *placement* of
the release -- which thread, how many times, and when relative to the
response -- not Django's own ``connections.close_all``.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from ai.core import db_hygiene
from asgiref.sync import SyncToAsync, ThreadSensitiveContext, sync_to_async

MAIN_THREAD = threading.get_ident()


class _Recorder:
    """Record every release call with the identity of the thread it ran on."""

    def __init__(self, raises: bool = False) -> None:
        self.threads: list[int] = []
        self.raises = raises

    def __call__(self) -> None:
        self.threads.append(threading.get_ident())
        if self.raises:
            raise RuntimeError("release exploded")


@pytest.fixture
def release(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr(db_hygiene, "_close_all_django_connections", recorder)
    return recorder


# ---------------------------------------------------------------------------
# run_in_thread_releasing
# ---------------------------------------------------------------------------


async def test_run_in_thread_releasing_returns_value_and_releases_on_worker(
    release: _Recorder,
) -> None:
    seen: dict[str, Any] = {}

    def work(a: int, *, b: int) -> int:
        seen["thread"] = threading.get_ident()
        return a + b

    assert await db_hygiene.run_in_thread_releasing(work, 2, b=3) == 5
    assert seen["thread"] != MAIN_THREAD
    assert release.threads == [seen["thread"]], "released exactly once, on the worker thread"


async def test_run_in_thread_releasing_propagates_exception_after_release(
    release: _Recorder,
) -> None:
    def explode() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await db_hygiene.run_in_thread_releasing(explode)
    assert len(release.threads) == 1
    assert release.threads[0] != MAIN_THREAD


async def test_run_in_thread_releasing_swallows_release_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(raises=True)
    monkeypatch.setattr(db_hygiene, "_close_all_django_connections", recorder)

    assert await db_hygiene.run_in_thread_releasing(lambda: "ok") == "ok"
    assert len(recorder.threads) == 1


# ---------------------------------------------------------------------------
# ConnectionReleaseMiddleware
# ---------------------------------------------------------------------------


def _http_scope() -> dict[str, Any]:
    return {"type": "http", "method": "GET", "path": "/chat/stream", "headers": []}


async def _receive() -> dict[str, Any]:  # noqa: RUF029 - async by ASGI/test contract
    return {"type": "http.request", "body": b"", "more_body": False}


class _FakeApp:
    """Inner ASGI app that sends a complete http response.

    It records, in order, every ``send`` it makes (into the shared ``events``
    list the release recorder also appends to), the thread-sensitive context
    that was active while it ran, and the identity of the thread its own
    ``sync_to_async(thread_sensitive=True)`` hop landed on.
    """

    def __init__(self, events: list[tuple[str, Any]], raises: bool = False) -> None:
        self.events = events
        self.raises = raises
        self.context_seen: Any = "unset"
        self.sensitive_thread: int | None = None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.context_seen = SyncToAsync.thread_sensitive_context.get(None)
        self.sensitive_thread = await sync_to_async(threading.get_ident, thread_sensitive=True)()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        self.events.append(("send", "http.response.start"))
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})
        self.events.append(("send", "http.response.body"))
        if self.raises:
            raise RuntimeError("inner app failed after sending")


class _OrderedRelease:
    """Release recorder that also appends to the shared ordering log."""

    def __init__(self, events: list[tuple[str, Any]], raises: bool = False) -> None:
        self.events = events
        self.raises = raises
        self.threads: list[int] = []

    def __call__(self) -> None:
        ident = threading.get_ident()
        self.threads.append(ident)
        self.events.append(("release", ident))
        if self.raises:
            raise RuntimeError("release exploded")


async def _send(_message: dict[str, Any]) -> None:  # noqa: RUF029 - async by ASGI/test contract
    return None


async def test_http_request_releases_once_after_last_send_on_the_sensitive_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []
    recorder = _OrderedRelease(events)
    monkeypatch.setattr(db_hygiene, "_close_all_django_connections", recorder)
    inner = _FakeApp(events)

    assert SyncToAsync.thread_sensitive_context.get(None) is None, "no context before the request"
    await db_hygiene.ConnectionReleaseMiddleware(inner)(_http_scope(), _receive, _send)
    assert SyncToAsync.thread_sensitive_context.get(None) is None, "context reset afterwards"

    # A per-request ThreadSensitiveContext was active while the inner app ran.
    assert isinstance(inner.context_seen, ThreadSensitiveContext)

    # Exactly one release, and it is the LAST event: both sends precede it.
    assert [kind for kind, _ in events] == ["send", "send", "release"]
    assert events[-1] == ("release", recorder.threads[0])
    assert len(recorder.threads) == 1

    # The release ran on the request's own thread-sensitive executor thread:
    # the same thread the inner app's ORM-style hop used, never the loop thread.
    assert inner.sensitive_thread is not None
    assert inner.sensitive_thread != MAIN_THREAD
    assert recorder.threads == [inner.sensitive_thread]


async def test_two_http_requests_get_distinct_sensitive_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-request contexts: the executor is not the process-wide singleton."""
    events: list[tuple[str, Any]] = []
    recorder = _OrderedRelease(events)
    monkeypatch.setattr(db_hygiene, "_close_all_django_connections", recorder)
    first, second = _FakeApp(events), _FakeApp(events)
    middleware = db_hygiene.ConnectionReleaseMiddleware(first)
    await middleware(_http_scope(), _receive, _send)
    await db_hygiene.ConnectionReleaseMiddleware(second)(_http_scope(), _receive, _send)

    assert recorder.threads == [first.sensitive_thread, second.sensitive_thread]
    assert first.context_seen is not second.context_seen


async def test_inner_app_exception_propagates_after_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []
    recorder = _OrderedRelease(events)
    monkeypatch.setattr(db_hygiene, "_close_all_django_connections", recorder)
    inner = _FakeApp(events, raises=True)

    with pytest.raises(RuntimeError, match="inner app failed"):
        await db_hygiene.ConnectionReleaseMiddleware(inner)(_http_scope(), _receive, _send)

    assert [kind for kind, _ in events] == ["send", "send", "release"]
    assert recorder.threads == [inner.sensitive_thread]
    assert SyncToAsync.thread_sensitive_context.get(None) is None


async def test_release_failure_does_not_fail_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []
    recorder = _OrderedRelease(events, raises=True)
    monkeypatch.setattr(db_hygiene, "_close_all_django_connections", recorder)
    inner = _FakeApp(events)

    await db_hygiene.ConnectionReleaseMiddleware(inner)(_http_scope(), _receive, _send)

    assert [kind for kind, _ in events] == ["send", "send", "release"]
    assert len(recorder.threads) == 1


@pytest.mark.parametrize("scope_type", ["lifespan", "websocket"])
async def test_non_http_scopes_pass_through_without_context_or_release(
    monkeypatch: pytest.MonkeyPatch, scope_type: str
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(db_hygiene, "_close_all_django_connections", recorder)
    seen: dict[str, Any] = {}

    async def inner(scope: dict[str, Any], receive: Any, send: Any) -> None:  # noqa: RUF029 - async by ASGI/test contract
        seen["scope"] = scope
        seen["receive"] = receive
        seen["send"] = send
        seen["context"] = SyncToAsync.thread_sensitive_context.get(None)

    scope = {"type": scope_type}
    await db_hygiene.ConnectionReleaseMiddleware(inner)(scope, _receive, _send)

    assert seen["scope"] is scope
    assert seen["receive"] is _receive
    assert seen["send"] is _send
    assert seen["context"] is None, "lifespan/websocket are not wrapped"
    assert recorder.threads == []
