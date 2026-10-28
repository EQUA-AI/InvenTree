"""Bounded pause tolerance without changing voice transport or confirmation policy."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from ai.core.config import Settings
from ai.core.voice.gateway import VoiceLiveChannel
from ai.core.voice.provider import SessionPolicy
from pydantic import ValidationError


def test_default_payload_keeps_the_existing_silence_window():
    settings = Settings(_env_file=None)
    assert settings.azure_voicelive_vad_silence_duration_ms == 550
    assert (
        SessionPolicy().session_update_payload()["session"]["turn_detection"]["silence_duration_ms"]
        == 550
    )


def test_environment_window_reaches_real_gateway_payload_without_other_changes(monkeypatch):
    monkeypatch.setenv("AZURE_VOICELIVE_VAD_SILENCE_DURATION_MS", "1200")
    configured = Settings(_env_file=None)
    fake = SimpleNamespace(
        azure_voicelive_phrase_hints=[],
        azure_voicelive_voice="en-US-AvaNeural",
        azure_voicelive_language="en-US",
        azure_voicelive_transcription_model="gpt-4o-transcribe",
        feature_voice_native_sts=False,
        azure_voicelive_vad_silence_duration_ms=configured.azure_voicelive_vad_silence_duration_ms,
    )
    with patch("ai.core.config.get_settings", return_value=fake):
        actual = VoiceLiveChannel("test-session")._session_policy_payload()
    expected = SessionPolicy(transcription_model="gpt-4o-transcribe").session_update_payload()[
        "session"
    ]
    expected["turn_detection"]["silence_duration_ms"] = 1200
    assert actual == expected
    assert actual["turn_detection"]["create_response"] is False
    assert actual["tools"] == [] and actual["tool_choice"] == "none"


@pytest.mark.parametrize("value", [0, 199, 2001, 10000, -1, True, "invalid"])
def test_settings_reject_unbounded_or_invalid_turn_windows(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, AZURE_VOICELIVE_VAD_SILENCE_DURATION_MS=value)


@pytest.mark.parametrize("value", [False, 199, 2001, 1200.0, "1200"])
def test_direct_policy_cannot_bypass_typed_window_bounds(value):
    with pytest.raises(ValueError):
        SessionPolicy(vad_silence_duration_ms=value)


def test_native_transport_payload_does_not_gain_azure_vad_or_eou_fields():
    actual = SessionPolicy(native_sts=True, vad_silence_duration_ms=1200).session_update_payload()[
        "session"
    ]
    expected = SessionPolicy(native_sts=True).session_update_payload()["session"]
    assert actual == expected
    assert actual["turn_detection"]["create_response"] is False
