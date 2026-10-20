"""Route-level integration: one shared speech dispatcher + bounded truthful
progress across prompts, turns, presentation and decision readbacks.

These tests exercise the REAL routes with the REAL ``SessionSpeechDispatcher``,
the REAL ``VoiceLiveChannel`` + ``EventGate`` and real persisted utterances --
only the provider transport (the WebSocket) is faked, exactly as the dispatcher
suite does. Protocol under test:

* ONE dispatcher per session is the only sender (no raw competing
  ``response.create``/``response.cancel`` from any route);
* dispatch is REQUESTED -- never heard, never completed -- and only correlated
  provider events reconcile the persisted playback state (malformed, foreign
  or unknown terminal events can never complete an utterance);
* provider metadata is a consumer hint only (``aimms_speech_kind`` always,
  ``aimms_onset_kind`` only for actual optional waiting or a completed-answer
  dispatch), never authority;
* user cancel / end / foreground loss fence previously pending route output
  and every optional tracker, so a late reply never speaks;
* optional progress cues follow the bounded 2 s / 8 s schedule and can never
  interrupt an answer; preference ``off`` suppresses only optional waiting.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import ai.core.tests.test_realtime_session_api as api_fixtures  # noqa: E402
import pytest  # noqa: E402
from ai.core.auth import principal_context  # noqa: E402
from ai.core.tests.test_realtime_session_api import (  # noqa: E402
    _principal,
    _settings,
    _user,
)
from ai.core.tests.test_voice_speech_dispatch import FakeWs, _drain_once  # noqa: E402
from ai.core.tests.test_voice_status_phrases import _session_for  # noqa: E402
from ai.core.turn_service import NormalizedTurnResult  # noqa: E402
from ai.core.voice import route_speech, speech, speech_dispatch, status_phrases  # noqa: E402
from ai.core.voice.gateway import VoiceLiveChannel  # noqa: E402
from ai.core.voice.routes import (  # noqa: E402
    PresentationCommandRequest,
    VoicePromptRequest,
    VoiceTurnRequest,
    cancel_voice_playback,
    end_voice_session,
    presentation_command,
    request_voice_prompt,
    submit_voice_turn,
)
from fastapi import HTTPException  # noqa: E402

ai_app = api_fixtures.ai_app

ANSWER_TEXT = "Two likely causes for the vibration."


@pytest.fixture(scope="module", autouse=True)
def _database():
    # Not call_command("migrate"): the island migration graph is
    # inconsistent (held H07) -- real ORM tables via the established
    # test-only schema bootstrap instead.
    from ai.core.tests.focus_test_db import ensure_island_tables

    ensure_island_tables()
    yield
    speech_dispatch._dispatchers.clear()
    route_speech.clear_session_state()


class FakeClock:
    """Deterministic monotonic clock for the progress schedule tests."""

    def __init__(self, now: float = 100.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class AutoAckWs(FakeWs):
    """Faked provider transport that acknowledges like a real provider.

    Creates are acknowledged and cancels are confirmed; completion is NEVER
    auto-claimed, so a dispatch stays honestly REQUESTED (a send is never
    heard) until a real terminal event arrives.
    """

    def __init__(self):
        super().__init__()
        self._active: str | None = None

    async def send_json(self, payload):
        await super().send_json(payload)
        kind = payload.get("type")
        if kind == "response.create":
            self._active = f"resp-{len(self.sent)}"
            self.push({"type": "response.created", "response": {"id": self._active}})
        elif kind == "response.cancel" and self._active is not None:
            self.push({"type": "response.cancelled", "response": {"id": self._active}})
            self._active = None


class _AnswerTurnService:
    def __init__(self, delay: float = 0.0, turn_id: str = "turn-a"):
        self.delay = delay
        self.turn_id = turn_id
        self.calls: list[dict] = []

    async def process(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        return NormalizedTurnResult(
            thread_id=str(kwargs["thread_id"]),
            turn_id=self.turn_id,
            message="Two likely causes for the vibration.",
            workflow_used="wf1",
            spoken_summary=ANSWER_TEXT,
            canonical_response={"speak": True, "response_state": "complete"},
        )


class _FailingTurnService:
    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls: list[dict] = []

    async def process(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        raise RuntimeError("workflow exploded")


async def _scenario(main, *, clock=None, auto_ack=False):
    """One serving loop: real channel + real event gate, faked transport."""
    channel = VoiceLiveChannel("route-speech-" + uuid.uuid4().hex[:6])
    ws = AutoAckWs() if auto_ack else FakeWs()
    channel._ws = ws
    channel._drain_task = asyncio.create_task(channel._drain())
    closed: list[str] = []

    async def _closer(session_id):  # noqa: RUF029 - injected awaitable callback.
        closed.append(session_id)

    previous_clock = route_speech.TRACKER_CLOCK
    if clock is not None:
        route_speech.TRACKER_CLOCK = clock
    routes_mod = __import__("ai.core.voice.routes", fromlist=["routes"])
    routes_mod.set_provider_channel_factory(lambda session: channel)  # noqa: ARG005
    routes_mod.set_provider_channel_closer(_closer)
    try:
        return await main(channel, ws, closed)
    finally:
        routes_mod.set_provider_channel_factory(None)
        routes_mod.set_provider_channel_closer(None)
        route_speech.TRACKER_CLOCK = previous_clock
        with contextlib.suppress(Exception):
            await channel.close()


def _run_loop(principal, settings, main, *, turn_service=None, clock=None, auto_ack=False):
    stack = contextlib.ExitStack()
    with stack:
        stack.enter_context(patch.object(routes_mod(), "get_settings", return_value=settings))
        stack.enter_context(
            patch(
                "ai.core.trusted_context.build_trusted_turn_context",
                return_value=SimpleNamespace(),
            )
        )
        if turn_service is not None:
            stack.enter_context(patch.object(ai_app, "get_turn_service", return_value=turn_service))
        token = principal_context.set(principal)
        try:
            return asyncio.run(_scenario(main, clock=clock, auto_ack=auto_ack))
        finally:
            principal_context.reset(token)


def routes_mod():
    from ai.core.voice import routes as _routes

    return _routes


def _creates(ws):
    return [p for p in ws.sent if p.get("type") == "response.create"]


def _metadata(payload):
    return payload["response"].get("metadata") or {}


# -- 1. one speech owner across prompt / answer / presentation -------------- #


def test_prompt_answer_and_presentation_share_one_dispatcher():
    user = _user()
    settings = _settings([user.pk], FEATURE_VOICE_FOREGROUND_SESSION=True)
    principal = _principal(user)
    created = _session_for(principal, settings)
    service = _AnswerTurnService()

    async def main(channel, ws, closed):
        prompt = await request_voice_prompt(
            created["id"],
            VoicePromptRequest(kind="transcript_review", transcript="Pump is vibrating"),
        )
        result = await submit_voice_turn(
            created["id"], VoiceTurnRequest(transcript="Pump is vibrating.", item_id="item-1")
        )
        presentation = result.get("presentation") or {}
        assert presentation.get("id"), "foreground turn must create a presentation page"
        repeat = await presentation_command(
            created["id"],
            PresentationCommandRequest(
                id=presentation["id"],
                source_hash=presentation["source_hash"],
                index=0,
                presentation_command="repeat",
            ),
        )
        dispatcher = speech_dispatch._dispatchers.get(str(created["id"]))
        assert dispatcher is not None, "the route must register the shared dispatcher"
        # The listener on the real channel is the dispatcher's, installed once.
        bound = channel.lifecycle_listener
        assert getattr(bound, "__self__", None) is dispatcher
        assert getattr(bound, "__func__", None) is type(dispatcher).on_provider_event
        # Every provider create went through the single dispatcher (bounded
        # opaque event ids), never a raw competing response.create.
        creates = _creates(ws)
        assert len(creates) == 3  # prompt, answer, presentation page
        event_ids = [p.get("event_id") for p in creates]
        assert all(eid and str(eid).startswith("app-") for eid in event_ids)
        assert len(set(event_ids)) == 3
        # Every speech replacement is exactly one bounded cancel then one
        # create -- never a competing raw send.
        assert [p.get("type") for p in ws.sent] == [
            "response.create",
            "response.cancel",
            "response.create",
            "response.cancel",
            "response.create",
        ]
        # Dispatch is REQUESTED -- never heard, never completed.
        assert prompt["playback_state"] == "requested"
        assert result["spoken"]["playback_state"] == "requested"
        assert repeat["spoken"]["playback_state"] == "requested"
        return prompt, result, repeat

    _run_loop(principal, settings, main, turn_service=service, auto_ack=True)


# -- 2. metadata: dispatch class vs permitted onset classification ----------- #


def test_metadata_class_and_onset_are_strict_per_dispatch_class():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)
    allowed_keys = {
        "aimms_utterance_id",
        "aimms_spoken_hash",
        "aimms_speech_kind",
        "aimms_onset_kind",
        "aimms_dispatch_generation",
    }

    async def main(channel, ws, closed):
        await request_voice_prompt(
            created["id"],
            VoicePromptRequest(kind="transcript_review", transcript="Pump is vibrating"),
        )
        await submit_voice_turn(
            created["id"], VoiceTurnRequest(transcript="Pump is vibrating.", item_id="item-1")
        )
        creates = _creates(ws)
        assert len(creates) == 2
        prompt_meta = _metadata(creates[0])
        answer_meta = _metadata(creates[1])
        # Dispatch class is always present and derived from the application
        # stage -- never a client field.
        assert prompt_meta["aimms_speech_kind"] == "prompt"
        assert answer_meta["aimms_speech_kind"] == "answer"
        # Onset classification ONLY for a completed-answer dispatch here;
        # prompts (help/clarification/readback) never claim a useful answer.
        assert "aimms_onset_kind" not in prompt_meta
        assert answer_meta["aimms_onset_kind"] == "answer"
        # Generation is a bounded string, content-free.
        for meta in (prompt_meta, answer_meta):
            generation = meta["aimms_dispatch_generation"]
            assert isinstance(generation, str) and generation.isdigit()
            assert len(generation) <= 16
        assert set(prompt_meta) <= allowed_keys
        assert set(answer_meta) <= allowed_keys
        # Persisted binding remains the gate: exact id/hash from the row.
        assert uuid.UUID(answer_meta["aimms_utterance_id"])
        assert len(answer_meta["aimms_spoken_hash"]) == 64

    _run_loop(principal, settings, main, turn_service=_AnswerTurnService(), auto_ack=True)


def test_optional_cue_is_filler_and_required_failure_has_no_onset():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)
    clock = FakeClock()

    async def main(channel, ws, closed):
        task = asyncio.create_task(
            submit_voice_turn(
                created["id"],
                VoiceTurnRequest(
                    transcript="Pump is vibrating.", item_id="item-1", progress_mode="brief"
                ),
            )
        )
        # Optional waiting cue fires at the 2 s policy threshold (filler).
        await asyncio.sleep(0.05)
        clock.advance(2.0)
        await asyncio.sleep(0.2)
        with pytest.raises(HTTPException) as excinfo:
            await task
        assert excinfo.value.status_code == 500
        creates = _creates(ws)
        assert len(creates) == 2, "one optional cue plus the required failure outcome"
        cue_meta = _metadata(creates[0])
        failure_meta = _metadata(creates[1])
        assert cue_meta["aimms_speech_kind"] == "status"
        assert cue_meta["aimms_onset_kind"] == "filler"
        # Required outcomes never classify an onset: no client can call a
        # failure phrase a useful answer or an optional cue.
        assert failure_meta["aimms_speech_kind"] == "status"
        assert "aimms_onset_kind" not in failure_meta
        cue_text = creates[0]["response"]["pre_generated_assistant_message"]["content"][0]["text"]
        failure_text = creates[1]["response"]["pre_generated_assistant_message"]["content"][0][
            "text"
        ]
        assert cue_text in status_phrases.ALLOWED_STATUS_PHRASES
        assert failure_text in status_phrases.ALLOWED_STATUS_PHRASES

    _run_loop(
        principal,
        settings,
        main,
        turn_service=_FailingTurnService(delay=0.6),
        clock=clock,
        auto_ack=True,
    )


def test_required_outcomes_speak_without_onset_even_when_the_turn_fails():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)

    async def main(channel, ws, closed):
        with pytest.raises(HTTPException) as excinfo:
            await submit_voice_turn(
                created["id"],
                VoiceTurnRequest(
                    transcript="Pump is vibrating.", item_id="item-1", progress_mode="off"
                ),
            )
        assert excinfo.value.status_code == 500
        creates = _creates(ws)
        assert len(creates) == 1  # required failure outcome only
        meta = _metadata(creates[0])
        assert meta["aimms_speech_kind"] == "status"
        assert "aimms_onset_kind" not in meta
        text = creates[0]["response"]["pre_generated_assistant_message"]["content"][0]["text"]
        assert text in status_phrases.ALLOWED_STATUS_PHRASES

    _run_loop(principal, settings, main, turn_service=_FailingTurnService())


def test_off_suppresses_optional_cues_but_still_speaks_the_answer():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)
    clock = FakeClock()

    async def main(channel, ws, closed):
        task = asyncio.create_task(
            submit_voice_turn(
                created["id"],
                VoiceTurnRequest(
                    transcript="Pump is vibrating.", item_id="item-1", progress_mode="off"
                ),
            )
        )
        await asyncio.sleep(0.05)
        clock.advance(20.0)  # far past both optional thresholds
        await asyncio.sleep(0.15)
        result = await task
        creates = _creates(ws)
        assert len(creates) == 1, "preference off suppresses every optional cue"
        assert _metadata(creates[0])["aimms_onset_kind"] == "answer"
        assert result["spoken"]["playback_state"] == "requested"

    _run_loop(
        principal,
        settings,
        main,
        turn_service=_AnswerTurnService(delay=0.3),
        clock=clock,
        auto_ack=True,
    )


# -- 3. cancel / end fence pending route output and optional trackers ------- #


def test_cancel_fences_the_late_reply_and_every_optional_tracker():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)
    clock = FakeClock()

    async def main(channel, ws, closed):
        task = asyncio.create_task(
            submit_voice_turn(
                created["id"],
                VoiceTurnRequest(transcript="Pump is vibrating.", item_id="item-1"),
            )
        )
        await asyncio.sleep(0.05)
        canceled = await cancel_voice_playback(created["id"])
        assert canceled["id"] == str(created["id"])
        sent_at_cancel = len(ws.sent)
        # The turn keeps running (business work is once, never re-invoked);
        # its late output must never reach the provider.
        clock.advance(30.0)
        await task
        await asyncio.sleep(0.15)
        assert len(ws.sent) == sent_at_cancel, "the fenced reply never sends"
        assert not _creates(ws), "no cue and no answer after the cancel fence"

    _run_loop(
        principal,
        settings,
        main,
        turn_service=_AnswerTurnService(delay=0.3),
        clock=clock,
        auto_ack=True,
    )


def test_end_closes_speech_before_the_channel_and_late_reply_never_speaks():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)

    async def main(channel, ws, closed):
        task = asyncio.create_task(
            submit_voice_turn(
                created["id"],
                VoiceTurnRequest(
                    transcript="Pump is vibrating.", item_id="item-1", progress_mode="off"
                ),
            )
        )
        await asyncio.sleep(0.05)
        await end_voice_session(created["id"])
        # Speech closed BEFORE the provider channel closer ran.
        assert closed == [str(created["id"])]
        dispatcher = speech_dispatch._dispatchers.get(str(created["id"]))
        assert dispatcher is None, "the session dispatcher is released on end"
        sent_at_end = len(ws.sent)
        await task
        await asyncio.sleep(0.15)
        assert len(ws.sent) == sent_at_end, "a late reply never speaks after end"

    _run_loop(principal, settings, main, turn_service=_AnswerTurnService(delay=0.3))


# -- 4. provider events alone decide persisted playback truth --------------- #


def test_malformed_foreign_and_unknown_terminals_never_complete_an_utterance():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)

    async def main(channel, ws, closed):
        from voice.models import PlaybackState, VoiceUtterance

        prompt = await request_voice_prompt(
            created["id"],
            VoicePromptRequest(kind="transcript_review", transcript="Pump is vibrating"),
        )
        # A sent dispatch is REQUESTED -- never heard, never completed.
        assert prompt["playback_state"] == "requested"

        def _state():
            return VoiceUtterance.objects.get(id=prompt["utterance_id"]).playback_state

        def _check_state():
            from asgiref.sync import sync_to_async

            return sync_to_async(_state, thread_sensitive=True)()

        # Malformed acknowledgement binds nothing.
        ws.push({"type": "response.created", "response": {}})
        await _drain_once()
        assert await _check_state() == PlaybackState.REQUESTED
        # The real acknowledgement binds the owner.
        ws.push({"type": "response.created", "response": {"id": "resp-1"}})
        await _drain_once()
        assert await _check_state() == PlaybackState.REQUESTED
        # A foreign terminal never completes someone else's utterance.
        ws.push({"type": "response.done", "response": {"id": "resp-other", "status": "completed"}})
        await _drain_once()
        assert await _check_state() == PlaybackState.REQUESTED
        # An unknown terminal status is an honest failure -- never COMPLETED.
        ws.push({"type": "response.done", "response": {"id": "resp-1", "status": "weird"}})
        await _drain_once()
        assert await _check_state() == PlaybackState.FAILED

    _run_loop(principal, settings, main)


def test_explicit_completed_terminal_is_the_only_completion():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)

    async def main(channel, ws, closed):
        from asgiref.sync import sync_to_async
        from voice.models import PlaybackState, VoiceUtterance

        prompt = await request_voice_prompt(
            created["id"],
            VoicePromptRequest(kind="transcript_review", transcript="Pump is vibrating"),
        )
        ws.push({"type": "response.created", "response": {"id": "resp-1"}})
        await _drain_once()
        ws.push({"type": "response.done", "response": {"id": "resp-1", "status": "completed"}})
        await _drain_once()

        def _state():
            return VoiceUtterance.objects.get(id=prompt["utterance_id"]).playback_state

        assert await sync_to_async(_state, thread_sensitive=True)() == PlaybackState.DONE

    _run_loop(principal, settings, main)


# -- 5. presentation rate is a strict allow-list passthrough ---------------- #


def test_presentation_rate_is_strict_and_presentation_never_claims_onset():
    user = _user()
    settings = _settings([user.pk], FEATURE_VOICE_FOREGROUND_SESSION=True)
    principal = _principal(user)
    created = _session_for(principal, settings)

    async def main(channel, ws, closed):
        result = await submit_voice_turn(
            created["id"], VoiceTurnRequest(transcript="Pump is vibrating.", item_id="item-1")
        )
        presentation = result["presentation"]
        slower = await presentation_command(
            created["id"],
            PresentationCommandRequest(
                id=presentation["id"],
                source_hash=presentation["source_hash"],
                index=0,
                presentation_command="slower",
            ),
        )
        repeat = await presentation_command(
            created["id"],
            PresentationCommandRequest(
                id=presentation["id"],
                source_hash=presentation["source_hash"],
                index=0,
                presentation_command="repeat",
            ),
        )
        assert slower["spoken"]["playback_state"] in ("requested", "canceled")
        assert repeat["spoken"]["playback_state"] in ("requested", "canceled")
        creates = _creates(ws)
        assert len(creates) == 3
        assert _metadata(creates[1]).get("aimms_speech_kind") == "answer"
        assert _metadata(creates[2]).get("aimms_speech_kind") == "answer"
        # A presentation re-read is neither optional waiting nor a fresh
        # completed-answer dispatch: it never classifies an onset.
        assert "aimms_onset_kind" not in _metadata(creates[1])
        assert "aimms_onset_kind" not in _metadata(creates[2])
        rate_updates = [
            (offset, payload["session"]["voice"]["rate"])
            for offset, payload in enumerate(ws.sent)
            if payload.get("type") == "session.update"
        ]
        assert [rate for _, rate in rate_updates] == ["0.8", "1.0"]
        create_offsets = [
            offset
            for offset, payload in enumerate(ws.sent)
            if payload.get("type") == "response.create"
        ]
        assert (
            create_offsets[0]
            < rate_updates[0][0]
            < create_offsets[1]
            < rate_updates[1][0]
            < create_offsets[2]
        )
        for payload in creates:
            assert "_aimms_rate" not in payload
            assert "_aimms_rate" not in payload["response"]

    _run_loop(principal, settings, main, turn_service=_AnswerTurnService(), auto_ack=True)


def test_rate_and_onset_parameters_are_strictly_validated():
    text = "Two likely causes."
    good = speech.spoken_summary_hash(text)
    payload = speech.build_exact_tts_payload(
        persisted_text=text,
        persisted_hash=good,
        utterance_id="22222222-2222-2222-2222-222222222222",
        speech_kind="answer",
        onset_kind="answer",
        dispatch_generation="3",
        rate=0.8,
    )
    meta = payload["response"]["metadata"]
    assert meta["aimms_speech_kind"] == "answer"
    assert meta["aimms_onset_kind"] == "answer"
    assert meta["aimms_dispatch_generation"] == "3"
    assert payload["response"]["_aimms_rate"] == 0.8  # noqa: RUF069 - exact wire fixture.
    with pytest.raises(ValueError):
        speech.build_exact_tts_payload(persisted_text=text, persisted_hash=good, rate=1.5)
    with pytest.raises(ValueError):
        speech.build_exact_tts_payload(
            persisted_text=text, persisted_hash=good, onset_kind="useful"
        )
    with pytest.raises(ValueError):
        speech.build_exact_tts_payload(
            persisted_text=text, persisted_hash=good, dispatch_generation="x" * 40
        )


# -- 6. bounded truthful progress: 2 s / 8 s, max two, never over an answer #


def test_brief_progress_cues_follow_the_bounded_schedule():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)
    clock = FakeClock()

    async def main(channel, ws, closed):
        task = asyncio.create_task(
            submit_voice_turn(
                created["id"],
                VoiceTurnRequest(
                    transcript="Pump is vibrating.", item_id="item-1", progress_mode="brief"
                ),
            )
        )
        await asyncio.sleep(0.05)
        # Before the first threshold: nothing optional may be spoken.
        clock.advance(1.0)
        await asyncio.sleep(0.1)
        assert _creates(ws) == []
        # At the first threshold: exactly one optional cue.
        clock.advance(1.0)
        await asyncio.sleep(0.4)
        assert len(_creates(ws)) == 1
        assert _metadata(_creates(ws)[0])["aimms_onset_kind"] == "filler"
        # At the second threshold (8 s from admission, >=3 s gap): one more.
        clock.advance(6.0)
        await asyncio.sleep(0.4)
        assert len(_creates(ws)) == 2
        # A third cue is never due; the answer comes next and is never
        # interrupted by optional speech.
        clock.advance(30.0)
        await asyncio.sleep(0.4)
        result = await task
        creates = _creates(ws)
        assert len(creates) == 3, "two optional cues maximum, then the answer"
        assert _metadata(creates[2])["aimms_onset_kind"] == "answer"
        assert result["spoken"]["playback_state"] == "requested"

    _run_loop(
        principal,
        settings,
        main,
        turn_service=_AnswerTurnService(delay=1.6),
        clock=clock,
        auto_ack=True,
    )


def test_progress_preference_stays_outside_the_idempotency_input():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)
    service = _AnswerTurnService()

    async def main(channel, ws, closed):
        await submit_voice_turn(
            created["id"],
            VoiceTurnRequest(
                transcript="Pump is vibrating.", item_id="item-1", progress_mode="off"
            ),
        )
        await submit_voice_turn(
            created["id"],
            VoiceTurnRequest(
                transcript="Pump is vibrating.", item_id="item-1", progress_mode="guided"
            ),
        )
        assert len(service.calls) == 2
        first, second = service.calls
        assert first["idempotency_key"] == second["idempotency_key"]
        assert "progress_mode" not in first["modality_metadata"]
        assert "progress_mode" not in second["modality_metadata"]

    _run_loop(principal, settings, main, turn_service=service, auto_ack=True)
