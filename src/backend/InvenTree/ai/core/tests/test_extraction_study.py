"""Authored study preflight and deterministic statistical contract cases."""

import copy
from types import SimpleNamespace
from unittest import TestCase

from ai.core.evals.extraction_statistics import mcnemar_exact, paired_card
from ai.core.evals.run_extraction_study import (
    DEFAULT_CAMPAIGN,
    deterministic_failures,
    load_study,
    require_execution,
    windows,
)


class ExtractionStudyTests(TestCase):
    def test_draft_assets_are_complete_inputs_but_cannot_execute(self):
        campaign, cases, _ = load_study(DEFAULT_CAMPAIGN)
        self.assertGreaterEqual(len(cases), 50)
        self.assertEqual(sum(len(case["gold_atoms"]) for case in cases), 800)
        with self.assertRaises(ValueError):
            require_execution(campaign, cases, SimpleNamespace())
        for case in cases:
            for batch in windows(case):
                self.assertLessEqual(len(batch), 5)
                self.assertLessEqual(sum(len(row["content"]) for row in batch), 10000)

    def test_cross_client_and_forbidden_markers_are_hard_failures(self):
        _, cases, _ = load_study(DEFAULT_CAMPAIGN)
        case = cases[0]
        source = case["messages"][0]["id"]
        candidate = {
            "source_message_id": source,
            "entity_kind": "machine",
            "entity_id": "foreign",
            "text": case["forbidden_markers"][0],
            "prohibited": False,
        }
        self.assertEqual(
            set(deterministic_failures(case, [candidate], {source})),
            {"cross_client", "forbidden_marker"},
        )

    def test_pairing_and_human_review_are_required(self):
        rows = {
            str(i): [
                {
                    "pass_index": p,
                    "recall": 0.5,
                    "precision": 0.5,
                    "f1": 0.5,
                    "duplicate_rate": 0.0,
                    "hard_zero_failures": 0,
                    "reviewer": "fixture-human",
                }
                for p in range(5)
            ]
            for i in range(50)
        }
        alternative = copy.deepcopy(rows)
        alternative["0"][0]["reviewer"] = ""
        with self.assertRaises(ValueError):
            paired_card(rows, alternative, seed=1, replicates=1000)
        alternative = copy.deepcopy(rows)
        alternative["0"][0]["pass_index"] = 1
        with self.assertRaises(ValueError):
            paired_card(rows, alternative, seed=1, replicates=1000)

    def test_exact_discordance_retains_no_improvement_as_null(self):
        result = mcnemar_exact({"a": True, "b": False}, {"a": True, "b": False})
        self.assertEqual(result["p_two_sided"], 1.0)
        self.assertEqual(result["discordant"], 0)
