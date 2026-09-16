"""Judge calibration harness (S14, §13.5).

Before judge scores may be used, the battery judge must agree with a
human-rated sample on pass/fail at >= 90%. This module replays the judge
over the sample's (question, gold, answer) triples, computes agreement,
and writes the calibration ARTIFACT the battery runner requires: judge
layers stay ``not_scored`` unless a matching artifact (same judge
fingerprint, agreement >= 0.90) is supplied. Disagreements are listed and
go back to human review — on those cases the human verdict prevails.

The human sample is authored from run journals in the private store and
never committed (Q48). Row shape (JSONL):

    {"case_id": "Q31", "question": "...", "answer": "...",
     "human_pass": true, "reviewer": "named human", "adversarial": false,
     "notes": "optional"}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from .battery_judge import battery_judge_fingerprint, default_battery_judge_call
from .scenarios import GoldAtoms, load_gold

AGREEMENT_GATE = 0.90
MIN_RATED_ROWS = 50
CALIBRATION_VERSION = 2


@dataclass(frozen=True)
class Disagreement:
    case_id: str
    human_pass: bool
    judge_pass: bool
    rationale: str = ""


@dataclass
class CalibrationReport:
    judge_fingerprint: str
    sample_size: int
    judged: int
    agreement: float
    disagreements: list[Disagreement] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # case ids without gold
    rated_at: str = ""
    gold_revisions: list[str] = field(default_factory=list)
    adversarial_judged: int = 0
    reviewers: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    calibration_version: int = CALIBRATION_VERSION
    sample_sha256: str = ""

    @property
    def usable(self) -> bool:
        return judge_layers_enabled(asdict(self), self.judge_fingerprint)


def fold_verdict_to_pass(verdict: dict[str, Any]) -> bool:
    """The scorer's layer-7/8 fold reduced to one pass/fail bit."""
    required = verdict.get("required_claims_present")
    return (
        isinstance(required, dict)
        and all(value is True for value in required.values())
        and verdict.get("forbidden_claims_absent") is True
        and verdict.get("calculations_within_tolerance") is True
        and verdict.get("no_overclaim") is True
    )


def load_sample(path: Path) -> list[dict[str, Any]]:
    """Load the human-rated JSONL sample."""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def calibrate(
    sample: list[dict[str, Any]],
    gold_dir: Path,
    judge_call: Callable[[str, GoldAtoms, str], dict[str, Any]] | None = None,
) -> CalibrationReport:
    """Replay the judge over the human-rated sample and measure agreement."""
    judge = judge_call or default_battery_judge_call
    disagreements: list[Disagreement] = []
    skipped: list[str] = []
    revisions: set[str] = set()
    agreed = 0
    judged = 0
    adversarial_judged = 0
    reviewers: set[str] = set()
    invalid: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for row in sample:
        case_id = str(row.get("case_id") or "")
        identity = (case_id, str(row.get("question") or ""), str(row.get("answer") or ""))
        reviewer = row.get("reviewer")
        if (
            not all(identity)
            or identity in seen
            or type(row.get("human_pass")) is not bool
            or type(row.get("adversarial")) is not bool
            or not isinstance(reviewer, str)
            or not reviewer.strip()
        ):
            invalid.append(case_id)
            continue
        seen.add(identity)
        gold = load_gold(gold_dir, case_id)
        if gold is None:
            skipped.append(case_id)
            continue
        if gold.gold_revision:
            revisions.add(gold.gold_revision)
        verdict = judge(str(row.get("question") or ""), gold, str(row.get("answer") or ""))
        judge_pass = fold_verdict_to_pass(verdict)
        human_pass = row["human_pass"]
        judged += 1
        adversarial_judged += int(row["adversarial"])
        reviewers.add(reviewer.strip())
        if judge_pass == human_pass:
            agreed += 1
        else:
            disagreements.append(
                Disagreement(
                    case_id=case_id,
                    human_pass=human_pass,
                    judge_pass=judge_pass,
                    rationale=str(verdict.get("rationale") or ""),
                )
            )
    return CalibrationReport(
        judge_fingerprint=battery_judge_fingerprint(),
        sample_size=len(sample),
        judged=judged,
        agreement=(agreed / judged) if judged else 0.0,
        disagreements=disagreements,
        skipped=skipped,
        rated_at=datetime.now(UTC).isoformat(),
        gold_revisions=sorted(revisions),
        adversarial_judged=adversarial_judged,
        reviewers=sorted(reviewers),
        invalid=invalid,
        sample_sha256=hashlib.sha256(
            json.dumps(sample, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest(),
    )


def load_calibration(path: Path) -> dict[str, Any]:
    """Load a previously written calibration artifact."""
    return json.loads(path.read_text(encoding="utf-8"))


def judge_layers_enabled(artifact: dict[str, Any] | None, fingerprint: str) -> bool:
    """Whether a runner may emit judge layers under this artifact.

    Requires the SAME judge fingerprint (prompt, schema, deployment) and a
    measured agreement at or above the 90% gate on at least 50 human-rated
    rows, at least 20% adversarial. Legacy or incomplete artifacts fail closed.
    """
    if not isinstance(artifact, dict):
        return False
    judged = artifact.get("judged")
    adversarial = artifact.get("adversarial_judged")
    agreement = artifact.get("agreement")
    reviewers = artifact.get("reviewers")
    return (
        artifact.get("calibration_version") == CALIBRATION_VERSION
        and str(artifact.get("judge_fingerprint") or "") == fingerprint
        and type(judged) is int
        and judged >= MIN_RATED_ROWS
        and type(artifact.get("sample_size")) is int
        and artifact["sample_size"] == judged
        and type(adversarial) is int
        and 0 <= adversarial <= judged
        and adversarial * 5 >= judged
        and type(agreement) in (int, float)
        and math.isfinite(agreement)
        and AGREEMENT_GATE <= agreement <= 1
        and artifact.get("invalid") == []
        and artifact.get("skipped") == []
        and isinstance(reviewers, list)
        and bool(reviewers)
        and all(isinstance(reviewer, str) and reviewer.strip() for reviewer in reviewers)
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: calibrate and write the artifact; exit 1 below the gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", required=True, help="human-rated JSONL sample path")
    parser.add_argument("--gold-dir", required=True, help="private gold store (AIMMS_GOLD_DIR)")
    parser.add_argument("--json-out", default="", help="write the calibration artifact here")
    args = parser.parse_args(argv)

    report = calibrate(load_sample(Path(args.sample)), Path(args.gold_dir))
    document = asdict(report)
    document["usable"] = report.usable
    rendered = json.dumps(document, indent=2)
    if args.json_out:
        Path(args.json_out).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if not report.usable:
        print(
            f"CALIBRATION FAILED: requires >= {MIN_RATED_ROWS} complete human ratings, "
            f">= 20% adversarial and >= {AGREEMENT_GATE:.0%} agreement; "
            f"observed {report.judged} judged, {report.adversarial_judged} adversarial, "
            f"{report.agreement:.2%} agreement. "
            f"{len(report.disagreements)} disagreement(s) return to human review.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AGREEMENT_GATE",
    "CalibrationReport",
    "Disagreement",
    "calibrate",
    "fold_verdict_to_pass",
    "judge_layers_enabled",
    "load_calibration",
    "load_sample",
    "main",
]
