"""Per-intent stage-latency targets (S13, §8.9 SLO table).

Initial engineering SLOs for the supervised pilot — targets, not claims
about current capability (baseline battery: median 9.3 s, p95 68.2 s).
Telemetry only: one structured log line plus two allowlisted span attrs per
turn; no wire change, no gating.
"""

from __future__ import annotations

#: class -> (p50_s, p95_s, hard_turn_bound_s), mirroring §8.9.
SLO_TARGETS: dict[str, tuple[int, int, int]] = {
    "lookup": (10, 30, 45),
    "aggregate": (15, 40, 55),
    "synthesis": (20, 45, 60),
    "deterministic": (1, 2, 5),
}

#: Task intents that classify as server aggregate/trend work. Values are
#: real ``TaskIntent`` members (S7 fixed the phantom "trend"/"comparison"
#: strings that silently classed trend turns as lookups).
_AGGREGATE_INTENTS = frozenset({"fleet_aggregate", "trend_analysis"})

#: Deterministic boundary responses (no model in the loop).
_DETERMINISTIC_WORKFLOWS = frozenset({
    "safety_refusal",
    "injection_refusal",
    "advisory_intent",
    "analysis_unavailable",
})


#: M1 (plan §9.5 / GR-31): per-stage targets beside the turn classes —
#: (p50_s, p95_s, hard_s). The memory section is budgeted at 150 ms p95
#: in-region with a 400 ms hard cap (the builder degrades past it).
STAGE_TARGETS: dict[str, tuple[float, float, float]] = {
    "memory_context": (0.10, 0.15, 0.40),
}


def stage_breach(duration_s: float, stage: str) -> str | None:
    """The worst stage threshold ``duration_s`` crossed, or None inside p50."""
    targets = STAGE_TARGETS.get(stage)
    if targets is None:
        return None
    p50, p95, hard = targets
    if duration_s > hard:
        return "hard"
    if duration_s > p95:
        return "p95"
    if duration_s > p50:
        return "p50"
    return None


def slo_class_for(workflow_id: str | None, task_intent: str | None) -> str:
    """Map a turn's route to its §8.9 latency class."""
    if workflow_id in _DETERMINISTIC_WORKFLOWS:
        return "deterministic"
    if task_intent in _AGGREGATE_INTENTS:
        return "aggregate"
    if task_intent == "manual_wo_comparison":
        return "synthesis"
    return "lookup"


def slo_breach(duration_s: float, slo_class: str) -> str | None:
    """The worst threshold ``duration_s`` crossed, or None inside p50."""
    targets = SLO_TARGETS.get(slo_class)
    if targets is None:
        return None
    p50, p95, hard = targets
    if duration_s > hard:
        return "hard"
    if duration_s > p95:
        return "p95"
    if duration_s > p50:
        return "p50"
    return None


__all__ = ["SLO_TARGETS", "STAGE_TARGETS", "slo_breach", "slo_class_for", "stage_breach"]
