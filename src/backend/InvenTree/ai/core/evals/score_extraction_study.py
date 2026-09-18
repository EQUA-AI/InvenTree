"""Score fully reviewed private atom matches; never manufacture semantic labels.

This offline path makes no provider calls. A human must review every transcript
pass, including empty outputs, and label every admitted candidate. The exact
journal/campaign/corpus bytes bind the review. Optional arms remain unadmitted.
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .run_extraction_study import (
    DEFAULT_CAMPAIGN,
    PrivateJournal,
    deterministic_failures,
    load_study,
    require_scoring_review,
    windows,
)
from .scenarios import assert_outside_repo

MAX_PRIVATE_BYTES = 64 * 1024 * 1024


def score_reviewed(campaign, cases, campaign_hash, journal_bytes, review):
    """Return per-case/pass metrics only after complete explicit human annotation."""
    require_scoring_review(campaign)
    if (
        len(journal_bytes) > MAX_PRIVATE_BYTES
        or campaign.get("status") != "approved"
        or campaign.get("corpus_reviewed") is not True
        or not campaign.get("reviewer")
        or any(
            case.get("review_status") != "reviewed" or not case.get("reviewer") for case in cases
        )
        or any(
            atom.get("review_status") != "reviewed" for case in cases for atom in case["gold_atoms"]
        )
        or review.get("schema_version") != 1
        or review.get("reviewed") is not True
        or not isinstance(review.get("reviewer"), str)
        or not review["reviewer"].strip()
        or review.get("journal_sha256") != hashlib.sha256(journal_bytes).hexdigest()
    ):
        raise ValueError("Complete byte-bound human review required")
    records = [json.loads(line) for line in journal_bytes.splitlines() if line.strip()]
    if len(records) < 2:
        raise ValueError("Incomplete study journal")
    header, summary = records[0], records[-1]
    if (
        header.get("type") != "header"
        or header.get("arm") != "custom"
        or header.get("preregistration_sha256") != campaign_hash
        or header.get("corpus_sha256") != campaign["corpus_sha256"]
        or summary.get("type") != "summary"
        or summary.get("status") != "completed_unscored"
        or summary.get("complete") is not True
        or summary.get("preregistration_sha256") != campaign_hash
    ):
        raise ValueError("Incomplete or changed study registration")
    case_map = {case["case_id"]: case for case in cases}
    expected = {
        f"{pass_index}:{case['case_id']}:{index}": (case["case_id"], pass_index)
        for case in cases
        for pass_index in range(campaign["runs"])
        for index, _ in enumerate(windows(case))
    }
    expected_sources = {
        f"{pass_index}:{case['case_id']}:{index}": {row["source_message_id"] for row in documents}
        for case in cases
        for pass_index in range(campaign["runs"])
        for index, documents in enumerate(windows(case))
    }
    found, reservations, candidates = {}, set(), {}
    for row in records[1:-1]:
        identity = row.get("id")
        if row.get("type") == "reservation":
            if identity not in expected or identity in reservations:
                raise ValueError("Unexpected or duplicate reservation")
            reservations.add(identity)
            continue
        if (
            row.get("type") != "window"
            or identity not in expected
            or identity in found
            or identity not in reservations
        ):
            raise ValueError("Unexpected or unreserved window")
        case_id, pass_index = expected[identity]
        if (
            row.get("case_id") != case_id
            or type(row.get("pass_index")) is not int
            or row.get("pass_index") != pass_index
            or row.get("deterministic_failures") != []
        ):
            raise ValueError("Failed or mismatched study window")
        result = row["result"]
        if (
            not result.get("provider_skipped")
            and result.get("resolved_model") != campaign["expected_model"]
        ):
            raise ValueError("Model identity changed")
        raw = result.get("raw_candidates")
        if (
            not isinstance(raw, list)
            or len(raw) > 5
            or deterministic_failures(case_map[case_id], raw, expected_sources[identity])
        ):
            raise ValueError("Deterministic hard-zero evidence failed")
        proposals = result.get("candidates")
        if not isinstance(proposals, list) or len(proposals) > 5:
            raise ValueError("Invalid admitted candidate list")
        skipped = result.get("provider_skipped", False)
        if type(skipped) is not bool or (skipped and (raw or proposals)):
            raise ValueError("Skipped provider cannot produce extraction candidates")
        if any(candidate not in raw for candidate in proposals):
            raise ValueError("Admitted candidate not present in raw extraction")
        for index, candidate in enumerate(proposals):
            candidates[identity, index] = candidate
        found[identity] = row
    if (
        set(found) != set(expected)
        or reservations != set(expected)
        or summary.get("windows") != len(expected)
    ):
        raise ValueError("Missing transcript-pass windows")
    expected_passes = {
        (case_id, index) for case_id in case_map for index in range(campaign["runs"])
    }
    reviewed_passes = review.get("case_reviews")
    if not isinstance(reviewed_passes, list) or len(reviewed_passes) != len(expected_passes):
        raise ValueError("Every transcript pass needs review including empty output")
    seen_passes = set()
    for row in reviewed_passes:
        key = (row.get("case_id"), row.get("pass_index"))
        if (
            type(row.get("pass_index")) is not int
            or key not in expected_passes
            or key in seen_passes
            or row.get("reviewed") is not True
        ):
            raise ValueError("Invalid transcript-pass review")
        seen_passes.add(key)
    annotations = review.get("annotations")
    if not isinstance(annotations, list) or len(annotations) != len(candidates):
        raise ValueError("Every candidate requires an explicit match or rejection")
    tallies = {key: {"predicted": 0, "correct": 0, "matched": set()} for key in expected_passes}
    seen = set()
    for annotation in annotations:
        key = (annotation.get("window_id"), annotation.get("candidate_index"))
        if (
            type(annotation.get("candidate_index")) is not int
            or key not in candidates
            or key in seen
        ):
            raise ValueError("Invalid or duplicate candidate annotation")
        seen.add(key)
        case_id, pass_index = expected[key[0]]
        tally = tallies[case_id, pass_index]
        tally["predicted"] += 1
        gold_id = annotation.get("gold_atom_id")
        if gold_id is None:
            continue
        gold = {atom["id"]: atom for atom in case_map[case_id]["gold_atoms"]}
        if gold_id not in gold or gold[gold_id]["source_message_id"] != candidates[key].get(
            "source_message_id"
        ):
            raise ValueError("Matched atom belongs to another case or source")
        tally["correct"] += 1
        tally["matched"].add(gold_id)
    metrics = {}
    for case_id, pass_index in sorted(expected_passes):
        tally = tallies[case_id, pass_index]
        gold_count = len(case_map[case_id]["gold_atoms"])
        matched = len(tally["matched"])
        precision = tally["correct"] / tally["predicted"] if tally["predicted"] else 1.0
        recall = matched / gold_count if gold_count else float(tally["predicted"] == 0)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        metrics.setdefault(case_id, []).append({
            "pass_index": pass_index,
            "reviewer": review["reviewer"],
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "duplicate_rate": max(0.0, tally["predicted"] / matched - 1)
            if matched
            else float(tally["predicted"]),
            "hard_zero_failures": 0,
        })
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "method": "human_reviewed_atom_matches",
        "metrics": metrics,
        "campaign_sha256": campaign_hash,
        "journal_sha256": review["journal_sha256"],
        "review_sha256": hashlib.sha256(json.dumps(review, sort_keys=True).encode()).hexdigest(),
        "judge_status": "not_used_human_review",
        "decision": "not_qualified",
        "empty_prediction_precision": 1.0,
        "abstention_recall": "one_only_for_empty_predictions",
    }


def main(argv=None):
    """Read private artifacts and create new exclusive private scores, never stdout text."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    campaign, cases, fingerprint = load_study(args.campaign)
    journal = assert_outside_repo(args.journal).read_bytes()
    review_bytes = assert_outside_repo(args.review).read_bytes()
    if len(review_bytes) > MAX_PRIVATE_BYTES:
        raise ValueError("Private review exceeds bound")
    report = score_reviewed(campaign, cases, fingerprint, journal, json.loads(review_bytes))
    output = PrivateJournal(args.output)
    try:
        output.write(report)
    except BaseException:
        output.close()
        output.path.unlink(missing_ok=True)
        raise
    else:
        output.close()
    print(json.dumps({"transcripts": len(report["metrics"]), "decision": report["decision"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
