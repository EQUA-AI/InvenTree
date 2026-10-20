"""Package F: one exact-speech dispatch coordinator per voice session.

These tests exercise the REAL ``SessionSpeechDispatcher`` against the REAL
``VoiceLiveChannel`` event gate with a faked WebSocket and faked clocks --
no provider, no network, no database. The seams under test:

* pending/ack/cancellation event correlation (client event id -> provider
  response id -> persisted utterance id, kind and generation);
* truthful lifecycle mapping (send means REQUESTED, never heard; terminal
  states are REQUESTED/COMPLETED/CANCELED/FAILED);
* final-answer priority over optional status, bounded replacement, and
  fail-closed behaviour when a bounded wait expires;
* cancellation landing before the WebSocket send side effect and at it,
  plus allowance release/reconciliation around the side effect;
* no multi-pending FIFO guessing: one outstanding create with a known owner.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import aiohttp
import pytest
from ai.core.voice import speech_dispatch, timing
from ai.core.voice.gateway import VoiceLiveChannel
from ai.core.voice.provider import EventGate
from ai.core.voice.speech import spoken_summary_hash

STATUS_TEXT = "Let me check that."
ANSWER_TEXT = "Two likely causes."
PROMPT_TEXT = "The answer is ready in the chat."

STATUS_UID = "11111111-1111-1111-1111-111111111111"
ANSWER_UID = "22222222-2222-2222-2222-222222222222"
PROMPT_UID = "33333333-3333-3333-3333-333333333333"


def _payload(text, utterance_id):
    return {"text": text, "spoken_hash": spoken_summary_hash(text), "utterance_id": utterance_id}


class FakeWs:
    """Queue-driven aiohttp-shaped WebSocket double (events arrive on push)."""

    closed = False

    def __init__(self, fail_send=None):
        self.sent = []
        self.fail_send = fail_send
        self._queue: asyncio.Queue = asyncio.Queue()

    def push(self, event):
        self._queue.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.TEXT, json=lambda: event))

    async def send_json(self, payload):
        self.sent.append(payload)
        if self.fail_send is not None:
            raise self.fail_send

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self):
        self.closed = True

    def creates(self):
        return [p for p in self.sent if p.get("type") == "response.create"]

    def types(self):
        return [p.get("type") for p in self.sent]


class RecordingPlayback:
    def __init__(self):
        self.calls = []

    def __call__(self, utterance_id, state):
        self.calls.append((utterance_id, state))


def _real_dispatcher(**kwargs):
    """Real channel + real event gate + real dispatcher, faked transport."""
    channel = VoiceLiveChannel("session-dispatch")
    ws = FakeWs()
    channel._ws = ws
    playback = RecordingPlayback()
    closed = []
    dispatcher = speech_dispatch.SessionSpeechDispatcher(
        "session-dispatch",
        channel,
        on_playback=playback,
        on_close_channel=lambda reason: closed.append(reason),
        **kwargs,
    )
    channel.set_lifecycle_listener(dispatcher.on_provider_event)
    # Real gateway drain forwarding, over the faked transport.
    channel._drain_task = asyncio.create_task(channel._drain())
    return dispatcher, channel, ws, playback, closed


async def _drain_once():
    # Let the gateway drain task forward queued events to the dispatcher.
    for _ in range(20):
        await asyncio.sleep(0)


def test_send_marks_requested_not_completed_and_ack_binds_response():
    async def run():
        dispatcher, channel, ws, playback, _closed = _real_dispatcher()
        record = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        # Send succeeded: REQUESTED, not heard, not completed.
        assert record.state == "requested"
        assert record.client_event_id == ws.creates()[0]["event_id"]
        assert playback.calls == [(ANSWER_UID, "requested")]
        assert len(ws.creates()) == 1
        # The real gate holds the allowance and knows the owning event id.
        assert channel._gate.pending_app_responses == 1
        ws.push({"type": "response.created", "response": {"id": "resp-1"}})
        await _drain_once()
        assert record.response_id == "resp-1"
        assert record.ack_monotonic is not None
        assert channel._gate.owner_for_response("resp-1") == ANSWER_UID
        assert channel._gate.event_for_response("resp-1") == record.client_event_id
        ws.push({"type": "response.done", "response": {"id": "resp-1", "status": "completed"}})
        await _drain_once()
        assert record.state == "completed"
        assert playback.calls[-1] == (ANSWER_UID, "done")

    asyncio.run(run())


def test_sent_is_never_recorded_as_heard_or_completed():
    async def run():
        dispatcher, _channel, ws, playback, _closed = _real_dispatcher()
        record = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        assert record.state == "requested"
        assert all(state != "done" for _uid, state in playback.calls)
        ws.push({"type": "response.audio.delta", "response_id": "resp-1"})
        await _drain_once()
        # Provider audio deltas are not completion; only response.done is.
        assert record.state == "requested"

    asyncio.run(run())


def test_interrupted_is_never_completed():
    async def run():
        dispatcher, _channel, ws, playback, _closed = _real_dispatcher()
        record = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        ws.push({"type": "response.created", "response": {"id": "resp-1"}})
        await _drain_once()
        ws.push({"type": "response.cancelled", "response": {"id": "resp-1"}})
        await _drain_once()
        assert record.state == "canceled"
        # A late completion after the cancel must not resurrect the record.
        ws.push({"type": "response.done", "response": {"id": "resp-1", "status": "completed"}})
        await _drain_once()
        assert record.state == "canceled"
        assert playback.calls[-1][1] == "canceled"

    asyncio.run(run())


def test_optional_status_never_interrupts_an_active_answer():
    async def run():
        dispatcher, _channel, ws, playback, _closed = _real_dispatcher()
        answer = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        status = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        assert answer.state == "requested"
        assert status.state == "canceled"
        assert status.reason == "suppressed_behind_answer"
        assert len(ws.creates()) == 1  # nothing new reached the provider
        # The suppressed filler row is honestly canceled; the answer is only
        # requested -- never recorded as heard.
        assert playback.calls == [
            (ANSWER_UID, "requested"),
            (STATUS_UID, "canceled"),
        ]

    asyncio.run(run())


def test_answer_replaces_requested_status_with_ack_then_cancel_then_terminal():
    async def run():
        dispatcher, _channel, ws, playback, _closed = _real_dispatcher(
            replace_timeout_s=1.0, cancel_timeout_s=1.0
        )
        status = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        ws.push({"type": "response.created", "response": {"id": "resp-status"}})
        await _drain_once()
        answer_task = asyncio.create_task(
            dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        )
        await _drain_once()
        # Awaiting the ack, then cancel, then the terminal event -- bounded.
        assert ws.types() == ["response.create", "response.cancel"]
        ws.push({"type": "response.cancelled", "response": {"id": "resp-status"}})
        await _drain_once()
        answer = await answer_task
        assert status.state == "canceled"
        assert answer.state == "requested"
        assert ws.types() == ["response.create", "response.cancel", "response.create"]
        assert len(ws.creates()) == 2
        assert playback.calls == [
            (STATUS_UID, "requested"),
            (STATUS_UID, "canceled"),
            (ANSWER_UID, "requested"),
        ]

    asyncio.run(run())


def test_cancellation_landing_before_send_never_reaches_the_provider():
    async def run():
        dispatcher, _channel, ws, playback, closed = _real_dispatcher()
        filler_task = asyncio.create_task(
            dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        )
        # One cooperative yield: the filler is registered and parked right
        # before its send side effect.
        await asyncio.sleep(0)
        canceled = await dispatcher.cancel(kind="status", reason="result_ready")
        assert canceled is not None
        assert canceled.state == "canceled"
        filler = await filler_task
        assert filler.state == "canceled"
        assert filler.reason == "result_ready"
        # Not-yet-sent filler: nothing reached the provider, no allowance.
        assert ws.sent == []
        assert closed == []
        answer = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        assert answer.state == "requested"
        assert len(ws.creates()) == 1
        assert playback.calls == [(STATUS_UID, "canceled"), (ANSWER_UID, "requested")]

    asyncio.run(run())


def test_bound_failure_fails_speech_closed_and_never_overlaps_or_replays():
    async def run():
        dispatcher, channel, ws, playback, closed = _real_dispatcher(
            replace_timeout_s=0.05, cancel_timeout_s=0.05
        )
        status = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        # The provider never acknowledges the filler create.
        answer = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        assert status.state == "failed"
        assert status.reason == "ack_bound_exceeded"
        assert answer.state == "failed"
        assert answer.reason == "speech_closed"
        # Fail CLOSED: the channel is reset/torn down, one create total, and
        # the business turn is never re-submitted to obtain speech.
        assert closed == ["ack_bound_exceeded"]
        assert channel._ws is None
        assert len(ws.creates()) == 1
        assert channel._gate.pending_app_responses == 0
        # Later speech attempts stay text-only instead of overlapping.
        later = await dispatcher.speak(kind="prompt", **_payload(PROMPT_TEXT, PROMPT_UID))
        assert later.state == "failed"
        assert later.reason == "channel_closed"
        assert len(ws.creates()) == 1
        assert playback.calls[-1][1] == "failed"

    asyncio.run(run())


def test_failed_send_releases_the_allowance_and_reports_failed():
    async def run():
        dispatcher, channel, ws, playback, closed = _real_dispatcher()
        ws.fail_send = ConnectionResetError("mid-write")
        record = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        assert record.state == "failed"
        assert record.reason == "send_failed"
        assert channel._gate.pending_app_responses == 0
        assert closed == []  # a definite send failure does not tear down
        assert playback.calls[-1][1] == "failed"

    asyncio.run(run())


def test_provider_error_is_correlated_to_its_own_request():
    async def run():
        dispatcher, _channel, ws, playback, _closed = _real_dispatcher()
        record = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        event_id = record.client_event_id
        # An unattributable error must not steal this dispatch.
        ws.push({"type": "error", "error": {"code": "response_cancel_not_active"}})
        await _drain_once()
        assert record.state == "requested"
        ws.push({"type": "error", "error": {"code": "conflict", "event_id": event_id}})
        await _drain_once()
        assert record.state == "failed"
        assert playback.calls[-1][1] == "failed"

    asyncio.run(run())


def test_generation_invalidation_cancels_old_status_and_ignores_late_events():
    async def run():
        dispatcher, _channel, ws, _playback, _closed = _real_dispatcher(
            replace_timeout_s=0.05, cancel_timeout_s=0.05
        )
        status = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        ws.push({"type": "response.created", "response": {"id": "resp-old"}})
        await _drain_once()
        dispatcher.invalidate("barge_in")
        await _drain_once()
        assert status.state == "canceled"
        generation = dispatcher.generation
        # A late completion of the invalidated generation is ignored.
        ws.push({"type": "response.done", "response": {"id": "resp-old", "status": "completed"}})
        await _drain_once()
        assert status.state == "canceled"
        assert dispatcher.generation == generation
        # A new dispatch runs under the new generation.
        fresh = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        assert fresh.generation == generation
        assert fresh.state == "requested"

    asyncio.run(run())


def test_end_closes_dispatch_and_future_speech_stays_text_only():
    async def run():
        dispatcher, _channel, _ws, playback, _closed = _real_dispatcher(
            replace_timeout_s=0.05, cancel_timeout_s=0.05
        )
        record = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        await dispatcher.close("session_ended")
        assert record.state == "canceled"
        assert playback.calls[-1] == (STATUS_UID, "canceled")
        late = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        assert late.state == "failed"
        assert late.reason == "channel_closed"

    asyncio.run(run())


def test_exact_speech_validation_runs_before_any_send():
    async def run():
        dispatcher, _channel, ws, _playback, _closed = _real_dispatcher()
        from ai.core.voice.speech import ExactSpeechViolation

        with pytest.raises(ExactSpeechViolation):
            await dispatcher.speak(
                kind="answer",
                text="Paraphrased without persistence.",
                spoken_hash=spoken_summary_hash(ANSWER_TEXT),
                utterance_id=ANSWER_UID,
            )
        assert ws.sent == []

    asyncio.run(run())


def test_dispatch_ack_duration_is_recorded_content_free():
    async def run():
        with timing.measurement():
            dispatcher, _channel, ws, _playback, _closed = _real_dispatcher()
            record = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
            ws.push({"type": "response.created", "response": {"id": "resp-1"}})
            await _drain_once()
            ws.push({"type": "response.done", "response": {"id": "resp-1", "status": "completed"}})
            await _drain_once()
            snapshot = timing.snapshot()
        assert record.state == "completed"
        phases = snapshot["phases"]
        assert phases["dispatch_ack"] >= 0
        assert phases["dispatch_terminal"] >= 0
        bindings = snapshot["bindings"]
        assert bindings["utterance_id"] == ANSWER_UID
        assert bindings["kind"] == "answer"
        assert "PRIVATE_SENTINEL" not in str(snapshot)

    asyncio.run(run())


def test_dispatcher_registry_is_one_per_session():
    channel = VoiceLiveChannel("session-registry")
    first = speech_dispatch.dispatcher_for_session("session-registry", channel)
    second = speech_dispatch.dispatcher_for_session("session-registry", channel)
    assert first is second
    speech_dispatch.release_dispatcher("session-registry")
    third = speech_dispatch.dispatcher_for_session("session-registry", channel)
    assert third is not first


# --------------------------------------------------------------------------- #
# Gateway-level pending ledger: cancellation around the send side effect       #
# --------------------------------------------------------------------------- #
def test_cancel_landing_at_send_side_effect_reconciles_and_fails_closed():
    async def run():
        ws = FakeWs(fail_send=asyncio.CancelledError())
        channel = VoiceLiveChannel("session-cancel")
        channel._ws = ws
        with pytest.raises(asyncio.CancelledError):
            await channel.send_control({
                "type": "response.create",
                "response": {
                    "pre_generated_assistant_message": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "text", "text": STATUS_TEXT}],
                    }
                },
            })
        # The side effect may have landed: the allowance cannot survive, and
        # the channel is reconciled closed so no stale response is ever
        # legitimized later.
        assert channel._gate.pending_app_responses == 0
        assert channel._ws is None
        assert ws.closed
        rogue = {"type": "response.created", "response": {"id": "resp_rogue"}}
        assert channel._gate.classify(rogue) == "forbidden"

    asyncio.run(run())


def test_send_ownership_lock_serializes_control_sends():
    async def run():
        channel = VoiceLiveChannel("session-lock")
        order = []
        slow = FakeWs()

        async def send_json(payload):
            order.append(("start", payload.get("type")))
            await asyncio.sleep(0.01)
            order.append(("end", payload.get("type")))

        slow.send_json = send_json
        channel._ws = slow
        await asyncio.gather(
            channel.send_control({"type": "response.cancel"}),
            channel.send_control({"type": "response.cancel"}),
        )
        # Strict serialization: each send fully ends before the next starts.
        assert [phase for phase, _kind in order] == ["start", "end", "start", "end"]

    asyncio.run(run())


def test_multi_pending_create_is_rejected_not_guessed():
    gate = EventGate()
    gate.expect_app_response("evt-1", owner="utt-1")
    gate.expect_app_response("evt-2", owner="utt-2")
    # With two outstanding creates the gate cannot know which one a provider
    # response belongs to; policy is fail-closed, never FIFO guessing.
    ack = {"type": "response.created", "response": {"id": "resp_ambiguous"}}
    assert gate.classify(ack) == "forbidden"
    assert gate.owner_for_response("resp_ambiguous") is None
    assert gate.pending_app_responses == 2


def test_reset_pending_clears_the_historical_expected_set():
    gate = EventGate()
    gate.expect_response("resp-old")
    assert gate.classify({"type": "response.done", "response": {"id": "resp-old"}}) == (
        "speech_lifecycle"
    )
    gate.reset_pending()
    # The historical id must not legitimize a response after teardown.
    assert gate.classify({"type": "response.done", "response": {"id": "resp-old"}}) == "forbidden"


def test_has_active_app_response_counts_pending_creates():
    gate = EventGate()
    assert not gate.has_active_app_response()
    gate.expect_app_response("evt-1", owner="utt-1")
    # Awaiting acknowledgement is active: a boolean over acknowledged ids
    # alone would let new speech overlap the pending create.
    assert gate.has_active_app_response()
    gate.abandon_app_response("evt-1")
    assert not gate.has_active_app_response()


def test_adoption_maps_response_to_event_and_owner():
    gate = EventGate()
    gate.expect_app_response("evt-1", owner="utt-9")
    assert gate.classify({"type": "response.created", "response": {"id": "resp-9"}}) == (
        "speech_lifecycle"
    )
    assert gate.event_for_response("resp-9") == "evt-1"
    assert gate.owner_for_response("resp-9") == "utt-9"


# --------------------------------------------------------------------------- #
# Targeted race regressions (GPT source review 2026-10-05):                    #
# ack/terminal landing across the send side effect, local cancel vs provider   #
# ownership, owner correlation for terminals, honest terminal-status mapping,  #
# record-bound measurement, and bounded dispatch/teardown waits.               #
# --------------------------------------------------------------------------- #
class HoldingWs(FakeWs):
    """FakeWs whose selected sends park until ``release`` (side-effect window)."""

    def __init__(self, hold=()):
        super().__init__()
        self.hold = set(hold)
        self.release = asyncio.Event()

    async def send_json(self, payload):
        self.sent.append(payload)
        if payload.get("type") in self.hold:
            await self.release.wait()


def _dispatcher_with(ws, *, session_id="session-race", **kwargs):
    """Wire a real channel + gate + dispatcher around a caller-supplied ws."""
    channel = VoiceLiveChannel(session_id)
    channel._ws = ws
    playback = RecordingPlayback()
    closed = []
    dispatcher = speech_dispatch.SessionSpeechDispatcher(
        session_id,
        channel,
        on_playback=playback,
        on_close_channel=lambda reason: closed.append(reason),
        **kwargs,
    )
    channel.set_lifecycle_listener(dispatcher.on_provider_event)
    channel._drain_task = asyncio.create_task(channel._drain())
    return dispatcher, channel, playback, closed


def test_ack_during_send_is_bound_not_lost():
    async def run():
        ws = HoldingWs(hold={"response.create"})
        dispatcher, _channel, _playback, _closed = _dispatcher_with(ws)
        speak_task = asyncio.create_task(
            dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        )
        await _drain_once()
        assert len(ws.creates()) == 1
        assert not speak_task.done()
        # The acknowledgement lands while send_control still awaits: the
        # create is already outstanding and must be BOUND, not dropped.
        ws.push({"type": "response.created", "response": {"id": "resp-1"}})
        await _drain_once()
        ws.release.set()
        record = await speak_task
        assert record.state == "requested"
        assert record.response_id == "resp-1"
        assert record.ack_monotonic is not None
        # The bound identity now correlates its terminal event.
        ws.push({"type": "response.done", "response": {"id": "resp-1", "status": "completed"}})
        await _drain_once()
        assert record.state == "completed"

    asyncio.run(run())


def test_terminal_during_send_settles_without_regressing_to_requested():
    async def run():
        ws = HoldingWs(hold={"response.create"})
        dispatcher, _channel, playback, _closed = _dispatcher_with(ws)
        speak_task = asyncio.create_task(
            dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        )
        await _drain_once()
        ws.push({"type": "response.created", "response": {"id": "resp-1"}})
        ws.push({"type": "response.done", "response": {"id": "resp-1", "status": "completed"}})
        await _drain_once()
        ws.release.set()
        record = await speak_task
        # Terminal landed BEFORE the send await finished: the send completion
        # must never regress the terminal state back to requested.
        assert record.state == "completed"
        assert playback.calls[-1] == (ANSWER_UID, "done")

    asyncio.run(run())


def test_cancel_during_send_is_reconciled_before_replacement():
    async def run():
        ws = HoldingWs(hold={"response.create"})
        dispatcher, _channel, playback, _closed = _dispatcher_with(
            ws, replace_timeout_s=1.0, cancel_timeout_s=1.0
        )
        speak_task = asyncio.create_task(
            dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        )
        await _drain_once()
        # The create payload hit the wire; cancel lands immediately after the
        # side effect, while the send await is still in progress.
        cancel_task = asyncio.create_task(dispatcher.cancel(reason="result_ready"))
        await _drain_once()
        ws.release.set()
        status = await speak_task
        canceled = await cancel_task
        assert canceled is status
        # Local stop is immediate; the send completion never regresses it.
        assert status.state == "canceled"
        assert ws.types() == ["response.create", "response.cancel"]
        # The old create is still owned by the provider: the replacement must
        # reconcile create -> ack -> cancel -> terminal first, never overlap.
        answer_task = asyncio.create_task(
            dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        )
        await _drain_once()
        assert len(ws.creates()) == 1
        ws.push({"type": "response.created", "response": {"id": "resp-old"}})
        await _drain_once()
        assert len(ws.creates()) == 1
        ws.push({"type": "response.cancelled", "response": {"id": "resp-old"}})
        await _drain_once()
        answer = await answer_task
        assert answer.state == "requested"
        assert len(ws.creates()) == 2
        # The interrupted status is never resurrected or completed.
        assert status.state == "canceled"
        assert [state for _uid, state in playback.calls].count("canceled") == 1

    asyncio.run(run())


def test_invalidate_reconciles_old_provider_ownership_before_replacement():
    async def run():
        dispatcher, _channel, ws, _playback, _closed = _real_dispatcher(
            replace_timeout_s=1.0, cancel_timeout_s=1.0
        )
        status = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        ws.push({"type": "response.created", "response": {"id": "resp-old"}})
        await _drain_once()
        dispatcher.invalidate("barge_in")
        await _drain_once()
        assert status.state == "canceled"
        # The acknowledged old-generation response still owns provider
        # playback; generation invalidation must reconcile that ownership
        # (bounded) instead of ignoring the lifecycle needed to release it.
        answer_task = asyncio.create_task(
            dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        )
        await _drain_once()
        assert "response.cancel" in ws.types()
        assert len(ws.creates()) == 1
        ws.push({"type": "response.cancelled", "response": {"id": "resp-old"}})
        await _drain_once()
        answer = await answer_task
        assert answer.state == "requested"
        assert len(ws.creates()) == 2

    asyncio.run(run())


def test_missing_provider_terminal_fails_speech_closed_never_overlap():
    async def run():
        dispatcher, channel, ws, playback, closed = _real_dispatcher(
            replace_timeout_s=0.05, cancel_timeout_s=0.05
        )
        status = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        ws.push({"type": "response.created", "response": {"id": "resp-old"}})
        await _drain_once()
        await dispatcher.cancel(reason="result_ready")
        assert status.state == "canceled"
        answer = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        # The provider never confirms the cancel: the bounded wait expires and
        # speech fails CLOSED -- never a second create over the old response.
        assert status.state == "canceled"  # interrupted stays interrupted
        assert answer.state == "failed"
        assert answer.reason == "speech_closed"
        assert closed == ["cancel_bound_exceeded"]
        assert channel._ws is None
        assert len(ws.creates()) == 1
        assert playback.calls[-1][1] == "failed"

    asyncio.run(run())


def test_stale_terminal_for_a_historical_id_never_terminates_the_next_dispatch():
    async def run():
        dispatcher, _channel, ws, _playback, _closed = _real_dispatcher()
        first = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        ws.push({"type": "response.created", "response": {"id": "resp-1"}})
        await _drain_once()
        ws.push({"type": "response.done", "response": {"id": "resp-1", "status": "completed"}})
        await _drain_once()
        assert first.state == "completed"
        second = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        # A stale terminal for the HISTORICAL (still gate-expected) id must
        # not terminate the new, not-yet-acknowledged dispatch.
        ws.push({"type": "response.done", "response": {"id": "resp-1", "status": "completed"}})
        await _drain_once()
        # A terminal with no id at all terminates nothing either.
        await dispatcher.on_provider_event({"type": "response.done", "response": {}})
        assert second.state == "requested"
        assert second.response_id is None
        ws.push({"type": "response.created", "response": {"id": "resp-2"}})
        await _drain_once()
        ws.push({"type": "response.done", "response": {"id": "resp-2", "status": "completed"}})
        await _drain_once()
        assert second.state == "completed"

    asyncio.run(run())


def test_malformed_ack_never_consumes_the_allowance_or_binds():
    async def run():
        dispatcher, channel, ws, _playback, _closed = _real_dispatcher()
        record = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        # An id-less acknowledgement cannot be adopted: it must neither
        # consume the single-outstanding allowance nor bind a phantom id.
        ws.push({"type": "response.created", "response": {}})
        await _drain_once()
        assert record.response_id is None
        assert record.ack_monotonic is None
        assert channel._gate.pending_app_responses == 1
        # The real acknowledgement still adopts and binds cleanly.
        ws.push({"type": "response.created", "response": {"id": "resp-1"}})
        await _drain_once()
        assert record.response_id == "resp-1"
        assert channel._gate.pending_app_responses == 0
        assert channel._gate.owner_for_response("resp-1") == STATUS_UID

    asyncio.run(run())


def test_response_done_maps_the_actual_provider_terminal_status():
    async def _one(done_status, expected, expected_playback):
        dispatcher, _channel, ws, playback, _closed = _real_dispatcher()
        record = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        ws.push({"type": "response.created", "response": {"id": "resp-1"}})
        await _drain_once()
        ws.push({"type": "response.done", "response": {"id": "resp-1", "status": done_status}})
        await _drain_once()
        assert record.state == expected
        assert playback.calls[-1] == (STATUS_UID, expected_playback)

    async def run():
        # response.done is NOT blanket completion: a cancelled/failed response
        # is never recorded as completed (interrupted is never heard).
        await _one("cancelled", "canceled", "canceled")
        await _one("failed", "failed", "failed")
        await _one("completed", "completed", "done")

    asyncio.run(run())


def test_dispatch_durations_land_on_the_record_bound_measurement():
    async def run():
        ws = FakeWs()
        with timing.measurement():
            bound = timing.capture_measurement()
            dispatcher, _channel, _playback, _closed = _dispatcher_with(ws)
            record = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        # Provider events arrive in the gateway drain's context -- outside the
        # request measurement that spoke. The durations must land on the
        # measurement BOUND TO THE RECORD, never on a None/wrong-turn capture.
        ws.push({"type": "response.created", "response": {"id": "resp-1"}})
        await _drain_once()
        ws.push({"type": "response.done", "response": {"id": "resp-1", "status": "completed"}})
        await _drain_once()
        snapshot = timing.snapshot(bound)
        assert record.state == "completed"
        phases = snapshot["phases"]
        assert "dispatch_ack" in phases
        assert "dispatch_terminal" in phases
        assert "PRIVATE_SENTINEL" not in str(snapshot)

    asyncio.run(run())


def test_close_refuses_new_sends_before_awaits_and_bounds_its_waits():
    async def run():
        class HoldCancelWs(FakeWs):
            async def send_json(self, payload):
                self.sent.append(payload)
                if payload.get("type") == "response.cancel":
                    await asyncio.Event().wait()  # never resolves

        ws = HoldCancelWs()
        dispatcher, _channel, _playback, _closed = _dispatcher_with(
            ws, cancel_timeout_s=0.05, send_timeout_s=0.05
        )
        await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        close_task = asyncio.create_task(dispatcher.close("session_ended"))
        await _drain_once()
        # New speech is refused while close is still awaiting its cancel send.
        later = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        assert later.state == "failed"
        assert later.reason == "channel_closed"
        assert len(ws.creates()) == 1
        # Every owned wait is bounded: close returns although the cancel send
        # never completed.
        await asyncio.wait_for(close_task, 1.0)

    asyncio.run(run())


def test_fail_closed_teardown_callbacks_are_bounded():
    async def run():
        ws = FakeWs()
        channel = VoiceLiveChannel("session-teardown")
        channel._ws = ws
        closed = []

        async def hanging_close(reason):
            closed.append(reason)
            await asyncio.Event().wait()  # never resolves

        dispatcher = speech_dispatch.SessionSpeechDispatcher(
            "session-teardown",
            channel,
            on_playback=RecordingPlayback(),
            on_close_channel=hanging_close,
            replace_timeout_s=0.05,
            cancel_timeout_s=0.05,
            close_timeout_s=0.05,
        )
        channel.set_lifecycle_listener(dispatcher.on_provider_event)
        channel._drain_task = asyncio.create_task(channel._drain())
        await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        # The provider never acknowledges; the replacement bound expires and
        # speech fails CLOSED -- the teardown callback itself must be bounded.
        answer = await asyncio.wait_for(
            dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID)), 1.0
        )
        assert answer.state == "failed"
        assert answer.reason == "speech_closed"
        assert closed == ["ack_bound_exceeded"]

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Supervisor negative-control regressions (2026-10-05): generation fencing     #
# around every blocking/pre-send boundary, historical acknowledgement ids      #
# never binding a new owner, bounded owned playback callbacks, and honest      #
# unknown terminal-status mapping.                                             #
# --------------------------------------------------------------------------- #
def test_invalidate_before_send_fences_the_captured_generation():
    async def run():
        dispatcher, _channel, ws, _playback, _closed = _real_dispatcher()
        task = asyncio.create_task(
            dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        )
        await asyncio.sleep(0)
        dispatcher.invalidate(reason="barge_in")
        record = await task
        assert record.generation != dispatcher.generation
        assert ws.creates() == [], "Old-generation exact speech was dispatched after invalidation"

    asyncio.run(run())


def test_invalidate_capture_never_cancels_the_fresh_generation():
    async def run():
        dispatcher, _channel, ws, _playback, _closed = _real_dispatcher()
        stale = asyncio.create_task(
            dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        )
        await asyncio.sleep(0)
        dispatcher.invalidate(reason="barge_in")
        # New live-generation speech must survive the deferred cancellation of
        # the captured OLD record -- it may never be the cancel's target.
        fresh = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        old = await stale
        assert old.state == "canceled"
        assert old.generation != dispatcher.generation
        assert fresh.generation == dispatcher.generation
        assert fresh.state == "requested"
        assert len(ws.creates()) == 1  # only the fresh generation was sent

    asyncio.run(run())


def test_invalidation_during_replace_wait_fences_the_incoming_dispatch():
    async def run():
        dispatcher, _channel, ws, _playback, _closed = _real_dispatcher(
            replace_timeout_s=1.0, cancel_timeout_s=1.0
        )
        status = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        answer_task = asyncio.create_task(
            dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        )
        await _drain_once()
        assert ws.types() == ["response.create"]  # parked in the bounded ack wait
        dispatcher.invalidate(reason="barge_in")
        # The old provider ownership still reconciles: ack -> cancel -> terminal.
        ws.push({"type": "response.created", "response": {"id": "resp-old"}})
        await _drain_once()
        assert "response.cancel" in ws.types()
        ws.push({"type": "response.cancelled", "response": {"id": "resp-old"}})
        await _drain_once()
        answer = await answer_task
        assert status.state == "canceled"
        # The incoming record was created under the OLD generation: it must be
        # fenced right after the replacement waits, before any send.
        assert answer.state == "canceled"
        assert answer.reason == "invalidated_before_send"
        assert answer.generation != dispatcher.generation
        assert len(ws.creates()) == 1
        assert "response.cancel" in ws.types()

    asyncio.run(run())


def test_close_during_replace_wait_fences_the_incoming_dispatch():
    async def run():
        dispatcher, _channel, ws, _playback, _closed = _real_dispatcher(
            replace_timeout_s=1.0, cancel_timeout_s=1.0
        )
        status = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        answer_task = asyncio.create_task(
            dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        )
        await _drain_once()
        close_task = asyncio.create_task(dispatcher.close("session_ended"))
        ws.push({"type": "response.created", "response": {"id": "resp-old"}})
        ws.push({"type": "response.cancelled", "response": {"id": "resp-old"}})
        await _drain_once()
        answer = await answer_task
        await close_task
        # close refuses new sends BEFORE any await: its refusal must be
        # rechecked after the replacement waits, before the send side effect.
        assert answer.state == "failed"
        assert answer.reason == "channel_closed"
        assert len(ws.creates()) == 1
        assert dispatcher.closed
        assert status.state == "canceled"

    asyncio.run(run())


def test_historical_created_ack_cannot_bind_the_new_owner():
    async def run():
        dispatcher, channel, ws, _playback, _closed = _real_dispatcher()
        first = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
        ws.push({"type": "response.created", "response": {"id": "historical-id"}})
        ws.push({
            "type": "response.done",
            "response": {"id": "historical-id", "status": "completed"},
        })
        await _drain_once()
        assert first.state == "completed"
        answer = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        assert channel._gate.pending_app_responses == 1
        # A historical (gate-allowed) acknowledgement id is NOT the new owner:
        # binding it would let its stale terminal finish the new dispatch and
        # strand the real create's allowance. Duplicates are equally stale.
        ws.push({"type": "response.created", "response": {"id": "historical-id"}})
        ws.push({"type": "response.created", "response": {"id": "historical-id"}})
        await _drain_once()
        assert answer.response_id is None, "Historical ack was attributed to the new dispatch"
        assert answer.ack_monotonic is None
        assert channel._gate.pending_app_responses == 1
        ws.push({
            "type": "response.done",
            "response": {"id": "historical-id", "status": "completed"},
        })
        await _drain_once()
        assert answer.state == "requested"
        # A fresh acknowledgement still binds and completes its own dispatch.
        ws.push({"type": "response.created", "response": {"id": "resp-fresh"}})
        await _drain_once()
        assert answer.response_id == "resp-fresh"
        assert channel._gate.owner_for_response("resp-fresh") == ANSWER_UID
        ws.push({"type": "response.done", "response": {"id": "resp-fresh", "status": "completed"}})
        await _drain_once()
        assert answer.state == "completed"
        # Late duplicates of the historical id stay harmless forever.
        ws.push({"type": "response.created", "response": {"id": "historical-id"}})
        ws.push({
            "type": "response.done",
            "response": {"id": "historical-id", "status": "completed"},
        })
        await _drain_once()
        assert answer.state == "completed"

    asyncio.run(run())


def test_close_bounds_the_owned_playback_callback():
    async def run():
        dispatcher, _channel, _ws, _playback, _closed = _real_dispatcher(close_timeout_s=0.01)
        await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        never = asyncio.Event()

        async def stuck_callback(utterance_id, playback_state):
            await never.wait()

        dispatcher._on_playback = stuck_callback
        await asyncio.wait_for(dispatcher.close(reason="session_ended"), timeout=0.10)
        assert dispatcher.closed

    asyncio.run(run())


def test_failing_owned_playback_callback_never_hangs_or_regresses():
    async def run():
        dispatcher, _channel, ws, _playback, _closed = _real_dispatcher(close_timeout_s=0.05)

        async def bad_callback(utterance_id, playback_state):  # noqa: RUF029 - awaitable callback.
            raise RuntimeError("persistence down")

        dispatcher._on_playback = bad_callback
        record = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        # A failed audit/persistence write is dropped (never retried, never a
        # replayed business turn); it never forges or hangs the lifecycle.
        assert record.state == "requested"
        ws.push({"type": "response.created", "response": {"id": "resp-1"}})
        ws.push({"type": "response.done", "response": {"id": "resp-1", "status": "completed"}})
        await _drain_once()
        assert record.state == "completed"
        assert len(ws.creates()) == 1  # never a business retry

    asyncio.run(run())


def test_unknown_provider_terminal_status_is_not_completion():
    async def _one(done_response, expected, expected_playback):
        dispatcher, _channel, ws, playback, _closed = _real_dispatcher()
        record = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
        ws.push({"type": "response.created", "response": {"id": "resp-1"}})
        await _drain_once()
        ws.push({"type": "response.done", "response": done_response})
        await _drain_once()
        assert record.state == expected, (
            f"status {done_response.get('status', '<absent>')!r} mapped to {record.state!r}"
        )
        assert playback.calls[-1] == (ANSWER_UID, expected_playback)

    async def run():
        # An unknown/nonterminal provider status is NOT proof of completion.
        await _one({"id": "resp-1", "status": "in_progress"}, "failed", "failed")
        # An absent status is not proof of completion either.
        await _one({"id": "resp-1"}, "failed", "failed")
        # A malformed status is an honest failed record, never a false COMPLETED.
        await _one({"id": "resp-1", "status": 42}, "failed", "failed")
        # Positive known terminal statuses keep mapping exactly (controls).
        await _one({"id": "resp-1", "status": "completed"}, "completed", "done")
        await _one({"id": "resp-1", "status": "cancelled"}, "canceled", "canceled")
        await _one({"id": "resp-1", "status": "failed"}, "failed", "failed")

    asyncio.run(run())
