"""Phase 5: optional native-STS transport (A2), governance preserved.

Native STS swaps the transport to a gpt-realtime session model with native
semantic VAD for snappier turn-taking. It is a transport swap only: answers stay
governed (``create_response:false``, exact-TTS, no session tools, prompt of
record in the repo). These tests fix the fail-closed config and prove the
governance invariants hold in BOTH transports.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from ai.core.config import Settings
from ai.core.voice.provider import SessionPolicy
from pydantic import ValidationError

VALID_HOST = "aimms-foundry.services.ai.azure.com"


def _settings(**aliased: object) -> Settings:
    return Settings(_env_file=None, **aliased)


# --------------------------------------------------------------------------- #
# config fail-closed                                                          #
# --------------------------------------------------------------------------- #
def test_native_sts_is_off_by_default() -> None:
    assert _settings().feature_voice_native_sts is False


def test_native_sts_requires_master_flag() -> None:
    with pytest.raises(ValidationError):
        _settings(FEATURE_VOICE_NATIVE_STS=True)


def test_native_sts_requires_a_realtime_session_model() -> None:
    # Voice Live on, but the default gpt-4.1-mini is not a realtime model.
    with pytest.raises(ValidationError):
        _settings(
            FEATURE_VOICE_LIVE=True,
            FEATURE_VOICE_NATIVE_STS=True,
            AZURE_VOICELIVE_ENDPOINT=VALID_HOST,
        )


def test_native_sts_still_forbids_azure_speech_transcription() -> None:
    with pytest.raises(ValidationError):
        _settings(
            FEATURE_VOICE_LIVE=True,
            FEATURE_VOICE_NATIVE_STS=True,
            AZURE_VOICELIVE_ENDPOINT=VALID_HOST,
            AZURE_VOICELIVE_MODEL="gpt-realtime",
            AZURE_VOICELIVE_TRANSCRIPTION_MODEL="azure-speech",
        )


def test_valid_native_sts_configuration_passes() -> None:
    settings = _settings(
        FEATURE_VOICE_LIVE=True,
        FEATURE_VOICE_NATIVE_STS=True,
        AZURE_VOICELIVE_ENDPOINT=VALID_HOST,
        AZURE_VOICELIVE_MODEL="gpt-realtime",
        AZURE_VOICELIVE_TRANSCRIPTION_MODEL="whisper-1",
    )
    assert settings.feature_voice_native_sts is True
    assert settings.azure_voicelive_model == "gpt-realtime"


# --------------------------------------------------------------------------- #
# session policy: transport differs, governance does not                      #
# --------------------------------------------------------------------------- #
def _session(native: bool) -> dict:
    return SessionPolicy(native_sts=native).session_update_payload()["session"]


def test_cascaded_uses_azure_semantic_vad() -> None:
    session = _session(native=False)
    assert session["turn_detection"]["type"] == "azure_semantic_vad"


def test_native_sts_uses_realtime_semantic_vad() -> None:
    session = _session(native=True)
    assert session["turn_detection"]["type"] == "semantic_vad"


@pytest.mark.parametrize("native", [False, True])
def test_governance_invariants_hold_in_both_transports(native) -> None:
    session = _session(native=native)
    # Voice Live never answers, in either transport.
    assert session["turn_detection"]["create_response"] is False
    # Barge-in over exact TTS is preserved.
    assert session["turn_detection"]["interrupt_response"] is True
    # No tools are ever registered on the realtime session.
    assert session["tools"] == []
    assert session["tool_choice"] == "none"
    # No create_response:true anywhere in the emitted session.
    assert "true" not in _dump_create_response(session)


def _dump_create_response(session: dict) -> str:
    """Collect every create_response value in the session as lower-case text."""
    values = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "create_response":
                    values.append(str(value).lower())
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(session)
    return ",".join(values)


# --------------------------------------------------------------------------- #
# wiring: the flag reaches the emitted session policy                         #
# --------------------------------------------------------------------------- #
def test_gateway_threads_native_sts_flag_into_session_policy() -> None:
    from ai.core.voice.gateway import VoiceLiveChannel

    fake = SimpleNamespace(
        azure_voicelive_voice="en-US-AvaNeural",
        azure_voicelive_language="en-US",
        azure_voicelive_transcription_model="whisper-1",
        azure_voicelive_phrase_hints=[],
        feature_voice_native_sts=True,
    )
    with patch("ai.core.config.get_settings", return_value=fake):
        session = VoiceLiveChannel._session_policy_payload()
    assert session["turn_detection"]["type"] == "semantic_vad"
    assert session["input_audio_transcription"]["model"] == "whisper-1"
    # Governance survives the flag.
    assert session["turn_detection"]["create_response"] is False
    assert session["tools"] == []
