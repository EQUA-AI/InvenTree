"""WS6-T1: pilot cohort configuration fails closed."""

from __future__ import annotations

from ai.core.config import Settings


def _settings(**aliased):
    return Settings(_env_file=None, **aliased)  # ty: ignore[unknown-argument]  # pydantic-settings runtime kwarg


def test_voice_feature_is_not_limited_to_named_users():
    settings = _settings()
    assert settings.feature_voice_live is False
