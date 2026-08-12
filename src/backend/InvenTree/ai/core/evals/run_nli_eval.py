"""S40 offline NLI threshold sweep (dark; eval-workstation only).

Scores stored (evidence, answer) pairs with the NLI groundedness checker
and sweeps candidate thresholds, so the Phase 8 cascade can pick its
operating point from data instead of a guess. Requires
``ai/requirements-eval.txt`` to be installed manually; refuses politely
otherwise.

Input: a JSONL file where each line is
    {"id": str, "evidence": str, "answer": str, "grounded": bool}
(`grounded` is the human label; collect pairs from golden-set runs).

Usage:
    python -m ai.core.evals.run_nli_eval pairs.jsonl [--thresholds 0.3,0.5,0.7]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys


def _load_pairs(path: str) -> list[dict]:
    pairs = []
    with pathlib.Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pairs", help="JSONL of {id, evidence, answer, grounded}")
    parser.add_argument("--thresholds", default="0.2,0.3,0.4,0.5,0.6,0.7,0.8")
    args = parser.parse_args(argv)

    from ai.core.grounding_nli import NLIGroundednessChecker, is_available

    if not is_available():
        print(
            "NLI dependencies missing — pip install -r ai/requirements-eval.txt",
            file=sys.stderr,
        )
        return 2

    checker = NLIGroundednessChecker()
    pairs = _load_pairs(args.pairs)
    scored = []
    for pair in pairs:
        result = checker.score(str(pair["evidence"]), str(pair["answer"]))
        if result is None:
            continue
        scored.append((bool(pair["grounded"]), result.score, pair.get("id", "?")))

    if not scored:
        print("no pairs scored", file=sys.stderr)
        return 2

    print(f"scored {len(scored)} pairs with {checker.model_id}")
    print("threshold  precision  recall  flagged")
    for raw in args.thresholds.split(","):
        threshold = float(raw)
        # "flagged" = pairs the checker calls UNgrounded (score < threshold).
        flagged = [(grounded, score) for grounded, score, _ in scored if score < threshold]
        true_flags = [1 for grounded, _ in flagged if not grounded]
        actual_ungrounded = [1 for grounded, _, _ in scored if not grounded]
        precision = (sum(true_flags) / len(flagged)) if flagged else 0.0
        recall = sum(true_flags) / len(actual_ungrounded) if actual_ungrounded else 0.0
        print(f"{threshold:9.2f}  {precision:9.3f}  {recall:6.3f}  {len(flagged):7d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
