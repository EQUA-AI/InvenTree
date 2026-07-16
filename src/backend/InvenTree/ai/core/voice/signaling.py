"""Authenticated SDP relay contract (WS4-T5).

Pure module. The browser's SDP offer is opaque, sensitive, ephemeral session
data: it is validated for shape and size, forwarded through the backend-owned
provider channel, and never logged or persisted. One active call per session.
"""

from __future__ import annotations

from typing import Any, Protocol

MAX_SDP_BYTES = 64 * 1024


class SignalingError(Exception):
    """Stable-code signaling failure."""

    code = "VOICE_SIGNALING_FAILED"


class TransportUnavailable(SignalingError):
    """No provider channel is configured/connected for this deployment."""

    code = "VOICE_TRANSPORT_UNAVAILABLE"


class ProviderChannel(Protocol):
    """Minimal backend-owned Voice Live control channel surface."""

    async def request_sdp_answer(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send ``rtc.call.sdp.create`` and return the provider's reply."""
        ...


def validate_sdp_offer(sdp_offer: str) -> str:
    """Validate shape/size of a browser SDP offer without logging it."""
    if not isinstance(sdp_offer, str) or not sdp_offer.strip():
        raise SignalingError("missing SDP offer")
    if len(sdp_offer.encode("utf-8", errors="ignore")) > MAX_SDP_BYTES:
        raise SignalingError("SDP offer exceeds size bounds")
    if not sdp_offer.lstrip().startswith("v="):
        raise SignalingError("payload is not an SDP offer")
    return sdp_offer


async def relay_sdp_offer(*, channel: ProviderChannel | None, sdp_offer: str) -> str:
    """Relay one validated offer; return the provider's SDP answer.

    The reply envelope is checked strictly: only ``rtc.call.sdp.created``
    yields an answer; ``rtc.call.error`` and anything else fail closed.
    """
    offer = validate_sdp_offer(sdp_offer)
    if channel is None:
        raise TransportUnavailable("no Voice Live channel is available")
    reply = await channel.request_sdp_answer({"type": "rtc.call.sdp.create", "sdp_offer": offer})
    reply_type = reply.get("type")
    if reply_type == "rtc.call.sdp.created":
        answer = str(reply.get("sdp_answer", ""))
        if not answer.lstrip().startswith("v="):
            raise SignalingError("provider returned a malformed SDP answer")
        return answer
    if reply_type == "rtc.call.error":
        raise SignalingError("provider rejected the call offer")
    raise SignalingError("unexpected signaling reply")
