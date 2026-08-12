"""Golden-set item loading and validation (S39).

Pure Python + PyYAML: this module must import cleanly in CI without Azure
credentials, agent-framework, or Django — validation tests are always-on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

GOLDEN_DIR = Path(__file__).parent / "golden"

EXPECTED_BEHAVIORS = ("answer", "abstain", "clarify")
TRAP_TYPES = ("absent_spec", "ambiguous_symptom", "wrong_machine", "none")


@dataclass(frozen=True)
class GoldenItem:
    """One curated question with its expected behavior and ground truth."""

    id: str
    question: str
    expected_behavior: str
    ground_truth: str = ""
    ground_truth_keys: tuple[str, ...] = ()
    trap_type: str = "none"
    locale: str = "en"
    corpus_version: str | None = None
    dataset: str = "demo"

    @property
    def is_trap(self) -> bool:
        return self.trap_type != "none"


@dataclass(frozen=True)
class RedTeamCase:
    """One adversarial turn with deterministic scoring markers."""

    id: str
    question: str
    forbidden_markers: tuple[str, ...] = ()
    required_any: tuple[str, ...] = ()


@dataclass
class ValidationError:
    """One schema violation, addressable by item id."""

    item_id: str
    problem: str


def _load_yaml(path: Path) -> Any:
    import yaml

    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_items(path: Path | None = None) -> list[GoldenItem]:
    """Load and type the golden items; raises on structural violations."""
    raw = _load_yaml(path or GOLDEN_DIR / "items.yaml")
    items = []
    for entry in raw.get("items") or []:
        items.append(
            GoldenItem(
                id=str(entry.get("id") or ""),
                question=str(entry.get("question") or ""),
                expected_behavior=str(entry.get("expected_behavior") or ""),
                ground_truth=str(entry.get("ground_truth") or "").strip(),
                ground_truth_keys=tuple(entry.get("ground_truth_keys") or ()),
                trap_type=str(entry.get("trap_type") or "none"),
                locale=str(entry.get("locale") or "en"),
                corpus_version=entry.get("corpus_version"),
                dataset=str(entry.get("dataset") or "demo"),
            )
        )
    return items


def load_redteam(path: Path | None = None) -> list[RedTeamCase]:
    """Load the red-team cases."""
    raw = _load_yaml(path or GOLDEN_DIR / "redteam.yaml")
    return [
        RedTeamCase(
            id=str(entry.get("id") or ""),
            question=str(entry.get("question") or ""),
            forbidden_markers=tuple(entry.get("forbidden_markers") or ()),
            required_any=tuple(entry.get("required_any") or ()),
        )
        for entry in raw.get("cases") or []
    ]


def validate_items(items: list[GoldenItem]) -> list[ValidationError]:
    """Structural checks that keep the set curatable by non-engineers."""
    problems: list[ValidationError] = []
    seen: set[str] = set()
    for item in items:
        if not item.id:
            problems.append(ValidationError("?", "item without an id"))
            continue
        if item.id in seen:
            problems.append(ValidationError(item.id, "duplicate id"))
        seen.add(item.id)
        if not item.question.strip():
            problems.append(ValidationError(item.id, "empty question"))
        if item.expected_behavior not in EXPECTED_BEHAVIORS:
            problems.append(
                ValidationError(item.id, f"expected_behavior must be one of {EXPECTED_BEHAVIORS}")
            )
        if item.trap_type not in TRAP_TYPES:
            problems.append(ValidationError(item.id, f"trap_type must be one of {TRAP_TYPES}"))
        if item.expected_behavior == "answer" and not item.ground_truth:
            problems.append(
                ValidationError(item.id, "answer items need ground_truth for the judge")
            )
    return problems


def validate_redteam(cases: list[RedTeamCase]) -> list[ValidationError]:
    """Structural checks for the red-team file."""
    problems: list[ValidationError] = []
    seen: set[str] = set()
    for case in cases:
        if not case.id or case.id in seen:
            problems.append(ValidationError(case.id or "?", "missing or duplicate id"))
        seen.add(case.id)
        if not case.question.strip():
            problems.append(ValidationError(case.id, "empty question"))
    return problems


__all__ = [
    "EXPECTED_BEHAVIORS",
    "GOLDEN_DIR",
    "TRAP_TYPES",
    "GoldenItem",
    "RedTeamCase",
    "ValidationError",
    "load_items",
    "load_redteam",
    "validate_items",
    "validate_redteam",
]
