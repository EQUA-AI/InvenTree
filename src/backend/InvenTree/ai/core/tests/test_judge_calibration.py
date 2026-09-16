"""S14 judge accounting + calibration tests — no network, fakes only."""

from __future__ import annotations

import json
from dataclasses import asdict
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from ai.core.evals import judge as judge_mod
from ai.core.evals.battery_judge import (
    battery_judge_fingerprint,
    battery_judge_payload,
    battery_verdict_schema,
)
from ai.core.evals.calibration import (
    AGREEMENT_GATE,
    calibrate,
    fold_verdict_to_pass,
    judge_layers_enabled,
    load_sample,
    main,
)
from ai.core.evals.scenarios import GoldAtoms


def _gold(**over) -> GoldAtoms:
    base = {
        "case_id": "Q31",
        "answerability": "answerable",
        "required_claims": ("count_is_stated",),
        "required_facets": ("records", "coverage"),
        "forbidden_claims": ("root_cause_asserted",),
        "gold_revision": "gold-v1",
    }
    base.update(over)
    return GoldAtoms(**base)


def _response(prompt=100, completion=20, total=120, with_usage=True):
    usage = (
        SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)
        if with_usage
        else None
    )
    return SimpleNamespace(usage=usage)


# --------------------------------------------------------------------------- #
# Token accounting (WP-B6)                                                     #
# --------------------------------------------------------------------------- #
def test_judge_usage_accumulates_and_drains():
    judge_mod.drain_judge_usage()  # reset any prior state
    judge_mod.record_judge_usage(_response())
    judge_mod.record_judge_usage(_response(prompt=50, completion=10, total=60))
    drained = judge_mod.drain_judge_usage()
    assert drained == {
        "calls": 2,
        "prompt_tokens": 150,
        "completion_tokens": 30,
        "total_tokens": 180,
    }
    # Drain resets.
    assert judge_mod.drain_judge_usage()["calls"] == 0


def test_missing_usage_still_counts_the_call():
    judge_mod.drain_judge_usage()
    judge_mod.record_judge_usage(_response(with_usage=False))
    drained = judge_mod.drain_judge_usage()
    assert drained["calls"] == 1
    assert drained["total_tokens"] == 0


# --------------------------------------------------------------------------- #
# Fingerprints                                                                 #
# --------------------------------------------------------------------------- #
def test_fingerprints_are_stable_and_distinct():
    assert judge_mod.judge_fingerprint() == judge_mod.judge_fingerprint()
    assert battery_judge_fingerprint() == battery_judge_fingerprint()
    assert battery_judge_fingerprint() != judge_mod.judge_fingerprint()


def test_fingerprint_tracks_the_deployment(monkeypatch):
    before = battery_judge_fingerprint()
    monkeypatch.setenv("AIMMS_JUDGE_DEPLOYMENT", "some-other-deployment")
    assert battery_judge_fingerprint() != before


# --------------------------------------------------------------------------- #
# The per-case strict schema                                                   #
# --------------------------------------------------------------------------- #
def test_verdict_schema_is_strict_and_facet_fixed():
    schema = battery_verdict_schema(_gold())
    assert schema["additionalProperties"] is False
    claims = schema["properties"]["required_claims_present"]
    assert claims["additionalProperties"] is False
    assert claims["required"] == ["records", "coverage"]
    assert set(claims["properties"]) == {"records", "coverage"}
    # Facets fall back to required_claims when no facets are authored.
    fallback = battery_verdict_schema(_gold(required_facets=()))
    assert fallback["properties"]["required_claims_present"]["required"] == ["count_is_stated"]


def test_payload_carries_gold_atoms_and_truncates_the_answer():
    payload = json.loads(battery_judge_payload("How many?", _gold(), "x" * 9000))
    assert payload["forbidden_claims"] == ["root_cause_asserted"]
    assert payload["required_facets"] == ["records", "coverage"]
    assert len(payload["answer"]) == 8000


# --------------------------------------------------------------------------- #
# Fold + agreement                                                             #
# --------------------------------------------------------------------------- #
def test_fold_verdict_to_pass_semantics():
    good = {
        "required_claims_present": {"records": True},
        "forbidden_claims_absent": True,
        "calculations_within_tolerance": True,
        "no_overclaim": True,
    }
    assert fold_verdict_to_pass(good)
    assert not fold_verdict_to_pass(dict(good, required_claims_present={"records": False}))
    assert not fold_verdict_to_pass(dict(good, forbidden_claims_absent=False))
    assert not fold_verdict_to_pass(dict(good, no_overclaim=False))


def _write_gold(gold_dir: Path, case_id: str):
    (gold_dir / "gold").mkdir(exist_ok=True)
    (gold_dir / "gold" / f"{case_id}.yaml").write_text(
        "answerability: answerable\nrequired_facets: [records]\ngold_revision: gold-v1\n",
        encoding="utf-8",
    )


def test_calibrate_measures_agreement_and_lists_disagreements(tmp_path: Path):
    for case_id in ("Q01", "Q02", "Q03", "Q04"):
        _write_gold(tmp_path, case_id)
    sample = [
        {"case_id": "Q01", "question": "a", "answer": "x", "human_pass": True},
        {"case_id": "Q02", "question": "b", "answer": "y", "human_pass": True},
        {"case_id": "Q03", "question": "c", "answer": "z", "human_pass": False},
        {"case_id": "Q04", "question": "d", "answer": "w", "human_pass": False},
        {"case_id": "Q99", "question": "e", "answer": "v", "human_pass": True},  # no gold
    ]
    for row in sample:
        row.update(reviewer="test reviewer", adversarial=False)

    def fake_judge(question, gold, answer):
        # Judge passes everything -> disagrees with the two human fails.
        return {
            "required_claims_present": {"records": True},
            "forbidden_claims_absent": True,
            "calculations_within_tolerance": True,
            "no_overclaim": True,
            "rationale": "looks fine",
        }

    report = calibrate(sample, tmp_path, judge_call=fake_judge)
    assert report.sample_size == 5
    assert report.judged == 4
    assert report.agreement == pytest.approx(0.5)
    assert {d.case_id for d in report.disagreements} == {"Q03", "Q04"}
    assert report.skipped == ["Q99"]
    assert report.gold_revisions == ["gold-v1"]
    assert not report.usable


def test_the_gate_and_artifact_binding():
    fingerprint = battery_judge_fingerprint()
    good = {
        "judge_fingerprint": fingerprint,
        "agreement": 0.95,
        "judged": 50,
        "sample_size": 50,
        "adversarial_judged": 10,
        "reviewers": ["test reviewer"],
        "invalid": [],
        "skipped": [],
        "calibration_version": 2,
    }
    assert judge_layers_enabled(good, fingerprint)
    assert not judge_layers_enabled(None, fingerprint)
    assert not judge_layers_enabled(dict(good, agreement=AGREEMENT_GATE - 0.01), fingerprint)
    assert not judge_layers_enabled(dict(good, judged=0), fingerprint)
    assert not judge_layers_enabled(dict(good, judge_fingerprint="stale"), fingerprint)
    assert not judge_layers_enabled(dict(good, judged=49, sample_size=49), fingerprint)
    assert not judge_layers_enabled(dict(good, adversarial_judged=9), fingerprint)
    assert not judge_layers_enabled(dict(good, sample_size=51), fingerprint)
    assert not judge_layers_enabled(dict(good, reviewers=[]), fingerprint)
    assert not judge_layers_enabled(dict(good, invalid=["Q01"]), fingerprint)
    assert not judge_layers_enabled(dict(good, skipped=["Q01"]), fingerprint)
    for malformed in (None, "0.99", float("nan"), float("inf"), 1.1, True):
        assert not judge_layers_enabled(dict(good, agreement=malformed), fingerprint)
    assert not judge_layers_enabled(
        {"judge_fingerprint": fingerprint, "agreement": 1, "judged": 50}, fingerprint
    )


def test_cli_fails_closed_with_no_judgeable_sample(tmp_path: Path):
    """Every sample row lacking gold -> judged 0, no network, exit 1."""
    sample_path = tmp_path / "sample.jsonl"
    sample_path.write_text(
        json.dumps({
            "case_id": "Q99",
            "question": "a",
            "answer": "x",
            "human_pass": True,
            "reviewer": "test reviewer",
            "adversarial": False,
        })
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "gold").mkdir()
    artifact = tmp_path / "artifact.json"
    code = main([
        "--sample",
        str(sample_path),
        "--gold-dir",
        str(tmp_path),
        "--json-out",
        str(artifact),
    ])
    assert code == 1
    document = json.loads(artifact.read_text())
    assert document["usable"] is False
    assert document["judged"] == 0


def test_load_sample_skips_blank_lines(tmp_path: Path):
    path = tmp_path / "s.jsonl"
    path.write_text('{"case_id": "Q01"}\n\n{"case_id": "Q02"}\n', encoding="utf-8")
    assert [row["case_id"] for row in load_sample(path)] == ["Q01", "Q02"]


def _passing_judge(question, gold, answer):
    return {
        "required_claims_present": {"records": True},
        "forbidden_claims_absent": True,
        "calculations_within_tolerance": True,
        "no_overclaim": True,
    }


def _rated_sample():
    return [
        {
            "case_id": "Q01",
            "question": f"Question {i}",
            "answer": "Answer",
            "human_pass": True,
            "reviewer": "test reviewer",
            "adversarial": i < 10,
        }
        for i in range(50)
    ]


def test_complete_composition_enables_roundtrip_artifact(tmp_path):
    _write_gold(tmp_path, "Q01")
    report = calibrate(_rated_sample(), tmp_path, judge_call=_passing_judge)
    assert report.usable
    assert report.judged == 50
    assert report.adversarial_judged == 10
    assert len(report.sample_sha256) == 64
    assert judge_layers_enabled(json.loads(json.dumps(asdict(report))), report.judge_fingerprint)


@pytest.mark.parametrize("human_pass", [None, "false", 0, 1])
def test_missing_or_coerced_human_verdict_never_reaches_judge(tmp_path, human_pass):
    _write_gold(tmp_path, "Q01")
    sample = _rated_sample()[:1]
    sample[0]["human_pass"] = human_pass
    calls = []
    report = calibrate(sample, tmp_path, judge_call=lambda *args: calls.append(args))
    assert calls == []
    assert report.invalid == ["Q01"]
    assert report.judged == 0
    assert not report.usable


def test_duplicates_cannot_fill_sample_floor(tmp_path):
    _write_gold(tmp_path, "Q01")
    sample = _rated_sample()
    sample[-1] = dict(sample[0])
    report = calibrate(sample, tmp_path, judge_call=_passing_judge)
    assert report.judged == 49
    assert report.invalid == ["Q01"]
    assert not report.usable


def test_adversarial_share_is_counted_only_on_judged_rows(tmp_path):
    _write_gold(tmp_path, "Q01")
    sample = _rated_sample()
    sample[0]["case_id"] = "NO-GOLD"
    report = calibrate(sample, tmp_path, judge_call=_passing_judge)
    assert report.adversarial_judged == 9
    assert report.skipped == ["NO-GOLD"]
    assert not report.usable


def test_malformed_judge_verdict_cannot_be_a_pass():
    assert not fold_verdict_to_pass({})
    verdict = _passing_judge(None, None, None)
    verdict["required_claims_present"] = {"records": "false"}
    assert not fold_verdict_to_pass(verdict)
