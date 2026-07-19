"""Offline selection benchmark and live WF8 metric comparison."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

ALL_VIEW_PROFILE = frozenset({
    ("build", "view"),
    ("part", "view"),
    ("part_category", "view"),
    ("purchase_order", "view"),
    ("sales_order", "view"),
    ("stock", "view"),
    ("stock_location", "view"),
})


@dataclass(frozen=True)
class OfflineCase:
    """One deterministic capability-selection benchmark case."""

    case_id: str
    query: str
    lookup_type: str | None = None


DEFAULT_CASES = (
    OfflineCase("stock-ranking", "Which fastener has the highest stock?"),
    OfflineCase("part-details", "Show details for part ABC-123", "part_details"),
    OfflineCase("bom", "Show the BOM for assembly 42", "bom_query"),
    OfflineCase("procurement", "List supplier purchase orders", "supplier_list"),
    OfflineCase("sales", "List sales orders for this customer"),
    OfflineCase("build", "Show build order lines"),
    OfflineCase("documents", "Find the part datasheet"),
)


@dataclass(frozen=True)
class LiveTurnSample:
    """Content-free metrics for one completed live turn."""

    case_id: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    model_rounds: int
    tool_rounds: int
    success: bool
    first_call_input_tokens: int | None = None
    cached_input_tokens: int = 0
    ttft_ms: float | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> LiveTurnSample:
        sample = cls(
            case_id=str(value["case_id"]),
            latency_ms=float(value["latency_ms"]),
            input_tokens=int(value["input_tokens"]),
            output_tokens=int(value["output_tokens"]),
            model_rounds=int(value["model_rounds"]),
            tool_rounds=int(value["tool_rounds"]),
            success=bool(value["success"]),
            first_call_input_tokens=(
                int(value["first_call_input_tokens"])
                if value.get("first_call_input_tokens") is not None
                else None
            ),
            cached_input_tokens=int(value.get("cached_input_tokens", 0)),
            ttft_ms=(float(value["ttft_ms"]) if value.get("ttft_ms") is not None else None),
        )
        sample.validate()
        return sample

    def validate(self) -> None:
        """Reject invalid samples before they can distort rollout decisions."""
        if not self.case_id:
            raise ValueError("case_id is required")
        if self.latency_ms < 0 or (self.ttft_ms is not None and self.ttft_ms < 0):
            raise ValueError("latency values must be non-negative")
        for value in (
            self.input_tokens,
            self.output_tokens,
            self.model_rounds,
            self.tool_rounds,
            self.cached_input_tokens,
        ):
            if value < 0:
                raise ValueError("token and round counts must be non-negative")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")

    @property
    def uncached_input_tokens(self) -> int:
        return self.input_tokens - self.cached_input_tokens


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("at least one value is required")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return float(ordered[index])


def _reduction_pct(baseline: float, candidate: float) -> float:
    if baseline <= 0:
        raise ValueError("baseline must be greater than zero")
    return (1 - candidate / baseline) * 100


def run_offline_benchmark(
    *,
    iterations: int = 500,
    cases: Sequence[OfflineCase] = DEFAULT_CASES,
) -> dict[str, Any]:
    """Measure selector latency and normalized contract reduction without Azure."""
    if iterations < 1:
        raise ValueError("iterations must be at least one")
    if not cases:
        raise ValueError("at least one benchmark case is required")

    from ai.core.tools.capabilities import (
        MAX_INITIAL_TOOLS,
        capability_catalog,
        select_capabilities,
        serialized_contract_bytes,
    )

    baseline_tools = tuple(entry.tool for entry in capability_catalog())
    baseline_bytes = serialized_contract_bytes(baseline_tools)
    case_results: list[dict[str, Any]] = []
    all_timings_ms: list[float] = []

    for case in cases:
        expected = select_capabilities(
            case.query,
            lookup_type=case.lookup_type,
            profile=ALL_VIEW_PROFILE,
            authenticated=True,
        )
        timings_ms: list[float] = []
        for _ in range(iterations):
            started = time.perf_counter_ns()
            selection = select_capabilities(
                case.query,
                lookup_type=case.lookup_type,
                profile=ALL_VIEW_PROFILE,
                authenticated=True,
            )
            timings_ms.append((time.perf_counter_ns() - started) / 1_000_000)
            if selection != expected:
                raise AssertionError(f"selector is not deterministic for {case.case_id}")

        selected_bytes = serialized_contract_bytes(expected.tools)
        reduction = _reduction_pct(baseline_bytes, selected_bytes)
        all_timings_ms.extend(timings_ms)
        case_results.append({
            "case_id": case.case_id,
            "lookup_type": case.lookup_type,
            "pack_ids": expected.pack_ids,
            "tool_count": len(expected.tools),
            "tool_ids": expected.tool_ids,
            "contract_bytes": selected_bytes,
            "contract_reduction_pct": round(reduction, 3),
            "selector_median_ms": round(statistics.median(timings_ms), 6),
            "selector_p95_ms": round(_percentile(timings_ms, 0.95), 6),
        })

    reductions = [result["contract_reduction_pct"] for result in case_results]
    tool_counts = [result["tool_count"] for result in case_results]
    selector_p95_ms = _percentile(all_timings_ms, 0.95)
    median_reduction = statistics.median(reductions)
    max_tool_count = max(tool_counts)
    gates = {
        "contract_reduction_at_least_65_pct": median_reduction >= 65,
        "ordinary_tool_count_at_most_12": max_tool_count <= MAX_INITIAL_TOOLS,
        "selector_p95_at_most_10_ms": selector_p95_ms <= 10,
    }

    return {
        "baseline": {
            "tool_count": len(baseline_tools),
            "contract_bytes": baseline_bytes,
            "measurement": "normalized_local_contract_bytes_not_provider_tokens",
        },
        "iterations_per_case": iterations,
        "cases": case_results,
        "summary": {
            "median_contract_reduction_pct": round(median_reduction, 3),
            "median_tool_count": statistics.median(tool_counts),
            "max_tool_count": max_tool_count,
            "selector_median_ms": round(statistics.median(all_timings_ms), 6),
            "selector_p95_ms": round(selector_p95_ms, 6),
        },
        "acceptance": {**gates, "all_pass": all(gates.values())},
    }


def load_live_samples(path: Path) -> list[LiveTurnSample]:
    """Load content-free JSONL turn samples."""
    samples: list[LiveTurnSample] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            samples.append(LiveTurnSample.from_mapping(value))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid sample at {path}:{line_number}") from exc
    if not samples:
        raise ValueError(f"no samples found in {path}")
    return samples


def _median_optional(values: Iterable[float | int | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return statistics.median(present) if present else None


def summarize_live_samples(samples: Sequence[LiveTurnSample]) -> dict[str, Any]:
    """Summarize completed-turn provider metrics without payload data."""
    if not samples:
        raise ValueError("at least one live sample is required")
    latency = [sample.latency_ms for sample in samples]
    return {
        "sample_count": len(samples),
        "case_count": len({sample.case_id for sample in samples}),
        "median_latency_ms": statistics.median(latency),
        "p95_latency_ms": _percentile(latency, 0.95),
        "median_ttft_ms": _median_optional(sample.ttft_ms for sample in samples),
        "median_input_tokens": statistics.median(sample.input_tokens for sample in samples),
        "median_first_call_input_tokens": _median_optional(
            sample.first_call_input_tokens for sample in samples
        ),
        "median_cached_input_tokens": statistics.median(
            sample.cached_input_tokens for sample in samples
        ),
        "median_uncached_input_tokens": statistics.median(
            sample.uncached_input_tokens for sample in samples
        ),
        "median_output_tokens": statistics.median(sample.output_tokens for sample in samples),
        "median_model_rounds": statistics.median(sample.model_rounds for sample in samples),
        "median_tool_rounds": statistics.median(sample.tool_rounds for sample in samples),
        "success_rate": sum(sample.success for sample in samples) / len(samples),
    }


def compare_live_samples(
    baseline_samples: Sequence[LiveTurnSample],
    enforced_samples: Sequence[LiveTurnSample],
) -> dict[str, Any]:
    """Compare broad-tool and enforced-pack live samples against rollout gates."""
    baseline = summarize_live_samples(baseline_samples)
    enforced = summarize_live_samples(enforced_samples)
    first_baseline = baseline["median_first_call_input_tokens"]
    first_enforced = enforced["median_first_call_input_tokens"]
    first_call_reduction = (
        _reduction_pct(first_baseline, first_enforced)
        if first_baseline is not None and first_enforced is not None
        else None
    )
    deltas = {
        "median_latency_improvement_pct": _reduction_pct(
            baseline["median_latency_ms"], enforced["median_latency_ms"]
        ),
        "p95_latency_regression_pct": (enforced["p95_latency_ms"] / baseline["p95_latency_ms"] - 1)
        * 100,
        "median_total_input_token_reduction_pct": _reduction_pct(
            baseline["median_input_tokens"], enforced["median_input_tokens"]
        ),
        "median_first_call_input_token_reduction_pct": first_call_reduction,
        "median_uncached_input_token_reduction_pct": _reduction_pct(
            baseline["median_uncached_input_tokens"],
            enforced["median_uncached_input_tokens"],
        ),
        "success_rate_delta_percentage_points": (
            enforced["success_rate"] - baseline["success_rate"]
        )
        * 100,
        "median_model_round_delta": (
            enforced["median_model_rounds"] - baseline["median_model_rounds"]
        ),
        "median_tool_round_delta": (
            enforced["median_tool_rounds"] - baseline["median_tool_rounds"]
        ),
    }
    gates = {
        "median_latency_at_least_20_pct_faster": (deltas["median_latency_improvement_pct"] >= 20),
        "p95_latency_no_more_than_5_pct_regression": (deltas["p95_latency_regression_pct"] <= 5),
        "median_total_input_tokens_at_least_40_pct_lower": (
            deltas["median_total_input_token_reduction_pct"] >= 40
        ),
        "median_first_call_input_tokens_at_least_50_pct_lower": (
            first_call_reduction is not None and first_call_reduction >= 50
        ),
        "success_rate_no_more_than_2_points_lower": (
            deltas["success_rate_delta_percentage_points"] >= -2
        ),
        "median_model_rounds_not_higher": deltas["median_model_round_delta"] <= 0,
    }
    return {
        "baseline": baseline,
        "enforced": enforced,
        "deltas": deltas,
        "acceptance": {**gates, "all_pass": all(gates.values())},
    }


def _setup_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
    import django
    from django.apps import apps

    if not apps.ready:
        django.setup()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline-samples", type=Path)
    parser.add_argument("--enforced-samples", type=Path)
    parser.add_argument("--fail-on-threshold", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.baseline_samples) != bool(args.enforced_samples):
        parser.error("--baseline-samples and --enforced-samples must be provided together")

    _setup_django()
    report: dict[str, Any] = {"offline": run_offline_benchmark(iterations=args.iterations)}
    if args.baseline_samples and args.enforced_samples:
        report["live"] = compare_live_samples(
            load_live_samples(args.baseline_samples),
            load_live_samples(args.enforced_samples),
        )

    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)

    passed = report["offline"]["acceptance"]["all_pass"] and (
        "live" not in report or report["live"]["acceptance"]["all_pass"]
    )
    return 1 if args.fail_on_threshold and not passed else 0


if __name__ == "__main__":
    raise SystemExit(main())
