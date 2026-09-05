"""D4 (M1 gate): content-free baseline summaries and the candidate delta.

Raw evidence (journals, reports, dossiers) stays in the private store. What
the repo commits — ``baselines/memory_battery.json`` — holds ONLY ints,
floats, enums and hashes per environment x code revision: per-rail
accuracy/turns/forbidden hits/status and, per turn slot, pass counts,
deterministic-pass counts, layer-1..6 fail counts, the modal workflow and
the summary-present rate. Never answer text, never entity names.

``--summarize``  fold one or more ``campaign_report.json`` files (one per
                 ``--env LABEL=path``) into the committed summary.
``--baseline X --candidate Y``  compare two summaries (paths, or code
                 revisions looked up in the committed summary) per env:
                 exit 0 iff ``followup_parity`` passes on every env given
                 AND no regression — a layer clean at baseline that fails
                 on the candidate, or a lower deterministic pass rate.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SUMMARY_PATH = Path(__file__).parent / "baselines" / "memory_battery.json"
SCHEMA_VERSION = 1


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    """One environment's content-free summary from a campaign report."""
    aggregate = report.get("aggregate") or {}
    gates = {gate.get("id"): gate for gate in report.get("gates") or []}
    parity = gates.get("followup_parity") or {}
    rails: dict[str, Any] = {}
    for name, verdict in (parity.get("rails") or {}).items():
        rails[name] = {
            "accuracy": verdict.get("accuracy"),
            "turns": int(verdict.get("turns") or 0),
            "passed": int(verdict.get("passed") or 0),
            "forbidden_hits": int(verdict.get("forbidden_hits") or 0),
            "skipped_cases": int(verdict.get("skipped_cases") or 0),
            "status": str(verdict.get("status") or "not_scored"),
        }
    turns: dict[str, Any] = {}
    for slot, fold in (aggregate.get("turns") or {}).items():
        turns[slot] = {
            "runs": int(fold.get("runs") or 0),
            "passes": int(fold.get("passes") or 0),
            "deterministic_pass": int(fold.get("deterministic_pass") or 0),
            "layer_fail_counts": {
                str(layer): int((fold.get("layer_fail_counts") or {}).get(str(layer)) or 0)
                for layer in range(1, 7)
            },
            "workflow_used_modal": str(fold.get("workflow_used_modal") or ""),
            "summary_present_rate": fold.get("summary_present_rate"),
        }
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "evaluation_version": str(report.get("evaluation_version") or ""),
        "campaign_id": str(report.get("campaign_id") or ""),
        "preregistration_sha256": str(report.get("preregistration_sha256") or ""),
        "battery_sha256": str(report.get("battery_sha256") or ""),
        "code_revision": str(report.get("code_revision") or ""),
        "image_digest": str(report.get("image_digest") or ""),
        "seeds": [int(seed) for seed in report.get("seeds") or []],
        "runs": int(report.get("runs") or 0),
        "parity_status": str(parity.get("status") or "not_scored"),
        "critical_failures": len(aggregate.get("critical_failures") or []),
        "rails": rails,
        "turns": turns,
    }


def _assert_content_free(summary: dict[str, Any], where: str = "summary") -> None:
    """Only ints/floats/bools/None, enum-ish short strings, hashes and ids."""
    allowed_str_keys = {
        "generated_at",
        "evaluation_version",
        "campaign_id",
        "preregistration_sha256",
        "battery_sha256",
        "code_revision",
        "image_digest",
        "parity_status",
        "status",
        "workflow_used_modal",
    }

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, str):
            key = path.rsplit(".", 1)[-1]
            if key not in allowed_str_keys or len(value) > 80 or "\n" in value:
                raise ValueError(
                    f"{where}: free text at {path} is not allowed in a committed summary"
                )

    walk(summary, where)


def merge_summary(existing: dict[str, Any], label: str, summary: dict[str, Any]) -> dict[str, Any]:
    """Insert ``summary`` under ``environments[label][code_revision]``."""
    merged = dict(existing or {})
    merged["schema_version"] = SCHEMA_VERSION
    environments = dict(merged.get("environments") or {})
    env = dict(environments.get(label) or {})
    key = summary.get("code_revision") or "unknown"
    env[key] = summary
    environments[label] = env
    merged["environments"] = environments
    return merged


def _parse_env_args(values: list[str]) -> dict[str, Path]:
    envs: dict[str, Path] = {}
    for value in values:
        label, separator, path = value.partition("=")
        if not separator or not label.strip() or not path.strip():
            raise ValueError(f"--env expects LABEL=path, got {value!r}")
        envs[label.strip()] = Path(path.strip())
    return envs


def _resolve_summary(ref: str, *, committed: dict[str, Any], label: str) -> dict[str, Any]:
    """A summary by path (whole file or one env) or by code revision."""
    path = Path(ref)
    if path.is_file():
        data = _load_json(path)
        environments = data.get("environments")
        if isinstance(environments, dict):
            env = environments.get(label) or {}
            if not env:
                raise KeyError(f"{ref} has no environment {label!r}")
            # newest generated_at wins when several revisions are present
            return max(env.values(), key=lambda item: str(item.get("generated_at")))
        return data
    env = (committed.get("environments") or {}).get(label) or {}
    if ref not in env:
        raise KeyError(f"no committed summary for env {label!r} at revision {ref!r}")
    return env[ref]


def compare(
    baseline: dict[str, Any], candidate: dict[str, Any], *, allow_battery_drift: bool = False
) -> dict[str, Any]:
    """The delta for ONE environment: parity verdict + regressions."""
    notes: list[str] = []
    if baseline.get("battery_sha256") != candidate.get("battery_sha256"):
        if not allow_battery_drift:
            raise ValueError(
                "battery drift: baseline and candidate ran different memory_battery.yaml "
                "(pass --allow-battery-drift to compare anyway; the note is journaled)"
            )
        notes.append("battery_sha256 differs (compared under --allow-battery-drift)")
    regressions: list[dict[str, Any]] = []
    base_turns = baseline.get("turns") or {}
    for slot, cand in (candidate.get("turns") or {}).items():
        base = base_turns.get(slot)
        if not base:
            continue
        for layer in range(1, 7):
            key = str(layer)
            before = int((base.get("layer_fail_counts") or {}).get(key) or 0)
            after = int((cand.get("layer_fail_counts") or {}).get(key) or 0)
            if before == 0 and after > 0:
                regressions.append({
                    "slot": slot,
                    "layer": layer,
                    "baseline": 0,
                    "candidate": after,
                })
        base_rate = _rate(base.get("deterministic_pass"), base.get("runs"))
        cand_rate = _rate(cand.get("deterministic_pass"), cand.get("runs"))
        if base_rate is not None and cand_rate is not None and cand_rate < base_rate:
            regressions.append({
                "slot": slot,
                "layer": "deterministic_pass_rate",
                "baseline": base_rate,
                "candidate": cand_rate,
            })
    parity = str(candidate.get("parity_status") or "not_scored")
    return {
        "parity_status": parity,
        "parity_pass": parity == "pass",
        "regressions": regressions,
        "rails": candidate.get("rails") or {},
        "baseline_revision": baseline.get("code_revision"),
        "candidate_revision": candidate.get("code_revision"),
        "notes": notes,
    }


def _rate(numerator: Any, denominator: Any) -> float | None:
    try:
        if not denominator:
            return None
        return round(float(numerator or 0) / float(denominator), 4)
    except (TypeError, ValueError):
        return None


def main(args: Any) -> int:
    """Entry from ``run_battery.main`` for the offline flags."""
    try:
        envs = _parse_env_args(list(args.env or []))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not envs:
        print("at least one --env LABEL=campaign_report.json is required", file=sys.stderr)
        return 2

    if args.summarize:
        target = Path(args.json_out) if args.json_out else SUMMARY_PATH
        existing = _load_json(target) if target.is_file() else {}
        for label, path in envs.items():
            summary = summarize_report(_load_json(path))
            _assert_content_free(summary, f"{label}")
            existing = merge_summary(existing, label, summary)
            print(
                f"{label}: revision={summary['code_revision'] or 'unknown'} "
                f"parity={summary['parity_status']} rails={len(summary['rails'])} "
                f"turns={len(summary['turns'])}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {target}")
        return 0

    if not (args.baseline and args.candidate):
        print("--baseline and --candidate are both required", file=sys.stderr)
        return 2
    committed = _load_json(SUMMARY_PATH) if SUMMARY_PATH.is_file() else {}
    deltas: dict[str, Any] = {}
    exit_code = 0
    for label, path in envs.items():
        try:
            # --env here names the environment label; the path is where the
            # delta for that env is written (or "-" for stdout only).
            base = _resolve_summary(args.baseline, committed=committed, label=label)
            cand = _resolve_summary(args.candidate, committed=committed, label=label)
            delta = compare(base, cand, allow_battery_drift=bool(args.allow_battery_drift))
        except (KeyError, ValueError) as exc:
            print(f"{label}: {exc}", file=sys.stderr)
            return 2
        deltas[label] = delta
        ok = delta["parity_pass"] and not delta["regressions"]
        exit_code = exit_code if ok else 1
        print(
            f"{label}: parity={delta['parity_status']} regressions={len(delta['regressions'])} "
            f"-> {'OK' if ok else 'BLOCK'}"
        )
        if str(path) != "-":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(delta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(deltas, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return exit_code


__all__ = [
    "SCHEMA_VERSION",
    "SUMMARY_PATH",
    "compare",
    "main",
    "merge_summary",
    "summarize_report",
]
