"""Authored offline grading contracts with explicit synthetic reviewer fixtures."""

import hashlib
import json
from unittest import TestCase

from ai.core.evals.run_extraction_study import SCORING_CONVENTIONS
from ai.core.evals.score_extraction_study import score_reviewed


def fixture(*, duplicates=1):
    campaign = {
        "status": "approved",
        "corpus_reviewed": True,
        "reviewer": "fixture-human",
        "runs": 5,
        "corpus_sha256": "fixture-corpus",
        "expected_model": "fixture-model",
        "scoring_conventions": {
            **SCORING_CONVENTIONS,
            "status": "reviewed",
            "reviewer": "fixture-human",
        },
    }
    cases = [
        {
            "case_id": "case-1",
            "review_status": "reviewed",
            "reviewer": "fixture-human",
            "messages": [{"id": "source-1", "content": "synthetic preference", "eligible": True}],
            "gold_atoms": [
                {"id": "atom-1", "source_message_id": "source-1", "review_status": "reviewed"}
            ],
            "allowed_entities": [["user", "1"]],
            "forbidden_markers": ["OFFLIMITS"],
        }
    ]
    records = [
        {
            "type": "header",
            "arm": "custom",
            "preregistration_sha256": "fixture-campaign",
            "corpus_sha256": "fixture-corpus",
        }
    ]
    annotations = []
    for pass_index in range(5):
        identity = f"{pass_index}:case-1:0"
        proposals = [
            {
                "source_message_id": "source-1",
                "entity_kind": "user",
                "entity_id": "1",
                "prohibited": False,
                "text": "synthetic preference",
            }
            for _ in range(duplicates)
        ]
        records.extend([
            {"type": "reservation", "id": identity},
            {
                "type": "window",
                "id": identity,
                "case_id": "case-1",
                "pass_index": pass_index,
                "deterministic_failures": [],
                "result": {
                    "resolved_model": "fixture-model",
                    "candidates": proposals,
                    "raw_candidates": proposals,
                },
            },
        ])
        annotations.extend(
            {"window_id": identity, "candidate_index": index, "gold_atom_id": "atom-1"}
            for index in range(duplicates)
        )
    records.append({
        "type": "summary",
        "status": "completed_unscored",
        "complete": True,
        "preregistration_sha256": "fixture-campaign",
        "windows": 5,
    })
    raw = "\n".join(json.dumps(row) for row in records).encode()
    review = {
        "schema_version": 1,
        "reviewed": True,
        "reviewer": "fixture-human",
        "journal_sha256": hashlib.sha256(raw).hexdigest(),
        "annotations": annotations,
        "case_reviews": [
            {"case_id": "case-1", "pass_index": index, "reviewed": True} for index in range(5)
        ],
    }
    return campaign, cases, "fixture-campaign", raw, review


class ExtractionScoringTests(TestCase):
    def test_complete_review_scores_but_does_not_qualify(self):
        result = score_reviewed(*fixture(duplicates=3))
        row = result["metrics"]["case-1"][0]
        self.assertEqual((row["precision"], row["recall"], row["duplicate_rate"]), (1, 1, 2))
        self.assertEqual(result["decision"], "not_qualified")
        self.assertNotIn("synthetic preference", json.dumps(result))

    def test_missing_case_review_or_changed_bytes_refuses(self):
        args = list(fixture())
        args[-1]["case_reviews"].pop()
        with self.assertRaises(ValueError):
            score_reviewed(*args)
        args = list(fixture())
        args[3] += b"\n"
        with self.assertRaises(ValueError):
            score_reviewed(*args)

    def test_hard_zero_is_recomputed_not_trusted_from_journal_flag(self):
        args = list(fixture())
        args[3] = args[3].replace(b"synthetic preference", b"OFFLIMITS")
        args[-1]["journal_sha256"] = hashlib.sha256(args[3]).hexdigest()
        with self.assertRaises(ValueError):
            score_reviewed(*args)

    def test_unreviewed_or_changed_conventions_refuse(self):
        for key, value in (("status", "draft"), ("precision", "different"), ("reviewer", " ")):
            args = list(fixture())
            args[0]["scoring_conventions"][key] = value
            with self.assertRaises(ValueError):
                score_reviewed(*args)

    def test_boolean_pass_and_skipped_provider_candidates_refuse(self):
        args = list(fixture())
        args[-1]["case_reviews"][1]["pass_index"] = True
        with self.assertRaises(ValueError):
            score_reviewed(*args)
        args = list(fixture())
        records = [json.loads(line) for line in args[3].splitlines()]
        records[2]["result"].update(provider_skipped=True, resolved_model="changed")
        args[3] = "\n".join(json.dumps(row) for row in records).encode()
        args[-1]["journal_sha256"] = hashlib.sha256(args[3]).hexdigest()
        with self.assertRaises(ValueError):
            score_reviewed(*args)
