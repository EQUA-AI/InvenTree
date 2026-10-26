"""WebRTC lifecycle hints release scheduling, never playback/action authority."""

import pytest
from ai.core.auth import principal_context
from ai.core.tests import test_voice_route_speech_integration as fixtures
from ai.core.tests.focus_test_db import ensure_island_tables
from ai.core.voice import route_speech, routes
from asgiref.sync import sync_to_async
from fastapi import HTTPException
from pydantic import ValidationError
from voice.models import VoiceUtterance


@pytest.fixture(scope="module", autouse=True)
def _database():
    ensure_island_tables()
    yield
    route_speech.clear_session_state()


def test_ten_real_route_turns_with_browser_only_lifecycle_do_not_close_input_or_replay():
    user = fixtures._user()
    principal = fixtures._principal(user)
    settings = fixtures._settings([user.pk], FEATURE_VOICE_WRITE_CONFIRMATION=False)
    session = fixtures._session_for(principal, settings)
    service = fixtures._AnswerTurnService()

    async def main(channel, ws, closed):
        for n in range(10):
            service.turn_id = f"browser-turn-{n}"
            body = await routes.submit_voice_turn(
                session["id"],
                routes.VoiceTurnRequest(
                    transcript="Hello",
                    item_id=f"item-{n}",
                    progress_mode="off",
                ),
            )
            assert body["spoken"]["playback_state"] == "requested"
            for event_type, status in (
                ("response.created", "in_progress"),
                ("response.done", "completed"),
            ):
                result = await routes.observe_voice_lifecycle(
                    session["id"],
                    routes.VoiceLifecycleObservation(
                        type=event_type,
                        response_id=f"browser-response-{n}",
                        status=status,
                    ),
                )
                assert result["observed"] is True
                assert result["channel_closed"] is False
            state = await sync_to_async(
                lambda uid=body["spoken"]["utterance_id"]: (
                    VoiceUtterance.objects.get(pk=uid).playback_state
                )
            )()
            assert state == "requested", "browser observations cannot certify delivery"
        assert len(service.calls) == 10
        assert len(fixtures._creates(ws)) == 10
        assert not channel.has_active_app_response()
        assert closed == []

    fixtures._run_loop(principal, settings, main, turn_service=service)


def test_lifecycle_observation_checks_ownership_and_cannot_mint_a_dispatcher():
    owner = fixtures._user()
    principal = fixtures._principal(owner)
    settings = fixtures._settings([owner.pk], FEATURE_VOICE_WRITE_CONFIRMATION=False)
    session = fixtures._session_for(principal, settings)
    foreign = fixtures._principal(fixtures._user())

    async def main(channel, ws, closed):
        request = routes.VoiceLifecycleObservation(type="response.created", response_id="foreign")
        assert await routes.observe_voice_lifecycle(session["id"], request) == {"observed": False}
        token = principal_context.set(foreign)
        try:
            with pytest.raises(HTTPException) as error:
                await routes.observe_voice_lifecycle(session["id"], request)
            assert error.value.status_code == 404
        finally:
            principal_context.reset(token)
        assert ws.sent == []

    fixtures._run_loop(principal, settings, main)


def test_lifecycle_schema_refuses_audio_text_and_decision_fields():
    for field in ("transcript", "audio", "decision_id", "spoken_summary", "confirmed"):
        with pytest.raises(ValidationError):
            routes.VoiceLifecycleObservation(
                type="response.done", response_id="resp-1", **{field: "untrusted"}
            )
