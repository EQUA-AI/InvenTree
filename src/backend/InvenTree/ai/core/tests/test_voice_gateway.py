"""Unit tests for the Voice Live provider gateway (WS4-T4 wiring).

No network or Azure access: the WebSocket is faked at the ``_connect`` seam
so the tests exercise policy merging, reply selection, the event gate, and
registry lifecycle exactly as the routes consume them.
"""

from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
import pytest
from ai.core.config import Settings
from ai.core.voice import gateway
from ai.core.voice.gateway import VoiceLiveChannel, channel_for_session, close_channel
from ai.core.voice.signaling import SignalingError, TransportUnavailable


def _settings() -> Settings:
    return Settings(_env_file=None)


class FakeWs:
    """Minimal aiohttp-shaped WebSocket double."""

    def __init__(self, events):
        self._events = deque(
            SimpleNamespace(type=aiohttp.WSMsgType.TEXT, json=lambda e=e: e) for e in events
        )
        self.sent = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.popleft()

    async def close(self):
        self.closed = True


def _channel_with(events) -> tuple[VoiceLiveChannel, FakeWs]:
    channel = VoiceLiveChannel("session-1")
    ws = FakeWs(events)

    async def fake_connect():
        await asyncio.sleep(0)
        channel._ws = ws
        return ws

    channel._connect = fake_connect  # type: ignore[method-assign]
    return channel, ws


def test_sdp_request_merges_server_policy_and_returns_answer():
    channel, ws = _channel_with([
        {"type": "session.created"},
        {"type": "rtc.call.sdp.created", "sdp_answer": "v=0\r\nans"},
    ])

    async def run():
        with patch("ai.core.config.get_settings", return_value=_settings()):
            reply = await channel.request_sdp_answer({
                "type": "rtc.call.sdp.create",
                "sdp_offer": "v=0\r\noffer",
            })
        await channel.close()
        return reply

    reply = asyncio.run(run())
    assert reply["type"] == "rtc.call.sdp.created"
    assert reply["sdp_answer"] == "v=0\r\nans"
    # The server policy rides in the same request: Voice Live never answers
    # on its own and never carries tools.
    request = ws.sent[0]
    session = request["session"]
    assert session["turn_detection"]["create_response"] is False
    assert session["tools"] == []
    assert session["tool_choice"] == "none"


def test_autonomous_provider_response_fails_closed():
    channel, ws = _channel_with([
        # An answer nobody asked for: unknown response id -> forbidden.
        {"type": "response.done", "response": {"id": "rogue"}},
    ])

    async def run():
        with patch("ai.core.config.get_settings", return_value=_settings()):
            await channel.request_sdp_answer({
                "type": "rtc.call.sdp.create",
                "sdp_offer": "v=0\r\noffer",
            })

    with pytest.raises(SignalingError):
        asyncio.run(run())
    assert ws.closed


def test_provider_close_without_reply_is_transport_unavailable():
    channel, _ws = _channel_with([{"type": "session.created"}])

    async def run():
        with patch("ai.core.config.get_settings", return_value=_settings()):
            await channel.request_sdp_answer({
                "type": "rtc.call.sdp.create",
                "sdp_offer": "v=0\r\noffer",
            })

    with pytest.raises(TransportUnavailable):
        asyncio.run(run())


def test_send_control_without_connection_fails_closed():
    channel = VoiceLiveChannel("session-2")

    with pytest.raises(TransportUnavailable):
        asyncio.run(channel.send_control({"type": "response.create"}))


def test_registry_reuses_channels_and_close_is_idempotent():
    session = SimpleNamespace(id="reg-1")
    try:
        first = channel_for_session(session)
        second = channel_for_session(session)
        assert first is second
    finally:
        asyncio.run(close_channel("reg-1"))
    # Idempotent: closing an unknown/already-closed session is a no-op.
    asyncio.run(close_channel("reg-1"))
    assert "reg-1" not in gateway._channels
