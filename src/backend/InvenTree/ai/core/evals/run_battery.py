"""Live battery runner (S14, §13.6) — fresh threads, journals, no silent retries.

Drives the full evaluation battery against a live deployment through the
same HTTP surface real clients use, scores every turn through the layered
scorer, and journals EVERYTHING (exact sent bytes, raw responses, scored
artifacts) to the private store. ``run_golden.py`` stays untouched — the
golden set is a different contract.

Execution protocol (§13.6, owner decisions Q40/Q45/Q47):

- every standalone case runs in a FRESH client-minted thread; every
  M-scenario runs in exactly ONE continuous thread; threads are never
  reused across cases;
- seeded shuffle per pass (M-cases shuffle as units); five passes derive
  seeds ``base + pass_index``; every seed is journaled;
- NO silent retries and no prompt rewrites. The single carve-out is HTTP
  409 turn-serialization (the server finalizing the PREVIOUS turn's tail):
  the idempotency key is minted ONCE per (case, turn, pass) and reused so
  the server dedupes, and every attempt is journaled. A scope-conflict 409
  is never retried;
- preflight before Q01: planned request count computed FROM the scenario
  files (hard cap 300/invocation), quota preflight, model pins, fixture
  resolution, flag posture — all journaled as the run header.

Env contract (AIMMS_BATTERY_*):

    BASE_URL   required             BEARER/COOKIE/CSRF/ORIGIN  auth
    DATASET    fixture|solar        selects battery file + principal
    JOURNAL_DIR  required (or --journal-dir); refuses in-repo paths
    SEED       optional int         RPM  default 8/min
    EXPECTED_MODELS  csv of pinned model identities (drift aborts)
    MACHINE_<KEY>    live machine name for env-resolved fixture keys
    ADMIN_BEARER     staff credential for /config/effective capture only
    AIMMS_GOLD_DIR   private store (question refs + gold atoms)
    AIMMS_JUDGE_CALIBRATION  calibration artifact path (else judge off)
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import scoring
from .calibration import judge_layers_enabled, load_calibration
from .scenarios import (
    BATTERY_DIR,
    BatteryFile,
    ScenarioCase,
    assert_outside_repo,
    load_battery,
    load_fixture_keys,
    load_gold,
    load_questions,
    planned_request_count,
    validate_battery,
)

CHAT_PATH = "/api/ai/chat"
PROPOSALS_PATH = "/api/aichat/proposals/"
PREFLIGHT_PATH = "/api/ai/quota/preflight"
THREADS_PATH = "/api/ai/threads"
CONFIG_PATH = "/api/ai/config/effective"
MACHINES_PATH = os.environ.get("AIMMS_BATTERY_ASSETS_PATH", "/api/assets/machine/")

MAX_REQUESTS_PER_INVOCATION = 300

_last_request = 0.0


class PreflightError(RuntimeError):
    """A refusal to start (exit code 2)."""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"AIMMS_BATTERY_{name}", "") or default


def _client(base_url: str):
    import httpx

    headers = {
        "Accept": "application/json",
        "Origin": _env("ORIGIN") or base_url.rstrip("/"),
    }
    if _env("BEARER"):
        headers["Authorization"] = f"Bearer {_env('BEARER')}"
    if _env("COOKIE"):
        headers["Cookie"] = _env("COOKIE")
    if _env("CSRF"):
        headers["X-CSRFToken"] = _env("CSRF")
    return httpx.Client(base_url=base_url, headers=headers, timeout=120.0)


def _throttle() -> None:
    global _last_request
    rpm = float(_env("RPM", "8") or 8)
    interval = 60.0 / max(rpm, 0.1)
    wait = _last_request + interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _proposal_ids(client) -> set[str] | None:
    try:
        response = client.get(PROPOSALS_PATH)
        response.raise_for_status()
        return {str(row.get("id")) for row in response.json().get("results") or [] if row.get("id")}
    except Exception:
        return None


class Journal:
    """One JSONL journal per pass; append-only, exact bytes."""

    def __init__(self, path: Path):
        self.path = path
        self.request_count = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("w", encoding="utf-8")

    def write(self, record: dict[str, Any]) -> None:
        self._handle.write(json.dumps(record, ensure_ascii=True) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


# --------------------------------------------------------------------------- #
# Preflight                                                                    #
# --------------------------------------------------------------------------- #
def _resolve_machine_key(client, key: str, descriptor: dict[str, Any]) -> tuple[str, ...]:
    """Resolve one machine fixture key to live entity id strings."""
    name = str(descriptor.get("name") or "")
    if descriptor.get("env"):
        name = os.environ.get(str(descriptor["env"]), "")
        if not name:
            raise PreflightError(f"fixture key {key!r}: {descriptor['env']} is not set")
    serial = str(descriptor.get("serial") or "")
    response = client.get(MACHINES_PATH, params={"search": name or serial})
    response.raise_for_status()
    rows = response.json()
    if isinstance(rows, dict):
        rows = rows.get("results") or []
    matches = [
        row
        for row in rows
        if str(row.get("name") or "") == name or (serial and str(row.get("serial") or "") == serial)
    ]
    if len(matches) != 1:
        raise PreflightError(
            f"fixture key {key!r} resolved to {len(matches)} machines (need exactly 1)"
        )
    pk = matches[0].get("pk") or matches[0].get("id")
    return (f"machine:{pk}", str(pk))


def resolve_fixture_keys(
    client, manifest: dict[str, dict[str, Any]], needed: set[str]
) -> dict[str, tuple[str, ...]]:
    resolved: dict[str, tuple[str, ...]] = {}
    for key in sorted(needed):
        descriptor = manifest.get(key)
        if descriptor is None:
            raise PreflightError(f"unknown fixture key {key!r}")
        kind = descriptor.get("kind")
        if kind == "machine":
            resolved[key] = _resolve_machine_key(client, key, descriptor)
        elif kind == "document":
            resolved[key] = (str(descriptor.get("document_id") or ""),)
        else:
            resolved[key] = ()  # markers carry their own text; attachments/WOs by marker
    return resolved


def _needed_keys(battery: BatteryFile) -> tuple[set[str], set[str]]:
    scope_keys: set[str] = set()
    forbidden_keys: set[str] = set()
    for case in battery.cases:
        scope_keys.update(case.scope_machine_fixture_keys)
        for turn in case.turns:
            forbidden_keys.update(turn.forbidden_entity_fixture_keys)
    return scope_keys, forbidden_keys


def preflight(
    client,
    battery: BatteryFile,
    manifest: dict[str, dict[str, Any]],
    *,
    planned: int,
    estimated_tokens: int,
    dossier_path: str = "",
    allow_unverified_flags: bool = False,
) -> dict[str, Any]:
    """Every §13.6/Q45/Q47 gate, or a refusal. Returns the journal header."""
    if planned > MAX_REQUESTS_PER_INVOCATION:
        raise PreflightError(
            f"planned {planned} requests exceed the {MAX_REQUESTS_PER_INVOCATION}-request "
            f"reservation; split the invocation (passes and tranche run separately)"
        )

    problems = validate_battery(battery, manifest)
    if problems:
        raise PreflightError(
            "battery validation failed: "
            + "; ".join(f"{p.item_id}: {p.problem}" for p in problems[:10])
        )

    response = client.get(
        PREFLIGHT_PATH,
        params={"estimated_tokens": estimated_tokens, "estimated_requests": planned},
    )
    response.raise_for_status()
    quota = response.json()
    if quota.get("fits") is False:
        raise PreflightError(f"quota preflight refuses the run: {quota}")
    store = quota.get("store") or {}
    if store.get("healthy") is False:
        raise PreflightError("quota store unhealthy")
    if quota.get("pilot_stopped") is True:
        raise PreflightError("the pilot-stop latch is set; no battery may run")

    flags: dict[str, Any] | None = None
    if dossier_path:
        flags = json.loads(Path(dossier_path).read_text(encoding="utf-8"))
    elif _env("ADMIN_BEARER"):
        config_response = client.get(
            CONFIG_PATH, headers={"Authorization": f"Bearer {_env('ADMIN_BEARER')}"}
        )
        if config_response.status_code == 200:
            flags = config_response.json()
    if flags is None and not allow_unverified_flags:
        raise PreflightError(
            "no flag capture: pass --dossier (preferred) or set "
            "AIMMS_BATTERY_ADMIN_BEARER, or run with --allow-unverified-flags"
        )

    scope_keys, forbidden_keys = _needed_keys(battery)
    resolved = resolve_fixture_keys(client, manifest, scope_keys | forbidden_keys)

    rpm = float(_env("RPM", "8") or 8)
    return {
        "kind": "preflight",
        "planned_requests": planned,
        "estimated_tokens": estimated_tokens,
        "quota": quota,
        "flags_captured": flags is not None,
        "flag_source": "dossier" if dossier_path else ("config" if flags else "none"),
        "fixture_resolution": {key: list(value) for key, value in resolved.items()},
        "expected_models": [m for m in _env("EXPECTED_MODELS").split(",") if m],
        "estimated_duration_s": planned * 60.0 / max(rpm, 0.1),
        "resolved": resolved,  # stripped before journaling
    }


# --------------------------------------------------------------------------- #
# Execution                                                                    #
# --------------------------------------------------------------------------- #
def _put_scope(client, thread_id: str, machine_pks: list[int]) -> int:
    response = client.put(
        f"{THREADS_PATH}/{thread_id}/scope",
        json={
            "expected_version": 0,
            "scope": {"mode": "explicit_assets", "machine_ids": machine_pks},
        },
    )
    response.raise_for_status()
    payload = response.json()
    return int(payload.get("version") or payload.get("scope_version") or 1)


def _get_scope_version(client, thread_id: str) -> int | None:
    try:
        response = client.get(f"{THREADS_PATH}/{thread_id}/scope")
        response.raise_for_status()
        payload = response.json()
        return int(payload.get("version") or 0)
    except Exception:
        return None


def _thread_artifacts(client, thread_id: str) -> dict[str, Any]:
    try:
        response = client.get(f"{THREADS_PATH}/{thread_id}")
        response.raise_for_status()
        return response.json()
    except Exception:
        return {}


def _submit_turn(
    client,
    journal: Journal,
    *,
    case_id: str,
    turn_index: int,
    pass_index: int,
    thread_id: str,
    question: str,
    scope_version: int | None,
) -> tuple[int, dict[str, Any]]:
    """One turn, one idempotency key, journaled attempts, 409-only retry."""
    idempotency_key = f"battery:{case_id}:{turn_index}:{pass_index}:{uuid.uuid4().hex[:8]}"
    body: dict[str, Any] = {
        "message": question,
        "thread_id": thread_id,
        "idempotency_key": idempotency_key,
    }
    if scope_version is not None:
        body["expected_scope_version"] = scope_version
    status = 0
    payload: dict[str, Any] = {}
    for attempt in range(1, 7):
        _throttle()
        started = time.monotonic()
        try:
            response = client.post(CHAT_PATH, json=body)
            status = response.status_code
            try:
                payload = response.json()
            except Exception:
                payload = {"raw": response.text[:2000]}
        except Exception as exc:
            status, payload = 0, {"transport_error": type(exc).__name__}
        journal.request_count += 1
        journal.write({
            "kind": "turn_attempt",
            "case_id": case_id,
            "turn_index": turn_index,
            "pass": pass_index,
            "attempt": attempt,
            "thread_id": thread_id,
            "idempotency_key": idempotency_key,
            "sent": body,
            "status": status,
            "response_body": payload,
            "elapsed_s": round(time.monotonic() - started, 3),
        })
        detail = str(payload.get("detail") or "")
        if status == 409 and detail != "scope_version_conflict":
            # Turn serialization: the SAME idempotency key is re-posted so
            # the server dedupes; the request count stays honest above.
            time.sleep(20)
            continue
        break
    return status, payload


def _artifacts_for_turn(
    client,
    *,
    status: int,
    payload: dict[str, Any],
    thread_id: str,
    scope_version: int | None,
    proposals_before: set[str] | None,
) -> scoring.TurnArtifacts:
    detail = _thread_artifacts(client, thread_id)
    messages = detail.get("messages") or []
    last_assistant = next((m for m in reversed(messages) if m.get("role") == "assistant"), {})
    proposals_after = _proposal_ids(client)
    delta = 0
    if proposals_before is not None and proposals_after is not None:
        delta = len(proposals_after - proposals_before)
    return scoring.TurnArtifacts(
        http_status=status,
        response_body=payload,
        thread_id=thread_id,
        message_text=str(payload.get("message") or last_assistant.get("content") or ""),
        evidence_analysis=last_assistant.get("evidence_analysis"),
        entities=last_assistant.get("entities"),
        response_state=last_assistant.get("response_state"),
        expected_scope_version=scope_version,
        post_scope_version=_get_scope_version(client, thread_id),
        route=None,  # not exposed to non-staff principals; layer 2 skips honestly
        proposal_ids_delta=delta,
        turn_metadata={
            "capability_tier": last_assistant.get("capability_tier"),
            "model_versions": last_assistant.get("model_versions"),
        },
    )


def _check_model_pins(expected: list[str], artifacts: scoring.TurnArtifacts) -> None:
    if not expected:
        return
    versions = artifacts.turn_metadata.get("model_versions") or {}
    seen = {str(value) for value in versions.values()}
    drifted = seen - set(expected)
    if seen and drifted:
        raise PreflightError(
            f"model identity drift mid-run: {sorted(drifted)} not in pinned {expected} "
            f"(Q50 stop trigger; aborting the run)"
        )


def run_case(
    client,
    journal: Journal,
    case: ScenarioCase,
    *,
    pass_index: int,
    tier: int,
    manifest: dict[str, dict[str, Any]],
    resolved: dict[str, tuple[str, ...]],
    questions: dict[str, str],
    golds: dict[str, Any],
    judge,
    expected_models: list[str],
) -> list[scoring.TurnScore]:
    thread_id = f"battery_{uuid.uuid4().hex[:16]}"
    scope_version: int | None = None
    if case.scope_machine_fixture_keys:
        machine_pks = [
            int(pk) for key in case.scope_machine_fixture_keys for pk in resolved.get(key, ())[1:]
        ]
        scope_version = _put_scope(client, thread_id, machine_pks)
    proposals_before = _proposal_ids(client)
    scores: list[scoring.TurnScore] = []
    gold = golds.get(case.id)
    for index, turn in enumerate(case.turns):
        question = turn.question or questions.get(turn.question_ref, "")
        status, payload = _submit_turn(
            client,
            journal,
            case_id=case.id,
            turn_index=index,
            pass_index=pass_index,
            thread_id=thread_id,
            question=question,
            scope_version=scope_version,
        )
        artifacts = _artifacts_for_turn(
            client,
            status=status,
            payload=payload,
            thread_id=thread_id,
            scope_version=scope_version,
            proposals_before=proposals_before,
        )
        _check_model_pins(expected_models, artifacts)
        resolution = scoring.resolution_from_manifest(manifest, case, turn, resolved)
        score = scoring.score_turn(
            case=case,
            turn=turn,
            turn_index=index,
            artifacts=artifacts,
            tier=tier,
            resolution=resolution,
            gold=gold,
            judge=judge,
            question_text=question,
        )
        scores.append(score)
        journal.write({
            "kind": "turn_score",
            "case_id": case.id,
            "turn_index": index,
            "pass": pass_index,
            "outcome": score.outcome,
            "substantive": score.substantive,
            "layers": [asdict(layer) for layer in score.layers],
            "latency_s": None,
        })
    return scores


def _shuffled(cases: tuple[ScenarioCase, ...], seed: int) -> list[ScenarioCase]:
    ordered = list(cases)
    random.Random(seed).shuffle(ordered)
    return ordered


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S14 battery runner")
    parser.add_argument("--passes", type=int, default=1)
    parser.add_argument("--tranche", action="store_true", help="run ONLY the repeat tranche")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--cases", default="", help="battery YAML override path")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--journal-dir", default="")
    parser.add_argument("--dossier", default="", help="evaluation_dossier output path")
    parser.add_argument("--tier", type=int, default=0, help="the deployment's declared tier")
    parser.add_argument("--estimated-tokens", type=int, default=2_000_000)
    parser.add_argument("--allow-unverified-flags", action="store_true")
    parser.add_argument("--repeat-case", default="", help="repeat one case id N times")
    parser.add_argument("--times", type=int, default=1)
    args = parser.parse_args(argv)

    base_url = _env("BASE_URL")
    if not base_url:
        print("AIMMS_BATTERY_BASE_URL is required", file=sys.stderr)
        return 2

    journal_dir = args.journal_dir or _env("JOURNAL_DIR")
    if not journal_dir:
        print("AIMMS_BATTERY_JOURNAL_DIR (or --journal-dir) is required", file=sys.stderr)
        return 2
    try:
        journal_root = assert_outside_repo(Path(journal_dir))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    dataset = _env("DATASET", "fixture")
    battery_path = Path(args.cases) if args.cases else BATTERY_DIR / f"{dataset}_battery.yaml"
    battery = load_battery(battery_path)
    manifest = load_fixture_keys()

    # Resolve private-store questions and gold.
    questions: dict[str, str] = {}
    golds: dict[str, Any] = {}
    gold_dir_env = os.environ.get("AIMMS_GOLD_DIR", "")
    if gold_dir_env:
        gold_dir = assert_outside_repo(Path(gold_dir_env))
        questions = load_questions(gold_dir)
        for case in battery.cases:
            golds[case.id] = load_gold(gold_dir, case.id)
    unresolved = [
        f"{case.id}[{index}]"
        for case in battery.cases
        for index, turn in enumerate(case.turns)
        if turn.question_ref and turn.question_ref not in questions
    ]
    if unresolved and battery.dataset == "solar":
        print(
            f"unresolved question refs (set AIMMS_GOLD_DIR): {unresolved[:8]}...",
            file=sys.stderr,
        )
        return 2

    # Judge only under a matching calibration artifact (§13.5).
    judge = None
    calibration_path = os.environ.get("AIMMS_JUDGE_CALIBRATION", "")
    if calibration_path:
        from .battery_judge import battery_judge_fingerprint, default_battery_judge_call

        artifact = load_calibration(Path(calibration_path))
        if judge_layers_enabled(artifact, battery_judge_fingerprint()):
            judge = default_battery_judge_call
        else:
            print(
                "calibration artifact unusable (fingerprint/agreement); judge layers stay not_scored",
                file=sys.stderr,
            )

    # Work list for this invocation.
    if args.repeat_case:
        target = battery.case(args.repeat_case)
        if target is None:
            print(f"unknown case {args.repeat_case!r}", file=sys.stderr)
            return 2
        work: list[tuple[int, list[ScenarioCase]]] = [
            (args.seed or 0, [target] * max(1, args.times))
        ]
        planned = len(target.turns) * max(1, args.times)
    elif args.tranche:
        if not battery.repeat_tranche:
            print("battery has no repeat_tranche", file=sys.stderr)
            return 2
        tranche_cases: list[ScenarioCase] = []
        for case_id in battery.repeat_tranche.case_ids:
            case = battery.case(case_id)
            if case is not None:
                tranche_cases.extend([case] * battery.repeat_tranche.repetitions)
        work = [(args.seed or 0, tranche_cases)]
        planned = sum(len(case.turns) for case in tranche_cases)
    else:
        base_seed = args.seed if args.seed is not None else int(_env("SEED", "0") or 0)
        if not base_seed:
            base_seed = int.from_bytes(os.urandom(4), "big")
        work = [
            (base_seed + pass_index, _shuffled(battery.cases, base_seed + pass_index))
            for pass_index in range(max(1, args.passes))
        ]
        planned = planned_request_count([battery]) * max(1, args.passes)

    client = _client(base_url)
    report: dict[str, Any] = {
        "dataset": battery.dataset,
        "passes": len(work),
        "planned_requests": planned,
        "per_case": {},
        "failures": [],
        "judge_enabled": judge is not None,
    }
    try:
        header = preflight(
            client,
            battery,
            manifest,
            planned=planned,
            estimated_tokens=args.estimated_tokens,
            dossier_path=args.dossier or _env("DOSSIER"),
            allow_unverified_flags=args.allow_unverified_flags,
        )
    except PreflightError as exc:
        print(f"PREFLIGHT REFUSED: {exc}", file=sys.stderr)
        return 2
    resolved = header.pop("resolved")
    expected_models = list(header.get("expected_models") or [])

    exit_code = 0
    for run_index, (seed, ordered) in enumerate(work, start=1):
        journal = Journal(journal_root / f"run-{int(time.time())}-pass{run_index}.jsonl")
        # generated_at makes journals self-describing for the 12-month
        # private-store pruning CLI (prune_journals) — Q48.
        journal.write({
            **header,
            "seed": seed,
            "pass": run_index,
            "generated_at": datetime.now(UTC).isoformat(),
        })
        try:
            for case in ordered:
                scores = run_case(
                    client,
                    journal,
                    case,
                    pass_index=run_index,
                    tier=args.tier,
                    manifest=manifest,
                    resolved=resolved,
                    questions=questions,
                    golds=golds,
                    judge=judge,
                    expected_models=expected_models,
                )
                bucket = report["per_case"].setdefault(
                    case.id, {"outcomes": [], "first_attempt": None}
                )
                outcome = (
                    "fail"
                    if any(score.outcome == "fail" for score in scores)
                    else scores[-1].outcome
                )
                bucket["outcomes"].append(outcome)
                if bucket["first_attempt"] is None:
                    bucket["first_attempt"] = outcome
                if outcome == "fail":
                    exit_code = 1
                    report["failures"].append({
                        "pass": run_index,
                        "case_id": case.id,
                        "layers": [
                            {"layer": layer.layer, "detail": layer.detail}
                            for score in scores
                            for layer in score.layers
                            if layer.status == "fail"
                        ],
                    })
        except PreflightError as exc:
            journal.write({"kind": "abort", "reason": str(exc)})
            print(f"RUN ABORTED: {exc}", file=sys.stderr)
            exit_code = 2
            break
        finally:
            journal.write({
                "kind": "summary",
                "pass": run_index,
                "request_count_actual": journal.request_count,
            })
            journal.close()

    from .judge import drain_judge_usage

    report["judge_usage"] = drain_judge_usage()
    rendered = json.dumps(report, indent=2)
    if args.json_out:
        Path(args.json_out).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
