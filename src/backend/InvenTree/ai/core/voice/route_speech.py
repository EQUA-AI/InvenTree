"""Shared per-session speech adapter for the voice route integration.

One :class:`~ai.core.voice.speech_dispatch.SessionSpeechDispatcher` per voice
session owns EVERY provider speech send -- prompt, status, answer,
presentation and decision readback alike. This module is the single seam the
routes use to obtain it:

* the dispatcher is created once per session against the session's real
  provider channel and never replaced by a later call while it is healthy
  (a dispatcher bound to a different, dead event loop is not healthy for the
  current serving loop and is re-adopted exactly once);
* exactly one provider lifecycle listener is installed on the channel and it
  is the dispatcher's own ``on_provider_event``;
* the dispatcher's playback callback reconciles the PERSISTED playback state
  (``voice.services.realtime.reconcile_playback``); the callback is bounded
  by the dispatcher and a failed audit write is dropped -- never retried,
  never a replayed business turn.

It also owns the route-level safety fences that the dispatcher cannot see:
a user cancel / foreground loss / session end bumps a per-session fence so
route output that was pending BEFORE the cancellation can never reach the
provider afterwards, and every optional progress tracker is invalidated.

Content-free telemetry only: nothing here stores transcripts, audio or any
business content, and provider metadata is a consumer hint (never authority).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import typing

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

#: Deterministic progress-schedule seam: tests substitute a fake monotonic
#: clock before a turn is admitted. Production uses ``time.monotonic``.
TRACKER_CLOCK = time.monotonic

#: Poll cadence for the bounded optional-cue timer. The SCHEDULE thresholds
#: live in ``ai.core.voice.progress`` (2 s / 8 s / >=3 s gap, max 2); this is
#: only how often the timer looks at that policy.
PROGRESS_POLL_S = 0.1

#: Per-session live progress trackers (one per turn, re-registered per turn)
#: so cancellation endpoints can fence optional speech before any await.
#: Overlapping queued turns of ONE session coexist: each turn unregisters only
#: its own tracker, and a cancellation fences every tracker of the session.
_trackers: dict[str, list[typing.Any]] = {}

#: Per-session phrase selectors: rotation must persist ACROSS turns of one
#: session (a fresh selector per tracker would repeat phrases every turn).
_selectors: dict[str, typing.Any] = {}

#: Route output fences: token bumped by cancel/end/foreground loss. A turn
#: captures the token at admission; any later speech from that turn is dead.
_fences: dict[str, int] = {}


# -- fence (route output ownership beyond the dispatcher) ------------------- #


def fence(session_id: str) -> int:
    """Invalidate every not-yet-dispatched route output for this session."""
    key = str(session_id)
    value = _fences.get(key, 0) + 1
    _fences[key] = value
    return value


def fence_token(session_id: str) -> int:
    """The fence token a turn must capture at admission."""
    return _fences.get(str(session_id), 0)


def fenced(session_id: str, token: int) -> bool:
    """Whether this turn's output was fenced after ``token`` was captured."""
    return _fences.get(str(session_id), 0) != token


# -- one dispatcher per session -------------------------------------------- #


def _channel_for(session) -> typing.Any:
    from ai.core.voice import routes

    factory = routes._provider_channel_factory
    return factory(session) if factory is not None else None


def _reconcile_playback(session):
    """Bounded, explicitly acknowledged playback audit (never a replay)."""

    async def _apply(utterance_id: str, state: str) -> str:
        from voice.services import realtime

        def _write():
            row = realtime.reconcile_playback(
                session=session, utterance_id=utterance_id, state=state
            )
            if (
                row is not None
                and str(row.session_id) == str(session.pk)
                and row.playback_state == state
            ):
                return "confirmed"
            return "uncertain"

        try:
            return await sync_to_async(_write, thread_sensitive=True)()
        except Exception:
            logger.warning(
                "Playback reconciliation dropped", extra={"utterance": str(utterance_id)}
            )
            return "uncertain"

    return _apply


def _bind_listener(channel, dispatcher) -> None:
    """Install THE lifecycle listener on the real channel (once per owner)."""
    set_listener = getattr(channel, "set_lifecycle_listener", None)
    if set_listener is None:
        return
    if getattr(channel, "_aimms_lifecycle_owner", None) is dispatcher:
        return
    set_listener(dispatcher.on_provider_event)
    with contextlib.suppress(Exception):
        channel._aimms_lifecycle_owner = dispatcher


def session_dispatcher(session, *, channel: typing.Any = None):
    """Return THE shared dispatcher for this session (or ``None`` w/o channel).

    A healthy instance is never replaced by a later call. The only re-adoption
    is a dispatcher bound to a different (dead) event loop: its asyncio
    primitives cannot run on the current serving loop, so a fresh dispatcher
    takes over -- impossible in a single ASGI process, which serves every
    request of a session in one loop.
    """
    from ai.core.voice import speech_dispatch

    if channel is None:
        channel = _channel_for(session)
    if channel is None:
        return None
    key = str(session.id)
    loop = asyncio.get_running_loop()
    existing = speech_dispatch._dispatchers.get(key)
    if existing is not None and getattr(existing, "_aimms_loop", None) not in (None, loop):
        speech_dispatch.release_dispatcher(key)
        existing = None
    dispatcher = speech_dispatch.dispatcher_for_session(
        key,
        channel,
        on_playback=_reconcile_playback(session),
    )
    if getattr(dispatcher, "_aimms_loop", None) is None:
        with contextlib.suppress(Exception):
            dispatcher._aimms_loop = loop
    _bind_listener(channel, dispatcher)
    return dispatcher


def dispatch_state_for(record) -> str:
    """Honest persisted-playback state for one dispatch record."""
    from ai.core.voice import speech_dispatch

    return speech_dispatch.PLAYBACK_STATE_FOR.get(record.state, "pending")


def dispatch_audit_state_for(record) -> str:
    """Content-free audit truth, never a provider playback state."""
    value = getattr(record, "playback_audit_state", "unavailable")
    if isinstance(value, str) and value in ("unavailable", "confirmed", "uncertain"):
        return value
    return "uncertain"


def session_audit_snapshot(session_id: str) -> dict | None:
    """Read retained same-loop audit metadata without creating a dispatcher."""
    from ai.core.voice import speech_dispatch

    dispatcher = speech_dispatch._dispatchers.get(str(session_id))
    if dispatcher is None:
        return None
    if getattr(dispatcher, "_aimms_loop", None) not in (None, asyncio.get_running_loop()):
        return None
    snapshot = dispatcher.snapshot()
    return snapshot if snapshot.get("utterance_id") else None


async def speak_persisted(
    dispatcher,
    *,
    kind: str,
    utterance,
    onset_kind: str | None = None,
    rate: float | None = None,
    required: bool = False,
):
    """Dispatch one EXACT persisted utterance through the shared dispatcher.

    ``kind`` and ``onset_kind`` are derived from the persisted row and the
    application stage by the caller -- never from client input. ``onset_kind``
    is ``"filler"`` only for actual optional waiting and ``"answer"`` only for
    a completed-answer dispatch; it is absent for required status, help,
    clarification and readback speech.
    """
    return await dispatcher.speak(
        kind=kind,
        text=utterance.spoken_summary,
        spoken_hash=utterance.spoken_summary_hash,
        utterance_id=str(utterance.id),
        onset_kind=onset_kind,
        rate=rate,
        required=required,
    )


async def end_session_speech(session_id: str) -> None:
    """Close the session's speech BEFORE the provider channel is torn down.

    ``close`` refuses new sends before its awaits and bounds its own waits, so
    ending can never reintroduce a fresh send; the dispatcher is then
    released. Idempotent.
    """
    from ai.core.voice import speech_dispatch

    key = str(session_id)
    try:
        dispatcher = speech_dispatch.dispatcher_for_session(key)
    except speech_dispatch.SpeechDispatchError:
        return
    if getattr(dispatcher, "_aimms_loop", None) not in (None, asyncio.get_running_loop()):
        # Dead loop: its bounded close cannot run here; dropping the
        # instance is the honest release (the channel close reconciles the
        # provider side).
        speech_dispatch.release_dispatcher(key)
        return
    try:
        await dispatcher.close("session_ended")
    except Exception:
        logger.warning("Voice speech close failed", extra={"session": key})
    finally:
        speech_dispatch.release_dispatcher(key)


# -- progress trackers (one per turn, fenceable from the endpoints) -------- #


def new_progress_tracker(session, preference: str):
    """One tracker per turn, sharing the session's phrase-rotation selector."""
    from ai.core.voice import progress, status_phrases

    key = str(session.id)
    selector = _selectors.get(key)
    if selector is None:
        selector = status_phrases.StatusPhraseSelector(key)
        _selectors[key] = selector
    return progress.ProgressTracker(
        session_key=key,
        locale=getattr(session, "locale", None),
        preference=preference,
        clock=TRACKER_CLOCK,
        selector=selector,
    )


def register_tracker(session_id: str, tracker) -> None:
    """Register one turn's tracker; overlapping queued turns coexist."""
    _trackers.setdefault(str(session_id), []).append(tracker)


def pop_tracker(session_id: str, tracker=None):
    """Unregister ONE turn's tracker -- never another still-active turn's."""
    key = str(session_id)
    registered = _trackers.get(key)
    if not registered:
        return None
    if tracker is None:
        removed = registered.pop()
    else:
        for index, candidate in enumerate(registered):
            if candidate is tracker:
                removed = registered.pop(index)
                break
        else:
            return None
    if not registered:
        _trackers.pop(key, None)
    return removed


def invalidate_trackers(session_id: str, reason: str) -> None:
    """Stop every optional prompt for this session (before any await)."""
    for tracker in list(_trackers.get(str(session_id), ())):
        with contextlib.suppress(ValueError):
            tracker.invalidate(reason)


def release_session_state(session_id: str) -> None:
    """Release trackers/selectors for an ended session (fence is kept)."""
    _trackers.pop(str(session_id), None)
    _selectors.pop(str(session_id), None)


def clear_session_state() -> None:
    """Test/teardown hygiene only -- never called on a live session."""
    _trackers.clear()
    _selectors.clear()
    _fences.clear()
