"""Azure Voice Live provider gateway (WS4-T4 deployment wiring).

Owns the backend-side Voice Live connection for each active realtime
session. This is the production implementation of the provider-channel
hook exposed by ``ai.core.voice.routes.set_provider_channel_factory``:

- Entra tokens come from ``DefaultAzureCredential`` (managed identity in
  the container, developer login locally) against ``endpoints.TOKEN_SCOPE``.
  No credential, token, or provider URL ever reaches the browser.
- One WebSocket per session to the public-preview
  ``/voice-live/realtime/calls`` endpoint carries SDP signaling and the
  application's exact-TTS ``response.create`` events. RTP media flows
  browser <-> Azure and never transits this process.
- The server session policy (``provider.SessionPolicy``) is merged into the
  ``rtc.call.sdp.create`` request, so ``create_response`` stays false and no
  tools are ever registered on the realtime session.
- Every failure degrades to ``TransportUnavailable``/``SignalingError`` so
  the routes keep reporting honest transport state and text remains the
  fallback. SDP bodies and tokens are never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from typing import Any

from ai.core.voice.endpoints import TOKEN_SCOPE, build_calls_url
from ai.core.voice.provider import EventGate, SessionPolicy
from ai.core.voice.signaling import SignalingError, TransportUnavailable

logger = logging.getLogger(__name__)

#: Seconds to wait for the provider's SDP answer before failing closed.
SDP_ANSWER_TIMEOUT_S = 20.0
#: Refresh margin so a token is never presented near its expiry.
TOKEN_REFRESH_MARGIN_S = 300.0


class _TokenCache:
    """Process-wide Entra token cache for the Voice Live scope."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_on: float = 0.0
        self._credential: Any | None = None
        self._lock = asyncio.Lock()

    async def bearer(self) -> str:
        async with self._lock:
            if self._token and time.time() < self._expires_on - TOKEN_REFRESH_MARGIN_S:
                return self._token
            try:
                from azure.identity.aio import DefaultAzureCredential
            except ImportError as exc:  # pragma: no cover - deployment packaging
                raise TransportUnavailable("azure-identity is not installed") from exc
            if self._credential is None:
                self._credential = DefaultAzureCredential()
            try:
                token = await self._credential.get_token(TOKEN_SCOPE)
            except Exception as exc:
                # Never include the exception body: it can carry request URLs.
                logger.warning("Voice Live token acquisition failed: %s", type(exc).__name__)
                raise TransportUnavailable("credential acquisition failed") from exc
            self._token = token.token
            self._expires_on = float(token.expires_on)
            return self._token

    async def close(self) -> None:
        async with self._lock:
            credential, self._credential = self._credential, None
            self._token = None
            self._expires_on = 0.0
        if credential is not None:  # pragma: no cover - best-effort cleanup
            with contextlib.suppress(Exception):
                await credential.close()


_token_cache = _TokenCache()


class VoiceLiveChannel:
    """Backend-owned Voice Live calls connection for one session.

    Implements the ``ProviderChannel`` protocol used by the SDP relay and
    adds ``send_control`` for exact TTS. The connection is opened lazily on
    the first SDP request and torn down through :func:`close_channel`.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._ws: Any | None = None
        self._http: Any | None = None
        self._drain_task: asyncio.Task | None = None
        self._gate = EventGate()
        self._lock = asyncio.Lock()

    async def _connect(self) -> Any:
        if self._ws is not None and not self._ws.closed:
            return self._ws
        from ai.core.config import get_settings

        try:
            import aiohttp
        except ImportError as exc:  # pragma: no cover - deployment packaging
            raise TransportUnavailable("aiohttp is not installed") from exc

        settings = get_settings()
        url = build_calls_url(
            settings.azure_voicelive_endpoint,
            settings.azure_voicelive_model,
            api_version=settings.azure_voicelive_webrtc_api_version,
        )
        bearer = await _token_cache.bearer()
        self._http = aiohttp.ClientSession()
        try:
            self._ws = await self._http.ws_connect(
                url,
                headers={"Authorization": f"Bearer {bearer}"},
                heartbeat=30.0,
                max_msg_size=4 * 1024 * 1024,
            )
        except Exception as exc:
            await self._teardown()
            logger.warning(
                "Voice Live connect failed for session %s: %s",
                self.session_id,
                type(exc).__name__,
            )
            raise TransportUnavailable("provider connection failed") from exc
        return self._ws

    @staticmethod
    def _session_policy_payload() -> dict[str, Any]:
        from ai.core.config import get_settings

        settings = get_settings()
        policy = SessionPolicy(
            voice_name=settings.azure_voicelive_voice,
            language=settings.azure_voicelive_language,
            transcription_model=settings.azure_voicelive_transcription_model,
            phrase_hints=tuple(settings.azure_voicelive_phrase_hints),
            native_sts=settings.feature_voice_native_sts,
        )
        return policy.session_update_payload()["session"]

    async def request_sdp_answer(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send ``rtc.call.sdp.create`` with server policy; await the reply."""
        async with self._lock:
            ws = await self._connect()
            request = dict(payload)
            request["session"] = self._session_policy_payload()
            try:
                await ws.send_json(request)
                reply = await asyncio.wait_for(
                    self._read_until_sdp_reply(ws), timeout=SDP_ANSWER_TIMEOUT_S
                )
            except (SignalingError, TransportUnavailable):
                await self._teardown()
                raise
            except TimeoutError as exc:
                await self._teardown()
                raise SignalingError("provider signaling timed out") from exc
            except Exception as exc:
                await self._teardown()
                logger.warning(
                    "Voice Live signaling failed for session %s: %s",
                    self.session_id,
                    type(exc).__name__,
                )
                raise SignalingError("provider signaling failed") from exc
            # Keep the control socket alive for exact TTS; drain and discard
            # everything else so provider buffers never back up.
            if self._drain_task is None or self._drain_task.done():
                self._drain_task = asyncio.create_task(self._drain())
            return reply

    async def _read_until_sdp_reply(self, ws: Any) -> dict[str, Any]:
        import aiohttp

        async for message in ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                event = message.json()
            except ValueError:
                continue
            kind = self._gate.classify(event)
            if kind in ("sdp_created", "sdp_error"):
                return event
            if kind == "forbidden":
                # Autonomous provider output: the two-agent drift case.
                raise SignalingError("provider violated the session policy")
            # session acks and lifecycle events are expected; keep reading.
        raise TransportUnavailable("provider closed the signaling channel")

    def has_active_app_response(self) -> bool:
        """Whether an application-requested response may still be playing."""
        return self._gate.has_active_app_response()

    async def send_control(self, payload: dict[str, Any]) -> None:
        """Send one application control event (exact TTS ``response.create``)."""
        ws = self._ws
        if ws is None or ws.closed:
            raise TransportUnavailable("no active provider channel")
        if payload.get("type") == "response.create":
            # Let the gate adopt the acknowledging response id instead of
            # flagging the application's own speech as a policy violation.
            # The client event id lets a provider error be attributed to
            # exactly this request and no other.
            event_id = str(payload.get("event_id") or "") or f"app-{uuid.uuid4().hex}"
            payload = {**payload, "event_id": event_id}
            self._gate.expect_app_response(event_id)
            try:
                await ws.send_json(payload)
            except Exception:
                # A failed send never acknowledges; the allowance must not
                # survive to legitimize an autonomous response later.
                self._gate.abandon_app_response(event_id)
                raise
            return
        await ws.send_json(payload)

    async def _drain(self) -> None:
        """Read and discard provider events after signaling completes.

        Transcripts reach the browser on its own ``voice-live-events`` data
        channel; the backend only watches for policy violations here.
        """
        import aiohttp

        ws = self._ws
        if ws is None:
            return
        try:
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    event = message.json()
                except ValueError:
                    continue
                if self._gate.classify(event) == "forbidden":
                    logger.warning("Voice Live policy violation on session %s", self.session_id)
        except Exception:  # pragma: no cover - connection teardown races
            pass

    async def _teardown(self) -> None:
        ws, self._ws = self._ws, None
        http, self._http = self._http, None
        # No allowance may outlive the connection whose acknowledgement it
        # awaits; the channel object itself is reused across reconnects.
        self._gate.reset_pending()
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()
        if http is not None:
            with contextlib.suppress(Exception):
                await http.close()

    async def close(self) -> None:
        """Close the provider connection and stop the drain task."""
        task, self._drain_task = self._drain_task, None
        if task is not None:
            task.cancel()
        await self._teardown()


_channels: dict[str, VoiceLiveChannel] = {}


def channel_for_session(session) -> VoiceLiveChannel:
    """Provider-channel factory installed by the application lifespan."""
    session_id = str(session.id)
    channel = _channels.get(session_id)
    if channel is None:
        channel = VoiceLiveChannel(session_id)
        _channels[session_id] = channel
    return channel


async def close_channel(session_id: str) -> None:
    """Tear down the channel for one ended session (idempotent)."""
    channel = _channels.pop(str(session_id), None)
    if channel is not None:
        await channel.close()


async def shutdown() -> None:
    """Close every channel and the credential on application shutdown."""
    for session_id in list(_channels):
        await close_channel(session_id)
    await _token_cache.close()
