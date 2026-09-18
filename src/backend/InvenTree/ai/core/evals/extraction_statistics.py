"""Transcript-clustered paired study statistics over reviewed per-pass metrics.

This module never judges prose, invents labels, imports an optional engine or
promotes an arm. The remaining governance/card rows require their own evidence.
"""

import math
import random
from statistics import mean

METRICS = ("recall", "precision", "f1", "duplicate_rate")


def _validate(arm):
    if not isinstance(arm, dict) or len(arm) < 50:
        raise ValueError("At least fifty transcript clusters are required")
    for rows in arm.values():
        if not isinstance(rows, list) or len(rows) != 5:
            raise ValueError("Five reviewed passes are required per transcript")
        if {row.get("pass_index") for row in rows} != set(range(5)):
            raise ValueError("Pass identities must be unique")
        for row in rows:
            if not isinstance(row.get("reviewer"), str) or not row["reviewer"].strip():
                raise ValueError("A human reviewer is required")
            for metric in METRICS:
                if (
                    type(row.get(metric)) not in (int, float)
                    or not math.isfinite(row[metric])
                    or row[metric] < 0
                    or (metric != "duplicate_rate" and row[metric] > 1)
                ):
                    raise ValueError("Invalid reviewed metric")
            expected = (
                2 * row["recall"] * row["precision"] / (row["recall"] + row["precision"])
                if row["recall"] + row["precision"]
                else 0
            )
            if abs(expected - row["f1"]) > 1e-9:
                raise ValueError("F1 does not match reviewed precision and recall")
            if type(row.get("hard_zero_failures")) is not int or row["hard_zero_failures"] < 0:
                raise ValueError("Missing deterministic family evidence")


def paired_card(custom, alternative, *, seed, replicates=5000):
    """Bootstrap transcript means, retaining all five paired passes per cluster."""
    _validate(custom)
    _validate(alternative)
    if (
        set(custom) != set(alternative)
        or type(seed) is not int
        or type(replicates) is not int
        or not 1000 <= replicates <= 10000
    ):
        raise ValueError("Invalid paired study design")
    identities = sorted(custom)
    deltas = {
        metric: [
            mean(row[metric] for row in alternative[key]) - mean(row[metric] for row in custom[key])
            for key in identities
        ]
        for metric in METRICS
    }
    samples = {metric: [] for metric in METRICS}
    rng = random.Random(seed)
    for _ in range(replicates):
        indices = [rng.randrange(len(identities)) for _ in identities]
        for metric in METRICS:
            samples[metric].append(mean(deltas[metric][i] for i in indices))
    metrics = {}
    for metric in METRICS:
        values = sorted(samples[metric])
        metrics[metric] = {
            "delta": mean(deltas[metric]),
            "ci90": [values[int(replicates * 0.05)], values[int(replicates * 0.95) - 1]],
        }
    hard_zero = any(
        row["hard_zero_failures"]
        for arm in (custom, alternative)
        for rows in arm.values()
        for row in rows
    )
    gates = {
        "1": metrics["recall"]["delta"] >= 0.05 and metrics["recall"]["ci90"][0] > 0,
        "2": metrics["precision"]["ci90"][0] > -0.03,
        "3a": metrics["f1"]["delta"] >= 0.10,
        "4": metrics["duplicate_rate"]["delta"] <= 0,
        "hard_zeros": not hard_zero,
    }
    return {
        "metrics": metrics,
        "gates": gates,
        "clusters": len(identities),
        "passes": 5,
        "bootstrap_seed": seed,
        "replicates": replicates,
        "decision": "not_qualified",
        "remaining_card_rows": [
            "3b",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10",
            "11",
            "12",
            "13",
            "14",
            "15",
            "16",
        ],
    }


def mcnemar_exact(custom_correct, alternative_correct):
    """Two-sided exact discordance check for matched, reviewed atom booleans."""
    if set(custom_correct) != set(alternative_correct) or any(
        type(value) is not bool
        for arm in (custom_correct, alternative_correct)
        for value in arm.values()
    ):
        raise ValueError("Atom identities and boolean verdicts must match")
    gained = sum(alternative_correct[key] and not custom_correct[key] for key in custom_correct)
    lost = sum(custom_correct[key] and not alternative_correct[key] for key in custom_correct)
    discordant = gained + lost
    probability = (
        min(
            1.0,
            2 * sum(math.comb(discordant, i) for i in range(min(gained, lost) + 1)) / 2**discordant,
        )
        if discordant
        else 1.0
    )
    return {"gained": gained, "lost": lost, "discordant": discordant, "p_two_sided": probability}
