"""D4 (M1 gate): content-free baseline summaries and the candidate delta."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from ai.core.evals import baseline, run_battery

if TYPE_CHECKING:
    from pathlib import Path


def _campaign_report(*, revision="rev-a", sha="b" * 64, wf8=1.0, rbac=0.97, layer4=0, det=5):
    return {
        "campaign_id": "memory-baseline-dev",
        "preregistration_sha256": "p" * 64,
        "evaluation_version": "ev1",
        "battery_sha256": sha,
        "code_revision": revision,
        "env_label": "dev",
        "seeds": [11, 12, 13, 14, 15],
        "runs": 5,
        "aggregate": {
            "critical_failures": [],
            "turns": {
                "M-MEM-01:1": {
                    "runs": 5,
                    "passes": det,
                    "deterministic_pass": det,
                    "layer_fail_counts": {"1": 0, "2": 0, "3": 0, "4": layer4, "5": 0, "6": 0},
                    "workflow_used_modal": "wf8",
                    "summary_present_rate": 0.0,
                }
            },
        },
        "gates": [
            {
                "id": "followup_parity",
                "status": "pass" if rbac >= 0.95 else "fail",
                "rails": {
                    "wf8": {
                        "accuracy": wf8,
                        "turns": 130,
                        "passed": int(130 * wf8),
                        "forbidden_hits": 0,
                        "skipped_cases": 0,
                        "status": "pass",
                    },
                    "rbac_run": {
                        "accuracy": rbac,
                        "turns": 35,
                        "passed": int(35 * rbac),
                        "forbidden_hits": 0,
                        "skipped_cases": 0,
                        "status": "pass" if rbac >= 0.95 else "fail",
                    },
                    "reasoning": {
                        "accuracy": None,
                        "turns": 0,
                        "passed": 0,
                        "forbidden_hits": 0,
                        "skipped_cases": 3,
                        "status": "skip",
                    },
                },
            }
        ],
    }


def test_summary_is_content_free_and_carries_the_gate_facts():
    summary = baseline.summarize_report(_campaign_report())
    baseline._assert_content_free(summary)
    assert summary["battery_sha256"] == "b" * 64
    assert summary["code_revision"] == "rev-a"
    assert summary["parity_status"] == "pass"
    assert summary["rails"]["reasoning"]["status"] == "skip"
    assert summary["turns"]["M-MEM-01:1"]["layer_fail_counts"]["4"] == 0
    assert summary["turns"]["M-MEM-01:1"]["workflow_used_modal"] == "wf8"


def test_free_text_is_refused_in_a_committed_summary():
    summary = baseline.summarize_report(_campaign_report())
    summary["turns"]["M-MEM-01:1"]["answer"] = "The HX-200 heat exchanger had similar repairs."
    with pytest.raises(ValueError, match="free text"):
        baseline._assert_content_free(summary)


def test_merge_keys_by_environment_then_revision():
    first = baseline.summarize_report(_campaign_report(revision="rev-a"))
    second = baseline.summarize_report(_campaign_report(revision="rev-b"))
    merged = baseline.merge_summary({}, "dev", first)
    merged = baseline.merge_summary(merged, "dev", second)
    merged = baseline.merge_summary(merged, "exp", first)
    assert merged["schema_version"] == baseline.SCHEMA_VERSION
    assert set(merged["environments"]["dev"]) == {"rev-a", "rev-b"}
    assert set(merged["environments"]["exp"]) == {"rev-a"}


def test_compare_flags_a_layer_regression_and_a_lower_pass_rate():
    base = baseline.summarize_report(_campaign_report(revision="rev-a"))
    cand = baseline.summarize_report(_campaign_report(revision="rev-b", layer4=2, det=3))
    delta = baseline.compare(base, cand)
    assert delta["parity_pass"] is True
    slots = {(r["slot"], r["layer"]) for r in delta["regressions"]}
    assert ("M-MEM-01:1", 4) in slots
    assert ("M-MEM-01:1", "deterministic_pass_rate") in slots


def test_compare_refuses_battery_drift_unless_allowed():
    base = baseline.summarize_report(_campaign_report(sha="b" * 64))
    cand = baseline.summarize_report(_campaign_report(sha="c" * 64))
    with pytest.raises(ValueError, match="battery drift"):
        baseline.compare(base, cand)
    delta = baseline.compare(base, cand, allow_battery_drift=True)
    assert delta["notes"] and "battery_sha256 differs" in delta["notes"][0]


def test_summarize_cli_writes_the_committed_file(tmp_path: Path):
    report_path = tmp_path / "campaign_report.json"
    report_path.write_text(json.dumps(_campaign_report()), encoding="utf-8")
    out = tmp_path / "baselines" / "memory_battery.json"
    code = run_battery.main([
        "--summarize",
        "--env",
        f"dev={report_path}",
        "--json-out",
        str(out),
    ])
    assert code == 0
    data = json.loads(out.read_text())
    assert data["environments"]["dev"]["rev-a"]["parity_status"] == "pass"
    # Re-running merges (a second revision) instead of clobbering.
    report_path.write_text(json.dumps(_campaign_report(revision="rev-b")), encoding="utf-8")
    assert (
        run_battery.main(["--summarize", "--env", f"dev={report_path}", "--json-out", str(out)])
        == 0
    )
    assert set(json.loads(out.read_text())["environments"]["dev"]) == {"rev-a", "rev-b"}


def test_compare_cli_exit_codes(tmp_path: Path, monkeypatch):
    committed = tmp_path / "memory_battery.json"
    merged = baseline.merge_summary(
        {}, "dev", baseline.summarize_report(_campaign_report(revision="rev-a"))
    )
    merged = baseline.merge_summary(
        merged, "dev", baseline.summarize_report(_campaign_report(revision="rev-b", rbac=0.9))
    )
    merged = baseline.merge_summary(
        merged, "dev", baseline.summarize_report(_campaign_report(revision="rev-c"))
    )
    committed.write_text(json.dumps(merged), encoding="utf-8")
    monkeypatch.setattr(baseline, "SUMMARY_PATH", committed)
    delta_out = tmp_path / "delta.json"
    # rev-b fails parity -> BLOCK (exit 1), delta written.
    code = run_battery.main([
        "--baseline",
        "rev-a",
        "--candidate",
        "rev-b",
        "--env",
        f"dev={delta_out}",
    ])
    assert code == 1
    delta = json.loads(delta_out.read_text())
    assert delta["parity_pass"] is False
    # rev-c passes parity with no regression -> OK (exit 0).
    assert run_battery.main(["--baseline", "rev-a", "--candidate", "rev-c", "--env", "dev=-"]) == 0
    # Unknown revision -> refused (exit 2).
    assert run_battery.main(["--baseline", "rev-a", "--candidate", "rev-x", "--env", "dev=-"]) == 2


def test_offline_flags_never_need_a_deployment(monkeypatch, capsys):
    monkeypatch.delenv("AIMMS_BATTERY_BASE_URL", raising=False)
    assert run_battery.main(["--summarize"]) == 2
    assert "--env" in capsys.readouterr().err


def test_main_namespace_shape_matches_the_runner_parser():
    """baseline.main reads exactly the fields run_battery's parser defines."""
    args = SimpleNamespace(
        summarize=False, baseline="", candidate="", env=[], allow_battery_drift=False, json_out=""
    )
    assert baseline.main(args) == 2
