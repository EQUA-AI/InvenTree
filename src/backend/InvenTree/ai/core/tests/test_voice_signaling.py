"""WS4-T5: SDP relay validation, strict reply envelope, and route wiring."""

from __future__ import annotations

import asyncio

import pytest
from ai.core.voice.signaling import (
    MAX_SDP_BYTES,
    SignalingError,
    TransportUnavailable,
    relay_sdp_offer,
    validate_sdp_offer,
)

OFFER = "v=0\r\no=- 46117 2 IN IP4 127.0.0.1\r\ns=-\r\n"


class FakeChannel:
    def __init__(self, reply):
        self.reply = reply
        self.sent = None

    async def request_sdp_answer(self, payload):
        self.sent = payload
        return self.reply


def test_valid_offer_passes():
    assert validate_sdp_offer(OFFER) == OFFER


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "not-sdp", '{"type":"evil"}', "v" * 0 + "x=0"],
)
def test_malformed_offers_are_rejected(bad):
    with pytest.raises(SignalingError):
        validate_sdp_offer(bad)


def test_oversized_offer_is_rejected():
    with pytest.raises(SignalingError):
        validate_sdp_offer("v=0" + "a" * MAX_SDP_BYTES)


def test_relay_returns_answer_and_exact_create_envelope():
    channel = FakeChannel({"type": "rtc.call.sdp.created", "sdp_answer": "v=0\r\nanswer"})
    answer = asyncio.run(relay_sdp_offer(channel=channel, sdp_offer=OFFER))
    assert answer.startswith("v=0")
    assert channel.sent == {"type": "rtc.call.sdp.create", "sdp_offer": OFFER}


def test_provider_error_fails_closed():
    channel = FakeChannel({"type": "rtc.call.error", "error": {"type": "invalid_request_error"}})
    with pytest.raises(SignalingError):
        asyncio.run(relay_sdp_offer(channel=channel, sdp_offer=OFFER))


def test_unexpected_reply_fails_closed():
    channel = FakeChannel({"type": "session.updated"})
    with pytest.raises(SignalingError):
        asyncio.run(relay_sdp_offer(channel=channel, sdp_offer=OFFER))


def test_malformed_answer_fails_closed():
    channel = FakeChannel({"type": "rtc.call.sdp.created", "sdp_answer": "{}"})
    with pytest.raises(SignalingError):
        asyncio.run(relay_sdp_offer(channel=channel, sdp_offer=OFFER))


def test_missing_channel_reports_transport_unavailable():
    with pytest.raises(TransportUnavailable):
        asyncio.run(relay_sdp_offer(channel=None, sdp_offer=OFFER))


def test_voice_routes_are_registered_on_the_ai_app():
    from ai.core.app import app

    paths = {route.path for route in app.routes}
    for expected in (
        "/voice/sessions",
        "/voice/sessions/{session_id}",
        "/voice/sessions/{session_id}/turns",
        "/voice/sessions/{session_id}/sdp",
        "/voice/sessions/{session_id}/cancel",
    ):
        assert expected in paths, f"missing voice route {expected}"
