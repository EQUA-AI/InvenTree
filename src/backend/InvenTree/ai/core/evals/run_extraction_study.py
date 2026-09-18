"""In-process extraction study; preparation is provider-free, execution is explicit.

The committed campaign is intentionally draft. No Django persistence, browser,
HTTP chat turn, optional-engine import or human rating is synthesized here.
Private JSONL journals share the existing generated_at/12-month prune contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from datetime import UTC, datetime
from pathlib import Path

from .scenarios import assert_outside_repo

DEFAULT_CAMPAIGN = Path(__file__).with_name("memory_campaign.yaml")
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
SCORING_CONVENTIONS = {
    "precision": "correctly_matched_proposals_over_proposals",
    "recall": "unique_matched_gold_over_gold",
    "empty_prediction_precision": 1,
    "abstention_recall": "one_only_if_no_predictions",
    "duplicate_rate": "max(0, proposals/unique_matched_gold-1); no matched gold uses proposal count",
}


def require_scoring_review(campaign):
    """Refuse unreviewed or unsupported metric definitions before spend or scoring."""
    conventions = campaign.get("scoring_conventions", {})
    if (
        not isinstance(conventions, dict)
        or conventions.get("status") != "reviewed"
        or not isinstance(conventions.get("reviewer"), str)
        or not conventions["reviewer"].strip()
        or any(
            type(conventions.get(key)) is not type(value) or conventions[key] != value
            for key, value in SCORING_CONVENTIONS.items()
        )
    ):
        raise ValueError("reviewed_supported_scoring_conventions_required")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def load_study(path):
    """Validate the exact committed JSON-compatible YAML and corpus bytes."""
    path = Path(path).resolve()
    raw = path.read_bytes()
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("campaign_size")
    campaign = json.loads(raw)
    corpus = (path.parent / campaign["corpus_path"]).resolve()
    if path.parent not in corpus.parents:
        raise ValueError("corpus_path")
    data = corpus.read_bytes()
    if len(data) > MAX_ARTIFACT_BYTES or digest(data) != campaign["corpus_sha256"]:
        raise ValueError("corpus_fingerprint")
    cases = [json.loads(line) for line in data.splitlines() if line.strip()]
    if (
        campaign.get("schema_version") != 1
        or campaign.get("runs") != 5
        or type(campaign.get("base_seed")) is not int
    ):
        raise ValueError("campaign_contract")
    if campaign.get("seed_controls") != "case_order_only" or campaign.get("arms") != ["custom"]:
        raise ValueError("unadmitted_arm_or_seed_contract")
    if len(cases) < 50 or sum(len(case["gold_atoms"]) for case in cases) < 400:
        raise ValueError("corpus_floor")
    seen, gold_ids = set(), set()
    for case in cases:
        if (
            case["case_id"] in seen
            or case["locale"] not in {"en", "es", "de", "fr"}
            or len(case["messages"]) < 16
        ):
            raise ValueError("corpus_case")
        seen.add(case["case_id"])
        sources = set()
        for row in case["messages"]:
            if (
                set(row) != {"id", "role", "content", "eligible"}
                or row["id"] in sources
                or not isinstance(row["id"], str)
                or not 1 <= len(row["id"]) <= 80
                or row["role"] not in {"user", "assistant"}
                or type(row["eligible"]) is not bool
                or not isinstance(row["content"], str)
                or len(row["content"]) > 10000
                or (row["eligible"] and row["role"] != "user")
            ):
                raise ValueError("corpus_source")
            sources.add(row["id"])
        eligible_sources = {row["id"] for row in case["messages"] if row["eligible"]}
        for atom in case["gold_atoms"]:
            if (
                atom["id"] in gold_ids
                or atom["source_message_id"] not in eligible_sources
                or atom["authority"] != "proposed_only"
            ):
                raise ValueError("corpus_gold")
            gold_ids.add(atom["id"])
    return campaign, cases, digest(raw)


def require_execution(campaign, cases, settings):
    """A draft, missing pin or zero budget cannot accidentally contact providers."""
    if (
        campaign.get("status") != "approved"
        or campaign.get("corpus_reviewed") is not True
        or not str(campaign.get("reviewer") or "").strip()
        or any(
            case.get("review_status") != "reviewed" or not case.get("reviewer") for case in cases
        )
        or any(
            atom.get("review_status") != "reviewed" for case in cases for atom in case["gold_atoms"]
        )
    ):
        raise ValueError("human_corpus_review_required")
    require_scoring_review(campaign)
    for key in (
        "extraction_deployment",
        "expected_model",
        "runtime_fingerprint",
        "judge_fingerprint",
    ):
        if (
            not isinstance(campaign.get(key), str)
            or not campaign[key]
            or campaign[key].startswith("REQUIRED")
        ):
            raise ValueError("reviewed_pins_required")
    if campaign["extraction_deployment"] != settings.memory_extraction_deployment:
        raise ValueError("deployment_drift")
    if any(
        type(campaign.get(key)) is not int or campaign[key] <= 0
        for key in ("max_tokens", "max_provider_calls")
    ):
        raise ValueError("execution_budget_required")


class PrivateJournal:
    """Exclusive mode-0600 creation; interruption retains explicit incomplete proof."""

    def __init__(self, path):
        self.path = assert_outside_repo(Path(path))
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.stream = os.fdopen(descriptor, "w", encoding="utf-8")

    def write(self, record):
        self.stream.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self):
        self.stream.close()


def windows(case):
    """Same five-source/10K-character input bound as the custom worker."""
    pending, size = [], 0
    for source in case["messages"]:
        if not source["eligible"]:
            continue
        if pending and (len(pending) == 5 or size + len(source["content"]) > 10000):
            yield pending
            pending, size = [], 0
        pending.append({"source_message_id": source["id"], "content": source["content"]})
        size += len(source["content"])
    if pending:
        yield pending


def deterministic_failures(case, candidates, source_ids):
    """Hard-zero checks precede all semantic review; no aggregate can erase a hit."""
    failures = []
    allowed = {tuple(pair) for pair in case["allowed_entities"]}
    for row in candidates:
        if not isinstance(row, dict) or row.get("source_message_id") not in source_ids:
            failures.append("source_binding")
            continue
        if (row.get("entity_kind"), row.get("entity_id")) not in allowed:
            failures.append("cross_client")
        encoded = json.dumps(row, ensure_ascii=False).casefold()
        if any(marker.casefold() in encoded for marker in case["forbidden_markers"]):
            failures.append("forbidden_marker")
        if row.get("prohibited") is not False:
            failures.append("prohibited")
    return failures


class CustomArm:
    """The production extractor/policy/shield seams without any database writes."""

    def __init__(self, settings):
        self.settings = settings

    def bound(self, case, documents):
        from ai.core.integrations.memory_extractor import request_payload, reservation_bound

        return reservation_bound(
            request_payload(documents, owner_id=case["owner_id"], locale=case["locale"])
        )

    def __call__(self, case, documents, seed):
        from ai.core.integrations.memory_extractor import extract
        from ai.core.integrations.memory_providers import shield_documents
        from aichat.services.memory_policy import validate_candidate, validate_source_text

        # The seed controls order only, as preregistered; no unsupported provider
        # sampling-seed option is silently invented for the model deployment.
        del seed
        allowed = []
        for row in documents:
            try:
                validate_source_text(row["content"])
                allowed.append(row)
            except ValueError:
                pass
        if not allowed:
            return {
                "candidates": [],
                "raw_candidates": [],
                "resolved_model": "",
                "provider_skipped": True,
            }
        source_shields = shield_documents(
            [row["content"] for row in allowed], settings=self.settings
        )
        if len(source_shields.states) != len(allowed) or "unavailable" in source_shields.states:
            raise ValueError("source_shield_unavailable")
        result = extract(
            allowed, owner_id=case["owner_id"], locale=case["locale"], settings=self.settings
        )
        if result.error_code:
            raise ValueError("extraction_unavailable")
        candidates = []
        for row in result.candidates:
            try:
                validate_candidate(
                    {key: value for key, value in row.items() if key != "source_message_id"},
                    locale=case["locale"],
                )
                candidates.append(row)
            except ValueError:
                pass
        candidate_states = []
        if candidates:
            shields = shield_documents([row["text"] for row in candidates], settings=self.settings)
            if len(shields.states) != len(candidates) or "unavailable" in shields.states:
                raise ValueError("candidate_shield_unavailable")
            candidate_states = list(shields.states)
        return {
            "candidates": candidates,
            "raw_candidates": list(result.candidates),
            "resolved_model": result.resolved_model,
            "provider_skipped": False,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "usage_known": result.usage_known,
            "source_shields": list(source_shields.states),
            "candidate_shields": candidate_states,
            "source_policy_rejected": len(documents) - len(allowed),
        }


def run_study(campaign, cases, preregistration, *, settings, journal_path, adapter=None):
    """Run a frozen custom arm and retain incomplete runs; never assign human grades."""
    require_execution(campaign, cases, settings)
    adapter = adapter or CustomArm(settings)
    journal = PrivateJournal(journal_path)
    tokens = calls = completed = 0
    outcome = "incomplete"
    try:
        journal.write({
            "type": "header",
            "generated_at": datetime.now(UTC).isoformat(),
            "campaign_id": campaign["campaign_id"],
            "preregistration_sha256": preregistration,
            "corpus_sha256": campaign["corpus_sha256"],
            "runtime_fingerprint": campaign["runtime_fingerprint"],
            "judge_fingerprint": campaign["judge_fingerprint"],
            "arm": "custom",
            "seed_controls": campaign["seed_controls"],
            "complete": False,
        })
        for pass_index in range(campaign["runs"]):
            seed = campaign["base_seed"] + pass_index
            ordered = list(cases)
            random.Random(seed).shuffle(ordered)
            for case in ordered:
                for window_index, documents in enumerate(windows(case)):
                    bound = adapter.bound(case, documents)
                    if (
                        type(bound) is not int
                        or bound < 1
                        or tokens + bound > campaign["max_tokens"]
                        or calls + 3 > campaign["max_provider_calls"]
                    ):
                        raise ValueError("study_budget_exhausted")
                    # Reserve a worst-case extraction plus up to two shield calls.
                    # Unknown outcomes retain the reservation across interruption.
                    tokens += bound
                    calls += 3
                    identity = f"{pass_index}:{case['case_id']}:{window_index}"
                    journal.write({
                        "type": "reservation",
                        "id": identity,
                        "tokens_upper_bound": bound,
                        "provider_calls_upper_bound": 3,
                    })
                    started = time.perf_counter()
                    result = adapter(case, documents, seed)
                    if (
                        not result.get("provider_skipped")
                        and result.get("resolved_model") != campaign["expected_model"]
                    ):
                        raise ValueError("model_drift")
                    candidates = result.get("raw_candidates", [])
                    if not isinstance(candidates, list) or len(candidates) > 5:
                        raise ValueError("candidate_bounds")
                    failures = deterministic_failures(
                        case, candidates, {row["source_message_id"] for row in documents}
                    )
                    journal.write({
                        "type": "window",
                        "id": identity,
                        "case_id": case["case_id"],
                        "pass_index": pass_index,
                        "seed": seed,
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                        "result": result,
                        "deterministic_failures": failures,
                        "semantic_score": "not_scored",
                    })
                    if failures:
                        raise ValueError("deterministic_hard_zero")
                    completed += 1
        outcome = "completed_unscored"
    except Exception:
        # Never print provider exception strings or discard a failed run.
        outcome = "incomplete"
    finally:
        report = {
            "type": "summary",
            "status": outcome,
            "complete": outcome == "completed_unscored",
            "windows": completed,
            "reserved_tokens": tokens,
            "reserved_provider_calls": calls,
            "preregistration_sha256": preregistration,
            "judge_status": "not_scored",
            "decision": "not_qualified",
        }
        journal.write(report)
        journal.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--journal", type=Path)
    args = parser.parse_args(argv)
    campaign, cases, fingerprint = load_study(args.campaign)
    if not args.execute:
        print(
            json.dumps({
                "status": "prepared_only",
                "campaign_sha256": fingerprint,
                "transcripts": len(cases),
                "gold_atoms": sum(len(case["gold_atoms"]) for case in cases),
                "provider_calls": 0,
            })
        )
        return 0
    if args.journal is None:
        parser.error("--execute requires a new private --journal path")
    from ai.core.config import get_settings

    report = run_study(
        campaign, cases, fingerprint, settings=get_settings(), journal_path=args.journal
    )
    print(json.dumps(report))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
