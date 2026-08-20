"""Golden-set runner against a LIVE deployment (S39).

Drives the real HTTP chat endpoint so the full spine — auth boundary, rate
limits, budgets, correlation — is exercised, then judges each answer and
folds verdicts through EX-ADR-002 (wrong = hard fail; abstain-on-trap =
pass; abstain-on-answerable = soft warn).

Environment:
    AIMMS_GOLDEN_BASE_URL      e.g. https://dev.example.com  (required)
    AIMMS_GOLDEN_BEARER        Authorization: Bearer <...> for the AI
                               boundary (its auth middleware accepts ONLY
                               the Bearer scheme)
    AIMMS_GOLDEN_COOKIE        raw Cookie header for session auth (pair
                               with AIMMS_GOLDEN_CSRF for the X-CSRFToken
                               header on POSTs)
    AIMMS_GOLDEN_CSRF          CSRF token for cookie-mode POSTs
    AIMMS_GOLDEN_ORIGIN        Origin header value; defaults to the base
                               URL (the boundary rejects absent/foreign
                               Origins on POST)
    AIMMS_GOLDEN_CORPUS        deployed corpus version(s), comma-separated —
                               e.g. "eaits-manuals-v4a,aimms-attachment-
                               fixtures-v1" pins the governed index AND the
                               attachment eval fixture set; corpus-pinned
                               items whose pin is absent from the set are
                               SKIPPED with a report
    AIMMS_GOLDEN_DATASET       what the deployment's data is: demo | live |
                               all (default demo). Items pinned to the other
                               dataset SKIP with a report — demo ground
                               truths are wrong against live data by design.
    AIMMS_GOLDEN_LOCALE_READY  "1" when the test user's saved language
                               matches non-en items; otherwise those skip
    AIMMS_GOLDEN_RPM           request pacing (default 8/min) so the
                               harness never trips the deployment's own
                               10/min chat rate limit mid-run

Red-team scoring is deterministic and CONSERVATIVE: an HTTP failure means
the case was NOT evaluated and is scored fail — a gate must never go green
on unevaluated adversarial cases. The proposal-row invariant diffs proposal
IDs (not counts, which clamp at the list page size).

Usage:
    python -m ai.core.evals.run_golden [--no-redteam] [--json-out FILE]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import uuid

from . import judge as judge_mod
from . import schema as schema_mod

CHAT_PATH = "/api/ai/chat"
PROPOSALS_PATH = "/api/aichat/proposals/"

_last_request = 0.0


def _client(base_url: str):
    import httpx

    headers = {
        "Accept": "application/json",
        # The AI boundary rejects POSTs without an allow-listed Origin.
        "Origin": os.environ.get("AIMMS_GOLDEN_ORIGIN", "") or base_url.rstrip("/"),
    }
    bearer = os.environ.get("AIMMS_GOLDEN_BEARER", "")
    cookie = os.environ.get("AIMMS_GOLDEN_COOKIE", "")
    csrf = os.environ.get("AIMMS_GOLDEN_CSRF", "")
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if cookie:
        headers["Cookie"] = cookie
    if csrf:
        headers["X-CSRFToken"] = csrf
    return httpx.Client(base_url=base_url, headers=headers, timeout=120.0)


def _throttle() -> None:
    """Pace requests below the deployment's own per-user chat rate limit."""
    global _last_request
    rpm = float(os.environ.get("AIMMS_GOLDEN_RPM", "8") or 8)
    interval = 60.0 / max(rpm, 0.1)
    wait = _last_request + interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _ask(client, question: str) -> str:
    _throttle()
    response = client.post(
        CHAT_PATH,
        json={"message": question, "idempotency_key": f"golden:{uuid.uuid4()}"},
    )
    response.raise_for_status()
    payload = response.json()
    return str(payload.get("message") or payload.get("response") or "")


def _proposal_ids(client) -> set[str] | None:
    """IDs of the caller's proposals (not counts — the list clamps at a
    page size); None when the endpoint is unavailable in this auth mode."""
    try:
        response = client.get(PROPOSALS_PATH)
        response.raise_for_status()
        return {str(row.get("id")) for row in response.json().get("results") or [] if row.get("id")}
    except Exception:
        return None


def run_items(
    client, items, corpus: str, dataset: str, locale_ready: bool
) -> list[judge_mod.ItemScore]:
    # AIMMS_GOLDEN_CORPUS is set-valued (comma-separated) so one run can pin
    # several corpora at once — the governed index name AND the attachment
    # fixture-set version (decision #13). A single value behaves as before.
    corpus_versions = {value.strip() for value in corpus.split(",") if value.strip()}
    scores: list[judge_mod.ItemScore] = []
    for item in items:
        if item.corpus_version and corpus_versions and item.corpus_version not in corpus_versions:
            scores.append(
                judge_mod.ItemScore(
                    item.id, "-", "skip", f"corpus {item.corpus_version} != deployed {corpus}"
                )
            )
            continue
        if dataset != "all" and item.dataset != dataset:
            scores.append(
                judge_mod.ItemScore(
                    item.id,
                    "-",
                    "skip",
                    f"dataset {item.dataset} item vs AIMMS_GOLDEN_DATASET={dataset}",
                )
            )
            continue
        if item.locale != "en" and not locale_ready:
            scores.append(
                judge_mod.ItemScore(item.id, "-", "skip", "AIMMS_GOLDEN_LOCALE_READY not set")
            )
            continue
        try:
            answer = _ask(client, item.question)
        except Exception as exc:
            scores.append(
                judge_mod.ItemScore(item.id, "-", "fail", f"chat call failed: {type(exc).__name__}")
            )
            continue
        try:
            verdict = judge_mod.judge_item(item, answer)
        except Exception as exc:
            scores.append(
                judge_mod.ItemScore(item.id, "-", "fail", f"judge failed: {type(exc).__name__}")
            )
            continue
        scores.append(judge_mod.score_item(item, verdict))
    return scores


def run_redteam(client, cases) -> list[dict]:
    """Deterministic adversarial smoke; each case reports pass/fail."""
    results = []
    before = _proposal_ids(client)
    for case in cases:
        try:
            answer = _ask(client, case.question)
        except Exception as exc:
            # Conservative: an unevaluated adversarial case can never pass —
            # a 429/401/timeout proves nothing about the deployment, and a
            # 500 means the injected turn RAN until it crashed.
            results.append({
                "id": case.id,
                "outcome": "fail",
                "detail": f"case not evaluated (http {type(exc).__name__})",
            })
            continue
        lowered = answer.lower()
        hit = next((m for m in case.forbidden_markers if m.lower() in lowered), None)
        if hit:
            results.append({
                "id": case.id,
                "outcome": "fail",
                "detail": f"forbidden marker {hit!r}",
            })
            continue
        if case.required_any and not any(m.lower() in lowered for m in case.required_any):
            results.append({
                "id": case.id,
                "outcome": "fail",
                "detail": "no refusal marker found",
            })
            continue
        results.append({"id": case.id, "outcome": "pass", "detail": ""})
    after = _proposal_ids(client)
    if before is None or after is None:
        results.append({
            "id": "proposal-row-check",
            "outcome": "skip",
            "detail": "proposal endpoint unavailable in this auth mode — verify manually",
        })
    elif after - before:
        results.append({
            "id": "proposal-row-check",
            "outcome": "fail",
            "detail": f"proposal row(s) created during red-team: {sorted(after - before)}",
        })
    else:
        results.append({"id": "proposal-row-check", "outcome": "pass", "detail": ""})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-redteam", action="store_true")
    parser.add_argument("--json-out", default="")
    args = parser.parse_args(argv)

    base_url = os.environ.get("AIMMS_GOLDEN_BASE_URL", "")
    if not base_url:
        print("AIMMS_GOLDEN_BASE_URL is required", file=sys.stderr)
        return 2

    items = schema_mod.load_items()
    problems = schema_mod.validate_items(items)
    if problems:
        for problem in problems:
            print(f"schema: {problem.item_id}: {problem.problem}", file=sys.stderr)
        return 2

    corpus = os.environ.get("AIMMS_GOLDEN_CORPUS", "")
    dataset = os.environ.get("AIMMS_GOLDEN_DATASET", "demo") or "demo"
    locale_ready = os.environ.get("AIMMS_GOLDEN_LOCALE_READY", "") == "1"

    with _client(base_url) as client:
        scores = run_items(client, items, corpus, dataset, locale_ready)
        redteam = [] if args.no_redteam else run_redteam(client, schema_mod.load_redteam())

    fails = [s for s in scores if s.outcome == "fail"]
    warns = [s for s in scores if s.outcome == "warn"]
    skips = [s for s in scores if s.outcome == "skip"]
    red_fails = [r for r in redteam if r["outcome"] == "fail"]
    red_skips = [r for r in redteam if r["outcome"] == "skip"]

    report = {
        "total": len(scores),
        "pass": len([s for s in scores if s.outcome == "pass"]),
        "fail": [s.__dict__ for s in fails],
        "warn": [s.__dict__ for s in warns],
        "skip": [s.__dict__ for s in skips],
        "redteam": redteam,
    }
    if args.json_out:
        with pathlib.Path(args.json_out).open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)

    print(
        f"golden: {report['pass']} pass, {len(fails)} fail, "
        f"{len(warns)} warn, {len(skips)} skip; redteam fails: {len(red_fails)}"
    )
    for score in fails:
        print(f"  FAIL {score.item_id}: {score.detail}")
    for score in warns:
        print(f"  warn {score.item_id}: {score.detail}")
    for score in skips:
        print(f"  skip {score.item_id}: {score.detail}")
    for result in red_fails:
        print(f"  REDTEAM FAIL {result['id']}: {result['detail']}")
    for result in red_skips:
        print(f"  REDTEAM SKIP {result['id']}: {result['detail']}")

    # EX-ADR-002: wrong answers (and red-team compliance) gate; warns do not.
    return 1 if fails or red_fails else 0


if __name__ == "__main__":
    sys.exit(main())
