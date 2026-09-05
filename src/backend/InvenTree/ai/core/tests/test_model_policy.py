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
from ai.core.model_policy import (
    ModelPurpose,
    _legacy_choice,
    _policy_choice,
    call_options,
    select_deployment,
)

FAST = "fast-mini"
STANDARD = "standard-4o"
OVERRIDE = "luna-dz"


def _settings(**overrides) -> Settings:
    base = {
        "AZURE_OPENAI_DEPLOYMENT": STANDARD,
        "AZURE_OPENAI_FAST_DEPLOYMENT": FAST,
        "AZURE_OPENAI_SUMMARIZATION_DEPLOYMENT": "",
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
        # D-10: summarization/extraction never take the fast tier.
        (ModelPurpose.SUMMARIZATION, "text", STANDARD),
        (ModelPurpose.EXTRACTION, "text", STANDARD),
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


# --------------------------------------------------------------------------- #
# D-10 / CR-2: the SUMMARIZATION/EXTRACTION routing override
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("purpose", [ModelPurpose.SUMMARIZATION, ModelPurpose.EXTRACTION])
@pytest.mark.parametrize("shadow", [False, True], ids=["shadow-off", "shadow-on"])
@pytest.mark.parametrize("enforce", [False, True], ids=["enforce-off", "enforce-on"])
@pytest.mark.parametrize("override", ["", OVERRIDE], ids=["no-override", "override"])
def test_summarization_and_extraction_never_take_fast(
    monkeypatch, caplog, purpose, shadow, enforce, override
):
    """Every ladder state resolves both purposes to override-or-standard.

    The worker that runs ``_summarize`` sets no tiering flag, so the rule has
    to hold on BOTH branches — and it must never log a divergence, because a
    divergence would mean the branches disagree where nobody is watching.
    """
    settings = _settings(
        FEATURE_MODEL_TIERING_SHADOW=shadow,
        FEATURE_MODEL_TIERING_ENFORCE=enforce,
        AZURE_OPENAI_SUMMARIZATION_DEPLOYMENT=override,
    )
    monkeypatch.setattr("ai.core.config.get_settings", lambda: settings)
    with caplog.at_level(logging.WARNING, logger="ai.core.model_policy"):
        chosen = select_deployment(purpose)
    assert chosen == (override or STANDARD)
    assert chosen != FAST
    assert _legacy_choice(settings, purpose, "text") == _policy_choice(settings, purpose, "text")
    assert not any("model_tiering.divergence" in r.getMessage() for r in caplog.records)


def test_override_is_whitespace_trimmed(monkeypatch):
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(AZURE_OPENAI_SUMMARIZATION_DEPLOYMENT=f"  {OVERRIDE}  "),
    )
    assert select_deployment(ModelPurpose.SUMMARIZATION) == OVERRIDE


@pytest.mark.parametrize("purpose", [ModelPurpose.SUMMARIZATION, ModelPurpose.EXTRACTION])
def test_call_options_send_reasoning_effort_only_with_override(monkeypatch, purpose):
    monkeypatch.setattr("ai.core.config.get_settings", _settings)
    assert call_options(purpose) == {}

    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(AZURE_OPENAI_SUMMARIZATION_DEPLOYMENT=OVERRIDE),
    )
    assert call_options(purpose) == {"reasoning_effort": "low"}

    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(
            AZURE_OPENAI_SUMMARIZATION_DEPLOYMENT=OVERRIDE,
            AZURE_SUMMARIZATION_REASONING_EFFORT="high",
        ),
    )
    assert call_options(purpose) == {"reasoning_effort": "high"}


def test_call_options_never_apply_to_other_purposes(monkeypatch):
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(AZURE_OPENAI_SUMMARIZATION_DEPLOYMENT=OVERRIDE),
    )
    for purpose in ModelPurpose:
        if purpose in (ModelPurpose.SUMMARIZATION, ModelPurpose.EXTRACTION):
            continue
        assert call_options(purpose) == {}


def test_invalid_reasoning_effort_is_rejected():
    with pytest.raises(ValueError):
        _settings(AZURE_SUMMARIZATION_REASONING_EFFORT="max")
