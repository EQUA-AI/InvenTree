"""S37: model tiering — identity table, ladder, wf8 text fast tier."""

# ruff: noqa: E402

from __future__ import annotations

import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.config import Settings
from ai.core.model_policy import ModelPurpose, select_deployment

FAST = "fast-mini"
STANDARD = "standard-4o"


def _settings(**overrides) -> Settings:
    base = {
        "AZURE_OPENAI_DEPLOYMENT": STANDARD,
        "AZURE_OPENAI_FAST_DEPLOYMENT": FAST,
        "FEATURE_MODEL_TIERING_SHADOW": True,
        "FEATURE_MODEL_TIERING_ENFORCE": False,
        "FEATURE_WF8_TEXT_FAST_TIER": False,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.mark.parametrize(
    ("purpose", "modality", "expected"),
    [
        (ModelPurpose.WF8_PRIMARY, "text", STANDARD),
        (ModelPurpose.WF8_PRIMARY, "voice", FAST),
        (ModelPurpose.FALLBACK_CLASSIFIER, "text", FAST),
        (ModelPurpose.GROUNDING_AUDIT, "text", FAST),
        (ModelPurpose.CLOSEOUT_BINDING, "text", FAST),
        (ModelPurpose.SUMMARIZATION, "text", FAST),
        (ModelPurpose.MEDIA_CAPTION, "text", STANDARD),
    ],
)
@pytest.mark.parametrize("enforce", [False, True])
def test_identity_table_makes_enforce_a_noop(monkeypatch, purpose, modality, expected, enforce):
    """Legacy and policy agree on every purpose — flipping enforce changes nothing."""
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(FEATURE_MODEL_TIERING_ENFORCE=enforce),
    )
    assert select_deployment(purpose, modality=modality) == expected


def test_identity_table_logs_no_divergence(monkeypatch, caplog):
    monkeypatch.setattr("ai.core.config.get_settings", _settings)
    with caplog.at_level(logging.WARNING, logger="ai.core.model_policy"):
        for purpose in ModelPurpose:
            select_deployment(purpose, modality="text")
            select_deployment(purpose, modality="voice")
    assert not any("model_tiering.divergence" in r.getMessage() for r in caplog.records)


def test_wf8_text_fast_tier_diverges_in_shadow_but_returns_legacy(monkeypatch, caplog):
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(FEATURE_WF8_TEXT_FAST_TIER=True),
    )
    with caplog.at_level(logging.WARNING, logger="ai.core.model_policy"):
        chosen = select_deployment(ModelPurpose.WF8_PRIMARY, modality="text")
    assert chosen == STANDARD, "shadow must keep the legacy choice"
    assert any("model_tiering.divergence" in r.getMessage() for r in caplog.records)


def test_wf8_text_fast_tier_applies_only_under_enforce(monkeypatch):
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(FEATURE_WF8_TEXT_FAST_TIER=True, FEATURE_MODEL_TIERING_ENFORCE=True),
    )
    assert select_deployment(ModelPurpose.WF8_PRIMARY, modality="text") == FAST
    # Voice was already fast; unchanged.
    assert select_deployment(ModelPurpose.WF8_PRIMARY, modality="voice") == FAST


def test_ladder_off_returns_legacy_without_computing_policy(monkeypatch):
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(FEATURE_MODEL_TIERING_SHADOW=False, FEATURE_WF8_TEXT_FAST_TIER=True),
    )
    assert select_deployment(ModelPurpose.WF8_PRIMARY, modality="text") == STANDARD
