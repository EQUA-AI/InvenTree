"""Authenticated realtime Voice session routes (WS4-T3/T5/T7/T9).

Mounted on the hardened AI app, so every route already requires the boundary
principal. Responses never carry Azure credentials, provider URLs, SDP
payload echoes, or session configuration authority.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.auth import AIPrincipal, get_current_principal
from ai.core.config import get_settings
from ai.core.voice import signaling as sdp_signaling
from ai.core.voice.transcription import (
    FINAL_EVENT_TYPE,
    TranscriptEventError,
    normalize_final_transcript,
)
from asgiref.sync import sync_to_async
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

#: Deployment hook: WS2/VA2 wiring installs a factory returning the
#: backend-owned provider channel for an active session. Until then the
#: transport is honestly unavailable and text remains the fallback.
_provider_channel_factory = None

#: Companion hook: an async callable tearing down the provider channel for
#: one ended session id. Installed beside the factory by the app lifespan.
_provider_channel_closer = None


def set_provider_channel_factory(factory) -> None:
    """Install (or clear) the Voice Live provider channel factory."""
    global _provider_channel_factory
    _provider_channel_factory = factory


def set_provider_channel_closer(closer) -> None:
    """Install (or clear) the async per-session provider channel closer."""
    global _provider_channel_closer
    _provider_channel_closer = closer


class VoiceSessionCreateRequest(BaseModel):
    """Client hints only; the server derives all authority."""

    thread_id: str | None = None


class VoiceTurnRequest(BaseModel):
    """One completed transcript submitted as a normalized voice turn."""

    transcript: str
    item_id: str
    confidence: float | None = None
    language: str = "en-US"


class VoiceSdpRequest(BaseModel):
    """Browser SDP offer relayed through the authenticated backend."""

    sdp_offer: str = Field(min_length=1)


def _principal() -> AIPrincipal:
    principal = get_current_principal()
    if not isinstance(principal, AIPrincipal):
        raise HTTPException(status_code=401, detail="AI authentication required")
    return principal


def is_pilot_user(settings, user_pk) -> bool:
    """Fail-closed cohort check: an empty pilot list admits nobody."""
    try:
        return int(user_pk) in set(settings.voice_pilot_user_ids)
    except (TypeError, ValueError):
        return False


def _require_voice_enabled():
    settings = get_settings()
    if not settings.feature_voice_live:
        raise HTTPException(status_code=404, detail="VOICE_SESSION_UNAVAILABLE")
    principal = _principal()
    if not is_pilot_user(settings, principal.user_pk):
        # Non-cohort actors see the same absence as a disabled feature.
        raise HTTPException(status_code=404, detail="VOICE_SESSION_UNAVAILABLE")
    return settings


def _limits(settings):
    from voice.services.realtime import SessionLimits

    return SessionLimits(
        max_active_per_user=settings.voice_live_max_active_sessions_per_user,
        idle_timeout_s=settings.voice_live_idle_timeout_s,
        max_age_s=settings.voice_live_max_session_age_s,
        max_turns=settings.voice_live_max_turns_per_session,
    )


def _session_error(exc) -> HTTPException:
    from voice.services import realtime

    status = {
        realtime.VoiceSessionForbidden.code: 404,
        realtime.VoiceSessionLimit.code: 429,
        realtime.VoiceSessionExpired.code: 409,
        realtime.ExactSpeechConflict.code: 409,
    }.get(exc.code, 503)
    return HTTPException(status_code=status, detail=exc.code)


def _session_payload(session, settings) -> dict[str, Any]:
    return {
        "id": str(session.id),
        "state": session.state,
        "thread_id": session.thread_id,
        "transport": session.transport or None,
        "transports_allowed": {
            "webrtc": settings.feature_voice_live_webrtc,
            "relay": settings.feature_voice_live_relay,
        },
        "webrtc_preview": True,
        "turn_count": session.turn_count,
        "policy_version": session.policy_version,
        "terminal_reason": session.terminal_reason or None,
    }


async def _owned_session(principal: AIPrincipal, session_id: str, settings):
    from voice.services import realtime

    def _get():
        return realtime.get_owned_session(
            owner=principal.user_pk,
            scope_key=principal.scope,
            session_id=session_id,
            limits=_limits(settings),
        )

    try:
        return await sync_to_async(_get, thread_sensitive=True)()
    except realtime.VoiceSessionError as exc:
        raise _session_error(exc) from None


@router.get("/capability")
async def voice_capability() -> dict:
    """Report whether this authenticated actor may use voice at all.

    Unlike every other voice route this never 404s: the UI uses it to hide
    the voice control entirely for disabled deployments and non-cohort
    users, disclosing nothing beyond a boolean.
    """
    settings = get_settings()
    principal = _principal()
    enabled = settings.feature_voice_live and is_pilot_user(settings, principal.user_pk)
    return {
        "enabled": enabled,
        "webrtc": enabled and settings.feature_voice_live_webrtc,
        "relay": enabled and settings.feature_voice_live_relay,
        "confidence_floor": settings.voice_confidence_floor,
    }


@router.post("/sessions", status_code=201)
async def create_voice_session(request: VoiceSessionCreateRequest) -> dict:
    """Create one owned, bounded, visible realtime session."""
    settings = _require_voice_enabled()
    principal = _principal()

    from aichat.models import generate_thread_id
    from django.contrib.auth import get_user_model
    from voice.services import realtime

    thread_id = request.thread_id or generate_thread_id()
    if thread_id.startswith("scoped_"):
        # Record-grounded sessions require the external #14 substrate.
        raise HTTPException(status_code=404, detail="VOICE_SESSION_FORBIDDEN")

    def _create():
        owner = get_user_model().objects.get(pk=principal.user_pk)
        return realtime.create_session(
            owner=owner,
            thread_id=thread_id,
            scope_key=principal.scope,
            policy_version=principal.policy_version,
            limits=_limits(settings),
        )

    try:
        session = await sync_to_async(_create, thread_sensitive=True)()
    except realtime.VoiceSessionError as exc:
        raise _session_error(exc) from None
    return _session_payload(session, settings)


@router.get("/sessions/{session_id}")
async def get_voice_session(session_id: str) -> dict:
    """Owner-safe session status."""
    settings = _require_voice_enabled()
    session = await _owned_session(_principal(), session_id, settings)
    return _session_payload(session, settings)


@router.delete("/sessions/{session_id}")
async def end_voice_session(session_id: str) -> dict:
    """Idempotently end the session and cancel queued playback."""
    settings = _require_voice_enabled()
    principal = _principal()
    session = await _owned_session(principal, session_id, settings)

    from voice.services import realtime

    session = await sync_to_async(
        lambda: realtime.end_session(session=session), thread_sensitive=True
    )()
    if _provider_channel_closer is not None:
        try:
            await _provider_channel_closer(str(session.id))
        except Exception:
            # The sweeper reconciles channels we could not close cleanly.
            logger.warning("Voice channel close failed", extra={"session": str(session.id)})
    return _session_payload(session, settings)


@router.post("/sessions/{session_id}/cancel")
async def cancel_voice_playback(session_id: str) -> dict:
    """Cancel queued/active playback without deleting the input turn."""
    settings = _require_voice_enabled()
    session = await _owned_session(_principal(), session_id, settings)

    from voice.models import PlaybackState

    def _cancel() -> int:
        return session.utterances.filter(
            playback_state__in=[PlaybackState.PENDING, PlaybackState.REQUESTED]
        ).update(playback_state=PlaybackState.CANCELED)

    canceled = await sync_to_async(_cancel, thread_sensitive=True)()
    return {"id": str(session.id), "canceled_utterances": canceled}


@router.post("/sessions/{session_id}/sdp")
async def relay_sdp(session_id: str, request: VoiceSdpRequest) -> dict:
    """Relay one browser SDP offer through the backend-owned channel.

    The offer/answer bodies are never logged. WebRTC is public preview and
    separately flagged; without a provider channel the transport reports
    honestly unavailable and text remains the fallback.
    """
    settings = _require_voice_enabled()
    if not settings.feature_voice_live_webrtc:
        raise HTTPException(status_code=404, detail="VOICE_TRANSPORT_UNAVAILABLE")
    principal = _principal()
    session = await _owned_session(principal, session_id, settings)
    if session.is_terminal:
        raise HTTPException(status_code=409, detail="VOICE_SESSION_EXPIRED")

    from voice.services import realtime

    channel = _provider_channel_factory(session) if _provider_channel_factory else None
    try:
        answer = await sdp_signaling.relay_sdp_offer(channel=channel, sdp_offer=request.sdp_offer)
    except sdp_signaling.TransportUnavailable as exc:
        await sync_to_async(
            lambda: realtime.record_transport_attempt(
                session=session,
                transport="webrtc",
                outcome="failed",
                reason="transport_unavailable",
            ),
            thread_sensitive=True,
        )()
        raise HTTPException(status_code=503, detail=exc.code) from None
    except sdp_signaling.SignalingError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from None

    await sync_to_async(
        lambda: realtime.record_transport_attempt(
            session=session, transport="webrtc", outcome="connected"
        ),
        thread_sensitive=True,
    )()
    return {"sdp_answer": answer}


@router.post("/sessions/{session_id}/turns")
async def submit_voice_turn(session_id: str, request: VoiceTurnRequest) -> dict:
    """Submit one completed transcript to the shared normalized turn service."""
    settings = _require_voice_enabled()
    principal = _principal()
    session = await _owned_session(principal, session_id, settings)

    try:
        final = normalize_final_transcript({
            "type": FINAL_EVENT_TYPE,
            "transcript": request.transcript,
            "item_id": request.item_id,
            "confidence": request.confidence,
            "language": request.language,
        })
    except TranscriptEventError:
        raise HTTPException(status_code=422, detail="VOICE_TRANSCRIPT_INCOMPLETE") from None

    from ai.core.app import get_turn_service
    from ai.core.trusted_context import build_trusted_turn_context
    from ai.core.turn_service import IdempotencyConflict, TurnAlreadyRunning
    from aichat.models import TurnModality, TurnState
    from voice.models import VoiceUtteranceType
    from voice.services import realtime

    idempotency_key = final.idempotency_key(str(session.id))
    correlation_id = str(session.correlation_id)
    trusted_context = build_trusted_turn_context(
        principal,
        correlation_id=correlation_id,
        browser_context=None,
        server_route_hints=("/voice/turns",),
    )

    def _touch():
        return realtime.touch_session(session=session, limits=_limits(settings), count_turn=True)

    try:
        await sync_to_async(_touch, thread_sensitive=True)()
        result = await get_turn_service().process(
            actor=principal,
            thread_id=session.thread_id,
            content=final.text,
            modality=TurnModality.VOICE,
            trusted_context=trusted_context,
            modality_metadata=final.modality_metadata(),
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
    except realtime.VoiceSessionError as exc:
        raise _session_error(exc) from None
    except (IdempotencyConflict, TurnAlreadyRunning):
        raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT") from None
    except ValueError:
        raise HTTPException(status_code=422, detail="VOICE_TRANSCRIPT_INCOMPLETE") from None
    except Exception:
        logger.error("Voice turn failed", extra={"session": str(session.id)})
        raise HTTPException(status_code=500, detail="VOICE_RESPONSE_INCOMPLETE") from None

    spoken: dict[str, Any] | None = None
    speak_flag = bool((result.canonical_response or {}).get("speak", False))
    if result.response_state == TurnState.COMPLETE and result.spoken_summary.strip() and speak_flag:

        def _persist():
            return realtime.persist_utterance(
                session=session,
                utterance_type=VoiceUtteranceType.COMPLETED_ANSWER,
                spoken_summary=result.spoken_summary,
                response_id=result.turn_id,
                turn_id=result.turn_id,
            )

        try:
            utterance = await sync_to_async(_persist, thread_sensitive=True)()
            spoken = {
                "utterance_id": str(utterance.id),
                "spoken_summary": utterance.spoken_summary,
                "spoken_summary_hash": utterance.spoken_summary_hash,
                "playback_state": utterance.playback_state,
            }
        except realtime.VoiceSessionError as exc:
            raise _session_error(exc) from None

        # Exact TTS (WS4-T8): persist-before-speak already happened above, so
        # the payload builder can prove the text/hash pair before any speech
        # request. A missing or failed channel leaves playback honestly
        # pending; the visible chat answer is never blocked by TTS.
        channel = _provider_channel_factory(session) if _provider_channel_factory else None
        send_control = getattr(channel, "send_control", None)
        if send_control is not None:
            from ai.core.voice.speech import ExactSpeechViolation, build_exact_tts_payload
            from voice.models import PlaybackState

            try:
                tts_payload = build_exact_tts_payload(
                    persisted_text=utterance.spoken_summary,
                    persisted_hash=utterance.spoken_summary_hash,
                )
                await send_control(tts_payload)
            except ExactSpeechViolation as exc:
                raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT") from exc
            except Exception:
                logger.warning("Exact TTS dispatch failed", extra={"session": str(session.id)})
            else:
                utterance = await sync_to_async(
                    lambda: realtime.mark_playback(
                        utterance=utterance, state=PlaybackState.REQUESTED
                    ),
                    thread_sensitive=True,
                )()
                spoken["playback_state"] = utterance.playback_state

    return {
        "session_id": str(session.id),
        "thread_id": result.thread_id,
        "turn_id": result.turn_id,
        "message": result.message,
        "workflow_used": result.workflow_used,
        "response_state": result.response_state,
        "replayed": result.replayed,
        "spoken": spoken,
    }
