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


# --------------------------------------------------------------------------- #
# D1 (M1 gate): route facts, flag skips, report fields                        #
# --------------------------------------------------------------------------- #
MEMORY_BATTERY = """
schema_version: 1
dataset: fixture
fixture_set_versions: [aimms-analysis-fixtures-v1]
cases:
  - id: M-MEM-01
    rail: wf8
    scope: {machine_fixture_keys: [solar_a]}
    required_assertions: [scope_persisted]
    turns:
      - question: Which machine is in scope?
        expected_workflow: analysis_executor
        required_entity_fixture_keys: [solar_a]
      - question: And its documents?
        expected_workflow: analysis_executor
        expect_conversation_summary_present: true
        forbidden_entity_fixture_keys: [hx200]
  - id: M-MEM-05
    rail: reasoning
    requires_flags: [FEATURE_VOICE_LIVE_DIAGNOSIS]
    turns:
      - question: Diagnose it.
      - question: And then?
"""


def _run_memory(tmp_path: Path, server, *extra: str, dossier: dict | None = None):
    battery_path = tmp_path / "memory_battery.yaml"
    battery_path.write_text(MEMORY_BATTERY, encoding="utf-8")
    journal_dir = tmp_path / "journals"
    out = tmp_path / "report.json"
    argv = [
        "--cases",
        str(battery_path),
        "--journal-dir",
        str(journal_dir),
        "--json-out",
        str(out),
        "--tier",
        "1",
        "--seed",
        "3",
        *extra,
    ]
    if dossier is not None:
        dossier_path = tmp_path / "dossier.json"
        dossier_path.write_text(json.dumps(dossier), encoding="utf-8")
        argv += ["--dossier", str(dossier_path)]
    else:
        argv.append("--allow-unverified-flags")
    code = run_battery.main(argv)
    report = json.loads(out.read_text()) if out.exists() else {}
    records = []
    for journal in sorted(journal_dir.glob("*.jsonl")):
        records.extend(json.loads(line) for line in journal.read_text().splitlines())
    return code, report, records, battery_path


def test_route_facts_from_a_d0_image_reach_layer_two(tmp_path, server):
    """A D0 image projects the facts; layer 2 asserts the intent and reports the slot."""
    server.assistant_message = dict(
        server.assistant_message,
        task_intent="record_retrieval",
        conversation_summary_present=False,
        workflow_id="analysis_executor",
    )
    code, report, records, _ = _run_memory(tmp_path, server)
    assert code == 0, report
    scores = [r for r in records if r["kind"] == "turn_score" and r["case_id"] == "M-MEM-01"]
    assert [r["summary_present"] for r in scores] == [False, False]
    assert scores[1]["expected_summary_present"] is True
    assert scores[1]["rail"] == "wf8"
    assert scores[1]["workflow_used"] == "analysis_executor"
    layer_two = next(layer for layer in scores[1]["layers"] if layer["layer"] == 2)
    assert layer_two["status"] == "pass"
    assert "mismatch, reported" in layer_two["detail"]
    # The report carries the per-turn rows the campaign folds.
    rows = [row for row in report["per_turn"] if row["case_id"] == "M-MEM-01"]
    assert [row["turn_index"] for row in rows] == [0, 1]
    assert rows[1]["deterministic_pass"] is True
    assert rows[1]["forbidden_hits"] == []


def test_old_images_without_route_facts_keep_layer_two_skipping(tmp_path, server):
    code, _report, records, _ = _run_memory(tmp_path, server)
    assert code == 0
    scores = [r for r in records if r["kind"] == "turn_score" and r["case_id"] == "M-MEM-01"]
    assert scores[0]["summary_present"] is None
    layer_two = next(layer for layer in scores[0]["layers"] if layer["layer"] == 2)
    # expected_workflow is asserted from workflow_used; the intent skip is honest.
    assert layer_two["status"] == "pass"


def test_required_key_miss_on_a_follow_up_fails_coverage(tmp_path, server):
    server.assistant_message = dict(
        server.assistant_message,
        content="Nothing on file.",
        entities=None,
        evidence_analysis=None,
    )
    code, report, _records, _ = _run_memory(tmp_path, server)
    assert code == 1
    failing = [f for f in report["failures"] if f["case_id"] == "M-MEM-01"]
    assert any(
        "required entity missing: solar_a" in layer["detail"] for layer in failing[0]["layers"]
    )


def test_a_dark_required_flag_skips_the_case_with_a_journaled_reason(tmp_path, server):
    dossier = {
        "flags": [
            {"env_name": "FEATURE_VOICE_LIVE_DIAGNOSIS", "default": False, "effective": False},
        ]
    }
    code, report, records, _ = _run_memory(tmp_path, server, dossier=dossier)
    assert code == 0, report
    assert "M-MEM-05" not in report["per_case"]
    assert report["skipped_cases"] == [
        {
            "case_id": "M-MEM-05",
            "rail": "reasoning",
            "pass": 1,
            "reason": "requires FEATURE_VOICE_LIVE_DIAGNOSIS=on; captured False",
        }
    ]
    skip = next(r for r in records if r["kind"] == "case_skip")
    assert skip["case_id"] == "M-MEM-05" and skip["rail"] == "reasoning"
    # No request was spent on the skipped case.
    assert not [r for r in records if r["kind"] == "turn_attempt" and r["case_id"] == "M-MEM-05"]


def test_an_uncaptured_flag_never_skips(tmp_path, server):
    dossier = {"flags": [{"env_name": "FEATURE_OTHER", "default": True, "effective": True}]}
    code, report, _records, _ = _run_memory(tmp_path, server, dossier=dossier)
    assert code in (0, 1)
    assert "M-MEM-05" in report["per_case"]
    assert report["skipped_cases"] == []


def test_config_effective_shape_is_read_too():
    flags = {"settings": {"feature_voice_live_diagnosis": False}}
    assert run_battery._flag_value(flags, "FEATURE_VOICE_LIVE_DIAGNOSIS") is False
    assert run_battery._flag_value({}, "FEATURE_VOICE_LIVE_DIAGNOSIS") is None
    rows = {"flags": [{"env_name": "FEATURE_X", "default": True, "effective": None}]}
    assert run_battery._flag_value(rows, "FEATURE_X") is True


def test_report_and_journal_header_carry_the_battery_identity(tmp_path, server, monkeypatch):
    monkeypatch.setenv("AIMMS_BATTERY_ENV_LABEL", "dev")
    monkeypatch.setenv("AIMMS_BATTERY_REVISION", "aimms-dev--abc123")
    code, report, records, battery_path = _run_memory(tmp_path, server)
    assert code in (0, 1)
    import hashlib

    expected = hashlib.sha256(battery_path.read_bytes()).hexdigest()
    assert report["battery_sha256"] == expected
    assert report["battery"] == "memory_battery.yaml"
    assert report["env_label"] == "dev"
    assert report["code_revision"] == "aimms-dev--abc123"
    header = records[0]
    assert header["kind"] == "preflight"
    assert header["battery_sha256"] == expected
    assert header["env_label"] == "dev"
    assert header["revision"] == "aimms-dev--abc123"
    assert "flags" not in header and "resolved" not in header


def test_signed_subject_auth_rides_the_runner_client(monkeypatch):
    """With the env set, every /api/ai/ request carries a freshly minted subject."""
    from ai.core.evals import live_auth

    monkeypatch.setenv("AIMMS_BATTERY_SIGNED_SUBJECT_USER", "yesworkorders")
    monkeypatch.setenv("AIMMS_BATTERY_DJANGO_TOKEN", "drf")
    minted = []

    def fake_auth():
        return live_auth.SignedSubjectAuth(
            "yesworkorders",
            django_token="drf",
            signer=lambda user: (minted.append(user.pk), f"sub-{len(minted)}")[1],
            user_loader=lambda _n: type(
                "U", (), {"pk": 9, "is_staff": False, "is_superuser": False}
            )(),
        )

    monkeypatch.setattr(run_battery, "auth_from_env", fake_auth)
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get("Authorization")))
        return httpx.Response(200, json={})

    client = run_battery._client("http://battery.test")
    client._transport = httpx.MockTransport(handler)
    client.get("/api/ai/quota/preflight")
    client.get("/api/ai/threads/t1")
    client.get("/api/assets/machine/")
    assert seen == [
        ("/api/ai/quota/preflight", "Bearer sub-1"),
        ("/api/ai/threads/t1", "Bearer sub-2"),
        ("/api/assets/machine/", "Token drf"),
    ]
