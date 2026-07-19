"""Eyes-free status speech tests for the voice turn bridge (§7.6).

Field technicians may be unable to look at the screen, so every turn
outcome must be audible: a delayed thinking phrase while processing, a
pointer phrase when a completed answer has no spoken form, and a failure
phrase when the turn dies. All phrases are static, allow-listed, and
persisted before speech like any other utterance.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import ai.core.tests.test_realtime_session_api as api_fixtures  # noqa: E402
import pytest  # noqa: E402
from ai.core.tests.test_realtime_session_api import (  # noqa: E402
    _principal,
    _run,
    _settings,
    _user,
)
from ai.core.turn_service import NormalizedTurnResult  # noqa: E402
from ai.core.voice import routes, status_phrases  # noqa: E402
from ai.core.voice.routes import (  # noqa: E402
    VoiceSessionCreateRequest,
    VoiceTurnRequest,
    create_voice_session,
    submit_voice_turn,
)
from django.core.management import call_command  # noqa: E402
from fastapi import HTTPException  # noqa: E402

ai_app = api_fixtures.ai_app


@pytest.fixture(scope="module", autouse=True)
def _database():
    call_command("migrate", verbosity=0, interactive=False)
    yield


class RecordingChannel:
    def __init__(self):
        self.sent = []

    async def request_sdp_answer(self, _payload):  # pragma: no cover - unused
        raise AssertionError("no SDP expected in these tests")

    async def send_control(self, payload):
        self.sent.append(payload)


def _spoken_texts(sent):
    return [
        payload["response"]["pre_generated_assistant_message"]["content"][0]["text"]
        for payload in sent
        if payload.get("type") == "response.create"
    ]


def _session_for(principal, settings):
    return _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
    )


class _SlowSpeakingTurnService:
    async def process(self, **kwargs):
        await asyncio.sleep(0.15)
        return NormalizedTurnResult(
            thread_id=str(kwargs["thread_id"]),
            turn_id="turn-slow",
            message="Detailed diagnostic text.",
            workflow_used="wf1",
            spoken_summary="Two likely causes.",
            canonical_response={"speak": True, "response_state": "complete"},
        )


class _SilentTurnService:
    async def process(self, **kwargs):
        return NormalizedTurnResult(
            thread_id=str(kwargs["thread_id"]),
            turn_id="turn-silent",
            message="A very long answer that has no schema-valid spoken form.",
            workflow_used="wf1",
            spoken_summary="",
            canonical_response={"speak": False, "response_state": "complete"},
        )


class _FailingTurnService:
    async def process(self, **kwargs):
        raise RuntimeError("workflow exploded")


class _IncompleteTurnService:
    async def process(self, **kwargs):
        return NormalizedTurnResult(
            thread_id=str(kwargs["thread_id"]),
            turn_id="turn-inc",
            message="The bounded diagnostic review ended before a complete answer.",
            workflow_used="wf1",
            response_state="incomplete",
            spoken_summary="",
            canonical_response={"speak": False, "response_state": "incomplete"},
        )


def _submit(principal, settings, session_id, turn_service):
    with (
        patch.object(ai_app, "get_turn_service", return_value=turn_service),
        patch(
            "ai.core.trusted_context.build_trusted_turn_context",
            return_value=SimpleNamespace(),
        ),
    ):
        return _run(
            principal,
            lambda: submit_voice_turn(
                session_id,
                VoiceTurnRequest(transcript="Pump is vibrating.", item_id="item-s"),
            ),
            settings,
        )


def test_slow_turn_speaks_thinking_then_cancels_and_speaks_answer():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)
    channel = RecordingChannel()

    routes.set_provider_channel_factory(lambda session: channel)  # noqa: ARG005
    try:
        with patch.object(status_phrases, "INTERIM_STATUS_DELAY_S", 0.01):
            result = _submit(principal, settings, created["id"], _SlowSpeakingTurnService())
    finally:
        routes.set_provider_channel_factory(None)

    texts = _spoken_texts(channel.sent)
    assert texts == [status_phrases.THINKING, "Two likely causes."]
    # The still-playing thinking phrase is cancelled before the answer.
    types = [payload["type"] for payload in channel.sent]
    assert types == ["response.create", "response.cancel", "response.create"]
    assert result["spoken"]["playback_state"] == "requested"

    from voice.models import VoiceUtterance, VoiceUtteranceType

    interim = VoiceUtterance.objects.filter(
        session_id=created["id"], utterance_type=VoiceUtteranceType.INTERIM_STATUS
    )
    assert [u.spoken_summary for u in interim] == [status_phrases.THINKING]
    assert all(u.policy_version == status_phrases.STATUS_PHRASE_POLICY_VERSION for u in interim)


def test_fast_turn_never_speaks_filler():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)
    channel = RecordingChannel()

    routes.set_provider_channel_factory(lambda session: channel)  # noqa: ARG005
    try:
        result = _submit(principal, settings, created["id"], api_fixtures._FakeTurnService())
    finally:
        routes.set_provider_channel_factory(None)

    texts = _spoken_texts(channel.sent)
    assert texts == [result["spoken"]["spoken_summary"]]
    assert all(payload["type"] == "response.create" for payload in channel.sent)


def test_silent_complete_answer_speaks_the_pointer_phrase():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)
    channel = RecordingChannel()

    routes.set_provider_channel_factory(lambda session: channel)  # noqa: ARG005
    try:
        result = _submit(principal, settings, created["id"], _SilentTurnService())
    finally:
        routes.set_provider_channel_factory(None)

    assert result["spoken"] is None
    assert _spoken_texts(channel.sent) == [status_phrases.ANSWER_IN_CHAT]


def test_failed_turn_speaks_the_failure_phrase():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)
    channel = RecordingChannel()

    routes.set_provider_channel_factory(lambda session: channel)  # noqa: ARG005
    try:
        with pytest.raises(HTTPException) as excinfo:
            _submit(principal, settings, created["id"], _FailingTurnService())
    finally:
        routes.set_provider_channel_factory(None)

    assert excinfo.value.status_code == 500
    assert _spoken_texts(channel.sent) == [status_phrases.TURN_FAILED]

    from voice.models import VoiceUtterance, VoiceUtteranceType

    failures = VoiceUtterance.objects.filter(
        session_id=created["id"], utterance_type=VoiceUtteranceType.FAILURE_STATUS
    )
    assert [u.spoken_summary for u in failures] == [status_phrases.TURN_FAILED]


def test_incomplete_turn_speaks_the_incomplete_phrase():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)
    channel = RecordingChannel()

    routes.set_provider_channel_factory(lambda session: channel)  # noqa: ARG005
    try:
        result = _submit(principal, settings, created["id"], _IncompleteTurnService())
    finally:
        routes.set_provider_channel_factory(None)

    assert result["response_state"] == "incomplete"
    assert result["spoken"] is None
    assert _spoken_texts(channel.sent) == [status_phrases.ANSWER_INCOMPLETE]


def test_missing_channel_keeps_turns_working_without_status_speech():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)

    result = _submit(principal, settings, created["id"], _SilentTurnService())
    assert result["response_state"] == "complete"
    assert result["spoken"] is None
