"""RV03: real speech must stop BEFORE cancel/suspend persistence awaits.

The route's real ``sync_to_async`` persistence continuation -- the cancel's
utterance-state ORM bulk update, and for foreground loss the earlier
``invalidate_epoch`` write -- is deterministically held at the inspected seam
while the ORIGINAL ORM closure is executed only after release. While that
continuation is held the shared real ``SessionSpeechDispatcher`` must already
be generation-invalidated and locally stopped (the real playback callback has
written the canceled state), a stale-generation dispatch captured before the
entry (mid real replacement) must never reach a provider ``response.create``,
and after release the durable rows must reload in the exact canceled state
with identity/text/hash/input fields preserved, one business call only, no
approval/effect-ledger consumption and no speech replay. Fresh uncanceled
speech keeps dispatching (control).
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from unittest.mock import patch

import ai.core.tests.test_voice_route_speech_integration as fixtures
import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone


@pytest.fixture(scope="module", autouse=True)
def _database():
    from ai.core.tests.focus_test_db import ensure_island_tables

    ensure_island_tables()
    yield
    from ai.core.voice import route_speech, speech_dispatch

    speech_dispatch._dispatchers.clear()
    route_speech.clear_session_state()


def _coordinates():
    user = fixtures._user()
    settings = fixtures._settings([user.pk])
    principal = fixtures._principal(user)
    session = fixtures._session_for(principal, settings)
    return principal, settings, session


def _ws_creates(ws):
    return [p for p in ws.sent if p.get("type") == "response.create"]


async def _reload(created):
    """Real ORM readback of the session's rows (declared identity fields)."""

    def _read():
        from voice.models import VoiceSession

        session = VoiceSession.objects.get(pk=created["id"])
        rows = {}
        for utterance in session.utterances.all():
            rows[str(utterance.id)] = {
                "id": str(utterance.id),
                "session_id": str(utterance.session_id),
                "turn_id": utterance.turn_id,
                "utterance_type": utterance.utterance_type,
                "spoken_summary": utterance.spoken_summary,
                "spoken_summary_hash": utterance.spoken_summary_hash,
                "response_id": utterance.response_id,
                "policy_version": utterance.policy_version,
                "playback_state": utterance.playback_state,
            }
        epoch = session.timing_epoch.hex if session.timing_epoch is not None else None
        return rows, epoch, session.operations.count()

    return await sync_to_async(_read, thread_sensitive=True)()


def _identity(row):
    return {key: value for key, value in row.items() if key != "playback_state"}


def _persist_rows(created, prompt_row_id):
    """Three REAL extra ledger rows: a stale optional prompt (dispatched mid
    replacement), a stale pending required status (never dispatched: durable
    SQL cancel only) and a stale required status captured by the challenge
    dispatch (stale required-status send suppression)."""

    def _write():
        from ai.core.voice.speech import spoken_summary_hash
        from voice.models import VoiceSession, VoiceUtterance, VoiceUtteranceType

        session = VoiceSession.objects.get(pk=created["id"])
        stale_text = "Stale optional prompt captured before the cancellation."
        stale = VoiceUtterance.objects.create(
            session=session,
            utterance_type=VoiceUtteranceType.PROMPT,
            spoken_summary=stale_text,
            spoken_summary_hash=spoken_summary_hash(stale_text),
            policy_version=session.policy_version,
        )
        pending_text = "Stale required status persisted before the cancellation."
        pending = VoiceUtterance.objects.create(
            session=session,
            utterance_type=VoiceUtteranceType.FAILURE_STATUS,
            spoken_summary=pending_text,
            spoken_summary_hash=spoken_summary_hash(pending_text),
            policy_version=session.policy_version,
        )
        challenge_text = "Stale required status captured by the challenge dispatch."
        challenge = VoiceUtterance.objects.create(
            session=session,
            utterance_type=VoiceUtteranceType.FAILURE_STATUS,
            spoken_summary=challenge_text,
            spoken_summary_hash=spoken_summary_hash(challenge_text),
            policy_version=session.policy_version,
        )
        session.timing_epoch = uuid.uuid4()
        session.timing_epoch_started_at = timezone.now()
        session.save(update_fields=["timing_epoch", "timing_epoch_started_at"])
        return str(stale.id), str(pending.id), str(challenge.id)

    return sync_to_async(_write, thread_sensitive=True)()


def _run_held_entry(entry):
    principal, settings, created = _coordinates()
    service = fixtures._AnswerTurnService()

    async def main(channel, ws, closed):
        from ai.core.voice import speech_dispatch

        routes = fixtures.routes_mod()
        # -- positive control: legitimate persisted speech really dispatches --
        await routes.submit_voice_turn(
            created["id"],
            routes.VoiceTurnRequest(
                transcript="Pump is vibrating.", item_id="item-1", progress_mode="off"
            ),
        )
        await fixtures._drain_once()
        assert _ws_creates(ws), "positive control: a real answer dispatches a create"
        ws.push({"type": "response.created", "response": {"id": "resp-answer"}})
        await fixtures._drain_once()
        ws.push({
            "type": "response.done",
            "response": {"id": "resp-answer", "status": "completed"},
        })
        await fixtures._drain_once()
        await routes.request_voice_prompt(
            created["id"],
            routes.VoicePromptRequest(kind="transcript_review", transcript="Fixture input"),
        )
        await fixtures._drain_once()
        prompt_creates = len(_ws_creates(ws))
        assert prompt_creates == 2, "positive control: the prompt dispatches a create too"
        ws.push({"type": "response.created", "response": {"id": "resp-prompt"}})
        await fixtures._drain_once()
        dispatcher = speech_dispatch._dispatchers[str(created["id"])]
        prior_record = dispatcher._current
        assert prior_record is not None and prior_record.state == "requested", (
            "positive control: the prompt is really playing (requested, not heard)"
        )
        assert prior_record.provider_state == "response_active"

        rows_before, epoch_before, operations_before = await _reload(created)
        prompt_row_id = next(
            row_id for row_id, row in rows_before.items() if row["utterance_type"] == "prompt"
        )
        answer_row_id = next(
            row_id
            for row_id, row in rows_before.items()
            if row["utterance_type"] == "completed_answer"
        )
        stale_id, pending_id, challenge_id = await _persist_rows(created, prompt_row_id)
        rows_before, epoch_before, operations_before = await _reload(created)
        assert rows_before[pending_id]["playback_state"] == "pending"
        generation_before = dispatcher.generation
        creates_before = len(_ws_creates(ws))
        calls_before = list(service.calls)

        # -- the real persistence continuation is held at the inspected seam --
        first_sync_started = asyncio.Event()
        release_first_sync = asyncio.Event()
        held = asyncio.Event()
        release_hold = asyncio.Event()
        state = {"first": False, "held": False}
        real_sync = routes.sync_to_async

        def gated_sync(function, *args, **kwargs):
            call = real_sync(function, *args, **kwargs)
            target_name = "_cancel" if entry == "cancel" else "invalidate_epoch"

            async def invoke(*call_args, **call_kwargs):
                name = getattr(function, "__name__", "")
                if name == target_name and not state["held"]:
                    state["held"] = True
                    held.set()
                    await release_hold.wait()
                    return await call(*call_args, **call_kwargs)
                if not state["first"] and name != target_name:
                    state["first"] = True
                    first_sync_started.set()
                    await release_first_sync.wait()
                    return await call(*call_args, **call_kwargs)
                return await call(*call_args, **call_kwargs)

            return invoke

        stale_record = None
        stale_task = None
        status_record = None
        status_task = None
        entry_task = None
        patched_sync = patch.object(routes, "sync_to_async", gated_sync)
        patched_sync.start()
        try:
            if entry == "cancel":
                entry_task = asyncio.create_task(routes.cancel_voice_playback(created["id"]))
            else:
                entry_task = asyncio.create_task(routes.suspend_voice_session(created["id"]))
            await asyncio.wait_for(first_sync_started.wait(), 5)
            # Stale-generation dispatch captured BEFORE the entry's
            # invalidation: it is mid the real replacement path (it already
            # asked the provider to cancel the live prompt, and it waits for
            # the provider's terminal -- the exact window the persistence hold
            # must not widen).
            from ai.core.voice import route_speech

            stale_row = await _row(created, stale_id)
            challenge_row = await _row(created, challenge_id)
            stale_task = asyncio.create_task(
                route_speech.speak_persisted(dispatcher, kind="prompt", utterance=stale_row)
            )
            status_task = asyncio.create_task(
                route_speech.speak_persisted(
                    dispatcher, kind="status", utterance=challenge_row, required=True
                )
            )
            for _ in range(200):
                if any(p.get("type") == "response.cancel" for p in ws.sent):
                    break
                await asyncio.sleep(0)
            assert any(p.get("type") == "response.cancel" for p in ws.sent), (
                "the stale dispatch must really enter the replacement path"
            )
            assert not stale_task.done(), "the stale dispatch is in flight, not finished"
            assert not status_task.done(), (
                "the required-status challenge is generation-captured, not finished"
            )
            assert dispatcher._current is prior_record, (
                "the stale dispatch must not have taken over the session yet"
            )
            release_first_sync.set()
            # Independently observe real LOCAL stop before any confirming
            # provider terminal is injected. Generation refusal is not stop.
            loop = asyncio.get_running_loop()
            stop_deadline = loop.time() + 1.5
            while prior_record.state != "canceled" and loop.time() < stop_deadline:
                await asyncio.sleep(0.005)
            assert prior_record.state == "canceled", (
                "RV03_LOCAL_STOP_WITHOUT_PROVIDER_TERMINAL: "
                f"state={prior_record.state}; reason={prior_record.reason}; "
                f"generation_before={generation_before}; "
                f"generation_now={dispatcher.generation}; "
                f"persistence_held={held.is_set()}; "
                "confirming_provider_terminal_injected=False"
            )
            await asyncio.wait_for(held.wait(), 5)
            # ---- persistence continuation HELD: speech must already be dead --
            assert prior_record.state == "canceled", (
                "real local stop must land before the persistence await resumes"
            )
            assert dispatcher.generation > generation_before, (
                "the dispatcher must be generation-invalidated before the persistence await"
            )
            rows_held, epoch_held, _operations_held = await _reload(created)
            assert rows_held[prompt_row_id]["playback_state"] == "canceled", (
                "the real playback callback must record the local stop while held"
            )
            assert rows_held[pending_id]["playback_state"] == "pending", (
                "the ORM closure is still held: no durable write before release"
            )
            # Truthful intermediate (exact source schedule): the queued stale
            # required-status dispatch is still blocked behind the shared
            # provider-replacement lock while the persistence closure is held
            # (its DispatchRecord exists but its pre-send fence runs only
            # after that lock frees), so its row is still pending here and is
            # finalized only after release. The rejected "canceled while
            # held" intermediate was never reached and is not evidence; the
            # final durable contract below is unchanged.
            assert rows_held[challenge_id]["playback_state"] == "pending", (
                "queued stale required status is still pending while held"
            )
            if entry == "suspend":
                assert epoch_held == epoch_before, (
                    "the epoch closure is still held: it runs only after release"
                )
            assert len(_ws_creates(ws)) == creates_before, (
                "no stale-generation provider response.create while persistence is held"
            )
            # Only AFTER the local-stop and held-persistence observations may
            # the provider confirm the earlier replacement cancel (real
            # playback callback): both stale generations are then refused at
            # the pre-send gate.
            ws.push({"type": "response.cancelled", "response": {"id": "resp-prompt"}})
            await fixtures._drain_once()
            release_hold.set()
            result = await asyncio.wait_for(entry_task, 5)
            stale_record = await asyncio.wait_for(stale_task, 5)
            status_record = await asyncio.wait_for(status_task, 5)
            assert stale_record.state == "canceled", (
                "a stale-generation dispatch is interrupted, never sent and never heard"
            )
            assert stale_record.reason == "invalidated_before_send"
            assert status_record.state == "canceled", (
                "a stale required-status dispatch is interrupted, never sent"
            )
            assert status_record.reason == "invalidated_before_send"
            # Late/stale provider audio is excluded; interrupted is never completed.
            ws.push({
                "type": "response.done",
                "response": {"id": "resp-prompt", "status": "completed"},
            })
            ws.push({
                "type": "response.done",
                "response": {"id": "resp-foreign", "status": "completed"},
            })
            await fixtures._drain_once()
            # Late/foreign terminals never resurrect a cancel or complete an
            # interrupted dispatch: every observed record stays canceled.
            assert prior_record.state == "canceled", (
                "a late completion never resurrects the local stop"
            )
            assert stale_record.state == "canceled"
            assert status_record.state == "canceled"
            if entry == "cancel":
                assert result["id"] == str(created["id"])
                assert result["canceled_utterances"] >= 1, (
                    "the original ORM closure must touch the pending required status"
                )
            else:
                assert result == {"id": str(created["id"]), "suspended": True}

            rows_after, epoch_after, operations_after = await _reload(created)
            # Durable canceled state via the released original ORM closure and
            # the real playback reconciliation -- exact identity preserved.
            assert rows_after[pending_id]["playback_state"] == "canceled"
            assert rows_after[pending_id]["utterance_type"] == "failure_status"
            assert rows_after[challenge_id]["playback_state"] == "canceled", (
                "interrupted is never completed (stale required status)"
            )
            assert rows_after[stale_id]["playback_state"] == "canceled", (
                "interrupted is never completed"
            )
            assert rows_after[prompt_row_id]["playback_state"] == "canceled"
            assert rows_after[answer_row_id]["playback_state"] == "done"
            for row_id, before in rows_before.items():
                assert _identity(rows_after[row_id]) == _identity(before), (
                    "identity/text/hash/session/input fields are preserved exactly"
                )
            if entry == "suspend":
                assert epoch_after is None, "the epoch closure executed after release"
            else:
                assert epoch_after == epoch_before, "cancel never touches the timing epoch"
            assert operations_after == operations_before == 0, (
                "no approval/effect-ledger consumption"
            )
            assert service.calls == calls_before, "the business call is never duplicated"
            assert len(_ws_creates(ws)) == creates_before, "no speech replay after the entry"
        finally:
            release_hold.set()
            release_first_sync.set()
            pending_tasks = (entry_task, stale_task, status_task)
            tasks = [task for task in pending_tasks if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            patched_sync.stop()

    fixtures._run_loop(principal, settings, main, turn_service=service)


async def _row(created, row_id):
    def _read():
        from voice.models import VoiceUtterance

        return VoiceUtterance.objects.get(pk=row_id, session_id=created["id"])

    return await sync_to_async(_read, thread_sensitive=True)()


def test_cancel_persistence_hold_stops_speech_before_sql_release():
    """The cancel's held ORM bulk update must not delay real speech stop."""
    _run_held_entry("cancel")


def test_suspend_epoch_hold_stops_speech_before_epoch_persistence():
    """Foreground loss stops speech before its EARLIER epoch persistence await."""
    _run_held_entry("suspend")


def test_uncanceled_fresh_speech_still_dispatches():
    """Positive control: without a cancellation, fresh speech keeps playing."""
    principal, settings, created = _coordinates()

    async def main(channel, ws, closed):
        from ai.core.voice import speech_dispatch

        routes = fixtures.routes_mod()
        for index in range(2):
            await routes.request_voice_prompt(
                created["id"],
                routes.VoicePromptRequest(
                    kind="transcript_review", transcript=f"Fixture input {index}"
                ),
            )
            await fixtures._drain_once()
            ws.push({"type": "response.created", "response": {"id": f"resp-{index}"}})
            await fixtures._drain_once()
            ws.push({
                "type": "response.done",
                "response": {"id": f"resp-{index}", "status": "completed"},
            })
            await fixtures._drain_once()
        assert len(_ws_creates(ws)) == 2, "fresh uncanceled speech remains observable"
        dispatcher = speech_dispatch._dispatchers[str(created["id"])]
        assert dispatcher.generation == 0, "no cancellation means no invalidation"
        assert not any(p.get("type") == "response.cancel" for p in ws.sent)
        rows, _epoch, operations = await _reload(created)
        assert rows and all(row["playback_state"] == "done" for row in rows.values())
        assert operations == 0

    fixtures._run_loop(principal, settings, main)


def test_late_cancel_continuation_never_targets_fresh_provider_owner():
    """RV03: the held cancel continuation must never cancel a FRESH owner.

    The cancel's local stop and ``cancel_sent`` reservation land synchronously,
    but its one best-effort untargeted ``response.cancel`` is sent only after
    the real playback callback returns. If a real provider terminal releases
    the captured record's provider ownership inside that window and a fresh
    response then owns the provider, the stale continuation must send NO
    control frame: an untargeted ``response.cancel`` there is stale control
    traffic aimed at the fresh owner (late-owner crossing), not a live audio
    cancel. The captured-record ownership check must survive the await and
    must never consult or cancel a newer current record.
    """
    principal, settings, created = _coordinates()

    async def main(channel, ws, closed):
        from ai.core.voice import speech_dispatch

        routes = fixtures.routes_mod()
        await routes.request_voice_prompt(
            created["id"],
            routes.VoicePromptRequest(kind="transcript_review", transcript="Fixture input"),
        )
        await fixtures._drain_once()
        assert _ws_creates(ws), "the old prompt really dispatches a create"
        ws.push({"type": "response.created", "response": {"id": "resp-old"}})
        await fixtures._drain_once()
        dispatcher = speech_dispatch._dispatchers[str(created["id"])]
        old_record = dispatcher._current
        assert old_record is not None and old_record.state == "requested"
        assert old_record.provider_state == "response_active"

        held = asyncio.Event()
        release_hold = asyncio.Event()
        real_playback = dispatcher._on_playback

        async def held_playback(utterance_id, state):
            result = real_playback(utterance_id, state)
            if inspect.isawaitable(result):
                await result
            if utterance_id == old_record.utterance_id and state == "canceled":
                held.set()
                await release_hold.wait()

        cancel_task = None
        dispatcher._on_playback = held_playback
        try:
            cancel_task = asyncio.create_task(dispatcher.cancel(reason="barge_in"))
            await asyncio.wait_for(held.wait(), 5)
            # The synchronous claim already landed while the callback is
            # held: immediate local stop plus the unique cancel reservation.
            assert old_record.state == "canceled"
            assert old_record.cancel_sent is True
            assert not any(p.get("type") == "response.cancel" for p in ws.sent), (
                "the reservation precedes the provider send"
            )
            # A REAL old-provider terminal releases the captured ownership...
            ws.push({
                "type": "response.done",
                "response": {"id": "resp-old", "status": "completed"},
            })
            await fixtures._drain_once()
            assert old_record.provider_state == "released"
            assert dispatcher._owner is None
            # ...and then a fresh dispatch binds a NEW provider owner.
            await routes.request_voice_prompt(
                created["id"],
                routes.VoicePromptRequest(
                    kind="transcript_review", transcript="Fixture fresh input"
                ),
            )
            await fixtures._drain_once()
            ws.push({"type": "response.created", "response": {"id": "resp-fresh"}})
            await fixtures._drain_once()
            fresh_record = dispatcher._current
            assert fresh_record is not None and fresh_record is not old_record
            assert fresh_record.state == "requested"
            assert fresh_record.provider_state == "response_active"
            assert dispatcher._owner is fresh_record
            creates_before_release = len(_ws_creates(ws))

            # Release the held playback callback: the old cancel continuation
            # resumes carrying its stale send reservation.
            release_hold.set()
            await asyncio.wait_for(cancel_task, 5)
            await fixtures._drain_once()
            late_cancels = [p for p in ws.sent if p.get("type") == "response.cancel"]
            assert not late_cancels, (
                "RV03_LATE_CANCEL_CROSSES_FRESH_OWNER: the held cancel continuation "
                "sent stale untargeted response.cancel frames after a fresh provider "
                f"owner bound: {late_cancels}"
            )
            # The captured-record ownership check survives the await: the old
            # record keeps its honest canceled local state and the fresh owner
            # is untouched (no re-send, no replay, nothing re-targeted).
            assert old_record.state == "canceled"
            assert old_record.cancel_sent is True
            assert fresh_record.state == "requested"
            assert fresh_record.provider_state == "response_active"
            assert dispatcher._owner is fresh_record
            assert len(_ws_creates(ws)) == creates_before_release, (
                "no replayed response.create after the released continuation"
            )
        finally:
            release_hold.set()
            if cancel_task is not None and not cancel_task.done():
                cancel_task.cancel()
            if cancel_task is not None:
                await asyncio.gather(cancel_task, return_exceptions=True)
            dispatcher._on_playback = real_playback

    fixtures._run_loop(principal, settings, main)
