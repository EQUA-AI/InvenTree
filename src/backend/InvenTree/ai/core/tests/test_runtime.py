"""Deterministic startup faults; no provider, business operation or live DB."""

import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from ai.core.integrations.boot_budget import openai_probe_options, probe_budget, probe_timeout
from ai.core.integrations.model_pins import ModelPinError
from ai.core.runtime import (
    RuntimeSupervisor,
    classify_failure,
    liveness,
    readiness,
    require_ai_runtime,
)
from fastapi import Depends, FastAPI
from starlette.applications import Starlette
from starlette.routing import Mount, Route


class ProviderError(Exception):
    def __init__(self, status):
        super().__init__("PRIVATE_CREDENTIAL_URL_BODY")
        self.status_code = status
        self.retry_after = 200


def wrapped(error):
    outer = ModelPinError("PRIVATE_DEPLOYMENT", code="EMBEDDING_PROBE_UNREACHABLE")
    outer.__cause__ = error
    return outer


@pytest.mark.parametrize(
    "error,transient,reason",
    [
        (wrapped(ProviderError(429)), True, "provider_throttled"),
        (wrapped(ProviderError(503)), True, "provider_unreachable"),
        (wrapped(TimeoutError()), True, "provider_unreachable"),
        (wrapped(ConnectionError()), True, "provider_unreachable"),
        (wrapped(ProviderError(401)), False, "authentication"),
        (wrapped(ProviderError(403)), False, "authentication"),
        (wrapped(ProviderError(404)), False, "configuration"),
        (ModelPinError("private", code="EMBEDDING_DIMENSION_DRIFT"), False, "model_pin"),
        (ModelPinError("private", code="CHAT_MODEL_PIN_MISMATCH"), False, "model_pin"),
        (ValueError("private"), False, "configuration"),
        (RuntimeError("private"), False, "initialization"),
    ],
)
def test_normalized_failure(error, transient, reason):
    failure = classify_failure(error)
    assert (failure.transient, failure.reason) == (transient, reason)
    assert failure.retry_after <= 30
    assert "PRIVATE" not in repr(failure)


async def until(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("Expected lifecycle transition")


async def test_recovery_cleanup_single_supervisor_and_shutdown(caplog):
    supervisor = RuntimeSupervisor()
    calls, cleanup, waits = [], [], []

    async def wait(seconds):
        waits.append(seconds)
        await asyncio.sleep(0)

    supervisor._wait = wait

    @asynccontextmanager
    async def initialize(deadline):
        calls.append(deadline)
        try:
            if len(calls) == 1:
                raise wrapped(ProviderError(429))
            yield
        finally:
            cleanup.append(len(calls))

    first = supervisor.start(initialize)
    assert supervisor.start(initialize) is first
    await until(lambda: supervisor.snapshot().available)
    assert len(calls) == 2 and cleanup == [1]
    assert waits == [30]
    await supervisor.close()
    assert cleanup == [1, 2]
    assert supervisor.state == "stopping"
    assert not supervisor.snapshot().available
    assert "PRIVATE" not in caplog.text


async def test_repeated_throttles_bounded_cycles_then_recovery():
    supervisor = RuntimeSupervisor()
    count, waits = 0, []

    async def wait(seconds):
        waits.append(seconds)
        await asyncio.sleep(0)

    supervisor._wait = wait

    @asynccontextmanager
    async def initialize(deadline):
        nonlocal count
        count += 1
        if count <= 9:
            raise ProviderError(429)
        yield

    supervisor.start(initialize)
    await until(lambda: supervisor.snapshot().available)
    assert count == 10
    assert waits == [30, 30, 60, 30, 30, 120, 30, 30, 240]
    await supervisor.close()


async def test_permanent_failure_never_retries():
    supervisor = RuntimeSupervisor()
    attempts = []

    @asynccontextmanager
    async def initialize(deadline):
        attempts.append(deadline)
        raise wrapped(ProviderError(401))
        yield  # pragma: no cover

    supervisor.start(initialize)
    await until(lambda: supervisor.state == "permanently_failed")
    assert len(attempts) == 1
    assert not supervisor.snapshot().available
    await supervisor.close()


async def test_shutdown_does_not_cancel_or_duplicate_inflight_initializer():
    supervisor = RuntimeSupervisor()
    entered, release = asyncio.Event(), asyncio.Event()
    clean = []

    @asynccontextmanager
    async def initialize(deadline):
        entered.set()
        await release.wait()  # Models an in-flight, uncancellable probe thread.
        try:
            yield
        finally:
            clean.append(True)

    task = supervisor.start(initialize)
    await entered.wait()
    closing = asyncio.create_task(supervisor.close())
    await asyncio.sleep(0)
    assert not closing.done() and not task.cancelled()
    assert supervisor.start(initialize) is task
    assert not supervisor.snapshot().available
    release.set()
    await closing
    assert clean == [True]
    assert supervisor.state == "stopping"


async def test_readiness_gate_prevents_orphan_business_state(monkeypatch):
    import ai.core.runtime as module

    supervisor = RuntimeSupervisor()
    monkeypatch.setattr(module, "runtime", supervisor)
    writes = Mock()
    app = FastAPI(dependencies=[Depends(require_ai_runtime)])

    @app.post("/voice/sessions")
    async def create():
        writes()
        return {"created": True}

    @app.get("/voice/capability")
    async def capability():
        return {"enabled": True, "runtime": supervisor.snapshot().model_dump()}

    mounted = Starlette(
        routes=[
            Route("/health/live", liveness),
            Route("/health/ai-ready", readiness),
            Mount("/api/ai", app),
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mounted), base_url="http://test"
    ) as client:
        assert (await client.get("/health/live")).status_code == 200
        for state in ("starting", "transiently_unavailable", "permanently_failed", "stopping"):
            supervisor.state = state
            assert (await client.get("/health/ai-ready")).json() == {"status": "not_ready"}
            response = await client.post("/api/ai/voice/sessions", json={})
            assert response.status_code == 503
            cap = (await client.get("/api/ai/voice/capability")).json()
            assert cap["enabled"] and not cap["runtime"]["available"]
        writes.assert_not_called()
        supervisor.state = "ready"
        assert (await client.get("/health/ai-ready")).status_code == 200
        assert (await client.post("/api/ai/voice/sessions")).status_code == 200
        writes.assert_called_once()


def test_probe_budget_limits_only_probe_sdk_calls():
    assert openai_probe_options() == {}
    with probe_budget(time.monotonic() + 60):
        assert openai_probe_options() == {"timeout": 10, "max_retries": 0}
    assert openai_probe_options() == {}
    with probe_budget(time.monotonic() - 1), pytest.raises(TimeoutError):
        probe_timeout()


@pytest.mark.parametrize("voice,devui_failure", [(False, False), (True, False), (True, True)])
async def test_real_initializer_partial_resource_cleanup(monkeypatch, voice, devui_failure):
    import django

    django.setup()
    from ai.core import app
    from ai.core.integrations import model_pins
    from ai.core.voice import gateway, routes

    probes = Mock()
    monkeypatch.setattr(model_pins, "run_boot_probes", probes)
    monkeypatch.setattr(app, "get_settings", lambda: SimpleNamespace(feature_voice_live=voice))
    monkeypatch.setattr(app, "get_devui_settings", lambda: SimpleNamespace(enabled=True))
    monkeypatch.setattr(app, "get_workflow_root", Mock())
    devui = SimpleNamespace(
        start=AsyncMock(side_effect=RuntimeError("private") if devui_failure else None),
        stop=AsyncMock(),
    )
    monkeypatch.setattr(app, "get_devui", lambda: devui)
    shutdown = AsyncMock()
    factory, closer = Mock(), Mock()
    monkeypatch.setattr(gateway, "shutdown", shutdown)
    monkeypatch.setattr(routes, "set_provider_channel_factory", factory)
    monkeypatch.setattr(routes, "set_provider_channel_closer", closer)
    if devui_failure:
        with pytest.raises(RuntimeError):
            async with app.initialize_runtime(time.monotonic() + 60):
                pytest.fail("Partial initialization must not yield ready")
    else:
        async with app.initialize_runtime(time.monotonic() + 60):
            assert not devui.stop.called
            if voice:
                factory.assert_called_once_with(gateway.channel_for_session)
    probes.assert_called_once()
    devui.stop.assert_awaited_once()
    if voice:
        factory.assert_called_with(None)
        closer.assert_called_with(None)
        shutdown.assert_awaited_once()
    else:
        factory.assert_not_called()
        shutdown.assert_not_called()


def test_cohere_probe_does_not_multiply_retries_or_lose_status():
    from ai.core.integrations.embeddings_cohere import (
        AttachmentEmbeddingError,
        CohereEmbeddingClient,
    )

    transport = Mock()
    transport.post.return_value = SimpleNamespace(status_code=429, headers={"retry-after": "2"})
    client = CohereEmbeddingClient(
        endpoint="https://example.invalid", model="embed", dimensions=8, api_key="offline"
    )
    client._client = transport
    with probe_budget(time.monotonic() + 60), pytest.raises(AttachmentEmbeddingError) as caught:
        client.embed_query("synthetic probe")
    transport.post.assert_called_once()
    assert transport.post.call_args.kwargs["timeout"] == 10
    assert classify_failure(wrapped(caught.value)).reason == "provider_throttled"
    assert classify_failure(caught.value).retry_after == 2
    client.close()
    transport.close.assert_called_once()


def test_chat_probe_client_bounded_and_closed_on_failure(monkeypatch):
    import openai
    from ai.core.integrations.model_pins import _probe_chat_deployment

    client = Mock()
    client.chat.completions.create.side_effect = TimeoutError()
    factory = Mock(return_value=client)
    monkeypatch.setattr(openai, "AzureOpenAI", factory)
    settings = SimpleNamespace(
        azure_openai_endpoint="https://example.invalid",
        azure_openai_api_key="offline",
        azure_openai_api_version="test",
    )
    with probe_budget(time.monotonic() + 60), pytest.raises(TimeoutError):
        _probe_chat_deployment(settings, "synthetic", "")
    assert factory.call_args.kwargs["max_retries"] == 0
    assert factory.call_args.kwargs["timeout"] == 10
    client.close.assert_called_once()


async def test_invalid_config_before_lifespan_does_not_kill_django(monkeypatch, caplog):
    import importlib.util
    from pathlib import Path

    import django
    import django.core.asgi
    from ai.core import config
    from starlette.responses import JSONResponse

    django.setup()

    async def base_app(scope, receive, send):
        await JSONResponse({"inventory": "alive"})(scope, receive, send)

    monkeypatch.setattr(django.core.asgi, "get_asgi_application", lambda: base_app)
    monkeypatch.setattr(config, "get_settings", Mock(side_effect=ValueError("PRIVATE_CREDENTIAL")))
    spec = importlib.util.spec_from_file_location(
        "isolated_runtime_asgi", Path(__file__).parents[3] / "InvenTree" / "asgi.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    async with (
        module.lifespan(module.application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=module.application), base_url="http://test"
        ) as client,
    ):
        assert (await client.get("/")).json() == {"inventory": "alive"}
        assert (await client.get("/health/live")).status_code == 200
        assert (await client.get("/health/ai-ready")).status_code == 503
        for path in ("/api/ai/health", "/api/ai/voice/capability", "/api/ai/voice/sessions"):
            response = await client.get(path)
            assert response.status_code == 503
            assert response.json() == {"detail": "AI_RUNTIME_UNAVAILABLE"}
    assert "PRIVATE_CREDENTIAL" not in caplog.text
