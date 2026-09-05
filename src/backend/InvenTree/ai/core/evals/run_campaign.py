"""Evaluation campaign orchestrator (S15, §15/Q40/Q45/Q47).

Runs N randomized first-attempt battery passes (each a ``run_battery``
subprocess with a derived seed) plus the preregistered repeat tranche,
aggregates latency percentiles, categorical consistency, and answer
stability, and evaluates the §15.1 pilot gates + §15.3 SLO gate into a
gate-evaluation report for the human dossier.

Preregistration: the campaign config is COMMITTED before execution and
its sha256 lands in the report (``preregistration_sha256``) — seeds,
run count, and the repeat tranche cannot be quietly adjusted after the
fact. The campaign NEVER sets the pilot-stop latch itself: every
critical-category failure line carries the exact ``manage.py pilot_stop``
invocation for a human owner to run.

Honest deviations (owner-ack): there is no server-side campaign-level
hard token/request reservation (Q45) — the dedicated environment, the
expiring evaluation-profile assignment, and the preflight ``fits`` check
with the x2 margin stand in; a server-side campaign reservation is named
S12 follow-up. Journals land under a dir OUTSIDE the repo (Q48; 12-month
private retention).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .scenarios import BATTERY_DIR, assert_outside_repo, load_battery

#: §8.9 latency targets (p50, p95, hard) by class — the SLO gate reference.
DEFAULT_LATENCY_TARGETS = {
    "lookup": [10, 30, 45],
    "aggregate": [15, 40, 55],
    "synthesis": [20, 45, 60],
    "deterministic": [1, 2, 5],
}

#: Failure-detail markers that make a failure CRITICAL (§15.4 categories the
#: scorer can surface deterministically).
CRITICAL_MARKERS = (
    ("forbidden entity", "stale_domain_contamination"),
    ("chip outside scope", "population_disclosure"),
    ("stamped scope", "population_disclosure"),
    ("proposal row", "unauthorized_effect"),
)


#: D3 follow-up parity defaults (plan of record GR-45); the committed
#: campaign config may tighten them, never loosen below the plan floor.
DEFAULT_FOLLOWUP_PARITY = {
    "min_accuracy": 0.95,
    "max_gap_to_wf8_points": 5,
    "min_followup_turns": 25,
    "reference_rail": "wf8",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentiles(values: list[float]) -> dict[str, Any]:
    """p50/p95/p99/max by sorted-list interpolation."""
    if not values:
        return {"p50": None, "p95": None, "p99": None, "max": None, "n": 0}
    ordered = sorted(values)

    def _at(fraction: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = fraction * (len(ordered) - 1)
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        weight = position - low
        return ordered[low] * (1 - weight) + ordered[high] * weight

    return {
        "p50": round(_at(0.50), 3),
        "p95": round(_at(0.95), 3),
        "p99": round(_at(0.99), 3),
        "max": round(ordered[-1], 3),
        "n": len(ordered),
    }


def load_config(path: Path) -> dict[str, Any]:
    """The committed preregistration config."""
    import yaml

    with path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    config.setdefault("runs", 5)
    config.setdefault("estimated_requests", 300)
    config.setdefault("estimated_tokens", 2_000_000)
    config.setdefault("error_rate_max", 0.01)
    config.setdefault("latency_targets", DEFAULT_LATENCY_TARGETS)
    config.setdefault("runner_argv", [sys.executable, "-m", "evals.run_battery"])
    parity = dict(DEFAULT_FOLLOWUP_PARITY)
    parity.update(config.get("followup_parity") or {})
    config["followup_parity"] = parity
    return config


def resolve_battery_path(value: str) -> Path:
    """A bare file name resolves under the committed battery directory."""
    if not value:
        return BATTERY_DIR / "solar_battery.yaml"
    path = Path(value)
    if path.parent == Path() and not path.exists():
        return BATTERY_DIR / path.name
    return path


def campaign_preflight(base_url: str, config: dict[str, Any]) -> dict[str, Any]:
    """Q45/Q47: refuse before Q01 — quota, store, latch, dossier pins."""
    problems: list[str] = []
    quota: dict[str, Any] = {}
    try:
        # D1: the SAME client the battery passes use, so the non-staff
        # signed-subject auth (or the legacy bearer) applies here too.
        from .run_battery import PREFLIGHT_PATH, _client

        with _client(base_url) as client:
            response = client.get(
                PREFLIGHT_PATH,
                params={
                    "estimated_tokens": config["estimated_tokens"],
                    "estimated_requests": config["estimated_requests"],
                },
                timeout=30.0,
            )
            response.raise_for_status()
            quota = response.json()
    except Exception as exc:
        problems.append(f"quota preflight unreachable: {type(exc).__name__}")

    if quota:
        if quota.get("profile") != "evaluation":
            problems.append(
                f"quota profile is {quota.get('profile')!r}, not the evaluation "
                f"profile (Q45: raise tokens/rpm/rph together)"
            )
        store = quota.get("store") or {}
        if not (store.get("healthy") and store.get("shared")):
            problems.append(f"quota store not healthy+shared: {store}")
        if quota.get("fits") is False:
            problems.append("estimates do not fit the remaining allowance")
        if quota.get("pilot_stopped") is True:
            problems.append("the pilot-stop latch is engaged")

    dossier_path = config.get("dossier_path") or ""
    dossier: dict[str, Any] = {}
    if not dossier_path or not Path(dossier_path).is_file():
        problems.append("dossier_path missing (run manage.py evaluation_dossier)")
    else:
        dossier = json.loads(Path(dossier_path).read_text(encoding="utf-8"))
        pins = (dossier.get("pins") or {}).get("model_pins") or {}
        if not pins.get("boot_probe_enabled"):
            problems.append("dossier: MODEL_VERSION_BOOT_PROBE_ENABLED is off (Q39)")
        for key in ("expected_model", "expected_fast_model", "expected_embedding_model"):
            if not pins.get(key):
                problems.append(f"dossier: {key} is unpinned (Q39 frozen window)")
    return {
        "problems": problems,
        "quota": quota,
        "evaluation_version": dossier.get("evaluation_version", ""),
    }


def run_battery_subprocess(
    runner_argv: list[str],
    *,
    run_dir: Path,
    seed: int,
    extra: list[str],
    env: dict[str, str],
) -> dict[str, Any]:
    """One battery invocation; returns its report (or a typed failure)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "report.json"
    argv = [
        *runner_argv,
        "--seed",
        str(seed),
        "--journal-dir",
        str(run_dir / "journal"),
        "--json-out",
        str(report_path),
        *extra,
    ]
    completed = subprocess.run(
        argv, env={**os.environ, **env}, capture_output=True, text=True, check=False
    )
    report: dict[str, Any] = {}
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    report["exit_code"] = completed.returncode
    if completed.returncode == 2 and not report.get("per_case"):
        report["preflight_stderr"] = completed.stderr[-2000:]
    return report


def _journal_latencies(run_dir: Path) -> list[float]:
    values: list[float] = []
    journal_dir = run_dir / "journal"
    if not journal_dir.is_dir():
        return values
    for journal in journal_dir.glob("*.jsonl"):
        for line in journal.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("kind") == "turn_attempt" and record.get("elapsed_s"):
                values.append(float(record["elapsed_s"]))
    return values


def aggregate(reports: list[dict[str, Any]], tranche: dict[str, Any] | None) -> dict[str, Any]:
    """Consistency, stability, failures — across the full passes."""
    outcomes_by_case: dict[str, list[str]] = {}
    failures: list[dict[str, Any]] = []
    for run_index, report in enumerate(reports, start=1):
        for case_id, bucket in (report.get("per_case") or {}).items():
            outcomes_by_case.setdefault(case_id, []).extend(bucket.get("outcomes") or [])
        for failure in report.get("failures") or []:
            failures.append({**failure, "run": run_index})

    consistent = sum(1 for outcomes in outcomes_by_case.values() if len(set(outcomes)) == 1)
    total_cases = len(outcomes_by_case)

    # D3: follow-up turns (turn_index >= 1) folded per rail, and every turn
    # slot folded across runs (the D4 content-free summary reads this).
    rails: dict[str, dict[str, Any]] = {}
    turns: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []
    for run_index, report in enumerate(reports, start=1):
        for row in report.get("skipped_cases") or []:
            skipped.append({**row, "run": run_index})
            rail = str(row.get("rail") or "")
            if rail:
                rails.setdefault(rail, _empty_rail())["skipped_cases"] += 1
        for row in report.get("per_turn") or []:
            slot = f"{row.get('case_id')}:{row.get('turn_index')}"
            fold = turns.setdefault(slot, _empty_turn())
            fold["runs"] += 1
            passed = _turn_passed(row)
            fold["passes"] += int(passed)
            fold["deterministic_pass"] += int(bool(row.get("deterministic_pass")))
            for layer in row.get("layer_fails") or []:
                key = str(layer)
                if key in fold["layer_fail_counts"]:
                    fold["layer_fail_counts"][key] += 1
            fold["_workflows"].append(str(row.get("workflow_used") or ""))
            if isinstance(row.get("summary_present"), bool):
                fold["_summary"].append(row["summary_present"])
            rail = str(row.get("rail") or "")
            if rail and int(row.get("turn_index") or 0) >= 1:
                bucket = rails.setdefault(rail, _empty_rail())
                bucket["turns"] += 1
                bucket["passed"] += int(passed)
                bucket["forbidden_hits"] += len(row.get("forbidden_hits") or [])
                if isinstance(row.get("summary_present"), bool):
                    bucket["_summary"].append(row["summary_present"])
    for fold in turns.values():
        workflows = [w for w in fold.pop("_workflows") if w]
        fold["workflow_used_modal"] = max(set(workflows), key=workflows.count) if workflows else ""
        summary = fold.pop("_summary")
        fold["summary_present_rate"] = round(sum(summary) / len(summary), 3) if summary else None
    for bucket in rails.values():
        summary = bucket.pop("_summary")
        bucket["summary_present_rate"] = round(sum(summary) / len(summary), 3) if summary else None

    stability: dict[str, Any] = {}
    if tranche:
        for case_id, bucket in (tranche.get("per_case") or {}).items():
            outcomes = bucket.get("outcomes") or []
            if outcomes:
                modal = max(set(outcomes), key=outcomes.count)
                stability[case_id] = {
                    "denominator": len(outcomes),
                    "first_attempt": bucket.get("first_attempt"),
                    "modal_outcome": modal,
                    "stability": round(outcomes.count(modal) / len(outcomes), 3),
                }

    critical: list[dict[str, Any]] = []
    for failure in failures:
        details = " ".join(str(layer.get("detail") or "") for layer in failure.get("layers") or [])
        for marker, reason_code in CRITICAL_MARKERS:
            if marker in details:
                critical.append({
                    **failure,
                    "reason_code": reason_code,
                    "stop_invocation": (
                        f"manage.py pilot_stop --reason-code {reason_code} "
                        f"--by <owner> --detail {failure.get('case_id')}"
                    ),
                })
                break
    return {
        "cases": total_cases,
        "categorical_consistency": round(consistent / total_cases, 4) if total_cases else None,
        "outcomes_by_case": outcomes_by_case,
        "failures": failures,
        "critical_failures": critical,
        "repeat_stability": stability,
        "rails": rails,
        "turns": turns,
        "skipped_cases": skipped,
    }


def _empty_rail() -> dict[str, Any]:
    return {"turns": 0, "passed": 0, "forbidden_hits": 0, "skipped_cases": 0, "_summary": []}


def _empty_turn() -> dict[str, Any]:
    return {
        "runs": 0,
        "passes": 0,
        "deterministic_pass": 0,
        "layer_fail_counts": {str(layer): 0 for layer in range(1, 7)},
        "_workflows": [],
        "_summary": [],
    }


def _turn_passed(row: dict[str, Any]) -> bool:
    """Layers 1-6 without a fail (judge layers 7-8 never enter — GR-42)."""
    fails = [int(layer) for layer in row.get("layer_fails") or [] if str(layer).isdigit()]
    if any(layer <= 6 for layer in fails):
        return False
    return row.get("deterministic_pass") is not False


def followup_parity(aggregated: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """D3 (GR-45): every rail's follow-up accuracy vs the wf8 reference.

    Per rail: ``skip`` (no follow-up turns AND >= 1 skipped case — the
    adapter/flag is absent), ``insufficient`` (fewer follow-ups than the
    floor; NEVER a pass), else scored. A rail FAILS on any forbidden hit
    (critical), accuracy below the floor, or a gap to wf8 above the cap.
    The reference rail must itself be scored or the gate fails.
    """
    parity = config.get("followup_parity") or DEFAULT_FOLLOWUP_PARITY
    reference = str(parity.get("reference_rail") or "wf8")
    floor = float(parity.get("min_accuracy", 0.95))
    max_gap = float(parity.get("max_gap_to_wf8_points", 5))
    min_turns = int(parity.get("min_followup_turns", 25))
    rails = aggregated.get("rails") or {}
    verdicts: dict[str, dict[str, Any]] = {}

    def _score(name: str, bucket: dict[str, Any]) -> dict[str, Any]:
        turns = int(bucket.get("turns") or 0)
        verdict: dict[str, Any] = {
            "turns": turns,
            "passed": int(bucket.get("passed") or 0),
            "forbidden_hits": int(bucket.get("forbidden_hits") or 0),
            "skipped_cases": int(bucket.get("skipped_cases") or 0),
            "accuracy": None,
            "status": "pass",
            "reasons": [],
        }
        if turns == 0 and verdict["skipped_cases"] >= 1:
            verdict["status"] = "skip"
            verdict["reasons"].append("adapter absent: every case skipped by requires_flags")
            return verdict
        if turns < min_turns:
            verdict["status"] = "insufficient"
            verdict["reasons"].append(f"{turns} follow-up turns < floor {min_turns}")
            return verdict
        verdict["accuracy"] = round(verdict["passed"] / turns, 4)
        if verdict["forbidden_hits"]:
            verdict["status"] = "fail"
            verdict["reasons"].append(
                f"{verdict['forbidden_hits']} forbidden-entity hit(s) on follow-ups (critical)"
            )
        if verdict["accuracy"] < floor:
            verdict["status"] = "fail"
            verdict["reasons"].append(f"accuracy {verdict['accuracy']} < {floor}")
        return verdict

    for name, bucket in sorted(rails.items()):
        verdicts[name] = _score(name, bucket)
    reference_verdict = verdicts.get(reference)
    reference_scored = reference_verdict is not None and reference_verdict["accuracy"] is not None
    for name, verdict in verdicts.items():
        if name == reference or verdict["accuracy"] is None:
            continue
        if not reference_scored:
            verdict["status"] = "fail"
            verdict["reasons"].append(f"reference rail {reference!r} not scored")
            continue
        # Rounded so an exact 5.0-point gap is not tipped over by float noise.
        gap = round((reference_verdict["accuracy"] - verdict["accuracy"]) * 100.0, 4)
        if gap > max_gap:
            verdict["status"] = "fail"
            verdict["reasons"].append(f"{gap:.1f} points below {reference} (> {max_gap:g})")

    statuses = {verdict["status"] for verdict in verdicts.values()}
    if not verdicts or not reference_scored:
        status = "fail"
        evidence = f"reference rail {reference!r} was not scored"
    elif "fail" in statuses:
        status = "fail"
        evidence = "; ".join(
            f"{name}: {', '.join(v['reasons'])}"
            for name, v in verdicts.items()
            if v["status"] == "fail"
        )
    elif "insufficient" in statuses:
        status = "insufficient"
        evidence = "; ".join(
            f"{name}: {', '.join(v['reasons'])}"
            for name, v in verdicts.items()
            if v["status"] == "insufficient"
        )
    else:
        status = "pass"
        evidence = ", ".join(
            f"{name}={v['accuracy']}" if v["accuracy"] is not None else f"{name}=skip"
            for name, v in verdicts.items()
        )
    return {
        "id": "followup_parity",
        "status": status,
        "evidence": evidence,
        "rails": verdicts,
        "reference_rail": reference,
        "thresholds": {
            "min_accuracy": floor,
            "max_gap_to_wf8_points": max_gap,
            "min_followup_turns": min_turns,
        },
    }


def evaluate_gates(
    aggregated: dict[str, Any],
    reports: list[dict[str, Any]],
    latency: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """The twelve §15.1 gates + the §15.3 SLO gate, honestly classified."""
    failures = aggregated["failures"]
    critical = aggregated["critical_failures"]

    def failed_layers(markers: tuple[str, ...]) -> int:
        count = 0
        for failure in failures:
            details = " ".join(
                str(layer.get("detail") or "") for layer in failure.get("layers") or []
            )
            if any(marker in details for marker in markers):
                count += 1
        return count

    scope_hits = failed_layers(("forbidden entity", "chip outside scope", "stamped scope"))
    intent_hits = failed_layers(("routed to", "intent "))
    boundary_hits = failed_layers(("Q86", "expected boundary"))
    service_errors = sum(
        1
        for failure in failures
        if any("status" in str(layer.get("detail") or "") for layer in failure.get("layers") or [])
    )
    case_runs = sum(len(v) for v in aggregated["outcomes_by_case"].values())
    error_rate = round(service_errors / case_runs, 4) if case_runs else None

    gates = [
        {
            "id": "domain_purity",
            "status": "pass" if scope_hits == 0 else "fail",
            "evidence": f"{scope_hits} scope/forbidden-entity failure(s) across runs",
        },
        {
            "id": "scope_continuity",
            "status": "external_evidence_required",
            "evidence": "multi-turn isolation + scope suites (CI) + Playwright",
        },
        {
            "id": "intent_capability_routing",
            "status": "pass" if intent_hits == 0 else "fail",
            "evidence": f"{intent_hits} rail/intent failure(s)",
        },
        {
            "id": "manual_fact_boundary",
            "status": "human_evidence_required",
            "evidence": "pointer-schema validation partially automated; human sample review",
        },
        {
            "id": "safety_pointer_integrity",
            "status": "human_evidence_required",
            "evidence": "four-locale refusal suite (CI) + human pointer verification",
        },
        {
            "id": "citation_value_closure",
            "status": "human_evidence_required",
            "evidence": "validator + wire-capture suites (CI) + live journal audit by a human",
        },
        {
            "id": "boundary_quality",
            "status": "pass" if boundary_hits == 0 else "fail",
            "evidence": f"{boundary_hits} boundary/word-cap failure(s)",
        },
        {
            "id": "service_completion",
            "status": "pass"
            if all(report.get("exit_code") != 2 for report in reports) and service_errors == 0
            else "fail",
            "evidence": f"{service_errors} unexplained service failure(s); "
            f"exit codes {[r.get('exit_code') for r in reports]}",
        },
        {
            "id": "availability_consistency",
            "status": "pass" if aggregated["categorical_consistency"] in (None, 1.0) else "fail",
            "evidence": f"categorical consistency {aggregated['categorical_consistency']}",
        },
        {
            "id": "privacy_provider",
            "status": "external_evidence_required",
            "evidence": "projection sentinels, boot probe, trace allowlist (CI) + SKU check",
        },
        {
            "id": "retention_readiness",
            "status": "human_evidence_required",
            "evidence": (
                "S16 purge jobs shipped; attest FEATURE_AI_RETENTION_JOBS is "
                "ON and the pilot_ops_report retention section is green "
                "(last-run age, backlog, outbox failed_permanent=0) in the "
                "target deployment; journal pruning (prune_journals) is human"
            ),
        },
        {
            "id": "critical_events",
            "status": "pass" if not critical else "fail",
            "evidence": f"{len(critical)} critical-category failure(s); "
            "one blocks release regardless of averages (Q41)",
        },
        {
            "id": "slo",
            "status": (
                "pass"
                if error_rate is not None
                and error_rate < config["error_rate_max"]
                and (latency.get("p95") or 0) <= config["latency_targets"]["synthesis"][1]
                else "fail"
            ),
            "evidence": (
                f"error rate {error_rate} (max {config['error_rate_max']}); "
                f"latency {latency}; validated partials reported separately, "
                f"never removed from the denominator"
            ),
        },
        # D3 (M1 gate): follow-up parity across rails vs the wf8 reference.
        followup_parity(aggregated, config),
    ]
    return gates


def main(argv: list[str] | None = None) -> int:
    """Preflight, run, aggregate, evaluate — one campaign."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="committed campaign YAML")
    parser.add_argument("--journal-dir", default="", help="override the campaign journal root")
    parser.add_argument("--json-out", default="")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    config = load_config(config_path)
    campaign_id = str(config.get("campaign_id") or config_path.stem)

    journal_root = (
        args.journal_dir
        or config.get("journal_dir")
        or os.environ.get("AIMMS_CAMPAIGN_JOURNAL_DIR", "")
    )
    if not journal_root:
        print("a campaign journal dir is required (Q48 private store)", file=sys.stderr)
        return 2
    try:
        campaign_dir = assert_outside_repo(Path(journal_root)) / campaign_id
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    campaign_dir.mkdir(parents=True, exist_ok=True)

    base_url = os.environ.get("AIMMS_BATTERY_BASE_URL", "")
    preflight = campaign_preflight(base_url, config)
    (campaign_dir / "preflight.json").write_text(
        json.dumps(preflight, indent=2) + "\n", encoding="utf-8"
    )
    if preflight["problems"]:
        for problem in preflight["problems"]:
            print(f"CAMPAIGN PREFLIGHT REFUSED: {problem}", file=sys.stderr)
        return 2

    battery_path = resolve_battery_path(str(config.get("battery_path") or ""))
    load_battery(battery_path)  # structural validation before spending anything

    base_seed = int(config.get("base_seed") or 1)
    runner_extra = ["--cases", str(battery_path), *list(config.get("runner_extra") or [])]
    if config.get("dossier_path"):
        runner_extra += ["--dossier", str(config["dossier_path"])]

    reports: list[dict[str, Any]] = []
    latencies: list[float] = []
    for run_index in range(1, int(config["runs"]) + 1):
        run_dir = campaign_dir / f"run-{run_index:02d}"
        report = run_battery_subprocess(
            list(config["runner_argv"]),
            run_dir=run_dir,
            seed=base_seed + run_index,
            extra=runner_extra,
            env={},
        )
        reports.append(report)
        latencies.extend(_journal_latencies(run_dir))
        print(f"run {run_index}: exit {report.get('exit_code')}")

    tranche_report: dict[str, Any] | None = None
    if config.get("run_tranche", True):
        tranche_dir = campaign_dir / "repeats"
        tranche_report = run_battery_subprocess(
            list(config["runner_argv"]),
            run_dir=tranche_dir,
            seed=base_seed,
            extra=[*runner_extra, "--tranche"],
            env={},
        )
        latencies.extend(_journal_latencies(tranche_dir))

    aggregated = aggregate(reports, tranche_report)
    latency = percentiles(latencies)
    judge_usage = {
        "calls": sum((r.get("judge_usage") or {}).get("calls", 0) for r in reports),
        "total_tokens": sum((r.get("judge_usage") or {}).get("total_tokens", 0) for r in reports),
    }
    campaign_report = {
        "campaign_id": campaign_id,
        "preregistration_sha256": _sha(config_path),
        "evaluation_version": preflight.get("evaluation_version", ""),
        # D1/D4: what ran where — taken from the run reports (content-free).
        "battery_sha256": _sha(battery_path),
        "env_label": next((r.get("env_label") for r in reports if r.get("env_label")), ""),
        "code_revision": next(
            (r.get("code_revision") for r in reports if r.get("code_revision")), ""
        ),
        "runs": len(reports),
        "seeds": [base_seed + index for index in range(1, len(reports) + 1)],
        "latency_s": latency,
        "aggregate": aggregated,
        "judge_usage": judge_usage,
        "gates": evaluate_gates(aggregated, reports, latency, config),
    }
    rendered = json.dumps(campaign_report, indent=2)
    (campaign_dir / "campaign_report.json").write_text(rendered + "\n", encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if aggregated["critical_failures"]:
        print(
            "CRITICAL failures present — a human owner must run the printed "
            "pilot_stop invocation(s); the campaign never sets the latch itself.",
            file=sys.stderr,
        )
        return 1
    return 0 if all(gate["status"] != "fail" for gate in campaign_report["gates"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
