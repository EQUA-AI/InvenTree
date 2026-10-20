"""Private native boundary: audio truth versus exact persisted playback audit."""

import asyncio
import copy
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from ai.core.auth import principal_context
from ai.core.tests.test_realtime_session_api import _principal, _settings
from ai.core.tests.test_voice_route_speech_integration import _AnswerTurnService, _run_loop
from ai.core.tests.test_voice_speech_dispatch import FakeWs, _drain_once
from ai.core.tests.test_voice_status_phrases import _session_for
from ai.core.voice import route_speech, routes, speech_dispatch
from ai.core.voice.gateway import VoiceLiveChannel
from ai.core.voice.speech import ExactSpeechViolation, spoken_summary_hash
from ai.core.voice.wire import VoiceSpokenPayload
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection
from django.db.models.query import QuerySet
from fastapi import HTTPException
from voice.models import (
    PlaybackState,
    VoiceSession,
    VoiceSessionState,
    VoiceUtterance,
    VoiceUtteranceType,
)
from voice.services import realtime


def fixture_rows():
    assert connection.settings_dict["ENGINE"] == "django.db.backends.sqlite3"
    assert Path(str(connection.settings_dict["NAME"])).is_relative_to("/scratch")
    actor = get_user_model().objects.create_user(
        username="rv12-private-" + uuid.uuid4().hex,
        password=None,
        is_staff=False,
        is_superuser=False,
    )
    assert not actor.is_staff and not actor.is_superuser
    session = VoiceSession.objects.create(
        owner=actor,
        thread_id="rv12-private-thread",
        scope_key="rv12-private-scope",  # gitleaks:allow
        scope_hash=spoken_summary_hash("rv12-private-scope"),
        state=VoiceSessionState.ACTIVE,
        policy_version="rv12-policy",
        consent_version="rv12-fixture",
    )
    text = "Inspect the inlet filter before continuing. Keep this exact wording."
    rows = [
        VoiceUtterance.objects.create(
            session=session,
            turn_id="rv12-turn-" + str(index),
            utterance_type=VoiceUtteranceType.INTERIM_STATUS,
            spoken_summary=text,
            spoken_summary_hash=spoken_summary_hash(text),
            policy_version="rv12-policy",
            playback_state=PlaybackState.PENDING,
        )
        for index in [1, 2]
    ]
    return session, rows


def scenario(mode):
    session, utterances = fixture_rows()
    first, second = utterances
    before = VoiceUtterance.objects.filter(pk=first.pk).values().get()
    session_before = VoiceSession.objects.filter(pk=session.pk).values().get()
    assert set(before) == {field.attname for field in VoiceUtterance._meta.concrete_fields}
    terminal_updates = []
    original_update = QuerySet.update

    def update(queryset, **kwargs):
        if kwargs.get("playback_state") == PlaybackState.DONE:
            terminal_updates.append(kwargs["playback_state"])
            if mode in ["fault", "fault_then_closed"]:
                raise DatabaseError("injected owned audit UPDATE refusal")
        return original_update(queryset, **kwargs)

    async def run():
        channel = VoiceLiveChannel(str(session.pk))
        socket = FakeWs()
        channel._ws = socket
        dispatcher = route_speech.session_dispatcher(session, channel=channel)
        callback = dispatcher._on_playback
        completed_callback = asyncio.Event()
        calls = []

        async def observe(utterance_id, state):
            calls.append((utterance_id, state))
            try:
                return await callback(utterance_id, state)
            finally:
                if state == "done":
                    completed_callback.set()

        dispatcher._on_playback = observe
        reader = asyncio.create_task(channel._drain())
        channel._drain_task = reader
        try:
            if mode == "invalid_hash":
                invalid = copy.copy(first)
                invalid.spoken_summary_hash = spoken_summary_hash("not the persisted words")
                with pytest.raises(ExactSpeechViolation):
                    await route_speech.speak_persisted(
                        dispatcher, kind="status", utterance=invalid, required=True
                    )
                assert socket.creates() == [] and calls == []
                assert dispatcher.snapshot()["state"] is None
                return
            record = await route_speech.speak_persisted(
                dispatcher, kind="status", utterance=first, required=True
            )
            assert record.state == "requested" and len(socket.creates()) == 1
            socket.push({"type": "response.created", "response": {"id": "rv12-native-response"}})
            socket.push({
                "type": "response.done",
                "response": {"id": "rv12-native-response", "status": "completed"},
            })
            await asyncio.wait_for(completed_callback.wait(), timeout=2)
            await _drain_once()
            saved = await sync_to_async(
                lambda: VoiceUtterance.objects.filter(pk=first.pk).values().get(),
                thread_sensitive=True,
            )()
            assert set(saved) == set(before)
            assert all(
                saved[key] == value
                for key, value in before.items()
                if key not in ["playback_state", "updated_at"]
            )
            assert record.state == "completed" and record.reason == "completed"
            assert route_speech.dispatch_state_for(record) == "done"
            assert calls == [(str(first.pk), "requested"), (str(first.pk), "done")]
            assert terminal_updates == [PlaybackState.DONE]
            assert len(socket.creates()) == 1 and not channel.has_active_app_response()
            report = dispatcher.snapshot()
            assert report["state"] == "completed" and report["session_id"] == str(session.pk)
            if mode in ["fault", "fault_then_closed"]:
                assert saved["playback_state"] == PlaybackState.REQUESTED
            else:
                assert saved["playback_state"] == PlaybackState.DONE
            if mode == "fault_then_closed":
                await dispatcher.close(reason="session_ended")
                refused = await route_speech.speak_persisted(
                    dispatcher, kind="status", utterance=second, required=True
                )
                assert refused.state == "failed" and refused.reason == "channel_closed"
                assert len(socket.creates()) == 1
            if mode == "fault":
                # PROPOSED content-free reporting witness. Its absence is a
                # missing uncertainty contract, NOT a false audio terminal.
                assert report.get("playback_audit_state") == "uncertain", report
        finally:
            await channel.close()
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            speech_dispatch.release_dispatcher(str(session.pk))

    with patch.object(QuerySet, "update", update):
        asyncio.run(run())
    after = VoiceUtterance.objects.filter(pk=first.pk).values().get()
    if mode == "invalid_hash":
        assert after == before
    else:
        assert all(
            after[key] == value
            for key, value in before.items()
            if key not in ["playback_state", "updated_at"]
        )
    assert VoiceSession.objects.filter(pk=session.pk).values().get() == session_before
    assert VoiceUtterance.objects.filter(session=session).count() == 2


def test_rv12_real_terminal_write_preserves_exact_durable_row():
    scenario("success")


def test_rv12_failed_terminal_write_reports_uncertainty_without_false_audio_failure():
    scenario("fault")


def test_rv12_failed_audit_does_not_bypass_existing_closed_dispatch_fence():
    scenario("fault_then_closed")


def test_rv12_persisted_hash_refusal_still_precedes_any_provider_effect():
    scenario("invalid_hash")


# Additional actual serialized-consumer probes; no authenticated HTTP or AI claim.


def ordinary_user():
    actor = get_user_model().objects.create_user(
        username="rv12-route-" + uuid.uuid4().hex, password=None, is_staff=False, is_superuser=False
    )
    assert not actor.is_staff and not actor.is_superuser
    return actor


@pytest.mark.parametrize("surface", ["prompt", "turn", "presentation"])
def test_rv12_actual_returned_surface_exposes_confirmed_requested_audit(surface):
    actor = ordinary_user()
    settings = _settings([actor.pk], FEATURE_VOICE_FOREGROUND_SESSION=True)
    principal = _principal(actor)
    created = _session_for(principal, settings)
    service = _AnswerTurnService(turn_id="rv12-wire-" + uuid.uuid4().hex)

    async def main(channel, socket, closed):
        if surface == "prompt":
            output = await routes.request_voice_prompt(
                created["id"],
                routes.VoicePromptRequest(
                    kind="transcript_review", transcript="Inspect the inlet filter."
                ),
            )
        else:
            result = await routes.submit_voice_turn(
                created["id"],
                routes.VoiceTurnRequest(
                    transcript="Inspect the inlet filter.", item_id="rv12-item-" + uuid.uuid4().hex
                ),
            )
            if surface == "turn":
                output = result["spoken"]
                assert len(service.calls) == 1
            else:
                presentation = result["presentation"]
                result = await routes.presentation_command(
                    created["id"],
                    routes.PresentationCommandRequest(
                        id=presentation["id"],
                        source_hash=presentation["source_hash"],
                        index=0,
                        presentation_command="repeat",
                    ),
                )
                output = result["spoken"]
                assert len(service.calls) == 1
        assert output["playback_state"] == "requested"
        stored = await sync_to_async(
            lambda: VoiceUtterance.objects.get(pk=output["utterance_id"]), thread_sensitive=True
        )()
        assert stored.playback_state == PlaybackState.REQUESTED
        assert stored.spoken_summary == output["spoken_summary"]
        assert stored.spoken_summary_hash == output["spoken_summary_hash"]
        assert output.get("playback_audit_state") == "confirmed", output
        coerced = VoiceSpokenPayload(**output).model_dump(mode="json")
        assert coerced["playback_audit_state"] == "confirmed"

    _run_loop(principal, settings, main, turn_service=service, auto_ack=True)


def session_status_scenario(foreign=False):
    session, utterances = fixture_rows()
    first = utterances[0]
    principal = _principal(session.owner)
    settings = _settings([session.owner_id])
    session.scope_key = principal.scope
    session.thread_id = str(uuid.uuid4())
    session.save(update_fields=["scope_key", "thread_id", "updated_at"])
    foreign_principal = _principal(ordinary_user()) if foreign else None
    original_update = QuerySet.update
    attempts = []

    def update(queryset, **kwargs):
        if kwargs.get("playback_state") == PlaybackState.DONE:
            attempts.append("done")
            raise DatabaseError("injected owned late audit UPDATE refusal")
        return original_update(queryset, **kwargs)

    async def run():
        token = principal_context.set(principal)
        channel = VoiceLiveChannel(str(session.pk))
        socket = FakeWs()
        channel._ws = socket
        dispatcher = route_speech.session_dispatcher(session, channel=channel)
        original = dispatcher._on_playback
        returned = asyncio.Event()

        async def observe(identity, state):
            try:
                return await original(identity, state)
            finally:
                if state == "done":
                    returned.set()

        dispatcher._on_playback = observe
        reader = asyncio.create_task(channel._drain())
        channel._drain_task = reader
        try:
            record = await route_speech.speak_persisted(
                dispatcher, kind="status", utterance=first, required=True
            )
            initial = await routes.get_voice_session(str(session.pk))
            assert initial["id"] == str(session.pk)
            socket.push({"type": "response.created", "response": {"id": "rv12-late-status"}})
            socket.push({
                "type": "response.done",
                "response": {"id": "rv12-late-status", "status": "completed"},
            })
            await asyncio.wait_for(returned.wait(), timeout=2)
            await _drain_once()
            saved = await sync_to_async(
                lambda: VoiceUtterance.objects.get(pk=first.pk), thread_sensitive=True
            )()
            assert saved.playback_state == PlaybackState.REQUESTED
            assert record.state == "completed" and route_speech.dispatch_state_for(record) == "done"
            assert attempts == ["done"] and len(socket.creates()) == 1
            if foreign:
                other = principal_context.set(foreign_principal)
                try:
                    with pytest.raises(HTTPException) as denied:
                        await routes.get_voice_session(str(session.pk))
                    expected = routes._session_error(
                        realtime.VoiceSessionForbidden("no such session")
                    )
                    assert denied.value.status_code == expected.status_code
                    assert denied.value.detail == expected.detail
                finally:
                    principal_context.reset(other)
            else:
                late = await routes.get_voice_session(str(session.pk))
                assert late.get("speech_utterance_id") == str(first.pk), late
                assert late.get("speech_playback_state") == "done", late
                assert late.get("speech_audit_state") == "uncertain", late
        finally:
            await channel.close()
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            speech_dispatch.release_dispatcher(str(session.pk))
            principal_context.reset(token)

    with (
        patch.object(routes, "get_settings", return_value=settings),
        patch.object(QuerySet, "update", update),
    ):
        asyncio.run(run())


def test_rv12_owner_status_serializes_late_terminal_audit_uncertainty():
    session_status_scenario()


def test_rv12_foreign_status_cannot_observe_another_owners_audit():
    session_status_scenario(foreign=True)


@pytest.mark.parametrize("outcome", ["none", "timeout", "malformed"])
def test_rv12_legacy_or_unavailable_audit_never_forges_durable_confirmation(outcome):
    async def run():
        channel = VoiceLiveChannel("rv12-callback-" + uuid.uuid4().hex)
        socket = FakeWs()
        channel._ws = socket

        async def callback(identity, state):
            if outcome == "timeout":
                await asyncio.Event().wait()
            if outcome == "malformed":
                return {"unexpected": True}
            return None

        dispatcher = speech_dispatch.SessionSpeechDispatcher(
            "rv12-standalone", channel, on_playback=callback, close_timeout_s=0.02
        )
        try:
            record = await dispatcher.speak(
                kind="status",
                text="Inspect the filter.",
                spoken_hash=spoken_summary_hash("Inspect the filter."),
                utterance_id=str(uuid.uuid4()),
                required=True,
            )
            assert record.state == "requested" and len(socket.creates()) == 1
            report = dispatcher.snapshot()
            expected = "unavailable" if outcome == "none" else "uncertain"
            assert report.get("playback_audit_state") == expected, report
        finally:
            await channel.close()

    asyncio.run(run())


def test_rv12_initial_requested_update_failure_survives_real_prompt_serialization():
    actor = ordinary_user()
    settings = _settings([actor.pk])
    principal = _principal(actor)
    created = _session_for(principal, settings)
    update = QuerySet.update
    attempts = []

    def refuse_requested(queryset, **kwargs):
        if kwargs.get("playback_state") == PlaybackState.REQUESTED:
            attempts.append("requested")
            raise DatabaseError("owned requested audit write refused")
        return update(queryset, **kwargs)

    async def main(channel, socket, closed):
        with patch.object(QuerySet, "update", refuse_requested):
            output = await routes.request_voice_prompt(
                created["id"],
                routes.VoicePromptRequest(
                    kind="transcript_review", transcript="Inspect the filter."
                ),
            )
        stored = await sync_to_async(
            lambda: VoiceUtterance.objects.get(pk=output["utterance_id"]), thread_sensitive=True
        )()
        assert stored.playback_state == PlaybackState.PENDING
        assert stored.spoken_summary == output["spoken_summary"]
        assert stored.spoken_summary_hash == output["spoken_summary_hash"]
        assert output["playback_state"] == "requested"
        assert attempts == ["requested"] and len(socket.sent) == 1
        assert output.get("playback_audit_state") == "uncertain", output
        assert (
            VoiceSpokenPayload(**output).model_dump(mode="json")["playback_audit_state"]
            == "uncertain"
        )

    _run_loop(principal, settings, main, auto_ack=True)


def test_rv12_sticky_canceled_storage_cannot_acknowledge_done_persistence():
    session, utterances = fixture_rows()
    first = utterances[0]
    first.playback_state = PlaybackState.CANCELED
    first.save(update_fields=["playback_state", "updated_at"])
    before = {
        field.attname: copy.deepcopy(getattr(first, field.attname))
        for field in first._meta.concrete_fields
    }
    verdict = asyncio.run(route_speech._reconcile_playback(session)(str(first.pk), "done"))
    first.refresh_from_db()
    assert first.playback_state == PlaybackState.CANCELED
    assert {
        field.attname: copy.deepcopy(getattr(first, field.attname))
        for field in first._meta.concrete_fields
    } == before
    assert verdict == "uncertain", verdict


def test_rv12_old_requested_callback_cannot_erase_new_terminal_audit_uncertainty():
    session, utterances = fixture_rows()
    first = utterances[0]
    update = QuerySet.update
    attempts = []

    def refuse_done(queryset, **kwargs):
        if kwargs.get("playback_state") == PlaybackState.DONE:
            attempts.append("done")
            raise DatabaseError("owned latest terminal audit refused")
        return update(queryset, **kwargs)

    async def run():
        channel = VoiceLiveChannel(str(session.pk))
        socket = FakeWs()
        channel._ws = socket
        dispatcher = route_speech.session_dispatcher(session, channel=channel)
        original = dispatcher._on_playback
        requested_written = asyncio.Event()
        release_requested = asyncio.Event()
        done_returned = asyncio.Event()

        async def observe(identity, state):
            verdict = await original(identity, state)
            if state == "requested":
                requested_written.set()
                await release_requested.wait()
            if state == "done":
                done_returned.set()
            return verdict

        dispatcher._on_playback = observe
        reader = asyncio.create_task(channel._drain())
        channel._drain_task = reader
        work = None
        try:
            work = asyncio.create_task(
                route_speech.speak_persisted(
                    dispatcher, kind="status", utterance=first, required=True
                )
            )
            await asyncio.wait_for(requested_written.wait(), timeout=2)
            assert dispatcher._current.state == "requested"
            socket.push({"type": "response.created", "response": {"id": "rv12-overlap"}})
            socket.push({
                "type": "response.done",
                "response": {"id": "rv12-overlap", "status": "completed"},
            })
            await asyncio.wait_for(done_returned.wait(), timeout=2)
            await _drain_once()
            assert dispatcher._current.state == "completed"
            assert attempts == ["done"] and len(socket.creates()) == 1
            release_requested.set()
            record = await asyncio.wait_for(work, timeout=2)
            assert record.state == "completed"
            saved = await sync_to_async(
                lambda: VoiceUtterance.objects.get(pk=first.pk), thread_sensitive=True
            )()
            assert saved.playback_state == PlaybackState.REQUESTED
            assert saved.spoken_summary == first.spoken_summary
            assert saved.spoken_summary_hash == first.spoken_summary_hash
            assert dispatcher.snapshot().get("playback_audit_state") == "uncertain"
        finally:
            release_requested.set()
            if work is not None:
                if not work.done():
                    work.cancel()
                await asyncio.gather(work, return_exceptions=True)
            await channel.close()
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            speech_dispatch.release_dispatcher(str(session.pk))

    with patch.object(QuerySet, "update", refuse_done):
        asyncio.run(run())


def test_rv12_actual_decision_reply_speech_returns_audit_separately():
    from ai.core.decisions.coordinator import DecisionReply
    from ai.core.decisions.routes import speak_reply

    actor = ordinary_user()
    settings = _settings([actor.pk])
    principal = _principal(actor)
    created = _session_for(principal, settings)

    async def main(channel, socket, closed):
        session = await sync_to_async(
            lambda: VoiceSession.objects.get(pk=created["id"]), thread_sensitive=True
        )()
        decision, output = await speak_reply(
            session, DecisionReply(spoken="Inspect the filter."), None, include_spoken=True
        )
        assert decision is None
        assert output is not None and output["playback_state"] == "requested"
        saved = await sync_to_async(
            lambda: VoiceUtterance.objects.get(pk=output["utterance_id"]), thread_sensitive=True
        )()
        assert saved.playback_state == PlaybackState.REQUESTED
        assert saved.spoken_summary == output["spoken_summary"]
        assert saved.spoken_summary_hash == output["spoken_summary_hash"]
        assert len(socket.sent) == 1
        assert output.get("playback_audit_state") == "confirmed", output

    _run_loop(principal, settings, main, auto_ack=True)


def test_rv12_actual_complete_generator_emits_optional_audit_response_contract():
    import base64

    from aichat.management.commands.generate_wire_contract import Command
    from django.utils import translation

    with translation.override("en"):
        generated = Command()._render()
    # Real generator output is exported through the existing captured stdout;
    # no transport/report schema modification and no stand-in application.
    print("\nRV12_FULL_GENERATOR_EXPORT=" + base64.b64encode(generated.encode()).decode())
    assert "  playback_audit_state?: 'unavailable' | 'confirmed' | 'uncertain';" in generated
    assert "  speech_utterance_id?: string | null;" in generated
    assert "  speech_playback_state?: string | null;" in generated
    assert "  speech_audit_state?: 'unavailable' | 'confirmed' | 'uncertain' | null;" in generated
