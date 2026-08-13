"""S46: content-free tool/step events — sink mechanics and fault discipline."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
from typing import ClassVar

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

from ai.core.streaming import EventType
from ai.core.tool_events import (
    MAX_TOOL_EVENTS_PER_TURN,
    ToolEventSink,
    bind_tool_event_sink,
    current_tool_event_sink,
)


class _Emitter:
    def __init__(self, broken: bool = False):
        self.events = []
        self._broken = broken

    async def emit(self, event):
        if self._broken:
            raise RuntimeError("emitter down")
        self.events.append(event)


def _run(coro):
    return asyncio.run(coro)


class TestToolEventSink:
    def test_started_and_ended_shapes_are_content_free(self) -> None:
        emitter = _Emitter()
        sink = ToolEventSink(emitter=emitter, thread_id="t1", run_id="r1")
        _run(sink.started("call1", "get_part"))
        _run(sink.ended("call1", "get_part", "ok", 12.34))
        assert [e.event_type for e in emitter.events] == [
            EventType.TOOL_CALL_START,
            EventType.TOOL_CALL_END,
        ]
        start_data = emitter.events[0].data
        end_data = emitter.events[1].data
        assert start_data == {"toolCallId": "call1", "toolCallName": "get_part"}
        assert end_data == {
            "toolCallId": "call1",
            "toolCallName": "get_part",
            "status": "ok",
            "durationMs": 12.3,
        }
        # The fault discipline in one assertion: nothing argument- or
        # result-shaped may exist on either payload.
        for data in (start_data, end_data):
            assert "arguments" not in data
            assert "result" not in data

    def test_broken_emitter_never_raises(self) -> None:
        sink = ToolEventSink(emitter=_Emitter(broken=True), thread_id="t", run_id="r")
        _run(sink.started("c", "tool"))
        _run(sink.ended("c", "tool", "error", 1.0))

    def test_event_cap_stops_emission(self) -> None:
        emitter = _Emitter()
        sink = ToolEventSink(emitter=emitter, thread_id="t", run_id="r")

        async def flood():
            for index in range(MAX_TOOL_EVENTS_PER_TURN + 20):
                await sink.started(f"c{index}", "tool")

        _run(flood())
        assert len(emitter.events) == MAX_TOOL_EVENTS_PER_TURN

    def test_bind_scopes_the_sink(self) -> None:
        emitter = _Emitter()
        assert current_tool_event_sink.get() is None
        with bind_tool_event_sink(emitter, "t1", "r1") as sink:
            assert current_tool_event_sink.get() is sink
        assert current_tool_event_sink.get() is None


class TestMiddlewareEmission:
    """The invocation guard emits through the bound sink around dispatch."""

    def test_ok_path_emits_start_then_ok(self) -> None:
        from unittest.mock import patch

        from ai.core.tools.invocation_guard import CapabilityInvocationMiddleware

        emitter = _Emitter()

        class _Fn:
            name = "get_part"

        class _Context:
            function = _Fn()
            arguments: ClassVar[dict] = {
                "part_id": 42,
                "secret_text": "never-on-the-wire",
            }
            result: ClassVar[dict] = {"pk": 42}

        async def _next(context):
            await asyncio.sleep(0)
            return None

        async def scenario():
            with (
                patch(
                    "ai.core.tools.invocation_guard.authorize_invocation",
                    return_value=None,
                ),
                bind_tool_event_sink(emitter, "t1", "r1"),
            ):
                await CapabilityInvocationMiddleware().process(_Context(), _next)

        _run(scenario())
        kinds = [e.event_type for e in emitter.events]
        assert kinds == [EventType.TOOL_CALL_START, EventType.TOOL_CALL_END]
        assert emitter.events[1].data["status"] == "ok"
        # Argument values must never appear anywhere in the emitted payloads.
        import json

        blob = json.dumps([e.data for e in emitter.events])
        assert "never-on-the-wire" not in blob
        assert "part_id" not in blob

    def test_error_path_emits_error_and_reraises(self) -> None:
        from unittest.mock import patch

        import pytest
        from ai.core.tools.invocation_guard import CapabilityInvocationMiddleware

        emitter = _Emitter()

        class _Fn:
            name = "get_part"

        class _Context:
            function = _Fn()
            arguments: ClassVar[dict] = {}
            result = None

        async def _next(context):
            await asyncio.sleep(0)
            raise ValueError("tool blew up")

        async def scenario():
            with (
                patch(
                    "ai.core.tools.invocation_guard.authorize_invocation",
                    return_value=None,
                ),
                bind_tool_event_sink(emitter, "t1", "r1"),
            ):
                await CapabilityInvocationMiddleware().process(_Context(), _next)

        with pytest.raises(ValueError):
            _run(scenario())
        assert emitter.events[-1].data["status"] == "error"

    def test_unbound_sink_is_a_no_op(self) -> None:
        from unittest.mock import patch

        from ai.core.tools.invocation_guard import CapabilityInvocationMiddleware

        class _Fn:
            name = "get_part"

        class _Context:
            function = _Fn()
            arguments: ClassVar[dict] = {}
            result = None

        async def _next(context):
            await asyncio.sleep(0)
            return None

        async def scenario():
            with patch(
                "ai.core.tools.invocation_guard.authorize_invocation",
                return_value=None,
            ):
                await CapabilityInvocationMiddleware().process(_Context(), _next)

        _run(scenario())  # must simply not raise


def test_contentful_tool_events_have_no_emit_sites() -> None:
    """TOOL_CALL_ARGS / TOOL_CALL_RESULT stay unemitted (fault discipline)."""
    from pathlib import Path

    core = Path(__file__).resolve().parents[1]
    offenders = []
    for path in core.rglob("*.py"):
        if "tests" in path.parts or "Tools-Testing" in path.parts:
            continue
        text = path.read_text()
        # The streaming helpers may REFERENCE the enum members; what must not
        # exist is a sink/emit call site outside streaming.py's dormant
        # helpers.
        if path.name == "streaming.py":
            continue
        if "EventType.TOOL_CALL_ARGS" in text or "EventType.TOOL_CALL_RESULT" in text:
            offenders.append(str(path))
    assert not offenders, f"contentful tool events referenced in: {offenders}"
