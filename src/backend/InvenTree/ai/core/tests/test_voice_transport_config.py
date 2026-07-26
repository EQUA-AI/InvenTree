"""WS4-T1: Voice Live transport settings fail closed.

Runs in the deterministic suite. ``Settings`` populates by alias, so every
construction here uses the deployment environment-variable names.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ai.core.config import Settings
from pydantic import ValidationError

VALID_HOST = "aimms-foundry.services.ai.azure.com"


def _settings(**aliased: object) -> Settings:
    return Settings(_env_file=None, **aliased)  # ty: ignore[unknown-argument]  # pydantic-settings runtime kwarg


def test_local_env_file_is_anchored_to_ai_package():
    env_file = Path(Settings.model_config["env_file"])

    assert env_file.is_absolute()
    assert env_file == Path(__file__).resolve().parents[2] / ".env"


def test_voice_live_is_off_by_default():
    settings = _settings()
    assert settings.feature_voice_live is False
    assert settings.feature_voice_live_webrtc is False
    assert settings.feature_voice_live_relay is False
    assert settings.voice_live_store_raw_audio is False
    assert settings.feature_capability_broker_enforce is True
    assert settings.feature_voice_fast_path is False
    # Voice-initiated writes are a per-deployment safety decision. This assertion
    # is a guard, not a preference: it previously read `is True` and that flipped
    # default reached production, where voice offered to execute a kanban write.
    assert settings.feature_voice_write_confirmation is False


def test_enabled_voice_live_with_valid_transport():
    settings = _settings(FEATURE_VOICE_LIVE=True, AZURE_VOICELIVE_ENDPOINT=VALID_HOST)
    assert settings.azure_voicelive_model == "gpt-4.1-mini"
    assert settings.azure_voicelive_transcription_model == "azure-speech"
    assert settings.azure_voicelive_api_version == "2026-04-10"
    assert settings.azure_voicelive_webrtc_api_version == "2026-01-01-preview"


def test_enabled_voice_live_requires_endpoint():
    with pytest.raises(ValidationError):
        _settings(FEATURE_VOICE_LIVE=True)


def test_webrtc_flag_requires_master_flag():
    with pytest.raises(ValidationError):
        _settings(FEATURE_VOICE_LIVE_WEBRTC=True)


def test_relay_flag_requires_master_flag():
    with pytest.raises(ValidationError):
        _settings(FEATURE_VOICE_LIVE_RELAY=True)


def test_raw_audio_retention_cannot_be_enabled():
    """VOICE_LIVE_STORE_RAW_AUDIO is a privacy invariant, not a setting."""
    with pytest.raises(ValidationError):
        _settings(VOICE_LIVE_STORE_RAW_AUDIO=True)


def test_non_azure_host_is_rejected():
    with pytest.raises(ValidationError):
        _settings(
            FEATURE_VOICE_LIVE=True,
            AZURE_VOICELIVE_ENDPOINT="evil.example.com",
        )


def test_realtime_session_model_cannot_pair_with_azure_speech():
    with pytest.raises(ValidationError):
        _settings(
            FEATURE_VOICE_LIVE=True,
            AZURE_VOICELIVE_ENDPOINT=VALID_HOST,
            AZURE_VOICELIVE_MODEL="gpt-realtime-mini",
        )


def test_session_limits_are_bounded():
    with pytest.raises(ValidationError):
        _settings(VOICE_LIVE_MAX_ACTIVE_SESSIONS_PER_USER=0)
    with pytest.raises(ValidationError):
        _settings(VOICE_LIVE_IDLE_TIMEOUT_S=5)


def test_disabled_voice_live_skips_transport_validation():
    """A deployment with voice off must not be forced to configure Azure."""
    settings = _settings(AZURE_VOICELIVE_ENDPOINT="")
    assert settings.feature_voice_live is False
