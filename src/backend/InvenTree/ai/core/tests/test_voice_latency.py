"""Phase 3 (V17-V20): stop paying for work whose answer is already decided.

Measured in the 2026-07-26 live voice test:

* V17 -- "Order 50 more M3 screws" took ~95 s to produce a constant refusal: the
  action planner ran four sequential agent loops at 8 tool iterations each, with
  no timeout anywhere on the path.
* V18 -- "Hello." took 4-6 s, spoke the "Let me check that" filler, and came back
  with the clarification agent's question, because a greeting scores no pack.
* V19 -- 30 of 32 turns crossed the 2.5 s filler threshold.
* V20 -- intent classification ran on the reasoning deployment.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.workflows.wf8_lookup import _is_social_turn  # noqa: E402


# --------------------------------------------------------------------------- #
# V18: social turns never reach the tool-less clarification agent              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "utterance",
    [
        "Hello.",
        "hi",
        "hey",
        "Good morning",
        "Thanks, that's all.",
        "thank you",
        "ok",
        "got it",
        "What can you do?",
        "help",
        "how can you help",
    ],
)
def test_social_turns_are_recognised(utterance):
    assert _is_social_turn(utterance) is True


@pytest.mark.parametrize(
    "utterance",
    [
        "What's the stock level for C_100pF_0402?",
        "hello, how many fasteners are over 2000?",  # a greeting plus a question
        "thanks - now show me the BOM for assembly 42",
        "what about that one?",
        "",
    ],
)
def test_real_questions_are_not_treated_as_social(utterance):
    assert _is_social_turn(utterance) is False


def test_social_classification_reuses_the_router_patterns():
    """The two classifications must not drift apart."""
    import inspect

    from ai.core.workflows import wf8_lookup

    source = inspect.getsource(wf8_lookup._is_social_turn)
    assert "VoiceComplexityRouter" in source


def test_clarify_is_suppressed_for_social_turns():
    import inspect

    from ai.core.workflows import wf8_lookup

    # S45 moved the clarify gating into the shared _prepare_run helper.
    source = inspect.getsource(wf8_lookup.T1LookupWorkflow._prepare_run)
    assert "not _is_social_turn(query)" in source


# --------------------------------------------------------------------------- #
# V17: the planner is bounded                                                  #
# --------------------------------------------------------------------------- #
def test_write_planner_has_a_timeout_setting():
    from ai.core.config import Settings

    settings = Settings(_env_file=None)  # ty: ignore[unknown-argument]

    assert settings.voice_write_plan_timeout_s == pytest.approx(8.0)


def test_turn_service_bounds_the_planner_await():
    import inspect

    from ai.core import turn_service

    source = inspect.getsource(turn_service.NormalizedTurnService._begin_voice_write)
    assert "asyncio.wait_for" in source
    assert "voice_write_plan_timeout_s" in source
    # A timeout degrades to the ordinary advisory refusal, not an error.
    assert "return None" in source


def test_planner_ladder_drops_the_most_expensive_pass():
    """Four sequential agent loops for a fixed refusal is the ~95 s defect."""
    import inspect

    from ai.core.voice import tool_actions

    source = inspect.getsource(tool_actions.VoiceToolActionResolver.resolve)
    # Three rungs kept (shortlist, shortlist+reads, all actions); the fourth
    # (all actions + all reads) is gone and the whole path is timeout-bounded.
    assert source.count("agent.run(") <= 3


def test_planner_iterations_are_capped_low():
    import inspect

    from ai.core.voice import tool_actions

    source = inspect.getsource(tool_actions.VoiceToolActionResolver)
    assert "max_iterations = 3" in source


# --------------------------------------------------------------------------- #
# V19/V20                                                                      #
# --------------------------------------------------------------------------- #
def test_filler_threshold_is_above_the_measured_turn_floor():
    from ai.core.voice import status_phrases

    assert status_phrases.INTERIM_STATUS_DELAY_S >= 4.0


def test_intent_classifier_prefers_the_fast_deployment():
    """S37: the choice now routes through the policy table, which keeps the
    classifier on the fast deployment (fast or standard fallback)."""
    import inspect
    from types import SimpleNamespace
    from unittest.mock import patch

    from ai.core.agents import routing
    from ai.core.model_policy import ModelPurpose, select_deployment

    source = inspect.getsource(routing.IntentClassifier._get_agent)
    assert "FALLBACK_CLASSIFIER" in source
    fake = SimpleNamespace(
        azure_openai_fast_deployment="fast-mini",
        azure_openai_deployment="standard",
        feature_model_tiering_shadow=False,
        feature_model_tiering_enforce=False,
    )
    with patch("ai.core.config.get_settings", return_value=fake):
        assert select_deployment(ModelPurpose.FALLBACK_CLASSIFIER) == "fast-mini"
