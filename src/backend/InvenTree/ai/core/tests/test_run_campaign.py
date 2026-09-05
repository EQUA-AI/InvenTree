"""S15 (WP-B7): the campaign orchestrator — fake runner, no network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from ai.core.evals import run_campaign

#: A stand-in runner: parses the battery-runner CLI surface the campaign
#: drives, writes a canned report + journal, exits per an env-free script
#: embedded in its own file. Invoked as a real subprocess (the actual seam).
FAKE_RUNNER = """
import argparse, json, sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--journal-dir", required=True)
parser.add_argument("--json-out", required=True)
parser.add_argument("--cases", default="")
parser.add_argument("--dossier", default="")
parser.add_argument("--tranche", action="store_true")
args = parser.parse_args()

journal = Path(args.journal_dir); journal.mkdir(parents=True, exist_ok=True)
records = [
    {"kind": "preflight", "seed": args.seed},
    {"kind": "turn_attempt", "case_id": "Q01", "elapsed_s": 4.0 + (args.seed % 3)},
    {"kind": "turn_attempt", "case_id": "Q02", "elapsed_s": 9.0},
    {"kind": "summary", "request_count_actual": 2},
]
(journal / "run.jsonl").write_text("\\n".join(json.dumps(r) for r in records))

if args.tranche:
    report = {"per_case": {"Q21": {"outcomes": ["pass"] * 18 + ["fail"] * 2,
                                    "first_attempt": "pass"}},
              "failures": [], "judge_usage": {"calls": 0, "total_tokens": 0}}
else:
    flaky = "pass" if args.seed % 2 == 0 else "partial"
    report = {
        "per_case": {"Q01": {"outcomes": ["pass"], "first_attempt": "pass"},
                     "Q02": {"outcomes": [flaky], "first_attempt": flaky}},
        "failures": [],
        "judge_usage": {"calls": 2, "total_tokens": 500},
    }
Path(args.json_out).write_text(json.dumps(report))
sys.exit(0)
"""


@pytest.fixture()
def campaign_env(tmp_path: Path, monkeypatch):
    runner_path = tmp_path / "fake_runner.py"
    runner_path.write_text(FAKE_RUNNER, encoding="utf-8")
    dossier = tmp_path / "dossier.json"
    dossier.write_text(
        json.dumps({
            "evaluation_version": "abc123",
            "pins": {
                "model_pins": {
                    "boot_probe_enabled": True,
                    "expected_model": "m",
                    "expected_fast_model": "m2",
                    "expected_embedding_model": "m3",
                }
            },
        }),
        encoding="utf-8",
    )
    battery = tmp_path / "battery.yaml"
    battery.write_text(
        """
schema_version: 1
dataset: fixture
cases:
  - id: Q01
    question: a
  - id: Q02
    question: b
repeat_tranche: {case_ids: [Q01], repetitions: 2}
""",
        encoding="utf-8",
    )
    config = tmp_path / "campaign.yaml"
    config.write_text(
        f"""
campaign_id: test-campaign
runs: 3
base_seed: 10
run_tranche: true
battery_path: {battery}
dossier_path: {dossier}
journal_dir: {tmp_path / "journals"}
runner_argv: ["{sys.executable}", "{runner_path}"]
""",
        encoding="utf-8",
    )
    good_preflight = {
        "problems": [],
        "quota": {"profile": "evaluation"},
        "evaluation_version": "abc123",
    }
    monkeypatch.setattr(
        run_campaign, "campaign_preflight", lambda _base_url, _cfg: dict(good_preflight)
    )
    return tmp_path, config


def test_campaign_runs_aggregates_and_evaluates_gates(campaign_env):
    tmp_path, config = campaign_env
    out = tmp_path / "campaign_out.json"
    code = run_campaign.main(["--config", str(config), "--json-out", str(out)])
    # Q02 deliberately flaps across seeds -> the availability gate fails,
    # and a failed gate is a nonzero exit by design.
    assert code == 1
    report = json.loads(out.read_text())

    assert report["campaign_id"] == "test-campaign"
    assert report["runs"] == 3
    assert report["seeds"] == [11, 12, 13]
    assert len(report["preregistration_sha256"]) == 64
    assert report["evaluation_version"] == "abc123"

    # Latency percentiles from the journals (3 runs + tranche = 8 attempts).
    assert report["latency_s"]["n"] == 8
    # Q02 alternates pass/partial by seed -> inconsistent; Q01 consistent.
    assert report["aggregate"]["categorical_consistency"] == pytest.approx(0.5)
    # Tranche stability: 18/20 modal pass.
    stability = report["aggregate"]["repeat_stability"]["Q21"]
    assert stability["denominator"] == 20
    assert stability["stability"] == pytest.approx(0.9)
    assert report["judge_usage"] == {"calls": 6, "total_tokens": 1500}

    gates = {gate["id"]: gate for gate in report["gates"]}
    assert len(gates) == 14  # the twelve §15.1 gates + SLO + D3 followup_parity
    assert gates["domain_purity"]["status"] == "pass"
    assert gates["critical_events"]["status"] == "pass"
    assert gates["availability_consistency"]["status"] == "fail"  # Q02 flapped
    assert gates["retention_readiness"]["status"] == "human_evidence_required"
    # Journal layout (Q48 private store).
    campaign_dir = tmp_path / "journals" / "test-campaign"
    assert (campaign_dir / "preflight.json").is_file()
    assert (campaign_dir / "run-01" / "report.json").is_file()
    assert (campaign_dir / "repeats" / "report.json").is_file()
    assert (campaign_dir / "campaign_report.json").is_file()


def test_critical_failures_print_the_stop_invocation_and_never_latch(campaign_env, capsys):
    tmp_path, config = campaign_env

    def critical_report(runner_argv, *, run_dir, seed, extra, env):
        run_dir.mkdir(parents=True, exist_ok=True)
        return {
            "per_case": {"Q12": {"outcomes": ["fail"], "first_attempt": "fail"}},
            "failures": [
                {
                    "case_id": "Q12",
                    "layers": [{"layer": 3, "detail": "forbidden entity surfaced: hx200:HX-200"}],
                }
            ],
            "exit_code": 1,
        }

    import unittest.mock as mock

    with mock.patch.object(run_campaign, "run_battery_subprocess", critical_report):
        code = run_campaign.main(["--config", str(config)])
    assert code == 1
    err = capsys.readouterr().err
    assert "never sets the latch itself" in err
    report = json.loads(
        (tmp_path / "journals" / "test-campaign" / "campaign_report.json").read_text()
    )
    critical = report["aggregate"]["critical_failures"]
    assert critical[0]["reason_code"] == "stale_domain_contamination"
    assert "pilot_stop --reason-code stale_domain_contamination" in critical[0]["stop_invocation"]
    gates = {gate["id"]: gate for gate in report["gates"]}
    assert gates["critical_events"]["status"] == "fail"


def test_preflight_problems_refuse_the_campaign(campaign_env, monkeypatch, capsys):
    _tmp_path, config = campaign_env
    monkeypatch.setattr(
        run_campaign,
        "campaign_preflight",
        lambda _base_url, _cfg: {
            "problems": ["the pilot-stop latch is engaged"],
            "quota": {},
            "evaluation_version": "",
        },
    )
    code = run_campaign.main(["--config", str(config)])
    assert code == 2
    assert "latch is engaged" in capsys.readouterr().err


def test_campaign_preflight_checks_the_q39_pins(tmp_path):
    """The real preflight (quota mocked out) demands dossier pins."""
    import unittest.mock as mock

    cfg = {
        "estimated_tokens": 1,
        "estimated_requests": 1,
        "dossier_path": str(tmp_path / "missing.json"),
    }
    with mock.patch("httpx.Client") as client:
        client.return_value.__enter__.return_value.get.return_value.json.return_value = {
            "profile": "evaluation",
            "store": {"healthy": True, "shared": True},
            "fits": True,
            "pilot_stopped": False,
        }
        client.return_value.__enter__.return_value.get.return_value.raise_for_status = lambda: None
        result = run_campaign.campaign_preflight("http://x", cfg)
    assert any("dossier_path missing" in problem for problem in result["problems"])

    unpinned = tmp_path / "dossier.json"
    unpinned.write_text(
        json.dumps({"pins": {"model_pins": {"boot_probe_enabled": False}}}),
        encoding="utf-8",
    )
    cfg["dossier_path"] = str(unpinned)
    with mock.patch("httpx.Client") as client:
        client.return_value.__enter__.return_value.get.return_value.json.return_value = {
            "profile": "evaluation",
            "store": {"healthy": True, "shared": True},
            "fits": True,
            "pilot_stopped": False,
        }
        client.return_value.__enter__.return_value.get.return_value.raise_for_status = lambda: None
        result = run_campaign.campaign_preflight("http://x", cfg)
    assert any("BOOT_PROBE" in problem.upper() for problem in result["problems"])
    assert any("expected_model" in problem for problem in result["problems"])


def test_in_repo_journal_dir_is_refused(campaign_env, capsys):
    _tmp_path, config = campaign_env
    code = run_campaign.main([
        "--config",
        str(config),
        "--journal-dir",
        str(Path(run_campaign.__file__).parent),
    ])
    assert code == 2
    assert "outside" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# D3 (M1 gate): followup_parity                                                #
# --------------------------------------------------------------------------- #
def _rows(rail: str, case_id: str, turns: int, *, passes: int, hits: int = 0, runs: int = 1):
    """Per-turn rows for one case: turn 0 plus ``turns - 1`` follow-ups."""
    rows = []
    for run in range(runs):
        for index in range(turns):
            failed = index >= 1 and (run * (turns - 1) + (index - 1)) >= passes
            rows.append({
                "case_id": case_id,
                "turn_index": index,
                "rail": rail,
                "outcome": "fail" if failed else "pass",
                "deterministic_pass": not failed,
                "forbidden_hits": ["hx200:HX-200"]
                if (index >= 1 and hits and run == 0 and index == 1)
                else [],
                "summary_present": False,
                "expected_summary_present": None,
                "workflow_used": "wf8",
                "layer_fails": [4] if failed else [],
            })
    return rows


def _report(*row_groups, skipped=()):
    return {
        "per_case": {},
        "failures": [],
        "per_turn": [row for group in row_groups for row in group],
        "skipped_cases": list(skipped),
    }


def _config(**over):
    base = {"followup_parity": dict(run_campaign.DEFAULT_FOLLOWUP_PARITY)}
    base["followup_parity"].update(over)
    return base


def test_followup_parity_passes_at_floor():
    # wf8: 30 follow-ups all pass; rbac_run: 30 follow-ups, 29 pass (0.9667 >= 0.95,
    # gap 3.3 points <= 5).
    report = _report(
        _rows("wf8", "M-1", 31, passes=30),
        _rows("rbac_run", "M-3", 31, passes=29),
    )
    aggregated = run_campaign.aggregate([report], None)
    gate = run_campaign.followup_parity(aggregated, _config())
    assert gate["status"] == "pass", gate
    assert gate["rails"]["wf8"]["accuracy"] == pytest.approx(1.0)
    assert gate["rails"]["rbac_run"]["accuracy"] == pytest.approx(0.9667, abs=1e-4)
    assert gate["rails"]["rbac_run"]["status"] == "pass"


def test_rail_five_points_below_wf8_fails():
    # rbac_run 0.9667 vs wf8 1.0 is 3.3 points (ok); routing 27/30 = 0.9 fails
    # the floor AND the gap.
    report = _report(
        _rows("wf8", "M-1", 31, passes=30),
        _rows("routing", "M-7", 31, passes=27),
    )
    gate = run_campaign.followup_parity(run_campaign.aggregate([report], None), _config())
    assert gate["status"] == "fail"
    routing = gate["rails"]["routing"]
    assert routing["status"] == "fail"
    assert any("accuracy 0.9 < 0.95" in reason for reason in routing["reasons"])
    assert any("points below wf8" in reason for reason in routing["reasons"])
    # A rail exactly 5 points below is still within the cap when above the floor.
    report = _report(
        _rows("wf8", "M-1", 41, passes=40),
        _rows("rbac_run", "M-3", 41, passes=38),  # 0.95, gap 5.0
    )
    gate = run_campaign.followup_parity(run_campaign.aggregate([report], None), _config())
    assert gate["rails"]["rbac_run"]["status"] == "pass", gate["rails"]["rbac_run"]


def test_forbidden_hit_on_followup_is_critical():
    report = _report(
        _rows("wf8", "M-1", 31, passes=30),
        _rows("rbac_run", "M-3", 31, passes=30, hits=1),
    )
    gate = run_campaign.followup_parity(run_campaign.aggregate([report], None), _config())
    assert gate["status"] == "fail"
    assert gate["rails"]["rbac_run"]["forbidden_hits"] == 1
    assert any("critical" in reason for reason in gate["rails"]["rbac_run"]["reasons"])


def test_absent_adapter_rail_skips_with_reason():
    report = _report(
        _rows("wf8", "M-1", 31, passes=30),
        skipped=[{"case_id": "M-5", "rail": "reasoning", "pass": 1, "reason": "requires X=on"}],
    )
    aggregated = run_campaign.aggregate([report], None)
    assert aggregated["skipped_cases"][0]["rail"] == "reasoning"
    gate = run_campaign.followup_parity(aggregated, _config())
    assert gate["status"] == "pass"
    assert gate["rails"]["reasoning"]["status"] == "skip"
    assert "adapter absent" in gate["rails"]["reasoning"]["reasons"][0]


def test_insufficient_followups_is_not_a_pass():
    report = _report(
        _rows("wf8", "M-1", 31, passes=30),
        _rows("routing", "M-7", 11, passes=10),  # 10 follow-ups < 25
    )
    gate = run_campaign.followup_parity(run_campaign.aggregate([report], None), _config())
    assert gate["status"] == "insufficient"
    assert gate["rails"]["routing"]["status"] == "insufficient"
    assert gate["rails"]["routing"]["accuracy"] is None


def test_unscored_reference_rail_fails_the_gate():
    report = _report(_rows("rbac_run", "M-3", 31, passes=30))
    gate = run_campaign.followup_parity(run_campaign.aggregate([report], None), _config())
    assert gate["status"] == "fail"
    assert "wf8" in gate["evidence"]


def test_aggregate_folds_turn_slots_content_free():
    report = _report(_rows("wf8", "M-1", 3, passes=1, runs=2))
    aggregated = run_campaign.aggregate([report], None)
    slot = aggregated["turns"]["M-1:2"]
    assert slot["runs"] == 2
    assert slot["workflow_used_modal"] == "wf8"
    assert slot["summary_present_rate"] == pytest.approx(0.0)
    assert set(slot["layer_fail_counts"]) == {"1", "2", "3", "4", "5", "6"}
    assert slot["layer_fail_counts"]["4"] >= 1
    assert "_workflows" not in slot and "_summary" not in slot


def test_bare_battery_names_resolve_under_the_committed_dir(tmp_path):
    from ai.core.evals.scenarios import BATTERY_DIR

    assert (
        run_campaign.resolve_battery_path("memory_battery.yaml")
        == BATTERY_DIR / "memory_battery.yaml"
    )
    assert run_campaign.resolve_battery_path("") == BATTERY_DIR / "solar_battery.yaml"
    explicit = tmp_path / "b.yaml"
    explicit.write_text("schema_version: 1\n", encoding="utf-8")
    assert run_campaign.resolve_battery_path(str(explicit)) == explicit


def test_load_config_applies_the_parity_floor_defaults(tmp_path):
    config = tmp_path / "c.yaml"
    config.write_text("campaign_id: x\nfollowup_parity: {min_accuracy: 0.97}\n", encoding="utf-8")
    loaded = run_campaign.load_config(config)
    assert loaded["followup_parity"]["min_accuracy"] == pytest.approx(0.97)
    assert loaded["followup_parity"]["min_followup_turns"] == 25
    assert loaded["followup_parity"]["reference_rail"] == "wf8"


def test_committed_memory_campaign_configs_preregister_the_plan_floor():
    from ai.core.evals.scenarios import BATTERY_DIR

    campaigns = Path(run_campaign.__file__).parent / "campaigns"
    for env in ("dev", "experimental"):
        config = run_campaign.load_config(campaigns / f"memory_baseline.{env}.yaml")
        assert config["campaign_id"] == f"memory-baseline-{env}"
        assert config["runs"] == 5
        assert config["run_tranche"] is False
        assert run_campaign.resolve_battery_path(config["battery_path"]) == (
            BATTERY_DIR / "memory_battery.yaml"
        )
        assert config["followup_parity"] == run_campaign.DEFAULT_FOLLOWUP_PARITY
        assert config["estimated_requests"] >= 57
