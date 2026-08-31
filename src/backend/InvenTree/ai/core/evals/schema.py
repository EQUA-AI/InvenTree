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
    #: R5 WP-I: a cross-corpus item pins a TUPLE — it runs only when every
    #: pinned set is deployed (an agreement question needs both corpora).
    corpus_version: str | tuple[str, ...] | None = None
    dataset: str = "demo"

    @property
    def is_trap(self) -> bool:
        return self.trap_type != "none"

    @property
    def corpus_pins(self) -> tuple[str, ...]:
        """The item's corpus pins as a tuple ('' never appears)."""
        if self.corpus_version is None:
            return ()
        if isinstance(self.corpus_version, str):
            return (self.corpus_version,) if self.corpus_version else ()
        return tuple(v for v in self.corpus_version if v)


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


#: Every key an item mapping may carry. load_items reads with .get(), so
#: before R5 a typo'd key (ground_truth_key:) was silently DROPPED and the
#: item then ran against every deployment — hence the loud refusal below.
ITEM_FIELDS = frozenset({
    "id",
    "question",
    "expected_behavior",
    "ground_truth",
    "ground_truth_keys",
    "trap_type",
    "locale",
    "corpus_version",
    "dataset",
})


def _typed_corpus_version(value: Any) -> str | tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return tuple(str(entry) for entry in value)
    return str(value)


def load_items(path: Path | None = None) -> list[GoldenItem]:
    """Load and type the golden items; raises on structural violations."""
    raw = _load_yaml(path or GOLDEN_DIR / "items.yaml")
    items = []
    for entry in raw.get("items") or []:
        unknown = sorted(set(entry) - ITEM_FIELDS)
        if unknown:
            raise ValueError(
                f"golden item {entry.get('id') or '?'} carries unknown "
                f"field(s) {unknown}; a typo here would silently drop data"
            )
        items.append(
            GoldenItem(
                id=str(entry.get("id") or ""),
                question=str(entry.get("question") or ""),
                expected_behavior=str(entry.get("expected_behavior") or ""),
                ground_truth=str(entry.get("ground_truth") or "").strip(),
                ground_truth_keys=tuple(entry.get("ground_truth_keys") or ()),
                trap_type=str(entry.get("trap_type") or "none"),
                locale=str(entry.get("locale") or "en"),
                corpus_version=_typed_corpus_version(entry.get("corpus_version")),
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


DATASETS = ("demo", "live")


def validate_items(
    items: list[GoldenItem], *, allowed_corpora: set[str] | None = None
) -> list[ValidationError]:
    """Structural checks that keep the set curatable by non-engineers.

    ``allowed_corpora`` is optional because scraping the seeder versions
    needs Django; the always-on CI test supplies it, the standalone runner
    passes None.
    """
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
        if item.dataset not in DATASETS:
            problems.append(ValidationError(item.id, f"dataset must be one of {DATASETS}"))
        if not item.locale or not item.locale.replace("-", "").isalpha() or len(item.locale) > 8:
            problems.append(ValidationError(item.id, "locale must be a short language tag"))
        for key in item.ground_truth_keys:
            if not isinstance(key, str) or not key.strip() or len(key) > 120:
                problems.append(
                    ValidationError(item.id, "ground_truth_keys must be short non-empty strings")
                )
                break
        if item.ground_truth_keys and item.expected_behavior != "answer":
            problems.append(
                ValidationError(item.id, "ground_truth_keys only make sense on answer items")
            )
        if allowed_corpora is not None:
            stray = [pin for pin in item.corpus_pins if pin not in allowed_corpora]
            if stray:
                problems.append(
                    ValidationError(
                        item.id,
                        f"corpus_version pins unknown set(s) {stray}; bump the "
                        "seeder and this allow-list together",
                    )
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
    "DATASETS",
    "EXPECTED_BEHAVIORS",
    "GOLDEN_DIR",
    "ITEM_FIELDS",
    "TRAP_TYPES",
    "GoldenItem",
    "RedTeamCase",
    "ValidationError",
    "load_items",
    "load_redteam",
    "validate_items",
    "validate_redteam",
]
