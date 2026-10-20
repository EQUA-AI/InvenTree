"""Authenticated realtime Voice session routes (WS4-T3/T5/T7/T9).

Mounted on the hardened AI app, so every route already requires the boundary
principal. Responses never carry Azure credentials, provider URLs, SDP
payload echoes, or session configuration authority.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Literal

from ai.core.auth import AIPrincipal, get_current_principal
from ai.core.config import get_settings
from ai.core.voice import route_speech, timing
from ai.core.voice import signaling as sdp_signaling
from ai.core.voice.timing import VoiceClientTiming  # noqa: TC002 - Pydantic resolves at runtime
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

#: Per-session turn serialization: hands-free auto-submit can deliver a new
#: utterance while the previous turn is still processing, and the shared
#: provider channel's speech slot must be handed over in order.
#:
#: S35 accepted exception to the "no in-process cross-request state"
#: invariant (ai/README.md): these locks assume a voice session is affine to
#: one replica, which holds because the session's WebSocket pins it to the
#: process that owns the provider channel. Residual risk: if voice turns
#: ever arrive over plain HTTP across replicas, serialization silently
#: weakens to per-replica — revisit before any such transport change.
_turn_locks: dict[str, asyncio.Lock] = {}


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
    locale: str = "en-US"
    voice: str = "en-US-AvaNeural"
    consent_version: str = ""
    # Optional compatible hint: the focus stamp the client last saw. When
    # present and stale the session is refused BEFORE it begins (409
    # VOICE_FOCUS_CHANGED) — confirm the focus candidate via the owner focus
    # PUT first, then start voice. Never a grant. Strictly a real
    # non-negative int (no bool/float/string coercion).
    expected_focus_version: int | None = Field(default=None, strict=True, ge=0, le=2**31 - 1)


class VoiceTurnRequest(BaseModel):
    """One completed transcript submitted as a normalized voice turn."""

    transcript: str
    item_id: str
    confidence: float | None = None
    language: str = "en-US"
    decision_context: dict[str, Any] | None = None
    timing: VoiceClientTiming | None = None
    # Optional compatible hint: the focus stamp recorded with THIS utterance.
    # Participates in the idempotency fingerprint and in admission, so queued
    # words are never reinterpreted against a later focus selection. A stale
    # stamp refuses the turn (409 VOICE_FOCUS_CHANGED); the transport and the
    # session stay alive — a focus change is not a scope change. Strictly a
    # real non-negative int (no bool/float/string coercion).
    expected_focus_version: int | None = Field(default=None, strict=True, ge=0, le=2**31 - 1)
    # The CLIENT'S local progress-cue preference for this transcript
    # (Off/Brief/Guided UX). A hint about output phrasing only: never stored
    # business input, never an approval/decision signal, and deliberately
    # OUTSIDE the business idempotency fingerprint (``timing`` likewise does
    # not enter it).
    progress_mode: Literal["off", "brief", "guided"] = "brief"


class VoicePromptRequest(BaseModel):
    """A client request for one allow-listed spoken prompt (voice-UX A6).

    The client never supplies speech text: ``kind`` selects a server template
    and ``transcript`` (transcript_review only) is the single sanitized slot.
    """

    kind: Literal["transcript_review", "status", "help"]
    transcript: str | None = Field(default=None, max_length=4000)
    item_id: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=64)


class VoiceSdpRequest(BaseModel):
    """Browser SDP offer relayed through the authenticated backend."""

    sdp_offer: str = Field(min_length=1)


def _principal() -> AIPrincipal:
    principal = get_current_principal()
    if not isinstance(principal, AIPrincipal):
        raise HTTPException(status_code=401, detail="AI authentication required")
    return principal


def _require_voice_enabled():
    settings = get_settings()
    if not settings.feature_voice_live:
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


def _session_payload(
    session, settings, focus_state: tuple | None = None, *, speech_audit: dict | None = None
) -> dict[str, Any]:
    # S43: built through the wire model so the generated TS contract and
    # this payload can never drift; model_dump() output is byte-identical
    # to the historic hand-built dict.
    from ai.core.voice.wire import VoiceSessionPayload, VoiceTransportsAllowed

    if focus_state is None:
        # Back-compat default: callers that cannot reach the ORM echo no
        # focus rather than failing the payload.
        focus_state = (None, 0)
    focus, focus_version = focus_state
    return VoiceSessionPayload(
        id=str(session.id),
        state=session.state,
        thread_id=session.thread_id,
        transport=session.transport or None,
        transports_allowed=VoiceTransportsAllowed(
            webrtc=settings.feature_voice_live_webrtc,
            relay=settings.feature_voice_live_relay,
        ),
        webrtc_preview=True,
        turn_count=session.turn_count,
        policy_version=session.policy_version,
        terminal_reason=session.terminal_reason or None,
        analysis_scope_version=getattr(session, "analysis_scope_version", 0),
        locale=getattr(session, "locale", "en-US"),
        voice=getattr(session, "voice", "en-US-AvaNeural"),
        consent_version=getattr(session, "consent_version", ""),
        focus=focus,
        focus_version=focus_version,
        speech_utterance_id=(speech_audit or {}).get("utterance_id"),
        speech_playback_state=(speech_audit or {}).get("playback_state"),
        speech_audit_state=(speech_audit or {}).get("playback_audit_state"),
    ).model_dump(mode="json")


def _thread_scope_version(thread_id: str, owner_pk) -> int:
    """The thread's current analysis-scope version, 0 when it does not exist.

    Owner-bound: a session binds only to a version the actor could read.
    Sync ORM — call from a ``sync_to_async`` context.
    """
    from aichat.models import ChatThread

    return (
        ChatThread.objects
        .filter(id=thread_id, owner_id=owner_pk)
        .values_list("analysis_scope_version", flat=True)
        .first()
        or 0
    )


def _thread_focus_state(thread_id: str, owner_pk, actor_pk) -> tuple[dict[str, Any] | None, int]:
    """The thread's confirmed focus projection + version (owner-bound).

    Echo-only: the projection is hash-free and read live at payload time,
    exactly like ``analysis_scope_version``. The STORED projection is never
    echoed: after the owner binding, the stored candidate is re-authorized
    against the current record readers as the CALLING principal (session
    ownership is not target read authorization), mirroring
    ``aichat.services.threads.ChatThreadService.get_focus``. A revoked,
    deleted or unreadable target withholds the label entirely (``focus`` is
    null) while the stored JSON and ``focus_version`` are never mutated.
    Sync ORM — call from a ``sync_to_async`` context.
    """
    from ai.core.focus import contract as focus_contract
    from ai.core.focus import resolution as focus_resolution
    from aichat.models import ChatThread

    row = (
        ChatThread.objects
        .filter(id=thread_id, owner_id=owner_pk)
        .values_list("conversation_focus", "focus_version")
        .first()
    )
    if row is None:
        return None, 0
    stored, version = row
    version = int(version or 0)
    target = focus_contract.focus_from_stored(stored)
    if target is None:
        return None, version
    from django.contrib.auth import get_user_model

    actor = get_user_model().objects.filter(pk=actor_pk).first()
    if actor is None:
        return None, version
    try:
        current = focus_resolution.resolve_focus_candidate(
            actor, focus_contract.FocusCandidate(kind=target.kind, id=target.id)
        )
    except focus_contract.FocusRejected:
        # Fail closed: withhold the stored label without distinguishing
        # unknown from unauthorized; stored state stays untouched.
        return None, version
    return focus_contract.focus_payload(current), version


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
    the voice control entirely for disabled deployments, disclosing nothing
    beyond capability booleans.
    """
    settings = get_settings()
    _principal()
    from ai.core.voice.experience import capability

    return capability(settings)


@router.post("/sessions", status_code=201)
async def create_voice_session(request: VoiceSessionCreateRequest) -> dict:
    """Create one owned, bounded, visible realtime session."""
    settings = _require_voice_enabled()
    principal = _principal()

    from ai.core.voice.experience import validate_preferences

    try:
        validate_preferences(request.locale, request.voice, request.consent_version)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    from aichat.models import generate_thread_id
    from django.contrib.auth import get_user_model
    from voice.services import realtime

    thread_id = request.thread_id or generate_thread_id()
    if thread_id.startswith("scoped_"):
        # Record-grounded sessions require the external #14 substrate.
        raise HTTPException(status_code=404, detail="VOICE_SESSION_FORBIDDEN")

    def _create():
        owner = get_user_model().objects.get(pk=principal.user_pk)
        focus_state = _thread_focus_state(thread_id, principal.user_pk, principal.user_pk)
        if (
            request.expected_focus_version is not None
            and focus_state[1] != request.expected_focus_version
        ):
            # Confirm the focus candidate (owner focus PUT) before voice
            # begins — a stale stamp must not silently start against a
            # selection the client never saw. Not a scope change.
            raise HTTPException(status_code=409, detail="VOICE_FOCUS_CHANGED")
        return (
            realtime.create_session(
                owner=owner,
                thread_id=thread_id,
                scope_key=principal.scope,
                policy_version=principal.policy_version,
                limits=_limits(settings),
                analysis_scope_version=_thread_scope_version(thread_id, principal.user_pk),
                locale=request.locale,
                voice=request.voice,
                consent_version=request.consent_version,
            ),
            focus_state,
        )

    try:
        session, focus_state = await sync_to_async(_create, thread_sensitive=True)()
    except realtime.VoiceSessionError as exc:
        raise _session_error(exc) from None
    return _session_payload(session, settings, focus_state)


@router.get("/sessions/{session_id}")
async def get_voice_session(session_id: str) -> dict:
    """Owner-safe session status."""
    settings = _require_voice_enabled()
    principal = _principal()
    session = await _owned_session(principal, session_id, settings)
    focus_state = await sync_to_async(_thread_focus_state, thread_sensitive=True)(
        session.thread_id, session.owner_id, principal.user_pk
    )
    return _session_payload(
        session,
        settings,
        focus_state,
        speech_audit=route_speech.session_audit_snapshot(str(session.id)),
    )


@router.delete("/sessions/{session_id}")
async def end_voice_session(session_id: str) -> dict:
    """Idempotently end the session and cancel queued playback."""
    settings = _require_voice_enabled()
    principal = _principal()
    session = await _owned_session(principal, session_id, settings)

    # Known end: pending speech and every optional tracker are invalidated
    # BEFORE any speech await.
    route_speech.invalidate_trackers(str(session.id), "end")
    route_speech.fence(str(session.id))

    from voice.services import realtime

    session = await sync_to_async(
        lambda: realtime.end_session(session=session), thread_sensitive=True
    )()
    if settings.feature_voice_decision_coordinator:
        from ai.core.decisions.coordinator import get_coordinator

        await sync_to_async(get_coordinator().disarm)(
            session.thread_id, "session_ended", set_aside=True
        )
    _turn_locks.pop(str(session.id), None)
    # Speech closes BEFORE the provider channel (close refuses new sends
    # before its awaits and bounds its own waits), then the dispatcher and
    # its per-session trackers/selectors are released.
    await route_speech.end_session_speech(str(session.id))
    route_speech.release_session_state(str(session.id))
    if _provider_channel_closer is not None:
        try:
            await _provider_channel_closer(str(session.id))
        except Exception:
            # The sweeper reconciles channels we could not close cleanly.
            logger.warning("Voice channel close failed", extra={"session": str(session.id)})
    focus_state = await sync_to_async(_thread_focus_state, thread_sensitive=True)(
        session.thread_id, session.owner_id, principal.user_pk
    )
    return _session_payload(session, settings, focus_state)


@router.post("/sessions/{session_id}/cancel")
async def cancel_voice_playback(session_id: str) -> dict:
    """Cancel queued/active playback without deleting the input turn."""
    settings = _require_voice_enabled()
    session = await _owned_session(_principal(), session_id, settings)
    return await _cancel_playback(session, settings, reason="user_canceled")


async def _cancel_playback(session, settings, *, reason: str) -> dict:
    """Known cancellation: fence pending route output BEFORE any await.

    Stops every optional tracker and the turn's not-yet-dispatched output,
    then lets the shared dispatcher reconcile provider ownership (local stop
    is immediate; interrupted is never completed). Cancellation never
    consumes an approval and never re-enters the turn.

    The generation invalidation and the shared dispatcher's local stop land
    BEFORE the utterance-state persistence await: a held persistence write
    must never resume while live output can still speak. The durable bulk
    update keeps the original closure, counts and payload and runs last,
    inside the same ``timing.stage("cancellation")``.
    """
    from voice.models import PlaybackState

    # Fence FIRST: a reply still computing can no longer reach the provider.
    route_speech.fence(str(session.id))
    route_speech.invalidate_trackers(
        str(session.id), "barge_in" if reason == "user_canceled" else "hidden"
    )

    def _cancel() -> int:
        return session.utterances.filter(
            playback_state__in=[PlaybackState.PENDING, PlaybackState.REQUESTED]
        ).update(playback_state=PlaybackState.CANCELED)

    with timing.stage("cancellation"):
        # Local stop BEFORE the persistence await: synchronously invalidate
        # the generation, then the shared dispatcher stops actual local
        # playback (bounded best-effort provider cancel included).
        dispatcher = route_speech.session_dispatcher(session)
        if dispatcher is not None:
            with contextlib.suppress(Exception):
                dispatcher.invalidate(reason)
            with contextlib.suppress(Exception):
                await dispatcher.cancel(reason=reason)
        canceled = await sync_to_async(_cancel, thread_sensitive=True)()
    return {"id": str(session.id), "canceled_utterances": canceled}


@router.post("/sessions/{session_id}/suspend")
async def suspend_voice_session(session_id: str) -> dict:
    """Foreground loss is fail-safe and non-consuming, never an approval.

    Local stop and generation invalidation land BEFORE the EARLIER epoch
    persistence await (and therefore before the cancel bulk inside
    ``_cancel_playback``): a held epoch write must never resume while live
    output can still speak. The epoch closure and the durable cancel bulk
    keep their original order after the stop.
    """
    settings = _require_voice_enabled()
    session = await _owned_session(_principal(), session_id, settings)
    from voice.services.timing import invalidate_epoch

    # Minimal pre-epoch dispatcher stop: fence, invalidate trackers and
    # stop real local playback before the earliest persistence await.
    route_speech.fence(str(session.id))
    route_speech.invalidate_trackers(str(session.id), "hidden")
    dispatcher = route_speech.session_dispatcher(session)
    if dispatcher is not None:
        with contextlib.suppress(Exception):
            dispatcher.invalidate("foreground_lost")
        with contextlib.suppress(Exception):
            await dispatcher.cancel(reason="foreground_lost")
    await sync_to_async(invalidate_epoch)(session)
    await _cancel_playback(session, settings, reason="foreground_lost")
    if settings.feature_voice_decision_coordinator:
        from ai.core.decisions.coordinator import get_coordinator

        await sync_to_async(get_coordinator().disarm)(
            session.thread_id, "foreground_lost", set_aside=True
        )
    return {"id": str(session.id), "suspended": True}


async def _timing_call(session_id, report=None):
    settings = _require_voice_enabled()
    if not getattr(settings, "feature_voice_validation_metrics", False):
        raise HTTPException(status_code=404, detail="VOICE_TIMING_UNAVAILABLE")
    principal = _principal()
    # Resolve untrusted IDs before any reporting query and do not disclose existence.
    await _owned_session(principal, session_id, settings)
    from voice.services import timing as reporting

    kwargs = {
        "owner": principal.user_pk,
        "scope_key": principal.scope,
        "session_id": session_id,
        "limits": _limits(settings),
    }
    try:
        if report is None:
            return await sync_to_async(reporting.begin_epoch)(**kwargs)
        return await sync_to_async(reporting.report)(**kwargs, observation=report)
    except Exception:
        # This independent endpoint never retries or changes the business turn.
        raise HTTPException(status_code=409, detail="VOICE_TIMING_UNAVAILABLE") from None


@router.post("/sessions/{session_id}/timing-epoch")
async def begin_timing_epoch(session_id: str) -> dict:
    """Mint a telemetry-only generation for the current owner/session."""
    return await _timing_call(session_id)


@router.post("/sessions/{session_id}/timing")
async def report_voice_timing(session_id: str, request: timing.VoiceTimingReport) -> dict:
    """Observations are never playback acknowledgments or review completion."""
    return await _timing_call(session_id, request)


@router.post("/sessions/{session_id}/prompts")
async def request_voice_prompt(session_id: str, request: VoicePromptRequest) -> dict:
    """Persist and speak one allow-listed prompt on the caller's own session (A6).

    Makes the client-side transcript hold audible without violating
    persist-before-speak: the text is composed server-side from a fixed
    template, stored as a ``VoiceUtterance`` and spoken via exact TTS. With no
    provider channel the prompt is honestly ``pending`` and the client shows
    the text instead.
    """
    settings = _require_voice_enabled()
    principal = _principal()
    # Route admission (BEFORE any await): captured once and never re-captured
    # after a cancel / end / foreground loss landing across the persistence
    # awaits below -- old callers can never adopt a newer speech generation.
    speech_fence = route_speech.fence_token(session_id)
    session = await _owned_session(principal, session_id, settings)

    from ai.core.trusted_context import resolve_actor_locale
    from ai.core.voice import prompts
    from ai.core.voice.wire import VoiceSpokenPayload
    from voice.models import VoiceUtterance, VoiceUtteranceType
    from voice.services import realtime

    locale = getattr(session, "locale", None) or await sync_to_async(
        resolve_actor_locale, thread_sensitive=True
    )(principal.user_pk)
    if request.kind == "transcript_review":
        if settings.feature_voice_decision_coordinator:
            from ai.core.decisions.coordinator import get_coordinator

            await sync_to_async(get_coordinator().disarm)(session.thread_id, "transcript_review")
        try:
            text = prompts.compose_transcript_review(request.transcript, locale)
        except ValueError:
            raise HTTPException(status_code=422, detail="VOICE_TRANSCRIPT_INCOMPLETE") from None
    elif request.kind == "help":
        from ai.core.voice.help import compose_help

        text = await sync_to_async(compose_help)(principal, session, settings)
    else:
        maybe = prompts.status_prompt(request.status, locale)
        if maybe is None:
            raise HTTPException(status_code=422, detail="VOICE_PROMPT_UNKNOWN")
        text = maybe

    # Only completed answers bind a response id (voice_utterance_answer_binds_
    # response); a prompt is its own row, so re-prompting a revised transcript
    # for the same item simply persists again.
    def _persist():
        if not (request.kind == "status" and request.status == "session_ended"):
            realtime.touch_session(session=session, limits=_limits(settings))
        return realtime.persist_utterance(
            session=session,
            utterance_type=VoiceUtteranceType.PROMPT,
            spoken_summary=text,
            policy_version=prompts.PROMPT_POLICY_VERSION,
        )

    try:
        utterance = await sync_to_async(_persist, thread_sensitive=True)()
    except realtime.VoiceSessionError as exc:
        raise _session_error(exc) from None

    # ONE shared dispatcher owns this prompt's provider send (persist first,
    # exact text/hash; the dispatcher reconciles REQUESTED honestly and its
    # metadata marks this as a prompt -- never a useful answer or a cue).
    playback_audit_state = "unavailable"
    dispatcher = route_speech.session_dispatcher(session)
    if dispatcher is not None and not route_speech.fenced(str(session.id), speech_fence):
        record = await route_speech.speak_persisted(dispatcher, kind="prompt", utterance=utterance)
        playback_state = route_speech.dispatch_state_for(record)
        playback_audit_state = route_speech.dispatch_audit_state_for(record)
    else:
        # Fenced across the persistence await (or no dispatcher): the row
        # persists and the cancel has already reconciled it honestly, but this
        # output never adopts the cancel's newer speech generation. Report the
        # row's CURRENT authoritative state -- never this caller's stale
        # in-memory instance.
        playback_state = await sync_to_async(
            lambda: VoiceUtterance.objects.get(pk=utterance.pk).playback_state,
            thread_sensitive=True,
        )()
    return VoiceSpokenPayload(
        utterance_id=str(utterance.id),
        spoken_summary=utterance.spoken_summary,
        spoken_summary_hash=utterance.spoken_summary_hash,
        playback_state=playback_state,
        playback_audit_state=playback_audit_state,
    ).model_dump(mode="json")


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
    with timing.measurement():
        with timing.stage("route"):
            result = await _submit_voice_turn(session_id, request)
        decision = result.get("pending_decision") or {}
        timing.attributes(
            decision_id=decision.get("decision_id"),
            operation_id=decision.get("operation_id"),
            decision_state=decision.get("state"),
            utterance_id=(result.get("spoken") or {}).get("utterance_id"),
        )
        return result


async def _submit_voice_turn(session_id: str, request: VoiceTurnRequest) -> dict:
    """Business path; timing has no effect on authority or idempotency."""
    settings = _require_voice_enabled()
    principal = _principal()
    # Route admission (BEFORE any await): captured once and never re-captured
    # after a cancel / end / foreground loss that lands across the persistence,
    # context or bind awaits below -- old callers can never adopt the cancel's
    # newer speech generation.
    speech_fence = route_speech.fence_token(session_id)
    session = await _owned_session(principal, session_id, settings)
    if getattr(settings, "feature_voice_decision_coordinator", False):
        from ai.core.decisions.coordinator import get_coordinator
        from ai.core.decisions.store import DecisionStoreUnavailable

        try:
            await sync_to_async(get_coordinator().store.read)(session.thread_id)
        except DecisionStoreUnavailable as exc:
            raise HTTPException(status_code=409, detail="VOICE_DECISION_UNAVAILABLE") from exc

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

    from ai.core.app import _record_rejection, get_turn_service
    from ai.core.pilot_latch import PilotLatchUnavailable, PilotStopped
    from ai.core.quota.admission import AdmissionSaturated
    from ai.core.trusted_context import build_trusted_turn_context, resolve_actor_locale
    from ai.core.turn_service import IdempotencyConflict, TurnAlreadyRunning
    from ai.core.voice import status_phrases
    from aichat.models import TurnModality, TurnState
    from aichat.services.threads import FocusVersionConflict
    from voice.models import VoiceUtteranceType
    from voice.services import realtime

    idempotency_key = final.idempotency_key(str(session.id))
    # S36: per-turn child correlation id, deterministically derived from the
    # session's id plus the turn's idempotency key. Session-granular ids made
    # every turn in a session indistinguishable on the spine; the session id
    # remains recoverable (and is logged on session telemetry) as the parent.
    from ai.core.correlation import voice_turn_correlation

    correlation_id = voice_turn_correlation(session.correlation_id, idempotency_key)
    trusted_context = build_trusted_turn_context(
        principal,
        correlation_id=correlation_id,
        browser_context=None,
        server_route_hints=("/voice/turns",),
        locale=getattr(session, "locale", None)
        or await sync_to_async(resolve_actor_locale, thread_sensitive=True)(principal.user_pk),
    )

    def _touch():
        return realtime.touch_session(session=session, limits=_limits(settings), count_turn=True)

    dispatcher = route_speech.session_dispatcher(session)
    # The decision pipeline's playback probe inspects the same real channel
    # the shared dispatcher owns (read-only probe, never a competing send).
    channel = _provider_channel_factory(session) if _provider_channel_factory else None
    # speech_fence was captured at route admission (before any await): a
    # cancel / end / foreground loss landing while this turn computes kills
    # every later send from THIS turn.

    async def _speak_status(
        utterance_type: str,
        phrase: str,
        *,
        onset_kind: str | None = None,
        required: bool = False,
    ) -> bool:
        """Persist then dispatch one allow-listed status phrase; never raises.

        Field technicians may be unable to look at the screen, so every turn
        outcome should be audible (§7.6). Status speech is best-effort: it can
        never block or fail the turn itself. ``onset_kind`` is derived from
        the application stage (optional waiting only) -- never client input.
        """
        # S33: localize at the single choke point; the allow-list check runs
        # on the RETURNED phrase, so an unlisted translation cannot speak.
        phrase = status_phrases.localized_status_phrase(
            phrase, getattr(trusted_context, "locale", "en")
        )
        if dispatcher is None or phrase not in status_phrases.ALLOWED_STATUS_PHRASES:
            return False
        if route_speech.fenced(str(session.id), speech_fence):
            # Cancel/end already fenced this turn's output; visible text
            # stays and the playback state stays honest (never requested).
            return False
        try:
            utterance = await sync_to_async(
                lambda: realtime.persist_utterance(
                    session=session,
                    utterance_type=utterance_type,
                    spoken_summary=phrase,
                    policy_version=status_phrases.STATUS_PHRASE_POLICY_VERSION,
                ),
                thread_sensitive=True,
            )()
            if route_speech.fenced(str(session.id), speech_fence):
                # Cancel completed across the persistence await: the row is
                # already reconciled honestly and must never dispatch now.
                return False
            record = await route_speech.speak_persisted(
                dispatcher,
                kind="status",
                utterance=utterance,
                onset_kind=onset_kind,
                required=required,
            )
        except Exception:
            logger.debug("Voice status phrase skipped", extra={"session": str(session.id)})
            return False
        return record.state in ("requested", "completed")

    # Receipt/queue lifecycle starts BEFORE the session lock is awaited: the
    # lock wait is measured for real and a queued turn can honestly say so.
    tracker = route_speech.new_progress_tracker(session, request.progress_mode)
    route_speech.register_tracker(str(session.id), tracker)
    tracker.admit()

    async def _progress_cues() -> None:
        """Bounded optional-cue timer; cues never raise or block the turn."""
        while True:
            await asyncio.sleep(route_speech.PROGRESS_POLL_S)
            prompt = tracker.due()
            if prompt is None:
                if tracker.invalidated:
                    return
                continue
            await _speak_status(
                VoiceUtteranceType.INTERIM_STATUS, prompt.phrase, onset_kind="filler"
            )

    cue_task = asyncio.create_task(_progress_cues())

    async def _stop_progress(reason: str) -> None:
        """Stop the optional timer BEFORE the answer becomes ready."""
        tracker.invalidate(reason)
        route_speech.pop_tracker(str(session.id), tracker)
        if not cue_task.done():
            cue_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await cue_task

    # One turn at a time per session: hands-free auto-submit can deliver a
    # new utterance while the previous turn is processing, and the shared
    # provider speech slot must be handed over in order.
    lock = _turn_locks.setdefault(str(session.id), asyncio.Lock())
    if lock.locked():
        # Verified application signal: this request is ACTUALLY waiting
        # behind another turn -- never an invented stage.
        tracker.signal_queued()
    acquired = False
    try:
        # Time the REAL lock wait (receipt -> lock acquired), never a guess.
        with timing.stage("lock_wait"):
            await lock.acquire()
        acquired = True
        # §13.3 pattern 10: the session is bound to one analysis-scope
        # version. A material change (e.g. a text-rail scope edit) refuses
        # further voice turns — no turn executes, no turn is counted — until
        # the user acknowledges by restarting voice, which re-binds.
        current_scope_version = await sync_to_async(
            lambda: _thread_scope_version(session.thread_id, principal.user_pk),
            thread_sensitive=True,
        )()
        if current_scope_version != session.analysis_scope_version:
            # Terminal teardown BEFORE the refusal is spoken: a refused turn
            # releases its own tracker and can never emit an optional cue.
            await _stop_progress("error")
            await _speak_status(
                VoiceUtteranceType.FAILURE_STATUS,
                status_phrases.SCOPE_CHANGED,
                required=True,
            )
            raise HTTPException(status_code=409, detail="VOICE_SCOPE_CHANGED")

        try:
            with timing.stage("admission"):
                await sync_to_async(_touch, thread_sensitive=True)()
            from ai.core.tracing import turn_span

            with turn_span(
                "aimms.voice.process",
                correlation_id=correlation_id,
                session_correlation_id=str(session.correlation_id),
                turn_sequence=getattr(session, "turn_count", None),
            ):
                timing.attributes(
                    correlation_id=correlation_id,
                    session_correlation_id=str(session.correlation_id),
                    turn_sequence=getattr(session, "turn_count", None),
                )
                from ai.core.decisions.pipeline import process_with_playback_probe

                result = await process_with_playback_probe(
                    get_turn_service(),
                    channel,
                    actor=principal,
                    thread_id=session.thread_id,
                    content=final.text,
                    modality=TurnModality.VOICE,
                    trusted_context=trusted_context,
                    modality_metadata={
                        **final.modality_metadata(),
                        "voice_session_id": str(session.pk),
                        "decision_context": request.decision_context,
                        **(
                            {"voice_timing": request.timing.model_dump(exclude_none=True)}
                            if request.timing is not None
                            and getattr(settings, "feature_voice_validation_metrics", False)
                            else {}
                        ),
                    },
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id,
                    expected_focus_version=request.expected_focus_version,
                )
        except realtime.VoiceSessionError as exc:
            await _stop_progress("error")
            raise _session_error(exc) from None
        except (IdempotencyConflict, TurnAlreadyRunning):
            await _stop_progress("error")
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT") from None
        except FocusVersionConflict:
            # The utterance was stamped against a focus selection the client
            # has not seen (someone switched the target meanwhile). Refuse
            # THIS turn; the session and transport stay alive — a focus
            # change is not a scope change.
            await _stop_progress("error")
            raise HTTPException(status_code=409, detail="VOICE_FOCUS_CHANGED") from None
        except AdmissionSaturated as exc:
            # S13: typed backpressure — voice turn submission shares the
            # model-turn admission gate; session signaling stays separate.
            await _stop_progress("error")
            raise HTTPException(
                status_code=503,
                detail="ai_capacity_busy",
                headers={"Retry-After": str(exc.retry_after)},
            ) from None
        except PilotStopped as exc:
            # S15: the pilot is stopped — typed, non-retryable, every rail.
            await _stop_progress("error")
            _record_rejection(exc.code, principal)
            raise HTTPException(status_code=503, detail=exc.code) from None
        except PilotLatchUnavailable as exc:
            await _stop_progress("error")
            _record_rejection(exc.code, principal)
            raise HTTPException(
                status_code=503,
                detail=exc.code,
                headers={"Retry-After": str(exc.retry_after)},
            ) from None
        except ValueError:
            await _stop_progress("error")
            raise HTTPException(status_code=422, detail="VOICE_TRANSCRIPT_INCOMPLETE") from None
        except Exception:
            logger.error("Voice turn failed", extra={"session": str(session.id)})
            await _stop_progress("error")
            await _speak_status(
                VoiceUtteranceType.FAILURE_STATUS, status_phrases.TURN_FAILED, required=True
            )
            raise HTTPException(status_code=500, detail="VOICE_RESPONSE_INCOMPLETE") from None

        await _stop_progress("result_ready")
        spoken: dict[str, Any] | None = None
        presentation_payload = None
        speak_flag = bool((result.canonical_response or {}).get("speak", False))
        # A3: a set-aside status is spoken BEFORE the answer. The provider plays
        # one app response at a time, so it rides in the same utterance as a
        # prefix (answers) or as one combined allow-listed phrase (statuses).
        pre_speech = result.pre_speech_status or None
        if (
            result.response_state == TurnState.COMPLETE
            and result.spoken_summary.strip()
            and speak_flag
        ):
            spoken_text = result.spoken_summary
            if pre_speech:
                prefix = status_phrases.localized_status_phrase(
                    pre_speech, getattr(trusted_context, "locale", "en")
                )
                if prefix in status_phrases.ALLOWED_STATUS_PHRASES:
                    spoken_text = f"{prefix} {result.spoken_summary}"

            def _persist():
                nonlocal presentation_payload
                if (
                    settings.feature_voice_foreground_session
                    and not getattr(result, "pending_decision", None)
                    and not getattr(result, "pending_question", None)
                ):
                    from voice.services import presentation

                    item, first = presentation.create(
                        session=session,
                        turn_id=result.turn_id,
                        text=spoken_text,
                        layout=result.message,
                        safety_boundary=(result.canonical_response or {}).get(
                            "safety_boundary", ""
                        ),
                    )
                    presentation_payload = presentation.payload(item)
                    return first
                return realtime.persist_utterance(
                    session=session,
                    utterance_type=VoiceUtteranceType.COMPLETED_ANSWER,
                    spoken_summary=spoken_text,
                    response_id=result.turn_id,
                    turn_id=result.turn_id,
                )

            try:
                with timing.stage("final_persist"):
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
            deferred_readback = (
                settings.feature_voice_foreground_session
                and (getattr(result, "pending_decision", None) or {}).get("state") == "presented"
                and (getattr(result, "decision_event", None) or {}).get("kind")
                in ("presented", "review")
            )
            if (
                dispatcher is not None
                and not deferred_readback
                and not route_speech.fenced(str(session.id), speech_fence)
            ):
                from ai.core.voice.speech import ExactSpeechViolation

                record = None
                dispatch_output = True

                try:
                    if getattr(result, "decision_event", None) and result.decision_event.get(
                        "kind"
                    ) in ("presented", "review"):
                        from ai.core.decisions.coordinator import get_coordinator

                        coordinator = get_coordinator()
                        current = await sync_to_async(coordinator.store.read)(session.thread_id)
                        expected = result.pending_decision or {}
                        if (
                            current
                            and current.decision_id == expected.get("decision_id")
                            and current.sequence == expected.get("sequence")
                        ):
                            if route_speech.fenced(str(session.id), speech_fence):
                                # Cancel completed across the store read: a
                                # bind now would be stale -- never bind and
                                # never dispatch.
                                dispatch_output = False
                            else:
                                # Decision bind BEFORE dispatch; the sequence and
                                # hash match checks stay exactly as they were.
                                bound = await sync_to_async(coordinator.bind_playback)(
                                    current,
                                    utterance_id=str(utterance.pk),
                                    spoken_text=utterance.spoken_summary,
                                    spoken_hash=utterance.spoken_summary_hash,
                                )
                                if (
                                    bound.utterance_id != str(utterance.pk)
                                    or bound.spoken_summary_hash != utterance.spoken_summary_hash
                                ):
                                    raise ValueError("The decision changed before speech dispatch.")
                        else:
                            raise ValueError("The decision changed before speech dispatch.")
                    if dispatch_output and not route_speech.fenced(str(session.id), speech_fence):
                        # Re-checked immediately before dispatch: a cancel that
                        # completed across the bind awaits kills this send.
                        with turn_span("aimms.voice.tts_request", utterance_id=str(utterance.pk)):
                            record = await route_speech.speak_persisted(
                                dispatcher,
                                kind="answer",
                                utterance=utterance,
                                onset_kind="answer",
                            )
                        timing.mark("ms_tts_request")
                except ExactSpeechViolation as exc:
                    raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT") from exc
                except Exception:
                    logger.warning("Exact TTS dispatch failed", extra={"session": str(session.id)})
                else:
                    if record is not None:
                        spoken["playback_state"] = route_speech.dispatch_state_for(record)
                        spoken["playback_audit_state"] = route_speech.dispatch_audit_state_for(
                            record
                        )
                        logger.info(
                            "Exact TTS dispatched",
                            extra={"session": str(session.id), "utterance": str(utterance.id)},
                        )
        elif result.response_state == TurnState.COMPLETE and (result.message or "").strip():
            # The answer completed but has no schema-valid spoken form. Say so:
            # a silent turn forces the technician to look at the screen.
            await _speak_status(
                VoiceUtteranceType.INTERIM_STATUS,
                status_phrases.combine_status(pre_speech, status_phrases.ANSWER_IN_CHAT),
                required=True,
            )
        elif result.response_state in (TurnState.INCOMPLETE, TurnState.FAILED):
            # Bounded-out or terminal-replayed turns return 200 without raising;
            # an eyes-free user still needs to hear that the turn ended.
            await _speak_status(
                VoiceUtteranceType.FAILURE_STATUS,
                status_phrases.combine_status(pre_speech, status_phrases.ANSWER_INCOMPLETE),
                required=True,
            )
        elif pre_speech:
            # Nothing else to say, but the set-aside must still be audible.
            await _speak_status(VoiceUtteranceType.INTERIM_STATUS, pre_speech, required=True)

        # S43: built through the wire model (see wire.py) so the generated
        # TS contract and this payload can never drift. pending_question
        # keeps S22/S23 semantics: the client renders the same card the
        # drawer shows.
        from ai.core.voice.wire import VoiceTurnResponse

        payload = VoiceTurnResponse(
            session_id=str(session.id),
            thread_id=result.thread_id,
            turn_id=result.turn_id,
            message=result.message,
            workflow_used=result.workflow_used,
            response_state=result.response_state,
            replayed=result.replayed,
            spoken=spoken,
            pending_question=None,
            pending_decision=getattr(result, "pending_decision", None),
            decision_event=getattr(result, "decision_event", None),
            presentation=presentation_payload,
            focus=result.focus,
            focus_version=result.focus_version,
            focus_refresh_required=result.focus_refresh_required,
        ).model_dump(mode="json")
        # The S22 question payload passes through VERBATIM — the question
        # schema owns its shape (the generated VoicePendingQuestion models
        # it with optional fields); round-tripping it through the model
        # would add None-valued keys the historic wire never carried.
        payload["pending_question"] = result.pending_question
        if settings.feature_voice_decision_coordinator:
            from ai.core.decisions.coordinator import get_coordinator

            try:
                current = await sync_to_async(get_coordinator().store.read)(session.thread_id)
                payload["pending_decision"] = current.to_public_dict() if current else None
            except DecisionStoreUnavailable as exc:
                raise HTTPException(status_code=409, detail="VOICE_DECISION_UNAVAILABLE") from exc
        return payload
    finally:
        # Idempotent terminal teardown for every exit -- including an
        # exception escaping the scope check or a cancel landing during the
        # lock wait. Normal paths already stopped their tracker and
        # unregistering is identity-aware.
        with contextlib.suppress(Exception):
            await _stop_progress("error")
        if acquired:
            lock.release()


class PresentationCommandRequest(BaseModel):
    """Exact advisory answer binding, not a business intent."""

    id: str
    source_hash: str = Field(min_length=64, max_length=64)
    index: int = Field(ge=0)
    presentation_command: Literal["next", "repeat", "slower", "short"]


@router.post("/sessions/{session_id}/presentation")
async def presentation_command(session_id: str, request: PresentationCommandRequest) -> dict:
    """Speak a stored page without entering the turn/decision pipeline."""
    settings = _require_voice_enabled()
    if not settings.feature_voice_foreground_session:
        raise HTTPException(status_code=404, detail="VOICE_PRESENTATION_UNAVAILABLE")
    # Route admission (BEFORE any await), never re-captured after a cancel.
    speech_fence = route_speech.fence_token(session_id)
    session = await _owned_session(_principal(), session_id, settings)
    from voice.services import presentation, realtime

    lock = _turn_locks.setdefault(str(session.id), asyncio.Lock())
    async with lock:
        try:
            await sync_to_async(realtime.touch_session)(session=session, limits=_limits(settings))
            item, utterance = await sync_to_async(presentation.command)(
                session=session,
                presentation_id=request.id,
                source_hash=request.source_hash,
                index=request.index,
                action=request.presentation_command,
            )
        except Exception:
            raise HTTPException(status_code=409, detail="VOICE_PRESENTATION_UNAVAILABLE") from None
        # Presentation rides the same single speech owner: exact text/hash
        # and the review rules come from the stored page, and the rate is the
        # finite allow-list passthrough (0.8 slower / 1.0 otherwise). A
        # presentation re-read never classifies a useful-answer onset.
        state = utterance.playback_state
        playback_audit_state = "unavailable"
        dispatcher = route_speech.session_dispatcher(session)
        if dispatcher is not None and not route_speech.fenced(str(session.id), speech_fence):
            try:
                record = await route_speech.speak_persisted(
                    dispatcher,
                    kind="answer",
                    utterance=utterance,
                    rate=0.8 if request.presentation_command == "slower" else 1.0,
                )
            except Exception:
                state = "failed"
            else:
                state = route_speech.dispatch_state_for(record)
                playback_audit_state = route_speech.dispatch_audit_state_for(record)
        return {
            "presentation": presentation.payload(item),
            "spoken": {
                "utterance_id": str(utterance.pk),
                "spoken_summary": utterance.spoken_summary,
                "spoken_summary_hash": utterance.spoken_summary_hash,
                "playback_state": state,
                "playback_audit_state": playback_audit_state,
            },
        }


class VoiceSampleRequest(BaseModel):
    """No arbitrary text, provider or sampling settings."""

    model_config = {"extra": "forbid"}
    locale: str
    voice: str


@router.post("/sample")
async def voice_sample(request: VoiceSampleRequest):
    """Fixed, bounded pre-session sample. Never creates VoiceSession or transcript."""
    settings = _require_voice_enabled()
    principal = _principal()
    from ai.core.voice.experience import voice_pairs
    from ai.core.voice.sample import reserve_sample, synthesize_sample
    from fastapi.responses import Response

    if (request.voice, request.locale) not in voice_pairs():
        raise HTTPException(status_code=422, detail="VOICE_LOCALE_UNSUPPORTED")
    if not settings.feature_voice_foreground_session:
        raise HTTPException(status_code=404, detail="VOICE_SAMPLE_UNAVAILABLE")
    if not await sync_to_async(reserve_sample)(principal.user_pk):
        raise HTTPException(
            status_code=429, detail="VOICE_SAMPLE_LIMIT", headers={"Retry-After": "60"}
        )
    try:
        audio = await synthesize_sample(request.locale, request.voice)
    except Exception:
        raise HTTPException(status_code=503, detail="VOICE_SAMPLE_UNAVAILABLE") from None
    return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})


class WalkthroughRequest(BaseModel):
    """One stateless guided-procedure walkthrough utterance (B7)."""

    work_order_id: int
    utterance: str = ""
    position: int = 0
    session_id: str | None = None
    value: Any = None
    passed: bool | None = None


@router.post("/procedures/walkthrough")
async def procedure_walkthrough(request: WalkthroughRequest) -> dict[str, Any]:
    """Advance a read-only voice step-through of an applied procedure.

    Flag-gated dark; an out-of-scope or missing work order is a 404
    indistinguishable from nonexistence. The reply's speak_text is either
    verbatim snapshot text or a fixed deterministic phrase — never
    generated.
    """
    from ai.core.config import get_settings
    from ai.core.voice.procedure_walkthrough import walkthrough_reply

    if not get_settings().feature_guided_procedures:
        raise HTTPException(status_code=404, detail="Not found")
    principal = _principal()

    def perform():
        from django.contrib.auth import get_user_model

        actor = get_user_model().objects.get(pk=principal.user_pk, is_active=True)
        return walkthrough_reply(
            actor=actor,
            work_order_id=request.work_order_id,
            utterance=request.utterance,
            position=request.position,
        )

    from django.contrib.auth import get_user_model
    from django.core.exceptions import PermissionDenied
    from tasks.models import WorkOrder
    from tasks.scope import ScopeError

    try:
        reply = await sync_to_async(perform, thread_sensitive=True)()
    except (WorkOrder.DoesNotExist, get_user_model().DoesNotExist, ScopeError):
        raise HTTPException(status_code=404, detail="Not found") from None
    except PermissionDenied:
        raise HTTPException(status_code=403, detail="PROCEDURE_PERMISSION_REQUIRED") from None
    except Exception:
        raise HTTPException(status_code=503, detail="PROCEDURE_WALKTHROUGH_UNAVAILABLE") from None
    result = {
        "action": reply.action,
        "position": reply.position,
        "total": reply.total,
        "speak_text": reply.speak_text,
        "done": reply.done,
        "step_key": reply.step_key,
        "completed": reply.completed,
        "error": reply.error,
        "application_id": reply.application_id,
        "step_version": reply.step_version,
        "audio_available": False,
    }
    if request.session_id:
        from uuid import uuid4

        from ai.core.decisions.coordinator import DecisionReply, get_coordinator
        from ai.core.decisions.resolver import WorkOrderIntent
        from ai.core.decisions.routes import speak_reply
        from ai.core.voice.experience import writes_eligible
        from aichat.services.proposals import ProposalError

        settings = _require_voice_enabled()
        session = await _owned_session(principal, request.session_id, settings)
        try:
            coordinator = get_coordinator() if settings.feature_voice_decision_coordinator else None
        except Exception:
            raise HTTPException(status_code=409, detail="VOICE_DECISION_UNAVAILABLE") from None
        decision_reply = DecisionReply(reply.speak_text)
        if coordinator is not None:
            disarmed = await sync_to_async(coordinator.disarm, thread_sensitive=True)(
                session.thread_id, "procedure_navigation"
            )
            decision_reply = DecisionReply(
                reply.speak_text, disarmed.decision, event="procedure_navigation"
            )
        if (
            reply.action == "complete_requested"
            and coordinator is not None
            and settings.feature_voice_procedure_complete
            and writes_eligible(session.locale)
        ):
            try:
                decision_reply = await sync_to_async(coordinator.present, thread_sensitive=True)(
                    WorkOrderIntent(
                        str(request.work_order_id),
                        "",
                        "procedure.complete",
                        {
                            "application_id": reply.application_id,
                            "step_key": reply.step_key,
                            "value": request.value,
                            "passed": request.passed,
                        },
                    ),
                    actor=principal,
                    session_id=str(session.pk),
                    thread_id=session.thread_id,
                    nonce=str(uuid4()),
                    source_content=request.utterance,
                    expected=await sync_to_async(coordinator.store.read)(session.thread_id),
                )
            except ProposalError as exc:
                decision_reply = DecisionReply(str(exc), event="refused")
        decision, spoken = await speak_reply(
            session, decision_reply, coordinator, include_spoken=True
        )
        result.update(
            speak_text=decision_reply.spoken,
            spoken=spoken,
            audio_available=spoken["playback_state"] == "requested",
            pending_decision=decision.to_public_dict() if decision else None,
            decision_event=decision_reply.event_dict(),
        )
    return result


from ai.core.decisions.routes import install_routes as _install_decision_routes  # noqa: E402

_install_decision_routes(router)
