"""S37: canonical usage vocabulary — normalization, totals, string fields."""

# ruff: noqa: E402

from __future__ import annotations

import os
import typing

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

from ai.core.usage import (
    TurnUsageLedger,
    maf_response_usage_metrics,
)


def test_maf_vocabulary_normalizes_to_canonical():
    """wf8/routing *_token_count keys land as canonical *_tokens."""
    ledger = TurnUsageLedger()
    ledger.record(
        "wf8_lookup",
        {
            "input_token_count": 100,
            "output_token_count": 20,
            "total_token_count": 120,
            "cached_input_token_count": 60,
        },
    )
    assert ledger.totals() == {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "cached_input_tokens": 60,
    }


def test_luna_vocabulary_is_already_canonical():
    ledger = TurnUsageLedger()
    ledger.record(
        "luna_diagnostics",
        {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60, "cached_input_tokens": 25},
    )
    assert ledger.totals()["cached_input_tokens"] == 25
    assert ledger.totals()["total_tokens"] == 60


def test_openai_chat_vocabulary_normalizes():
    """prompt/completion_tokens (grounding audit) map to canonical keys."""
    ledger = TurnUsageLedger()
    ledger.record(
        "grounding_audit",
        {"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 35, "cached_tokens": 12},
    )
    assert ledger.totals() == {
        "input_tokens": 30,
        "output_tokens": 5,
        "total_tokens": 35,
        "cached_input_tokens": 12,
    }


def test_cross_source_totals_merge_into_one_number():
    """The whole point: mixed vocabularies sum into comparable totals."""
    ledger = TurnUsageLedger()
    ledger.record("wf8_lookup", {"input_token_count": 100, "total_token_count": 120})
    ledger.record("luna_diagnostics", {"input_tokens": 50, "total_tokens": 60})
    ledger.record("grounding_audit", {"prompt_tokens": 30, "total_tokens": 35})
    totals = ledger.totals()
    assert totals["input_tokens"] == 180
    assert totals["total_tokens"] == 215


def test_non_canonical_int_keys_stay_event_detail_not_totals():
    ledger = TurnUsageLedger()
    ledger.record("history_replay", {"history_messages": 7, "input_tokens": 3})
    assert ledger.events[0]["history_messages"] == 7
    assert "history_messages" not in ledger.totals()
    assert ledger.totals()["input_tokens"] == 3


def test_named_string_fields_survive_but_nothing_else():
    ledger = TurnUsageLedger()
    ledger.record(
        "grounding_audit",
        {
            "input_tokens": 5,
            "deployment": "gpt-4o-mini",
            "model": "gpt-4o-mini-2024",
            "note": "should be dropped",
            "flag": True,
        },
    )
    event = ledger.events[0]
    assert event["deployment"] == "gpt-4o-mini"
    assert event["model"] == "gpt-4o-mini-2024"
    assert "note" not in event
    assert "flag" not in event


def test_string_only_event_is_not_recorded():
    """An event with no integer metric is noise, not usage."""
    ledger = TurnUsageLedger()
    ledger.record("grounding_audit", {"deployment": "gpt-4o-mini"})
    assert ledger.events == []


def test_maf_response_extractor_handles_object_and_dict_shapes():
    class _Usage:
        input_token_count = 10
        output_token_count = 2
        total_token_count = 12

    class _Response:
        usage_details = _Usage()

    assert maf_response_usage_metrics(_Response())["input_token_count"] == 10

    class _DictResponse:
        usage_details: typing.ClassVar[dict] = {
            "input_token_count": 8,
            "cache_read_input_token_count": 4,
        }

    metrics = maf_response_usage_metrics(_DictResponse())
    assert metrics["cached_input_token_count"] == 4
    assert metrics["uncached_input_token_count"] == 4

    class _Empty:
        usage_details = None

    assert maf_response_usage_metrics(_Empty()) == {}
