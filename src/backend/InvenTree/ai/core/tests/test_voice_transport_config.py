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
    assert settings.feature_voice_decision_coordinator is False
    assert settings.feature_voice_live_webrtc is False
    assert settings.feature_voice_live_relay is False
    assert settings.voice_live_store_raw_audio is False
    assert settings.voice_decision_ttl_s == 120
    assert settings.voice_decision_max_review_turns == 3
    assert settings.voice_decision_max_armed_s == 300
    assert settings.feature_capability_broker_enforce is True
    assert settings.feature_voice_fast_path is False


def test_voice_writes_are_governed_by_rbac_not_by_the_modality():
    """CONTRACT CHANGE: the write flag defaults ON again, deliberately this time.

    It shipped True in 7779b5720 while the gate behind it was unsound, so it was
    forced False as an incident mitigation and this assertion was inverted to
    hold that line. The three defects that made the flag load-bearing are fixed
    -- severity now comes from the resolved tool, the read-only fence covers the
    direct-ORM kanban/email writes, and injected turns are refused before
    routing -- so the boundary returns to where it belongs: a user's permissions
    decide what they may do, on voice exactly as on text.

    The kill switch is unchanged and still explicit.
    """
    assert _settings().feature_voice_write_confirmation is True
    assert _settings(FEATURE_VOICE_WRITE_CONFIRMATION=False).feature_voice_write_confirmation is (
        False
    )


def test_enabled_voice_live_with_valid_transport():
    settings = _settings(FEATURE_VOICE_LIVE=True, AZURE_VOICELIVE_ENDPOINT=VALID_HOST)
    assert settings.azure_voicelive_model == "gpt-4.1-mini"
    assert settings.azure_voicelive_transcription_model == "azure-speech"
    assert settings.azure_voicelive_api_version == "2026-04-10"
    assert settings.azure_voicelive_webrtc_api_version == "2026-01-01-preview"


def test_voice_decisions_accept_canonical_and_prefixed_aliases():
    common = {
        "FEATURE_VOICE_LIVE": True,
        "AZURE_VOICELIVE_ENDPOINT": VALID_HOST,
    }
    canonical = _settings(**common, FEATURE_VOICE_DECISIONS=True)
    prefixed = _settings(**common, AIMMS_FEATURE_VOICE_DECISIONS=True)

    assert canonical.feature_voice_decision_coordinator is True
    assert prefixed.feature_voice_decision_coordinator is True


def test_voice_decisions_require_voice_live_and_write_confirmation():
    with pytest.raises(
        ValidationError, match="FEATURE_VOICE_DECISIONS requires FEATURE_VOICE_LIVE"
    ):
        _settings(FEATURE_VOICE_DECISIONS=True)

    with pytest.raises(
        ValidationError,
        match="FEATURE_VOICE_DECISIONS requires FEATURE_VOICE_WRITE_CONFIRMATION",
    ):
        _settings(
            FEATURE_VOICE_DECISIONS=True,
            FEATURE_VOICE_LIVE=True,
            FEATURE_VOICE_WRITE_CONFIRMATION=False,
            AZURE_VOICELIVE_ENDPOINT=VALID_HOST,
        )


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


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("VOICE_DECISION_TTL_S", 0),
        ("VOICE_DECISION_TTL_S", 301),
        ("VOICE_DECISION_MAX_REVIEW_TURNS", -1),
        ("VOICE_DECISION_MAX_REVIEW_TURNS", 4),
        ("VOICE_DECISION_MAX_ARMED_S", 0),
        ("VOICE_DECISION_MAX_ARMED_S", 301),
    ],
)
def test_voice_decision_limits_are_bounded(setting: str, value: int):
    with pytest.raises(ValidationError):
        _settings(**{setting: value})


def test_enabled_voice_decision_lifetime_stays_within_hard_ceiling():
    with pytest.raises(
        ValidationError,
        match="VOICE_DECISION_TTL_S must not exceed VOICE_DECISION_MAX_ARMED_S",
    ):
        _settings(
            FEATURE_VOICE_DECISIONS=True,
            FEATURE_VOICE_LIVE=True,
            AZURE_VOICELIVE_ENDPOINT=VALID_HOST,
            VOICE_DECISION_TTL_S=121,
            VOICE_DECISION_MAX_ARMED_S=120,
        )


def test_enabled_voice_decision_ceiling_stays_within_session_idle_timeout():
    with pytest.raises(
        ValidationError,
        match=("VOICE_DECISION_MAX_ARMED_S must not exceed VOICE_LIVE_IDLE_TIMEOUT_S"),
    ):
        _settings(
            FEATURE_VOICE_DECISIONS=True,
            FEATURE_VOICE_LIVE=True,
            AZURE_VOICELIVE_ENDPOINT=VALID_HOST,
            VOICE_LIVE_IDLE_TIMEOUT_S=299,
        )


def test_disabled_voice_live_skips_transport_validation():
    """A deployment with voice off must not be forced to configure Azure."""
    settings = _settings(AZURE_VOICELIVE_ENDPOINT="")
    assert settings.feature_voice_live is False
