"""RV11 actual dispatcher/channel/gate/drain; only WebSocket is simulated."""

import asyncio

import pytest
from ai.core.tests.test_voice_speech_dispatch import (
    ANSWER_TEXT,
    ANSWER_UID,
    STATUS_TEXT,
    STATUS_UID,
    FakeWs,
    _drain_once,
    _payload,
    _real_dispatcher,
)
from ai.core.voice.speech import ExactSpeechViolation, spoken_summary_hash


async def cleanup(channel, *readers):
    current = channel._drain_task
    await channel.close()
    tasks = [task for task in [current, *readers] if task is not None]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def acknowledge(ws, record, response_id):
    ws.push({"type": "response.created", "response": {"id": response_id}})
    await _drain_once()
    assert record.response_id == response_id
    ws.push({
        "type": "response.done",
        "response": {"id": response_id, "status": "completed"},
    })
    await _drain_once()
    assert record.state == "completed"


def test_rv11_fresh_channel_control_acknowledges_and_completes_real_owner():
    async def run():
        dispatcher, channel, ws, playback, closed = _real_dispatcher()
        reader = channel._drain_task
        try:
            record = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
            assert record.state == "requested" and len(ws.creates()) == 1
            await acknowledge(ws, record, "rv11-fresh-positive")
            assert channel._gate.owner_for_response(record.response_id) == ANSWER_UID
            assert channel._gate.pending_app_responses == 0
            assert playback.calls[-1] == (ANSWER_UID, "done")
            assert not ws.closed and closed == []
        finally:
            await cleanup(channel, reader)

    asyncio.run(run())


@pytest.mark.parametrize("invalid", ["hash", "rate"])
def test_rv11_explicit_presend_refusal_keeps_connected_channel_useful(invalid):
    async def run():
        dispatcher, channel, ws, playback, closed = _real_dispatcher()
        reader = channel._drain_task
        try:
            payload = _payload(ANSWER_TEXT, ANSWER_UID)
            rate = None
            if invalid == "hash":
                payload["spoken_hash"] = spoken_summary_hash("Different persisted wording.")
                expected = ExactSpeechViolation
            else:
                rate = 1.5
                expected = ValueError
            with pytest.raises(expected):
                await dispatcher.speak(kind="answer", rate=rate, **payload)
            assert ws.sent == [] and not ws.closed and channel._ws is ws
            assert channel._gate.pending_app_responses == 0
            assert not channel.has_active_app_response() and closed == []
            valid = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
            await acknowledge(ws, valid, "rv11-after-presend-" + invalid)
            assert len(ws.creates()) == 1 and playback.calls[-1] == (ANSWER_UID, "done")
        finally:
            await cleanup(channel, reader)

    asyncio.run(run())


def test_rv11_midwrite_create_failure_retires_the_actual_socket_and_reader():
    async def run():
        dispatcher, channel, ws, playback, closed = _real_dispatcher()
        reader = channel._drain_task
        ws.fail_send = ConnectionResetError("simulated create mid-write")
        try:
            failed = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
            assert failed.state == "failed" and failed.reason == "send_failed"
            assert len(ws.creates()) == 1
            assert playback.calls[-1] == (STATUS_UID, "failed")
            await _drain_once()
            assert ws.closed and channel._ws is None, (
                "RV11 ambiguous create kept old transport alive"
            )
            assert channel._drain_task is None and reader.done()
            assert channel._gate.pending_app_responses == 0
            assert channel._gate.expected_response_ids == set()
            assert channel._gate.response_owners == {}
            assert closed == []
        finally:
            await cleanup(channel, reader)

    asyncio.run(run())


def test_rv11_late_created_before_next_request_cannot_reuse_the_old_transport():
    async def run():
        dispatcher, channel, ws, playback, closed = _real_dispatcher()
        reader = channel._drain_task
        ws.fail_send = ConnectionResetError("simulated create mid-write")
        try:
            failed = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
            assert failed.state == "failed" and len(ws.creates()) == 1
            ws.fail_send = None
            ws.push({"type": "response.created", "response": {"id": "rv11-old-before-next"}})
            await _drain_once()
            next_record = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
            assert len(ws.creates()) == 1, "RV11 next owner sent on ambiguous old transport"
            assert next_record.state == "failed" and next_record.reason == "send_failed"
            assert next_record.response_id is None
            assert channel._gate.owner_for_response("rv11-old-before-next") is None
            assert channel._gate.pending_app_responses == 0
            assert ws.closed and channel._ws is None
            assert failed.state == "failed" and playback.calls[-1] == (ANSWER_UID, "failed")
            assert closed == []
        finally:
            await cleanup(channel, reader)

    asyncio.run(run())


def test_rv11_unseen_late_ack_after_next_attempt_never_binds_the_new_utterance():
    async def run():
        dispatcher, channel, ws, playback, closed = _real_dispatcher()
        reader = channel._drain_task
        ws.fail_send = ConnectionResetError("simulated create mid-write")
        try:
            failed = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
            assert failed.state == "failed" and failed.response_id is None
            ws.fail_send = None
            next_record = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
            ws.push({"type": "response.created", "response": {"id": "rv11-old-after-next"}})
            await _drain_once()
            assert next_record.response_id is None, (
                "RV11 old create ACK was adopted by the next utterance"
            )
            assert channel._gate.owner_for_response("rv11-old-after-next") is None
            assert next_record.state == "failed" and len(ws.creates()) == 1
            assert channel._gate.pending_app_responses == 0
            assert failed.state == "failed" and playback.calls[-1] == (ANSWER_UID, "failed")
            assert closed == []
        finally:
            await cleanup(channel, reader)

    asyncio.run(run())


def test_rv11_explicit_fresh_transport_control_recovers_without_old_reader_attribution():
    async def run():
        dispatcher, channel, old_ws, playback, closed = _real_dispatcher()
        old_reader = channel._drain_task
        old_ws.fail_send = ConnectionResetError("simulated create mid-write")
        try:
            failed = await dispatcher.speak(kind="status", **_payload(STATUS_TEXT, STATUS_UID))
            assert failed.state == "failed" and len(old_ws.creates()) == 1
            # Explicit real close is a positive recovery control in both implementations.
            # Only the provider transport is replaced; channel/gate/dispatcher stay native.
            await cleanup(channel, old_reader)
            fresh_ws = FakeWs()
            channel._ws = fresh_ws
            channel._drain_task = asyncio.create_task(channel._drain())
            next_record = await dispatcher.speak(kind="answer", **_payload(ANSWER_TEXT, ANSWER_UID))
            assert next_record.state == "requested" and len(fresh_ws.creates()) == 1
            old_ws.push({"type": "response.created", "response": {"id": "rv11-retired-reader"}})
            await _drain_once()
            assert next_record.response_id is None
            assert channel._gate.pending_app_responses == 1
            assert channel._gate.owner_for_response("rv11-retired-reader") is None
            await acknowledge(fresh_ws, next_record, "rv11-fresh-after-retire")
            assert channel._gate.owner_for_response(next_record.response_id) == ANSWER_UID
            assert channel._gate.pending_app_responses == 0
            assert len(old_ws.creates()) == 1 and len(fresh_ws.creates()) == 1
            assert failed.state == "failed" and playback.calls[-1] == (ANSWER_UID, "done")
            assert closed == []
        finally:
            await cleanup(channel, old_reader)

    asyncio.run(run())
