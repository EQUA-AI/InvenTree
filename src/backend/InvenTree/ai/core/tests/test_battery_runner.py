"""S14 battery-runner tests — injected httpx transport, zero live calls."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from ai.core.evals import run_battery


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(run_battery, "_throttle", lambda: None)
    monkeypatch.setattr(run_battery.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(run_battery, "_last_request", 0.0)


class FakeServer:
    """A minimal deployment double for every path the runner touches."""

    def __init__(self):
        self.chat_calls: list[dict] = []
        self.chat_statuses: list[int] = []
        self.preflight = {
            "fits": True,
            "store": {"healthy": True, "shared": True},
            "pilot_stopped": False,
            "tokens": {},
            "requests": {},
        }
        self.assistant_message = {
            "role": "assistant",
            "content": "There are 28 recorded work orders. [1]",
            "response_state": "complete",
            "entities": [{"id": "machine:11", "ref": "machine:11", "label": "Inverter A"}],
            "evidence_analysis": {
                "response_state": "complete",
                "active_scope": {"display_label": "Inverter A", "version": 1},
                "claims": [
                    {
                        "claim_id": "c1",
                        "claim_role": "answer",
                        "evidence_classification": "calculated",
                        "citation_ordinals": [1],
                        "entity_refs": ["machine:11"],
                    }
                ],
                "citations": [{"ordinal": 1, "source_id": "calc_1", "available": True}],
                "coverage": {
                    "population_count": 28,
                    "returned_count": 25,
                    "complete_population": True,
                },
                "incomplete_reasons": [],
            },
            "model_versions": {"chat": "gpt-5.6-luna-2026-05-01"},
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == run_battery.PREFLIGHT_PATH:
            return httpx.Response(200, json=self.preflight)
        if path == "/api/assets/machine/":
            return httpx.Response(
                200,
                json=[
                    {
                        "pk": 11,
                        "name": "Analysis Eval SI-3000 Inverter A",
                        "serial": "EVAL-SI3000-A",
                    },
                    {
                        "pk": 12,
                        "name": "Analysis Eval SI-3000 Inverter B",
                        "serial": "EVAL-SI3000-B",
                    },
                    {"pk": 99, "name": "RAG Eval HX-200 Heat Exchanger", "serial": "EVAL-HX200"},
                    {"pk": 98, "name": "Analysis Eval WTP Influent Pump", "serial": "EVAL-WTP-IP1"},
                    {"pk": 97, "name": "Analysis Eval Test Bench TB-1", "serial": "EVAL-TB1"},
                    {
                        "pk": 96,
                        "name": "Analysis Eval SI-3000M Marine Inverter",
                        "serial": "EVAL-SI3000M",
                    },
                    {
                        "pk": 95,
                        "name": "Analysis Eval SI-300 String Inverter",
                        "serial": "EVAL-SI300",
                    },
                ],
            )
        if path == run_battery.PROPOSALS_PATH:
            return httpx.Response(200, json={"results": []})
        if path == run_battery.CHAT_PATH:
            body = json.loads(request.content)
            self.chat_calls.append(body)
            status = self.chat_statuses.pop(0) if self.chat_statuses else 200
            if status == 409:
                return httpx.Response(409, json={"detail": "turn_already_running"})
            return httpx.Response(
                200,
                json={
                    "thread_id": body["thread_id"],
                    "message": self.assistant_message["content"],
                    "agent": "aimms",
                    "workflow_used": "analysis_executor",
                },
            )
        if path.startswith("/api/ai/threads/") and path.endswith("/scope"):
            if request.method == "PUT":
                return httpx.Response(200, json={"version": 1})
            return httpx.Response(200, json={"version": 1})
        if path.startswith("/api/ai/threads/"):
            return httpx.Response(200, json={"messages": [self.assistant_message]})
        return httpx.Response(404, json={"detail": path})


@pytest.fixture()
def server(monkeypatch):
    fake = FakeServer()

    def fake_client(base_url: str):
        return httpx.Client(
            transport=httpx.MockTransport(fake.handler), base_url="http://battery.test"
        )

    monkeypatch.setattr(run_battery, "_client", fake_client)
    monkeypatch.setenv("AIMMS_BATTERY_BASE_URL", "http://battery.test")
    return fake


SMALL_BATTERY = """
schema_version: 1
dataset: fixture
fixture_set_versions: [aimms-analysis-fixtures-v1]
cases:
  - id: FB01
    scope: {machine_fixture_keys: [solar_a]}
    question: How many work orders are recorded?
    expected_intent: record_retrieval
    expected_behavior_by_tier: {1: answer}
    complete_population_required: true
    required_assertions: [scope_persisted, evidence_entails_claims, no_governed_effect]
  - id: FB02
    scope: {machine_fixture_keys: [solar_a]}
    question: Summarize recent repairs.
    expected_intent: record_retrieval
    forbidden_entity_fixture_keys: [hx200]
    required_assertions: [scope_persisted]
repeat_tranche:
  case_ids: [FB02]
  repetitions: 2
"""


def _run(tmp_path: Path, server, *extra: str) -> tuple[int, dict, list[dict]]:
    battery_path = tmp_path / "battery.yaml"
    battery_path.write_text(SMALL_BATTERY, encoding="utf-8")
    journal_dir = tmp_path / "journals"
    out = tmp_path / "report.json"
    code = run_battery.main([
        "--cases",
        str(battery_path),
        "--journal-dir",
        str(journal_dir),
        "--json-out",
        str(out),
        "--tier",
        "0",
        "--allow-unverified-flags",
        "--seed",
        "7",
        *extra,
    ])
    report = json.loads(out.read_text()) if out.exists() else {}
    records = []
    for journal in sorted(journal_dir.glob("*.jsonl")):
        records.extend(json.loads(line) for line in journal.read_text().splitlines())
    return code, report, records


def test_full_pass_journals_and_scores_every_turn(tmp_path, server):
    code, report, records = _run(tmp_path, server)
    # Tier 0: expected_behavior_by_tier has no tier-0 entry -> behavior "",
    # both cases score on deterministic layers and pass.
    assert code == 0, report
    assert set(report["per_case"]) == {"FB01", "FB02"}
    assert report["per_case"]["FB01"]["outcomes"] == ["pass"]
    kinds = [record["kind"] for record in records]
    assert kinds[0] == "preflight"
    assert "turn_attempt" in kinds
    assert "turn_score" in kinds
    assert kinds[-1] == "summary"
    summary = records[-1]
    attempts = [r for r in records if r["kind"] == "turn_attempt"]
    assert summary["request_count_actual"] == len(attempts) == 2
    # Fresh thread per case, never reused.
    threads = {r["thread_id"] for r in attempts}
    assert len(threads) == 2
    # Exact sent bytes journaled.
    assert attempts[0]["sent"]["message"]


def test_forbidden_entity_in_the_answer_fails_and_names_the_layer(tmp_path, server):
    server.assistant_message = dict(
        server.assistant_message,
        content="The HX-200 heat exchanger had similar repairs.",
        evidence_analysis=None,
        entities=None,
    )
    code, report, _ = _run(tmp_path, server)
    assert code == 1
    failing = [f for f in report["failures"] if f["case_id"] == "FB02"]
    assert failing, report["failures"]
    assert any("forbidden entity" in layer["detail"] for layer in failing[0]["layers"])


def test_409_serialization_reuses_the_same_idempotency_key(tmp_path, server):
    server.chat_statuses = [409, 200]
    code, _report, records = _run(tmp_path, server)
    assert code in (0, 1)
    attempts = [r for r in records if r["kind"] == "turn_attempt"]
    first_case = [r for r in attempts if r["case_id"] == attempts[0]["case_id"]]
    assert len(first_case) == 2
    assert first_case[0]["idempotency_key"] == first_case[1]["idempotency_key"]
    assert [r["attempt"] for r in first_case] == [1, 2]


def test_pilot_stop_latch_refuses_preflight(tmp_path, server):
    server.preflight["pilot_stopped"] = True
    code, _, _ = _run(tmp_path, server)
    assert code == 2


def test_unfitting_quota_refuses_preflight(tmp_path, server):
    server.preflight["fits"] = False
    code, _, _ = _run(tmp_path, server)
    assert code == 2


def test_model_identity_drift_aborts_the_run(tmp_path, server, monkeypatch):
    monkeypatch.setenv("AIMMS_BATTERY_EXPECTED_MODELS", "gpt-5.6-luna-2026-05-01")
    server.assistant_message = dict(
        server.assistant_message, model_versions={"chat": "gpt-5.7-swapped"}
    )
    code, _, records = _run(tmp_path, server)
    assert code == 2
    assert any(r["kind"] == "abort" and "drift" in r["reason"] for r in records)


def test_tranche_runs_only_the_preregistered_cases(tmp_path, server):
    code, report, records = _run(tmp_path, server, "--tranche")
    assert code == 0
    assert set(report["per_case"]) == {"FB02"}
    assert report["per_case"]["FB02"]["outcomes"] == ["pass", "pass"]
    attempts = [r for r in records if r["kind"] == "turn_attempt"]
    assert len(attempts) == 2  # 2 repetitions x 1 turn


def test_journal_dir_inside_the_repo_is_refused(tmp_path, server, capsys):
    battery_path = tmp_path / "battery.yaml"
    battery_path.write_text(SMALL_BATTERY, encoding="utf-8")
    code = run_battery.main([
        "--cases",
        str(battery_path),
        "--journal-dir",
        str(Path(run_battery.__file__).parent),
        "--allow-unverified-flags",
    ])
    assert code == 2
    assert "outside" in capsys.readouterr().err


def test_missing_base_url_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AIMMS_BATTERY_BASE_URL", raising=False)
    assert run_battery.main(["--journal-dir", str(tmp_path)]) == 2
