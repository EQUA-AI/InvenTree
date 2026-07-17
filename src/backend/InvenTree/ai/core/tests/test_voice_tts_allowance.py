"""Regression tests for exact-TTS response allowances on the event gate.

An allowance registered for the application's own ``response.create`` must
never outlive its request: a failed send, a provider rejection, or a torn
connection would otherwise leave a token that legitimizes the next
autonomous provider response — the two-agent-drift case the gate exists
to catch.
"""

from __future__ import annotations

import asyncio

import pytest
from ai.core.voice.gateway import VoiceLiveChannel
from ai.core.voice.provider import EventGate
from ai.core.voice.signaling import TransportUnavailable

TTS_PAYLOAD = {
    "type": "response.create",
    "response": {
        "pre_generated_assistant_message": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Spoken."}],
        }
    },
}


class FailingWs:
    closed = False

    async def send_json(self, _payload):
        raise ConnectionResetError("mid-write")


class RecordingWs:
    closed = False

    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


def test_send_failure_rolls_back_the_allowance():
    channel = VoiceLiveChannel("session-1")
    channel._ws = FailingWs()
    with pytest.raises(ConnectionResetError):
        asyncio.run(channel.send_control(TTS_PAYLOAD))
    assert channel._gate.pending_app_responses == 0
    rogue = {"type": "response.created", "response": {"id": "resp_rogue"}}
    assert channel._gate.classify(rogue) == "forbidden"


def test_successful_send_registers_exactly_one_allowance():
    channel = VoiceLiveChannel("session-2")
    ws = RecordingWs()
    channel._ws = ws
    asyncio.run(channel.send_control(TTS_PAYLOAD))
    assert ws.sent == [TTS_PAYLOAD]
    assert channel._gate.pending_app_responses == 1


def test_teardown_clears_pending_allowances():
    channel = VoiceLiveChannel("session-3")
    channel._ws = RecordingWs()
    asyncio.run(channel.send_control(TTS_PAYLOAD))
    asyncio.run(channel._teardown())
    assert channel._gate.pending_app_responses == 0


def test_closed_channel_still_fails_closed():
    channel = VoiceLiveChannel("session-4")
    with pytest.raises(TransportUnavailable):
        asyncio.run(channel.send_control(TTS_PAYLOAD))
    assert channel._gate.pending_app_responses == 0


def test_provider_error_consumes_the_allowance():
    gate = EventGate()
    gate.expect_app_response()
    assert gate.classify({"type": "error", "error": {"code": "conflict"}}) == "error"
    assert gate.pending_app_responses == 0
    rogue = {"type": "response.created", "response": {"id": "resp_rogue"}}
    assert gate.classify(rogue) == "forbidden"


def test_adopted_response_lifecycle_events_are_not_violations():
    gate = EventGate()
    gate.expect_app_response()
    created = {"type": "response.created", "response": {"id": "resp_app"}}
    assert gate.classify(created) == "speech_lifecycle"
    for event_type in (
        "response.output_item.added",
        "response.content_part.added",
        "response.audio_transcript.delta",
        "response.audio.delta",
        "response.audio.done",
        "response.audio_transcript.done",
        "response.content_part.done",
        "response.output_item.done",
        "response.done",
    ):
        event = {"type": event_type, "response_id": "resp_app"}
        assert gate.classify(event) == "speech_lifecycle", event_type
    assert gate.violations == []


def test_unrequested_lifecycle_events_remain_forbidden():
    gate = EventGate()
    for event_type in (
        "response.created",
        "response.output_item.added",
        "response.audio_transcript.delta",
        "response.audio.delta",
    ):
        event = {"type": event_type, "response_id": "resp_rogue"}
        assert gate.classify(event) == "forbidden", event_type
