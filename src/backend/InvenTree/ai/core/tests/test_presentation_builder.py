"""Deterministic layouts cannot change the canonical answer or invent data."""

from __future__ import annotations

import copy
import json

import pytest
from ai.core.agui.translate import SpecTranslator
from ai.core.presentation.builder import build_presentation, language_literal, project_presentation
from ai.core.presentation.datasets import normalize_datasets, trend_candidate
from ai.core.presentation.library import library_available


def canonical():
    return {
        "message": 'Quotes "\\\n机器 ]); Query("private")',
        "response_state": "complete",
        "entities": [{"model": "assetmachine", "pk": 7, "label": "Pump A"}],
        "media_evidence": [{"attachment_id": 19, "segment_index": None}],
        "evidence_analysis": {"coverage": {"population_count": 12, "returned_count": 3}},
    }


def test_builder_is_bounded_deterministic_and_does_not_copy_prose_or_labels():
    value = canonical()
    before = copy.deepcopy(value)
    built = build_presentation(value)
    assert value == before
    assert built == build_presentation(value)
    assert "Pump A" not in built["program"]
    assert value["message"] not in built["program"]
    assert (
        built["program"]
        == 'root = AnswerLayout([AnswerSummary(), AssetSummary("r0"), EvidenceList("r1"), CoverageNotice(), MetricComparison("r2")])'
    )
    assert built["references"][0]["source_key"] == "entity:assetmachine:7"


@pytest.mark.parametrize("state", ["failed", "canceled", "incomplete"])
def test_noncomplete_turns_do_not_gain_presentations(state):
    assert build_presentation({**canonical(), "response_state": state}) is None


def test_questions_and_plain_prose_use_existing_native_rendering():
    assert build_presentation({**canonical(), "question": {"interrupt_id": "q"}}) is None
    assert build_presentation({"message": "hello", "response_state": "complete"}) is None


@pytest.mark.parametrize("value", ['quote"', "\\", "line\nline", "机器 🛠", ']); Query("x")'])
def test_language_literal_roundtrips(value):
    assert json.loads(language_literal(value)) == value


def test_shared_projection_cannot_inherit_owner_references():
    built = build_presentation(canonical())
    assert project_presentation(built, shared=True) is None
    assert project_presentation(built, shared=False) == built
    assert project_presentation({**built, "library": "future"}, shared=False) is None


def test_live_and_replayed_translation_use_the_same_final_attachment():
    presentation = build_presentation(canonical())
    event = {
        "type": "STATE_DELTA",
        "kind": "presentation",
        "presentation": presentation,
        "presentation_data": [],
    }
    live = SpecTranslator(thread_id="thread", run_id="live").translate(event)
    replay = SpecTranslator(thread_id="thread", run_id="replay").translate(
        json.loads(json.dumps(event))
    )
    assert (
        live
        == replay
        == [
            {
                "type": "CUSTOM",
                "name": "aimms.presentation",
                "value": {"presentation": presentation, "presentation_data": []},
            }
        ]
    )


def test_packaged_versioned_artifacts_are_available():
    assert library_available()


def test_only_complete_authorized_gateway_buckets_become_candidates():
    result = {
        "available": True,
        "complete_population": True,
        "population_type": "work_orders",
        "timezone": "UTC",
        "buckets": [
            {"bucket": "2026-01-01", "group_count": 0},
            {"bucket": "2026-02-01", "group_count": 4},
        ],
    }
    data = trend_candidate(result, as_of="2026-10-24T00:00:00+00:00", source_revision="snapshot")
    assert [sample["value"] for sample in data[0]["samples"]] == [0, 4]
    assert (
        trend_candidate(
            {**result, "complete_population": False}, as_of="2026-10-24", source_revision="snapshot"
        )
        == []
    )
    data[0]["samples"][0]["value"] = None
    data[0]["samples"][0]["quality"] = "missing"
    assert normalize_datasets(data) == data
    for value in [False, float("inf"), "0"]:
        invalid = copy.deepcopy(data)
        invalid[0]["samples"][0]["value"] = value
        assert normalize_datasets(invalid) == []
    invalid = copy.deepcopy(data)
    invalid[0]["samples"].reverse()
    assert normalize_datasets(invalid) == []


def test_generation_is_enabled_by_default_and_environment_can_disable(monkeypatch):
    from ai.core.config import Settings

    monkeypatch.delenv("FEATURE_CHAT_PRESENTATION", raising=False)
    monkeypatch.delenv("AIMMS_FEATURE_CHAT_PRESENTATION", raising=False)
    assert Settings(_env_file=None).feature_chat_presentation is True
    monkeypatch.setenv("AIMMS_FEATURE_CHAT_PRESENTATION", "false")
    assert Settings(_env_file=None).feature_chat_presentation is False
