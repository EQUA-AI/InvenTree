"""Route admission + terminal progress regressions (probe-promoted slices).

Real routes, real ORM persistence, real ``SessionSpeechDispatcher`` over the
real ``VoiceLiveChannel``/event gate; only the provider transport (WebSocket)
and the TurnService are simulated -- the disclosed evidence boundary. The
continuation of an EXISTING real ``sync_to_async`` persistence await is
delayed, then a real ``/cancel`` completes before the caller resumes: output
persisted before a completed cancel must never dispatch afterwards, and it
must never adopt the cancel's newer speech generation.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import ai.core.decisions.routes as decisions_routes
import pytest
from ai.core.decisions.coordinator import DecisionReply
from ai.core.tests import test_voice_route_speech_integration as fixtures
from ai.core.voice import route_speech, routes
from fastapi import HTTPException


@pytest.fixture(scope="module", autouse=True)
def _database():
    from ai.core.tests.focus_test_db import ensure_island_tables

    ensure_island_tables()
    yield
    route_speech.clear_session_state()


def _coordinates():
    user = fixtures._user()
    settings = fixtures._settings([user.pk])
    principal = fixtures._principal(user)
    session = fixtures._session_for(principal, settings)
    return principal, settings, session


@pytest.mark.parametrize("path", ["prompt", "required_failure"])
@pytest.mark.parametrize("cancel", [False, True], ids=["unchanged_control", "cancel_after_persist"])
def test_captured_route_output_cannot_speak_after_cancel_at_existing_persistence_await(
    path, cancel
):
    """Output persisted before a completed cancel can never speak after it."""
    principal, settings, session = _coordinates()
    service = fixtures._FailingTurnService()

    async def main(channel, ws, closed):
        ready = asyncio.Event()
        release = asyncio.Event()
        real_sync = routes.sync_to_async
        held = False

        def delayed_continuation(function, *args, **kwargs):
            call = real_sync(function, *args, **kwargs)

            async def invoke(*call_args, **call_kwargs):
                nonlocal held
                result = await call(*call_args, **call_kwargs)
                freevars = set(getattr(getattr(function, "__code__", None), "co_freevars", ()))
                prompt_row = path == "prompt" and function.__name__ == "_persist"
                failure_row = (
                    path == "required_failure"
                    and {"phrase", "utterance_type", "session"} <= freevars
                )
                if not held and (prompt_row or failure_row):
                    held = True
                    ready.set()
                    await release.wait()
                return result

            return invoke

        async def request():
            if path == "prompt":
                return await routes.request_voice_prompt(
                    session["id"],
                    routes.VoicePromptRequest(kind="transcript_review", transcript="Fixture input"),
                )
            with pytest.raises(HTTPException) as error:
                await routes.submit_voice_turn(
                    session["id"],
                    routes.VoiceTurnRequest(
                        transcript="Fixture input", item_id="fixture-item", progress_mode="off"
                    ),
                )
            assert error.value.status_code == 500
            return None

        with patch.object(routes, "sync_to_async", delayed_continuation):
            task = asyncio.create_task(request())
            try:
                await asyncio.wait_for(ready.wait(), 3)
                assert fixtures._creates(ws) == [], "fixture must stop before any create"
                if cancel:
                    result = await routes.cancel_voice_playback(session["id"])
                    assert result["canceled_utterances"] >= 1, (
                        "real cancel must touch the persisted row"
                    )
                release.set()
                await asyncio.wait_for(task, 3)
                creates = fixtures._creates(ws)
                assert len(creates) == (0 if cancel else 1), (
                    "output persisted before a completed cancel must not adopt its newer speech generation"
                )
                if path == "required_failure":
                    assert len(service.calls) == 1, "business turn must never replay"
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    fixtures._run_loop(principal, settings, main, turn_service=service, auto_ack=True)


# -- terminal progress teardown + overlapping same-session trackers --------- #


@pytest.mark.parametrize(
    "scope_conflict", [False, True], ids=["successful_turn_control", "scope_refusal"]
)
def test_completed_route_releases_optional_progress_on_scope_refusal(scope_conflict):
    """A refused or finished turn releases its tracker and never cues again."""
    principal, settings, session = _coordinates()
    clock = fixtures.FakeClock()

    async def main(channel, ws, closed):
        # Simulate only the stored version advancing; the real 409 gate remains.
        current = int(session["analysis_scope_version"])
        with patch.object(
            routes, "_thread_scope_version", return_value=current + int(scope_conflict)
        ):
            if scope_conflict:
                with pytest.raises(HTTPException) as error:
                    await routes.submit_voice_turn(
                        session["id"],
                        routes.VoiceTurnRequest(transcript="Fixture input", item_id="fixture-item"),
                    )
                assert error.value.status_code == 409
                assert error.value.detail == "VOICE_SCOPE_CHANGED"
            else:
                await routes.submit_voice_turn(
                    session["id"],
                    routes.VoiceTurnRequest(transcript="Fixture input", item_id="fixture-item"),
                )
        creates = fixtures._creates(ws)
        assert len(creates) == 1, "one required outcome or useful answer before the timer threshold"
        clock.advance(2.0)
        await asyncio.sleep(0.35)
        assert len(fixtures._creates(ws)) == 1, (
            "a refused/finished turn must not emit an optional still-working cue"
        )
        assert route_speech._trackers.get(str(session["id"])) is None, (
            "finished route must release its own tracker"
        )

    fixtures._run_loop(
        principal,
        settings,
        main,
        turn_service=fixtures._AnswerTurnService(),
        clock=clock,
        auto_ack=True,
    )


class _TrackerDouble:
    def __init__(self):
        self.invalidated_with = []

    def invalidate(self, reason):
        self.invalidated_with.append(reason)


def test_unregistering_one_turns_tracker_keeps_a_queued_sibling_alive():
    """Identity-aware tracker teardown never touches a queued sibling."""
    route_speech.clear_session_state()
    active, queued = _TrackerDouble(), _TrackerDouble()
    route_speech.register_tracker("session-overlap", active)
    route_speech.register_tracker("session-overlap", queued)
    # One turn's terminal teardown never unregisters its sibling's tracker.
    assert route_speech.pop_tracker("session-overlap", active) is active
    assert route_speech.pop_tracker("session-overlap", active) is None
    assert route_speech._trackers.get("session-overlap") is not None, (
        "a queued sibling keeps its tracker"
    )
    # A cancellation still fences every remaining tracker of the session.
    route_speech.invalidate_trackers("session-overlap", "barge_in")
    assert active.invalidated_with == []
    assert queued.invalidated_with == ["barge_in"]
    assert route_speech.pop_tracker("session-overlap", queued) is queued
    assert route_speech._trackers.get("session-overlap") is None, (
        "no orphan registry entry survives"
    )


# -- canceled lock-wait teardown: tracker + optional-cue timer lifetime ---- #


@pytest.mark.parametrize(
    "cancel_waiter", [False, True], ids=["fresh_lock_control", "canceled_lock_wait"]
)
def test_canceled_lock_waiter_releases_its_tracker_before_any_optional_cue(cancel_waiter):
    """A turn canceled while waiting on the session lock cleans up completely."""
    principal, settings, session = _coordinates()
    clock = fixtures.FakeClock()

    async def main(channel, ws, closed):
        lock = routes._turn_locks.setdefault(str(session["id"]), asyncio.Lock())
        await lock.acquire()
        task = asyncio.create_task(
            routes.submit_voice_turn(
                session["id"],
                routes.VoiceTurnRequest(transcript="Fixture input", item_id="fixture-item"),
            )
        )
        try:
            for _ in range(200):
                if route_speech._trackers.get(str(session["id"])):
                    break
                await asyncio.sleep(0.01)
            assert route_speech._trackers.get(str(session["id"])), (
                "fixture must reach a registered real waiter"
            )
            await asyncio.sleep(0.03)
            assert not task.done(), "request must actually be waiting on the held lock"
            if cancel_waiter:
                task.cancel()
                outcome = (await asyncio.gather(task, return_exceptions=True))[0]
                assert isinstance(outcome, asyncio.CancelledError)
            else:
                lock.release()
                await asyncio.wait_for(task, 3)
            clock.advance(2.0)
            await asyncio.sleep(0.35)
            assert len(fixtures._creates(ws)) == (0 if cancel_waiter else 1), (
                "a canceled real lock waiter must not speak an optional queued cue; a fresh waiter must still answer"
            )
            assert route_speech._trackers.get(str(session["id"])) is None, (
                "no terminated waiter tracker may remain"
            )
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if lock.locked():
                lock.release()

    fixtures._run_loop(
        principal,
        settings,
        main,
        turn_service=fixtures._AnswerTurnService(),
        clock=clock,
        auto_ack=True,
    )


# -- truthful canceled readback: prompt + decision readback paths ----------- #


@pytest.mark.parametrize(
    "cancel", [False, True], ids=["fresh_prompt_control", "canceled_prompt_state"]
)
def test_prompt_reply_does_not_return_pending_for_a_row_already_canceled(cancel):
    """A fenced prompt reply reports the row's authoritative playback state."""
    principal, settings, session = _coordinates()

    async def main(channel, ws, closed):
        ready, release = asyncio.Event(), asyncio.Event()
        real_sync = routes.sync_to_async
        held = False

        def delayed_continuation(function, *args, **kwargs):
            call = real_sync(function, *args, **kwargs)

            async def invoke(*call_args, **call_kwargs):
                nonlocal held
                result = await call(*call_args, **call_kwargs)
                if not held and function.__name__ == "_persist":
                    held = True
                    ready.set()
                    await release.wait()
                return result

            return invoke

        with patch.object(routes, "sync_to_async", delayed_continuation):
            task = asyncio.create_task(
                routes.request_voice_prompt(
                    session["id"],
                    routes.VoicePromptRequest(kind="transcript_review", transcript="Fixture input"),
                )
            )
            try:
                await asyncio.wait_for(ready.wait(), 3)
                if cancel:
                    result = await routes.cancel_voice_playback(session["id"])
                    assert result["canceled_utterances"] >= 1
                release.set()
                body = await asyncio.wait_for(task, 3)
                from voice.models import VoiceUtterance

                row_state = await real_sync(
                    lambda: VoiceUtterance.objects.get(pk=body["utterance_id"]).playback_state,
                    thread_sensitive=True,
                )()
                if cancel:
                    assert row_state == "canceled", (
                        "actual cancel must already reconcile the exact persisted row"
                    )
                    assert fixtures._creates(ws) == []
                    assert body["playback_state"] == row_state, (
                        "a fenced reply must not advertise canceled speech as pending"
                    )
                else:
                    assert len(fixtures._creates(ws)) == 1
                    assert body["playback_state"] in ("requested", "completed")
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    fixtures._run_loop(
        principal, settings, main, turn_service=fixtures._AnswerTurnService(), auto_ack=True
    )


@pytest.mark.parametrize(
    "cancel", [False, True], ids=["fresh_readback_control", "canceled_readback_state"]
)
def test_speak_reply_readback_does_not_return_pending_for_a_row_already_canceled(cancel):
    """A fenced decision readback reports the row's authoritative state too."""
    principal, settings, session = _coordinates()

    async def main(channel, ws, closed):
        ready, release = asyncio.Event(), asyncio.Event()
        real_sync = decisions_routes.sync_to_async
        held = False

        def delayed_continuation(function, *args, **kwargs):
            call = real_sync(function, *args, **kwargs)

            async def invoke(*call_args, **call_kwargs):
                nonlocal held
                result = await call(*call_args, **call_kwargs)
                if not held and function.__name__ == "persist_utterance":
                    held = True
                    ready.set()
                    await release.wait()
                return result

            return invoke

        orm_session = await routes._owned_session(principal, session["id"], settings)
        with patch.object(decisions_routes, "sync_to_async", delayed_continuation):
            task = asyncio.create_task(
                decisions_routes.speak_reply(
                    orm_session,
                    DecisionReply(spoken="Fixture readback"),
                    object(),
                    include_spoken=True,
                )
            )
            try:
                await asyncio.wait_for(ready.wait(), 3)
                if cancel:
                    result = await routes.cancel_voice_playback(session["id"])
                    assert result["canceled_utterances"] >= 1
                release.set()
                _, body = await asyncio.wait_for(task, 3)
                from voice.models import VoiceUtterance

                row_state = await real_sync(
                    lambda: VoiceUtterance.objects.get(pk=body["utterance_id"]).playback_state,
                    thread_sensitive=True,
                )()
                if cancel:
                    assert row_state == "canceled", (
                        "actual cancel must reconcile the exact persisted row"
                    )
                    assert fixtures._creates(ws) == []
                    assert body["playback_state"] == row_state, (
                        "a fenced decision readback must not advertise canceled speech as pending"
                    )
                else:
                    assert len(fixtures._creates(ws)) == 1
                    assert body["playback_state"] in ("requested", "completed")
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    fixtures._run_loop(
        principal, settings, main, turn_service=fixtures._AnswerTurnService(), auto_ack=True
    )
