"""WS4-T3/T5/T7 route-level wiring tests against the real handlers.

These call the FastAPI voice handlers directly under a boundary principal,
exercising the real Django-backed session service, the SDP relay (with a
fake provider channel), and the turn bridge (with a fake normalized turn
service). No provider network access occurs.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import sys  # noqa: E402
import types  # noqa: E402
from pathlib import Path  # noqa: E402

import ai.core  # noqa: E402
import pytest  # noqa: E402
from django.core.management import call_command  # noqa: E402
from fastapi import HTTPException  # noqa: E402

# Avoid eagerly importing every provider workflow when ai.core.app loads
# (same isolation the typed-chat regression suite uses).
_workflows_package = types.ModuleType("ai.core.workflows")
_workflows_package.__path__ = [str(Path(ai.core.__file__).resolve().parent / "workflows")]
sys.modules.setdefault("ai.core.workflows", _workflows_package)

import ai.core.app as ai_app  # noqa: E402
from ai.core.auth import AIPrincipal, principal_context  # noqa: E402
from ai.core.config import Settings  # noqa: E402
from ai.core.turn_service import NormalizedTurnResult  # noqa: E402
from ai.core.voice import routes  # noqa: E402
from ai.core.voice.routes import (  # noqa: E402
    VoiceSdpRequest,
    VoiceSessionCreateRequest,
    VoiceTurnRequest,
    create_voice_session,
    end_voice_session,
    get_voice_session,
    relay_sdp,
    submit_voice_turn,
)

HOST_SETTING = "aimms-foundry.services.ai.azure.com"


@pytest.fixture(scope="module", autouse=True)
def _database():
    call_command("migrate", verbosity=0, interactive=False)
    yield


def _user():
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username=f"tech-{uuid.uuid4().hex[:8]}", password="unused"
    )


def _principal(user) -> AIPrincipal:
    return AIPrincipal(
        subject=f"user:{user.pk}",
        actor=f"user:{user.pk}",
        user_pk=str(user.pk),
        username=user.username,
        authentication_method="session",
        scope="site:pilot",
        policy_version="policy-v1",
        is_staff=False,
        is_superuser=False,
    )


def _settings(pilot_ids, **over) -> Settings:
    aliased = {
        "FEATURE_VOICE_LIVE": True,
        "AZURE_VOICELIVE_ENDPOINT": HOST_SETTING,
        "AIMMS_VOICE_PILOT_USER_IDS": pilot_ids,
    }
    aliased.update(over)
    return Settings(_env_file=None, **aliased)


def _run(principal, coroutine_factory, settings):
    with patch.object(routes, "get_settings", return_value=settings):
        token = principal_context.set(principal)
        try:
            return asyncio.run(coroutine_factory())
        finally:
            principal_context.reset(token)


def _expect_http(principal, coroutine_factory, settings, status, detail=None):
    with pytest.raises(HTTPException) as excinfo:
        _run(principal, coroutine_factory, settings)
    assert excinfo.value.status_code == status
    if detail is not None:
        assert excinfo.value.detail == detail
    return excinfo.value


def test_feature_flag_off_hides_every_voice_route():
    user = _user()
    settings = _settings([user.pk], FEATURE_VOICE_LIVE=False)
    _expect_http(
        _principal(user),
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
        404,
        "VOICE_SESSION_UNAVAILABLE",
    )


def test_non_pilot_user_sees_the_same_absence():
    user = _user()
    settings = _settings([])  # empty cohort admits nobody
    _expect_http(
        _principal(user),
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
        404,
        "VOICE_SESSION_UNAVAILABLE",
    )


def test_create_get_end_lifecycle_and_credential_free_payload():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)

    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
    )
    assert created["state"] == "created"
    assert created["thread_id"]
    assert set(created) == {
        "id",
        "state",
        "thread_id",
        "transport",
        "transports_allowed",
        "webrtc_preview",
        "turn_count",
        "policy_version",
        "terminal_reason",
    }, "payload grew a field; verify it cannot carry provider authority"

    fetched = _run(principal, lambda: get_voice_session(created["id"]), settings)
    assert fetched["id"] == created["id"]

    # Another authenticated user cannot even learn the session exists.
    stranger = _user()
    _expect_http(
        _principal(stranger),
        lambda: get_voice_session(created["id"]),
        _settings([stranger.pk]),
        404,
        "VOICE_SESSION_FORBIDDEN",
    )

    ended = _run(principal, lambda: end_voice_session(created["id"]), settings)
    assert ended["state"] == "ended"
    again = _run(principal, lambda: end_voice_session(created["id"]), settings)
    assert again["state"] == "ended", "end must be idempotent"


def test_scoped_thread_ids_are_rejected_pre_substrate():
    user = _user()
    settings = _settings([user.pk])
    _expect_http(
        _principal(user),
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id="scoped_abc123")),
        settings,
        404,
        "VOICE_SESSION_FORBIDDEN",
    )


def test_session_limit_returns_429():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
    )
    _expect_http(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
        429,
        "VOICE_SESSION_LIMIT",
    )


def test_sdp_requires_the_webrtc_preview_flag():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
    )
    _expect_http(
        principal,
        lambda: relay_sdp(created["id"], VoiceSdpRequest(sdp_offer="v=0\r\n")),
        settings,
        404,
        "VOICE_TRANSPORT_UNAVAILABLE",
    )


def test_sdp_without_provider_channel_is_honestly_unavailable():
    user = _user()
    settings = _settings([user.pk], FEATURE_VOICE_LIVE_WEBRTC=True)
    principal = _principal(user)
    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
    )
    routes.set_provider_channel_factory(None)
    _expect_http(
        principal,
        lambda: relay_sdp(created["id"], VoiceSdpRequest(sdp_offer="v=0\r\n")),
        settings,
        503,
        "VOICE_TRANSPORT_UNAVAILABLE",
    )


def test_sdp_relay_with_fake_channel_completes_and_binds_transport():
    user = _user()
    settings = _settings([user.pk], FEATURE_VOICE_LIVE_WEBRTC=True)
    principal = _principal(user)
    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
    )

    class FakeChannel:
        async def request_sdp_answer(self, payload):
            assert payload["type"] == "rtc.call.sdp.create"
            return {"type": "rtc.call.sdp.created", "sdp_answer": "v=0\r\nans"}

    routes.set_provider_channel_factory(lambda session: FakeChannel())  # noqa: ARG005
    try:
        reply = _run(
            principal,
            lambda: relay_sdp(created["id"], VoiceSdpRequest(sdp_offer="v=0\r\noffer")),
            settings,
        )
    finally:
        routes.set_provider_channel_factory(None)
    assert reply == {"sdp_answer": "v=0\r\nans"}
    fetched = _run(principal, lambda: get_voice_session(created["id"]), settings)
    assert fetched["transport"] == "webrtc"


class _FakeTurnService:
    def __init__(self):
        self.calls = []

    async def process(self, **kwargs):
        self.calls.append(kwargs)
        return NormalizedTurnResult(
            thread_id=str(kwargs["thread_id"]),
            turn_id="turn-1",
            message="Detailed diagnostic text.",
            workflow_used="wf1",
            spoken_summary="Two likely causes. Confirm the vibration reading.",
            canonical_response={"speak": True, "response_state": "complete"},
        )


def test_turn_bridge_persists_exact_spoken_summary_and_replays():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
    )
    fake = _FakeTurnService()
    request = VoiceTurnRequest(
        transcript="The pump is vibrating and the bearing is hot.",
        item_id="item-9",
        confidence=0.9,
        language="en-US",
    )

    with (
        patch.object(ai_app, "get_turn_service", return_value=fake),
        patch(
            "ai.core.trusted_context.build_trusted_turn_context",
            return_value=SimpleNamespace(),
        ),
    ):
        first = _run(
            principal,
            lambda: submit_voice_turn(created["id"], request),
            settings,
        )
        second = _run(
            principal,
            lambda: submit_voice_turn(created["id"], request),
            settings,
        )

    assert first["response_state"] == "complete"
    assert first["spoken"]["spoken_summary"] == (
        "Two likely causes. Confirm the vibration reading."
    )
    # The idempotency key binds session + provider item id.
    assert fake.calls[0]["idempotency_key"] == f"voice:{created['id']}:item-9"
    assert fake.calls[0]["modality_metadata"]["voice_live_item_id"] == "item-9"
    # A provider repeat replays the same persisted utterance.
    assert second["spoken"]["utterance_id"] == first["spoken"]["utterance_id"]


def test_turn_bridge_rejects_empty_transcript():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
    )
    _expect_http(
        principal,
        lambda: submit_voice_turn(
            created["id"],
            VoiceTurnRequest(transcript="   ", item_id="item-1"),
        ),
        settings,
        422,
        "VOICE_TRANSCRIPT_INCOMPLETE",
    )


def test_capability_probe_reports_disabled_without_erroring():
    user = _user()
    settings = _settings([user.pk], FEATURE_VOICE_LIVE=False)
    from ai.core.voice.routes import voice_capability

    result = _run(_principal(user), lambda: voice_capability(), settings)
    assert result["enabled"] is False
    assert result["webrtc"] is False
    assert result["relay"] is False
    # The confidence floor is served even when disabled (not a secret).
    assert result["confidence_floor"] == 0.85  # noqa: RUF069


def test_capability_probe_reports_cohort_membership():
    user = _user()
    stranger = _user()
    settings = _settings([user.pk], FEATURE_VOICE_LIVE_WEBRTC=True)
    from ai.core.voice.routes import voice_capability

    member = _run(_principal(user), lambda: voice_capability(), settings)
    assert member["enabled"] is True
    assert member["webrtc"] is True
    assert member["relay"] is False
    outsider = _run(_principal(stranger), lambda: voice_capability(), settings)
    assert outsider["enabled"] is False


def test_capability_probe_serves_configured_confidence_floor():
    user = _user()
    settings = _settings([user.pk], AIMMS_VOICE_CONFIDENCE_FLOOR=0.7)
    from ai.core.voice.routes import voice_capability

    result = _run(_principal(user), lambda: voice_capability(), settings)
    assert result["confidence_floor"] == 0.7  # noqa: RUF069


def test_turn_bridge_dispatches_exact_tts_through_provider_channel():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
    )

    sent = []

    class SpeakingChannel:
        async def request_sdp_answer(self, _payload):  # pragma: no cover - unused
            raise AssertionError("no SDP expected in this test")

        async def send_control(self, payload):
            sent.append(payload)

    routes.set_provider_channel_factory(lambda session: SpeakingChannel())  # noqa: ARG005
    try:
        with (
            patch.object(ai_app, "get_turn_service", return_value=_FakeTurnService()),
            patch(
                "ai.core.trusted_context.build_trusted_turn_context",
                return_value=SimpleNamespace(),
            ),
        ):
            result = _run(
                principal,
                lambda: submit_voice_turn(
                    created["id"],
                    VoiceTurnRequest(transcript="Pump is vibrating.", item_id="item-tts"),
                ),
                settings,
            )
    finally:
        routes.set_provider_channel_factory(None)

    # Persist-before-speak: playback advanced only after the exact payload
    # was accepted, and the spoken bytes equal the persisted summary.
    assert result["spoken"]["playback_state"] == "requested"
    assert len(sent) == 1
    assert sent[0]["type"] == "response.create"
    message = sent[0]["response"]["pre_generated_assistant_message"]
    assert message["content"][0]["text"] == result["spoken"]["spoken_summary"]


def test_turn_bridge_tts_failure_leaves_playback_honestly_pending():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
    )

    class BrokenChannel:
        async def request_sdp_answer(self, _payload):  # pragma: no cover - unused
            raise AssertionError("no SDP expected in this test")

        async def send_control(self, _payload):
            raise RuntimeError("provider connection lost")

    routes.set_provider_channel_factory(lambda session: BrokenChannel())  # noqa: ARG005
    try:
        with (
            patch.object(ai_app, "get_turn_service", return_value=_FakeTurnService()),
            patch(
                "ai.core.trusted_context.build_trusted_turn_context",
                return_value=SimpleNamespace(),
            ),
        ):
            result = _run(
                principal,
                lambda: submit_voice_turn(
                    created["id"],
                    VoiceTurnRequest(transcript="Pump is vibrating.", item_id="item-fail"),
                ),
                settings,
            )
    finally:
        routes.set_provider_channel_factory(None)

    # The visible chat answer is never blocked by TTS; playback stays pending.
    assert result["response_state"] == "complete"
    assert result["spoken"]["playback_state"] == "pending"
