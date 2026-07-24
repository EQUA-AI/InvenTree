"""Tests for tool-loading performance reports and provider usage metrics."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from ai.core.benchmarks.tool_loading import (
    LiveTurnSample,
    compare_live_samples,
    load_live_samples,
    run_offline_benchmark,
)
from ai.core.workflows.wf8_lookup import _response_usage_metrics


def _sample(
    case_id: str,
    *,
    latency_ms: float,
    input_tokens: int,
    first_call_input_tokens: int,
    cached_input_tokens: int = 0,
    success: bool = True,
    model_rounds: int = 2,
    tool_rounds: int = 1,
) -> LiveTurnSample:
    return LiveTurnSample(
        case_id=case_id,
        latency_ms=latency_ms,
        ttft_ms=latency_ms / 4,
        input_tokens=input_tokens,
        first_call_input_tokens=first_call_input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=100,
        model_rounds=model_rounds,
        tool_rounds=tool_rounds,
        success=success,
    )


def test_offline_benchmark_meets_static_selection_gates():
    report = run_offline_benchmark(iterations=3)

    # 46, not 47: delete_kanban_card is withheld from the agent's tool catalog
    # (see test_kanban_delete_withheld.py).
    assert report["baseline"]["tool_count"] == 46
    assert report["baseline"]["measurement"] == (
        "normalized_local_contract_bytes_not_provider_tokens"
    )
    assert report["summary"]["median_contract_reduction_pct"] >= 65
    assert report["summary"]["max_tool_count"] <= 12
    assert report["acceptance"]["all_pass"] is True


def test_live_comparison_applies_latency_token_quality_and_round_gates():
    baseline = [
        _sample("stock", latency_ms=100, input_tokens=1000, first_call_input_tokens=800),
        _sample("stock", latency_ms=110, input_tokens=1100, first_call_input_tokens=900),
        _sample("part", latency_ms=120, input_tokens=1200, first_call_input_tokens=1000),
    ]
    enforced = [
        _sample("stock", latency_ms=70, input_tokens=400, first_call_input_tokens=300),
        _sample("stock", latency_ms=80, input_tokens=450, first_call_input_tokens=350),
        _sample("part", latency_ms=85, input_tokens=500, first_call_input_tokens=400),
    ]

    report = compare_live_samples(baseline, enforced)

    assert report["deltas"]["median_latency_improvement_pct"] >= 20
    assert report["deltas"]["median_total_input_token_reduction_pct"] >= 40
    assert report["deltas"]["median_first_call_input_token_reduction_pct"] >= 50
    assert report["acceptance"]["all_pass"] is True


def test_live_comparison_requires_first_call_token_measurement():
    baseline = [
        LiveTurnSample(
            case_id="stock",
            latency_ms=100,
            input_tokens=1000,
            output_tokens=100,
            model_rounds=2,
            tool_rounds=1,
            success=True,
        )
    ]
    enforced = [
        LiveTurnSample(
            case_id="stock",
            latency_ms=70,
            input_tokens=400,
            output_tokens=100,
            model_rounds=2,
            tool_rounds=1,
            success=True,
        )
    ]

    report = compare_live_samples(baseline, enforced)

    assert report["deltas"]["median_first_call_input_token_reduction_pct"] is None
    assert report["acceptance"]["median_first_call_input_tokens_at_least_50_pct_lower"] is False
    assert report["acceptance"]["all_pass"] is False


def test_live_sample_rejects_impossible_cache_counts():
    with pytest.raises(ValueError, match="cannot exceed"):
        LiveTurnSample.from_mapping({
            "case_id": "stock",
            "latency_ms": 10,
            "input_tokens": 100,
            "cached_input_tokens": 101,
            "output_tokens": 10,
            "model_rounds": 1,
            "tool_rounds": 0,
            "success": True,
        })


def test_load_live_samples_reports_invalid_line_without_payload(tmp_path):
    path = tmp_path / "samples.jsonl"
    path.write_text(json.dumps({"case_id": "missing metrics"}) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"samples\.jsonl:1"):
        load_live_samples(path)


def test_wf8_usage_metrics_preserve_reported_and_derive_uncached_counts():
    usage = SimpleNamespace(
        to_dict=lambda **_kwargs: {
            "input_token_count": 1000,
            "output_token_count": 80,
            "total_token_count": 1080,
            "cache_read_input_token_count": 640,
            "reasoning_output_token_count": 20,
        }
    )

    metrics = _response_usage_metrics(SimpleNamespace(usage_details=usage))

    assert metrics == {
        "input_token_count": 1000,
        "output_token_count": 80,
        "total_token_count": 1080,
        "cache_read_input_token_count": 640,
        "reasoning_output_token_count": 20,
        "cached_input_token_count": 640,
        "uncached_input_token_count": 360,
    }
