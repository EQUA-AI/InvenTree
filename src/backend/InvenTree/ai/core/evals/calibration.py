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
     "human_pass": true, "notes": "optional"}
"""

from __future__ import annotations

import argparse
import json
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

    @property
    def usable(self) -> bool:
        return self.judged > 0 and self.agreement >= AGREEMENT_GATE


def fold_verdict_to_pass(verdict: dict[str, Any]) -> bool:
    """The scorer's layer-7/8 fold reduced to one pass/fail bit."""
    required = dict(verdict.get("required_claims_present") or {})
    return (
        all(required.values())
        and verdict.get("forbidden_claims_absent") is not False
        and verdict.get("calculations_within_tolerance") is not False
        and verdict.get("no_overclaim") is not False
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
    for row in sample:
        case_id = str(row.get("case_id") or "")
        gold = load_gold(gold_dir, case_id)
        if gold is None:
            skipped.append(case_id)
            continue
        if gold.gold_revision:
            revisions.add(gold.gold_revision)
        verdict = judge(str(row.get("question") or ""), gold, str(row.get("answer") or ""))
        judge_pass = fold_verdict_to_pass(verdict)
        human_pass = bool(row.get("human_pass"))
        judged += 1
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
    )


def load_calibration(path: Path) -> dict[str, Any]:
    """Load a previously written calibration artifact."""
    return json.loads(path.read_text(encoding="utf-8"))


def judge_layers_enabled(artifact: dict[str, Any] | None, fingerprint: str) -> bool:
    """Whether a runner may emit judge layers under this artifact.

    Requires the SAME judge fingerprint (prompt, schema, deployment) and a
    measured agreement at or above the 90% gate — anything else and the
    judge layers stay ``not_scored``.
    """
    if not artifact:
        return False
    return (
        str(artifact.get("judge_fingerprint") or "") == fingerprint
        and float(artifact.get("agreement") or 0.0) >= AGREEMENT_GATE
        and int(artifact.get("judged") or 0) > 0
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
            f"CALIBRATION FAILED: agreement {report.agreement:.2%} < {AGREEMENT_GATE:.0%} "
            f"({len(report.disagreements)} disagreement(s) return to human review)",
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
