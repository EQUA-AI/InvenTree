"""Fixed output sample transport bounds, without a provider or input audio."""

import base64
import io
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from ai.core.tests.test_realtime_session_api import _settings
from ai.core.voice import sample


class SampleSocket:
    def __init__(self, events):
        self.events = events
        self.sent = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True

    async def send_json(self, body):
        self.sent.append(body)

    async def __aiter__(self):
        for event in self.events:
            yield SimpleNamespace(type=aiohttp.WSMsgType.TEXT, json=lambda event=event: event)


async def run_sample(events):
    socket = SampleSocket(events)
    http = SampleSocket([])
    http.ws_connect = lambda *_args, **_kwargs: socket
    with (
        patch("aiohttp.ClientSession", return_value=http),
        patch("ai.core.voice.gateway._token_cache.bearer", new=AsyncMock(return_value="synthetic")),
        patch("ai.core.config.get_settings", return_value=_settings()),
    ):
        result = await sample.synthesize_sample("en-US", "en-US-AvaNeural")
    assert socket.closed and http.closed
    return result, socket


def events_for(audio=b"\x00\x00" * 240, status="completed"):
    return [
        {"type": "session.updated"},
        {"type": "response.created", "response": {"id": "sample-output"}},
        {
            "type": "response.audio.delta",
            "response_id": "sample-output",
            "delta": base64.b64encode(audio).decode(),
        },
        {"type": "response.done", "response": {"id": "sample-output", "status": status}},
    ]


@pytest.mark.asyncio
async def test_sample_only_sends_fixed_text_and_encodes_bounded_pcm():
    data, socket = await run_sample(events_for())
    with wave.open(io.BytesIO(data)) as output:
        assert output.getframerate() == 24000
        assert output.getnchannels() == 1
        assert output.getsampwidth() == 2
        assert output.getnframes() == 240
    assert [body["type"] for body in socket.sent] == ["session.update", "response.create"]
    policy = socket.sent[0]["session"]
    assert policy["tools"] == [] and policy["turn_detection"]["create_response"] is False
    assert policy["voice"]["name"] == "en-US-AvaNeural"
    assert sample.SAMPLE_TEXT["en-US"] in str(socket.sent[1])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        events_for(audio=b""),
        events_for(status="cancelled"),
        events_for(audio=b"\x00" * (sample.MAX_AUDIO_BYTES + 1)),
        [{"type": "response.created", "response": {"id": "unsolicited"}}],
        [{"type": "session.updated"}, {"type": "error"}],
    ],
)
async def test_sample_refuses_empty_incomplete_oversized_or_unrequested_output(events):
    with pytest.raises(ValueError):
        await run_sample(events)
