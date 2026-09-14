"""Deterministic Phase V scoring. No provider, network or database access."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Literal

from ai.core.voice.timing import VoiceClientTiming
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Workflow = Literal[
    "work_orders", "approvals", "inventory", "closeout", "procedures", "draft_workflows"
]
FAMILIES = set(Workflow.__args__)


class ValidationAttempt(BaseModel):
    """One predeclared attempt, including not-run/setup-failed cases and reruns."""

    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    rerun_of: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    scenario: str = Field(pattern=r"^S(?:0[1-9]|1[0-9]|2[0-2])$")
    workflow: Workflow
    accent: Literal["US", "CA", "GB"]
    input_voice: Literal["Jenny", "Andrew", "Clara", "Liam", "Sonia", "Ryan", "human"]
    rate: Literal["base", "plus15"]
    setup_ref: str = Field(pattern=r"^[a-f0-9]{32}$")
    route: Literal["headset", "earbuds", "speaker", "virtual"]
    mode: Literal["continuous", "push_to_talk"]
    temperature: Literal["cold", "warm"]
    task_kind: Literal["read", "preview"]
    method: Literal["unit", "emulated", "synthetic_real_provider", "human"]
    status: Literal["not_run", "setup_failed", "failed", "timeout", "completed"]
    provider_submitted: bool = False
    provider_turn_ref: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    hands_free_eligible: bool
    exclusion: Literal["email_paused", "screen_policy", "unavailable_dependency"] | None = None
    completion: Literal["none", "hands_free", "hybrid", "screen"] = "none"
    timing: VoiceClientTiming = Field(default_factory=VoiceClientTiming)
    audio_measurement: Literal["unmeasured", "acoustic_observed"] = "unmeasured"
    audio_evidence_ref: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    audible_ack_ms: float | None = None
    useful_audio_ms: float | None = None
    canonical_evidence_ref: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    wrong_effects: int | None = Field(default=None, ge=0, le=1000)
    duplicate_effects: int | None = Field(default=None, ge=0, le=1000)
    stale_effects: int | None = Field(default=None, ge=0, le=1000)
    unauthorized_effects: int | None = Field(default=None, ge=0, le=1000)
    review_integrity: bool | None = None
    outcome_honesty: bool | None = None
    critical_total: int = Field(default=0, ge=0, le=1000)
    critical_correct: int = Field(default=0, ge=0, le=1000)
    unnecessary_hold: bool | None = None
    correction_succeeded: bool | None = None
    interrupted_review: bool | None = None
    dropped_turn: bool | None = None

    @field_validator("audible_ack_ms", "useful_audio_ms", mode="before")
    @classmethod
    def latency(cls, value):
        return VoiceClientTiming.numeric_interval(value)

    @model_validator(mode="after")
    def consistent(self):
        """Reject misleading provenance and contradictory denominators."""
        if self.method == "human" and self.input_voice != "human":
            raise ValueError("Human participants are not synthetic input voices")
        if (
            self.method != "human"
            and self.input_voice
            not in {"US": {"Jenny", "Andrew"}, "CA": {"Clara", "Liam"}, "GB": {"Sonia", "Ryan"}}[
                self.accent
            ]
        ):
            raise ValueError("Voice/accent mismatch")
        if self.hands_free_eligible == (self.exclusion is not None):
            raise ValueError("Eligibility requires an explicit exclusion disposition")
        if self.status != "completed" and self.completion != "none":
            raise ValueError("Incomplete attempt cannot claim task completion")
        if not self.hands_free_eligible and self.completion == "hands_free":
            raise ValueError("Excluded action cannot count as hands-free")
        if self.status == "not_run" and (
            self.timing.model_dump(exclude_none=True)
            or self.audible_ack_ms is not None
            or self.useful_audio_ms is not None
            or self.critical_total
        ):
            raise ValueError("Unrun attempt cannot contain measurements")
        if self.critical_correct > self.critical_total:
            raise ValueError("Correct fields exceed attempted fields")
        if self.status in ("not_run", "setup_failed") and self.provider_submitted:
            raise ValueError("Unstarted attempt cannot count as a provider turn")
        if self.provider_submitted and self.method not in ("synthetic_real_provider", "human"):
            raise ValueError("Simulation cannot count as real-provider evidence")
        if self.provider_submitted and not self.provider_turn_ref:
            raise ValueError("Real-provider sample requires its distinct observed-turn reference")
        if (self.audible_ack_ms is not None or self.useful_audio_ms is not None) and (
            self.audio_measurement != "acoustic_observed" or not self.audio_evidence_ref
        ):
            raise ValueError("Playback proxies cannot qualify audible latency")
        return self


class ValidationCampaign(BaseModel):
    """Content-free provenance and the full planned denominator, not successes only."""

    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    source_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    image_digest: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    flags_evidence_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    earcons_enabled: bool
    output_locale: Literal["en-US"] = "en-US"
    output_voice: Literal["en-US-AvaNeural"] = "en-US-AvaNeural"
    vocabulary_owner: Literal["AIMMS"] = "AIMMS"
    attempts: list[ValidationAttempt] = Field(min_length=6, max_length=10000)

    @model_validator(mode="after")
    def denominator(self):
        """Reruns supplement, never overwrite original attempts."""
        seen = {}
        provider_turns = set()
        for attempt in self.attempts:
            if attempt.id in seen:
                raise ValueError("Duplicate attempt ID")
            if attempt.rerun_of:
                original = seen.get(attempt.rerun_of)
                if not original or original.rerun_of:
                    raise ValueError("Rerun must reference an earlier primary attempt")
                if (
                    original.workflow,
                    original.scenario,
                    original.accent,
                    original.rate,
                    original.setup_ref,
                    original.mode,
                ) != (
                    attempt.workflow,
                    attempt.scenario,
                    attempt.accent,
                    attempt.rate,
                    attempt.setup_ref,
                    attempt.mode,
                ):
                    raise ValueError("Rerun changes the declared case")
            seen[attempt.id] = attempt
            if attempt.provider_submitted:
                if attempt.provider_turn_ref in provider_turns:
                    raise ValueError("One provider turn cannot inflate multiple attempt samples")
                provider_turns.add(attempt.provider_turn_ref)
        if {a.workflow for a in self.attempts if not a.rerun_of} != FAMILIES:
            raise ValueError("Predeclare all six workflow families, including exclusions")
        return self


def percentile(values, quantile):
    """Nearest-rank percentile; empty is unknown, never zero."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)] if ordered else None


def distribution(values, denominator):
    values = [value for value in values if value is not None]
    return {
        "samples": len(values),
        "missing": denominator - len(values),
        "p50_ms": percentile(values, 0.5),
        "p95_ms": percentile(values, 0.95),
    }


def summarize(campaign: ValidationCampaign) -> dict:
    """Never pool accents, setups, modes, rates, cold starts or explicit reruns."""
    groups = defaultdict(list)
    for attempt in campaign.attempts:
        key = (
            attempt.accent,
            attempt.setup_ref,
            attempt.route,
            attempt.mode,
            attempt.temperature,
            attempt.rate,
            attempt.method,
            bool(attempt.rerun_of),
        )
        groups[key].append(attempt)
    rows = []
    stop = []
    for key, planned in sorted(groups.items()):
        attempted = [a for a in planned if a.status != "not_run"]
        eligible = [a for a in attempted if a.hands_free_eligible]
        metrics = {
            name: distribution([getattr(a.timing, name) for a in attempted], len(attempted))
            for name in VoiceClientTiming.model_fields
        }
        metrics["audible_ack_ms"] = distribution(
            [a.audible_ack_ms for a in attempted], len(attempted)
        )
        stop.append(metrics["local_stop_ms"]["p95_ms"])
        for kind in ("read", "preview"):
            subset = [a for a in attempted if a.task_kind == kind]
            metrics[f"useful_audio_{kind}_ms"] = distribution(
                [a.useful_audio_ms for a in subset], len(subset)
            )
        critical_total = sum(a.critical_total for a in attempted)
        rows.append({
            "accent": key[0],
            "setup_ref": key[1],
            "route": key[2],
            "mode": key[3],
            "temperature": key[4],
            "rate": key[5],
            "method": key[6],
            "rerun": key[7],
            "planned": len(planned),
            "attempted": len(attempted),
            "completed": sum(a.status == "completed" for a in attempted),
            "failed": sum(a.status in ("failed", "timeout", "setup_failed") for a in attempted),
            "status_counts": {
                status: sum(a.status == status for a in planned)
                for status in ("not_run", "setup_failed", "failed", "timeout", "completed")
            },
            "eligible_attempted": len(eligible),
            "hands_free_percent": 100
            * sum(a.status == "completed" and a.completion == "hands_free" for a in eligible)
            / len(eligible)
            if eligible
            else None,
            "critical_accuracy_percent": 100
            * sum(a.critical_correct for a in attempted)
            / critical_total
            if critical_total
            else None,
            "critical_total": critical_total,
            "observations": {
                name: {
                    "true": sum(getattr(a, name) is True for a in attempted),
                    "observed": sum(getattr(a, name) is not None for a in attempted),
                }
                for name in (
                    "unnecessary_hold",
                    "correction_succeeded",
                    "interrupted_review",
                    "dropped_turn",
                )
            },
            "metrics": metrics,
        })
    attempted = [a for a in campaign.attempts if a.status != "not_run"]
    effect_fields = ("wrong_effects", "duplicate_effects", "stale_effects", "unauthorized_effects")
    failed_safety = any(
        any((getattr(a, field) or 0) > 0 for field in effect_fields)
        or a.review_integrity is False
        or a.outcome_honesty is False
        for a in attempted
    )
    evidenced = [
        a
        for a in attempted
        if a.canonical_evidence_ref
        and all(getattr(a, field) == 0 for field in effect_fields)
        and a.review_integrity is True
        and a.outcome_honesty is True
    ]
    baseline = {
        accent: sum(
            a.accent == accent and a.rate == "base" and not a.rerun_of and a.provider_submitted
            for a in campaign.attempts
        )
        for accent in ("US", "CA", "GB")
    }
    return {
        "schema_version": 1,
        "run_id": campaign.run_id,
        "source_sha": campaign.source_sha,
        "image_digest": campaign.image_digest,
        "flags_evidence_ref": campaign.flags_evidence_ref,
        "percentile_method": "nearest-rank; missing observations are not zero",
        "planned": len(campaign.attempts),
        "attempted": len(attempted),
        "primary_planned": sum(a.rerun_of is None for a in campaign.attempts),
        "reruns": sum(a.rerun_of is not None for a in campaign.attempts),
        "exclusions": [
            {"id": a.id, "reason": a.exclusion} for a in campaign.attempts if a.exclusion
        ],
        "groups": rows,
        "real_provider_base_turns": baseline,
        "minimum_baseline_met": all(count >= 30 for count in baseline.values()),
        "workflow_coverage": {
            family: {
                "planned": sum(a.workflow == family and not a.rerun_of for a in campaign.attempts),
                "attempted": sum(a.workflow == family and not a.rerun_of for a in attempted),
                "eligible_attempted": sum(
                    a.workflow == family and not a.rerun_of and a.hands_free_eligible
                    for a in attempted
                ),
            }
            for family in sorted(FAMILIES)
        },
        "input_voice_base_turns": {
            voice: sum(
                a.input_voice == voice
                and a.rate == "base"
                and not a.rerun_of
                and a.provider_submitted
                for a in campaign.attempts
            )
            for voice in ("Jenny", "Andrew", "Clara", "Liam", "Sonia", "Ryan", "human")
        },
        "blocking": {
            "safety": "FAIL"
            if failed_safety
            else "PASS"
            if attempted and len(evidenced) == len(attempted)
            else "NOT MEASURED",
            "local_stop": "FAIL"
            if any(v is not None and v > 250 for v in stop)
            else "PASS"
            if any(v is not None for v in stop)
            else "NOT MEASURED",
        },
        "tracking": {
            "ack_p95_target_ms": 1500,
            "ack_prerequisite_met": campaign.earcons_enabled,
            "read_p95_target_ms": 5000,
            "preview_p95_target_ms": 8000,
            "hands_free_target_percent": 90,
        },
        "owner_enforcement_decision": "PENDING; targets do not automatically become blocking",
        "human_signoff": "NOT INFERRED; see VX-T1 through VX-T4",
    }
