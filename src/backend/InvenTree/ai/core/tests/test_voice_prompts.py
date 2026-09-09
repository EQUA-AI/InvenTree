"""A6: allow-listed spoken prompts are server-composed, persisted, then spoken."""

# ruff: noqa: E402

from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest
from ai.core.tests.test_realtime_session_api import _principal, _run, _settings, _user
from ai.core.tests.test_voice_status_phrases import RecordingChannel, _session_for, _spoken_texts
from ai.core.voice import prompts, routes
from ai.core.voice.routes import VoicePromptRequest, request_voice_prompt
from ai.core.voice.wire import SERVER_VOICE_ERROR_CODES
from django.core.management import call_command
from fastapi import HTTPException


@pytest.fixture(scope="module", autouse=True)
def _database():
    call_command("migrate", verbosity=0, interactive=False)
    yield


def _prompt(principal, settings, session_id, request, channel=None):
    routes.set_provider_channel_factory((lambda session: channel) if channel else None)  # noqa: ARG005
    try:
        with patch("ai.core.trusted_context.resolve_actor_locale", return_value="en"):
            return _run(
                principal,
                lambda: request_voice_prompt(session_id, request),
                settings,
            )
    finally:
        routes.set_provider_channel_factory(None)


# --------------------------------------------------------------------------- #
# pure composition                                                             #
# --------------------------------------------------------------------------- #
def test_transcript_review_prompt_is_templated_and_sanitized():
    spoken = prompts.compose_transcript_review("  fifty   psi\non the\tgauge. ")
    assert spoken == (
        "I heard: fifty psi on the gauge. Is that right? Say confirm, discard, or say it again."
    )


def test_transcript_is_bounded_and_control_characters_dropped():
    spoken = prompts.compose_transcript_review("x" * 1000 + "\x00\x1f")
    assert len(prompts.sanitize_transcript("x" * 1000)) == prompts.TRANSCRIPT_MAX_CHARS
    assert "\x00" not in spoken
    assert spoken.startswith("I heard: " + "x" * prompts.TRANSCRIPT_MAX_CHARS)


def test_empty_transcript_is_refused():
    with pytest.raises(ValueError):
        prompts.compose_transcript_review("   \n ")


def test_status_prompts_are_allow_listed_and_localized():
    assert prompts.status_prompt("listening") == prompts.STATUS_LISTENING
    assert (
        prompts.status_prompt("waiting_for_decision", "es-MX")
        == (prompts.LOCALIZED_TEMPLATES["es"][prompts.STATUS_WAITING_FOR_DECISION])
    )
    assert prompts.status_prompt("free text please") is None
    assert prompts.compose_transcript_review("cincuenta", "es").startswith("Escuché: cincuenta.")


def test_prompt_unknown_code_is_on_the_wire_contract():
    assert "VOICE_PROMPT_UNKNOWN" in SERVER_VOICE_ERROR_CODES


# --------------------------------------------------------------------------- #
# route                                                                        #
# --------------------------------------------------------------------------- #
def test_transcript_review_prompt_is_persisted_then_spoken():
    from voice.models import VoiceUtterance, VoiceUtteranceType

    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)
    channel = RecordingChannel()

    payload = _prompt(
        principal,
        settings,
        created["id"],
        VoicePromptRequest(kind="transcript_review", transcript="fifty psi", item_id="item-1"),
        channel,
    )

    expected = "I heard: fifty psi. Is that right? Say confirm, discard, or say it again."
    assert payload["spoken_summary"] == expected
    assert payload["playback_state"] == "requested"
    assert _spoken_texts(channel.sent) == [expected]
    stored = VoiceUtterance.objects.get(id=payload["utterance_id"])
    assert stored.utterance_type == VoiceUtteranceType.PROMPT
    assert stored.spoken_summary_hash == payload["spoken_summary_hash"]
    assert stored.policy_version == prompts.PROMPT_POLICY_VERSION


def test_without_a_channel_the_prompt_is_honestly_pending():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)

    payload = _prompt(
        principal,
        settings,
        created["id"],
        VoicePromptRequest(kind="status", status="waiting_for_decision"),
    )

    assert payload["spoken_summary"] == prompts.STATUS_WAITING_FOR_DECISION
    assert payload["playback_state"] == "pending"


def test_revised_transcript_for_the_same_item_re_prompts():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)
    channel = RecordingChannel()

    first = _prompt(
        principal,
        settings,
        created["id"],
        VoicePromptRequest(kind="transcript_review", transcript="fifteen psi", item_id="i"),
        channel,
    )
    second = _prompt(
        principal,
        settings,
        created["id"],
        VoicePromptRequest(kind="transcript_review", transcript="fifty psi", item_id="i"),
        channel,
    )
    assert first["utterance_id"] != second["utterance_id"]
    assert len(_spoken_texts(channel.sent)) == 2


def test_empty_transcript_and_unknown_status_are_422():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _session_for(principal, settings)

    with pytest.raises(HTTPException) as empty:
        _prompt(
            principal,
            settings,
            created["id"],
            VoicePromptRequest(kind="transcript_review", transcript="  "),
        )
    assert empty.value.status_code == 422
    assert empty.value.detail == "VOICE_TRANSCRIPT_INCOMPLETE"

    with pytest.raises(HTTPException) as unknown:
        _prompt(
            principal,
            settings,
            created["id"],
            VoicePromptRequest(kind="status", status="say anything you like"),
        )
    assert unknown.value.status_code == 422
    assert unknown.value.detail == "VOICE_PROMPT_UNKNOWN"


def test_foreign_session_is_not_prompted():
    owner = _user()
    other = _user()
    settings = _settings([owner.pk, other.pk])
    created = _session_for(_principal(owner), settings)

    with pytest.raises(HTTPException) as excinfo:
        _prompt(
            _principal(other),
            settings,
            created["id"],
            VoicePromptRequest(kind="status", status="listening"),
        )
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "VOICE_SESSION_FORBIDDEN"
