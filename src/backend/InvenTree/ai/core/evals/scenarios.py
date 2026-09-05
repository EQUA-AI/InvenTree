"""Battery scenario schema, loaders, and validation (S14, §13.6).

Companion to ``schema.py`` (the v1 golden items stay untouched). Pure
Python + PyYAML — imports cleanly in CI without Azure, agent-framework,
or Django, so the structural tests are always-on.

Two committed battery files live in ``battery/``:

- ``fixture_battery.yaml`` — synthetic-corpus scenarios with INLINE
  questions (sanitized by construction, §13.4).
- ``solar_battery.yaml`` — the live solar battery: ids, expectations, and
  ``question_ref`` ONLY. Verbatim solar question text in a solar file is a
  validation failure — that is the blinding fence (§13.5/Q48): the private
  question store (``AIMMS_GOLD_DIR/questions.yaml``) owns the text and
  never enters the repo.

Gold evidence atoms (§13.5) are HUMAN-authored, live in the private store
(``AIMMS_GOLD_DIR/gold/<case_id>.yaml``), and are loaded — never
fabricated — by :func:`load_gold`. Absent gold disables the judge layers
only; deterministic layers always run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schema import ValidationError

BATTERY_DIR = Path(__file__).parent / "battery"

#: Named deterministic boundary assertions (§13.6 example).
ASSERTION_NAMES = ("scope_persisted", "evidence_entails_claims", "no_governed_effect")

#: The TaskIntent contract (§7.2), mirrored by analysis_battery_cases.yaml.
TASK_INTENTS = (
    "source_inventory",
    "manual_fact",
    "record_retrieval",
    "fleet_aggregate",
    "trend_analysis",
    "manual_wo_comparison",
    "safety_lookup",
    "part_advice",
    "diagnostic",
    "governed_action",
    "general",
)

#: What a turn may be expected to do at a given tier. ``capability_boundary``
#: is the ONLY pass for a tier-disabled turn and never counts as substantive
#: completion (§13.6 scoring semantics).
TIER_BEHAVIORS = ("answer", "capability_boundary", "abstain", "clarify", "refuse")

ANSWERABILITY = ("answerable", "partially_answerable", "unanswerable")

DATASETS = ("fixture", "solar")

#: The rails a memory-battery case exercises (plan of record §9 / D2). Every
#: M-case in a ``memory_*`` battery file names exactly one.
RAILS = ("wf8", "rbac_run", "reasoning", "routing")

#: Workflow ids ``workflow_used`` may legitimately carry (turn-0
#: ``expected_workflow`` assertions are checked against this vocabulary).
KNOWN_WORKFLOWS = (
    "wf1",
    "wf2",
    "wf3",
    "wf4",
    "wf5",
    "wf6",
    "wf7",
    "wf8",
    "wf9",
    "analysis_executor",
    "safety_refusal",
    "reasoning_refusal",
    "question_declined",
)


@dataclass(frozen=True)
class ScenarioTurn:
    """One submitted request within a case."""

    question: str = ""  # inline text (fixture battery only)
    question_ref: str = ""  # key into the private question store (solar)
    expected_intent: str = ""  # empty = layer-2 intent unasserted
    expected_behavior_by_tier: tuple[tuple[int, str], ...] = ()
    complete_population_required: bool = False
    forbidden_entity_fixture_keys: tuple[str, ...] = ()
    clarification_followup_ref: str = ""  # fixed scripted follow-up (gold atom)
    # D1 (M1 gate): the workflow the turn must land on (empty = unasserted),
    # fixture keys whose id/marker MUST surface (the follow-up recall proof),
    # and whether the routing classifier should have seen a thread summary
    # (REPORTED per turn, never failing — the M1 exit gate reads the rate).
    expected_workflow: str = ""
    required_entity_fixture_keys: tuple[str, ...] = ()
    expect_conversation_summary_present: bool | None = None

    def behavior_for_tier(self, tier: int) -> str:
        """The expected behavior at ``tier`` (highest matching key wins)."""
        best = ""
        for min_tier, behavior in sorted(self.expected_behavior_by_tier):
            if tier >= min_tier:
                best = behavior
        return best


@dataclass(frozen=True)
class ScenarioService:
    """Service-level completion expectations."""

    allowed_statuses: tuple[int, ...] = (200,)


@dataclass(frozen=True)
class ScenarioCase:
    """One battery case: a fresh thread, or one continuous multi-turn thread."""

    id: str
    fresh_thread: bool = True
    minimum_capability_tier: int = 1
    scope_machine_fixture_keys: tuple[str, ...] = ()
    turns: tuple[ScenarioTurn, ...] = ()
    required_assertions: tuple[str, ...] = ()
    service: ScenarioService = field(default_factory=ScenarioService)
    dataset: str = "fixture"
    # D1 (M1 gate): the rail this case exercises (required for memory
    # M-cases) and the deployment flags it needs — a falsy flag in the
    # captured posture SKIPS the case with a journaled reason (never fails).
    rail: str = ""
    requires_flags: tuple[str, ...] = ()

    @property
    def is_multi_turn(self) -> bool:
        return len(self.turns) > 1


@dataclass(frozen=True)
class RepeatTranche:
    """The preregistered high-risk repeat tranche (§13.6/Q40)."""

    case_ids: tuple[str, ...] = ()
    repetitions: int = 20


@dataclass(frozen=True)
class BatteryFile:
    """One loaded battery scenario file."""

    schema_version: int
    dataset: str
    fixture_set_versions: tuple[str, ...]
    cases: tuple[ScenarioCase, ...]
    repeat_tranche: RepeatTranche | None = None
    #: The file name this battery was loaded from ("" for in-memory files).
    #: ``memory_*`` files carry the rail requirement (validate_battery).
    source: str = ""

    @property
    def is_memory_battery(self) -> bool:
        return self.source.startswith("memory_")

    def case(self, case_id: str) -> ScenarioCase | None:
        for entry in self.cases:
            if entry.id == case_id:
                return entry
        return None


# --------------------------------------------------------------------------- #
# Private gold atoms (§13.5) — loaded, never authored, from AIMMS_GOLD_DIR    #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GoldCalculation:
    """One independently recomputed calculation with its rules."""

    name: str
    value: str
    operands: tuple[str, ...] = ()
    inclusion_rule: str = ""
    date_field: str = ""
    timezone: str = ""
    tolerance: str = ""


@dataclass(frozen=True)
class GoldAtoms:
    """Human-authored evidence atoms for one case — never ideal prose."""

    case_id: str
    answerability: str
    minimum_tier: int = 1
    accepted_source_ids: tuple[str, ...] = ()
    accepted_revisions: tuple[str, ...] = ()
    required_claims: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    calculations: tuple[GoldCalculation, ...] = ()
    required_facets: tuple[str, ...] = ()
    clarification: str = ""
    gold_revision: str = ""


def _load_yaml(path: Path) -> Any:
    import yaml

    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _tier_map(raw: Any) -> tuple[tuple[int, str], ...]:
    if not raw:
        return ()
    return tuple(sorted((int(key), str(value)) for key, value in dict(raw).items()))


def _turn(raw: dict[str, Any]) -> ScenarioTurn:
    return ScenarioTurn(
        question=str(raw.get("question") or ""),
        question_ref=str(raw.get("question_ref") or ""),
        expected_intent=str(raw.get("expected_intent") or ""),
        expected_behavior_by_tier=_tier_map(raw.get("expected_behavior_by_tier")),
        complete_population_required=bool(raw.get("complete_population_required")),
        forbidden_entity_fixture_keys=tuple(raw.get("forbidden_entity_fixture_keys") or ()),
        clarification_followup_ref=str(raw.get("clarification_followup_ref") or ""),
        expected_workflow=str(raw.get("expected_workflow") or ""),
        required_entity_fixture_keys=tuple(raw.get("required_entity_fixture_keys") or ()),
        expect_conversation_summary_present=(
            None
            if raw.get("expect_conversation_summary_present") is None
            else bool(raw.get("expect_conversation_summary_present"))
        ),
    )


def _case(raw: dict[str, Any], dataset: str) -> ScenarioCase:
    scope = raw.get("scope") or {}
    raw_turns = raw.get("turns")
    if raw_turns is None and (raw.get("question") or raw.get("question_ref")):
        raw_turns = [raw]  # single-turn shorthand: turn fields on the case
    service = raw.get("service") or {}
    return ScenarioCase(
        id=str(raw.get("id") or ""),
        fresh_thread=bool(raw.get("fresh_thread", True)),
        minimum_capability_tier=int(raw.get("minimum_capability_tier", 1)),
        scope_machine_fixture_keys=tuple(scope.get("machine_fixture_keys") or ()),
        turns=tuple(_turn(dict(entry)) for entry in (raw_turns or [])),
        required_assertions=tuple(raw.get("required_assertions") or ()),
        service=ScenarioService(
            allowed_statuses=tuple(int(s) for s in service.get("allowed_statuses") or (200,))
        ),
        dataset=str(raw.get("dataset") or dataset),
        rail=str(raw.get("rail") or ""),
        requires_flags=tuple(str(flag) for flag in raw.get("requires_flags") or ()),
    )


def load_battery(path: Path) -> BatteryFile:
    """Load one battery scenario file (structure only; see validate_battery)."""
    raw = _load_yaml(path) or {}
    dataset = str(raw.get("dataset") or "fixture")
    tranche_raw = raw.get("repeat_tranche")
    tranche = None
    if tranche_raw:
        tranche = RepeatTranche(
            case_ids=tuple(tranche_raw.get("case_ids") or ()),
            repetitions=int(tranche_raw.get("repetitions", 20)),
        )
    return BatteryFile(
        schema_version=int(raw.get("schema_version", 1)),
        dataset=dataset,
        fixture_set_versions=tuple(raw.get("fixture_set_versions") or ()),
        cases=tuple(_case(dict(entry), dataset) for entry in raw.get("cases") or []),
        repeat_tranche=tranche,
        source=path.name,
    )


def load_fixture_keys(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """The committed fixture-key manifest: key -> descriptor."""
    raw = _load_yaml(path or BATTERY_DIR / "fixture_keys.yaml") or {}
    return {str(key): dict(value or {}) for key, value in (raw.get("keys") or {}).items()}


def validate_battery(
    battery: BatteryFile, fixture_keys: dict[str, dict[str, Any]]
) -> list[ValidationError]:
    """Structural checks, including the solar blinding fence."""
    problems: list[ValidationError] = []
    seen: set[str] = set()
    if battery.dataset not in DATASETS:
        problems.append(ValidationError("?", f"dataset must be one of {DATASETS}"))
    for case in battery.cases:
        if not case.id:
            problems.append(ValidationError("?", "case without an id"))
            continue
        if case.id in seen:
            problems.append(ValidationError(case.id, "duplicate id"))
        seen.add(case.id)
        if not case.turns:
            problems.append(ValidationError(case.id, "case has no turns"))
        if case.id.startswith("M") != case.is_multi_turn:
            problems.append(
                ValidationError(case.id, "M-prefixed ids and multi-turn cases must coincide")
            )
        if case.minimum_capability_tier < 1 or case.minimum_capability_tier > 3:
            problems.append(ValidationError(case.id, "minimum_capability_tier must be 1..3"))
        for name in case.required_assertions:
            if name not in ASSERTION_NAMES:
                problems.append(
                    ValidationError(
                        case.id, f"unknown assertion {name!r} (allowed: {ASSERTION_NAMES})"
                    )
                )
        for key in case.scope_machine_fixture_keys:
            if key not in fixture_keys:
                problems.append(ValidationError(case.id, f"unknown scope fixture key {key!r}"))
        if case.rail and case.rail not in RAILS:
            problems.append(
                ValidationError(case.id, f"unknown rail {case.rail!r} (allowed: {RAILS})")
            )
        if battery.is_memory_battery and case.is_multi_turn and not case.rail:
            problems.append(ValidationError(case.id, "memory M-cases must declare a rail"))
        for flag in case.requires_flags:
            if not (flag.startswith("FEATURE_") and flag == flag.upper()):
                problems.append(
                    ValidationError(
                        case.id, f"requires_flags entry {flag!r} is not a FEATURE_* name"
                    )
                )
        for index, turn in enumerate(case.turns):
            where = f"turn {index}"
            has_inline = bool(turn.question.strip())
            has_ref = bool(turn.question_ref.strip())
            if has_inline == has_ref:
                problems.append(
                    ValidationError(case.id, f"{where}: exactly one of question/question_ref")
                )
            if battery.dataset == "solar" and has_inline:
                # The blinding fence: solar battery files never carry text.
                problems.append(
                    ValidationError(
                        case.id,
                        f"{where}: verbatim question text in a solar battery file "
                        f"(use question_ref into the private store)",
                    )
                )
            if turn.expected_intent and turn.expected_intent not in TASK_INTENTS:
                problems.append(
                    ValidationError(case.id, f"{where}: unknown intent {turn.expected_intent!r}")
                )
            for tier, behavior in turn.expected_behavior_by_tier:
                if tier < 1 or tier > 3:
                    problems.append(ValidationError(case.id, f"{where}: tier keys must be 1..3"))
                if behavior not in TIER_BEHAVIORS:
                    problems.append(
                        ValidationError(case.id, f"{where}: unknown behavior {behavior!r}")
                    )
            for key in turn.forbidden_entity_fixture_keys:
                if key not in fixture_keys:
                    problems.append(
                        ValidationError(case.id, f"{where}: unknown forbidden fixture key {key!r}")
                    )
            for key in turn.required_entity_fixture_keys:
                if key not in fixture_keys:
                    problems.append(
                        ValidationError(case.id, f"{where}: unknown required fixture key {key!r}")
                    )
                elif key in turn.forbidden_entity_fixture_keys:
                    problems.append(
                        ValidationError(case.id, f"{where}: {key!r} is both required and forbidden")
                    )
            if turn.expected_workflow and turn.expected_workflow not in KNOWN_WORKFLOWS:
                problems.append(
                    ValidationError(
                        case.id, f"{where}: unknown expected_workflow {turn.expected_workflow!r}"
                    )
                )
    if battery.repeat_tranche:
        for case_id in battery.repeat_tranche.case_ids:
            if case_id not in seen:
                problems.append(
                    ValidationError(case_id, "repeat-tranche case is not in this battery")
                )
        if battery.repeat_tranche.repetitions < 1:
            problems.append(ValidationError("?", "repeat_tranche.repetitions must be >= 1"))
    return problems


def planned_request_count(batteries: list[BatteryFile], include_tranche: bool = False) -> int:
    """Requests one full pass will submit — computed, never hardcoded.

    One request per turn plus one per scripted clarification follow-up;
    the preregistered tranche adds repetitions x the case's turn count.
    This is the §13.6-vs-Q47 arithmetic resolution: preflight reports THIS
    number and reserves the agreed 300.
    """
    total = 0
    for battery in batteries:
        for case in battery.cases:
            total += len(case.turns)
            total += sum(1 for turn in case.turns if turn.clarification_followup_ref)
        if include_tranche and battery.repeat_tranche:
            for case_id in battery.repeat_tranche.case_ids:
                case = battery.case(case_id)
                if case is not None:
                    total += battery.repeat_tranche.repetitions * len(case.turns)
    return total


# --------------------------------------------------------------------------- #
# Private-store loading                                                        #
# --------------------------------------------------------------------------- #
def assert_outside_repo(path: Path) -> Path:
    """Refuse a private-store path inside the repository tree (Q48)."""
    resolved = path.resolve()
    repo_root = Path(__file__).resolve()
    for parent in repo_root.parents:
        if (parent / ".git").exists():
            if resolved == parent or parent in resolved.parents:
                raise ValueError(
                    f"{path} is inside the repository; private gold and journals "
                    f"must live outside it (Q48)"
                )
            break
    return resolved


def load_questions(gold_dir: Path) -> dict[str, str]:
    """The private question store: ref -> verbatim question text."""
    raw = _load_yaml(gold_dir / "questions.yaml") or {}
    return {str(key): str(value) for key, value in (raw.get("questions") or {}).items()}


def load_gold(gold_dir: Path, case_id: str) -> GoldAtoms | None:
    """Load one case's human-authored gold atoms; None when not yet authored."""
    path = gold_dir / "gold" / f"{case_id}.yaml"
    if not path.exists():
        return None
    raw = _load_yaml(path) or {}
    answerability = str(raw.get("answerability") or "")
    if answerability not in ANSWERABILITY:
        raise ValueError(f"{case_id}: gold answerability must be one of {ANSWERABILITY}")
    return GoldAtoms(
        case_id=case_id,
        answerability=answerability,
        minimum_tier=int(raw.get("minimum_tier", 1)),
        accepted_source_ids=tuple(raw.get("accepted_source_ids") or ()),
        accepted_revisions=tuple(raw.get("accepted_revisions") or ()),
        required_claims=tuple(raw.get("required_claims") or ()),
        forbidden_claims=tuple(raw.get("forbidden_claims") or ()),
        calculations=tuple(
            GoldCalculation(
                name=str(entry.get("name") or ""),
                value=str(entry.get("value") or ""),
                operands=tuple(entry.get("operands") or ()),
                inclusion_rule=str(entry.get("inclusion_rule") or ""),
                date_field=str(entry.get("date_field") or ""),
                timezone=str(entry.get("timezone") or ""),
                tolerance=str(entry.get("tolerance") or ""),
            )
            for entry in raw.get("calculations") or []
        ),
        required_facets=tuple(raw.get("required_facets") or ()),
        clarification=str(raw.get("clarification") or ""),
        gold_revision=str(raw.get("gold_revision") or ""),
    )


__all__ = [
    "ANSWERABILITY",
    "ASSERTION_NAMES",
    "BATTERY_DIR",
    "DATASETS",
    "KNOWN_WORKFLOWS",
    "RAILS",
    "TASK_INTENTS",
    "TIER_BEHAVIORS",
    "BatteryFile",
    "GoldAtoms",
    "GoldCalculation",
    "RepeatTranche",
    "ScenarioCase",
    "ScenarioService",
    "ScenarioTurn",
    "assert_outside_repo",
    "load_battery",
    "load_fixture_keys",
    "load_gold",
    "load_questions",
    "planned_request_count",
    "validate_battery",
]
