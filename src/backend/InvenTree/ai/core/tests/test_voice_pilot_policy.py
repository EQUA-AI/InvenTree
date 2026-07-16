"""WS6-T1: pilot cohort configuration fails closed."""

from __future__ import annotations

from ai.core.config import Settings
from ai.core.voice.routes import is_pilot_user


def _settings(**aliased):
    return Settings(_env_file=None, **aliased)


def test_default_cohort_is_empty_and_admits_nobody():
    settings = _settings()
    assert settings.voice_pilot_user_ids == []
    assert is_pilot_user(settings, 1) is False


def test_named_cohort_admits_exactly_its_members():
    settings = _settings(AIMMS_VOICE_PILOT_USER_IDS=[7, 12])
    assert is_pilot_user(settings, 7) is True
    assert is_pilot_user(settings, 12) is True
    assert is_pilot_user(settings, 8) is False


def test_malformed_actor_ids_fail_closed():
    settings = _settings(AIMMS_VOICE_PILOT_USER_IDS=[7])
    assert is_pilot_user(settings, None) is False
    assert is_pilot_user(settings, "not-a-pk") is False


def test_string_pk_of_member_is_admitted():
    settings = _settings(AIMMS_VOICE_PILOT_USER_IDS=[7])
    assert is_pilot_user(settings, "7") is True
