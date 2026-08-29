"""Layered battery scoring (S14, §13.6) — deterministic first, judge last.

Eight ordered layers. Layers 1-6 are DETERMINISTIC, scored from wire
payloads and persisted turn artifacts; the LLM judge may score only layer
7 and the prose half of layer 8. The precedence is structural: any
deterministic failure returns before the judge callable is ever invoked,
so no code path exists by which a semantic verdict overrides a scope,
citation, coverage, or safety failure — and a failed turn spends no judge
budget.

Pure Python: the runner (``run_battery``) assembles :class:`TurnArtifacts`
from HTTP responses and the persisted thread detail; everything here is
side-effect-free and unit-testable without a network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .scenarios import GoldAtoms, ScenarioCase, ScenarioTurn

LAYERS: tuple[tuple[int, str], ...] = (
    (1, "service_completion"),
    (2, "intent_and_rail"),
    (3, "scope_purity"),
    (4, "coverage_validity"),
    (5, "source_citation_entity_validity"),
    (6, "boundary_assertions"),
    (7, "semantic_correctness"),
    (8, "uncertainty_discipline"),
)

#: Words allowed in a capability-boundary / refusal response (Q86 gate).
BOUNDARY_WORD_CAP = 200

#: Diagnostic workflows an analysis-intent turn must never route to
#: (the S0 misroute gate: zero diagnostic routing).
DIAGNOSTIC_WORKFLOWS = frozenset({"wf1", "wf1_diagnostics"})

#: Analysis intents whose enforced answers ride the evidence rail.
ANALYSIS_INTENTS = frozenset({
    "record_retrieval",
    "manual_fact",
    "source_inventory",
    "fleet_aggregate",
    "trend_analysis",
    "manual_wo_comparison",
})


@dataclass(frozen=True)
class TurnArtifacts:
    """Everything one submitted turn left behind, as the runner saw it."""

    http_status: int
    response_body: dict[str, Any] = field(default_factory=dict)
    thread_id: str = ""
    message_text: str = ""
    evidence_analysis: dict[str, Any] | None = None
    entities: list[dict[str, Any]] | None = None
    response_state: str | None = None
    #: The scope version returned by the runner's pre-turn PUT (or GET).
    expected_scope_version: int | None = None
    #: The thread's scope version AFTER the turn (post-turn GET).
    post_scope_version: int | None = None
    #: Route facts when the deployment exposes them (task_intent, mode);
    #: absent -> the fine-grained intent assertion is honestly skipped.
    route: dict[str, Any] | None = None
    #: Proposal rows created during the case (no_governed_effect): 0 = none.
    proposal_ids_delta: int = 0
    #: Persisted turn metadata (capability_tier, model_versions, ...).
    turn_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Resolution:
    """Run-time resolution of the fixture-key manifest.

    ``scope_ids``: entity ids (as strings) the case's scope covers.
    ``forbidden``: fixture key -> (ids, markers) to scan for.
    """

    scope_ids: frozenset[str] = frozenset()
    forbidden: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = field(default_factory=dict)


@dataclass(frozen=True)
class LayerResult:
    layer: int
    name: str
    status: str  # pass | fail | partial | skip | not_scored
    detail: str = ""
    facet_credit: tuple[tuple[str, bool], ...] = ()


@dataclass(frozen=True)
class TurnScore:
    case_id: str
    turn_index: int
    layers: tuple[LayerResult, ...]
    deterministic_pass: bool
    substantive: bool
    outcome: str  # pass | fail | partial | boundary_pass

    def layer(self, number: int) -> LayerResult:
        for result in self.layers:
            if result.layer == number:
                return result
        raise KeyError(number)


#: Judge seam: called ONLY after layers 1-6 pass, ONLY with gold present.
#: Returns {"required_claims_present": {claim: bool}, "forbidden_claims_absent": bool,
#:          "calculations_within_tolerance": bool, "no_overclaim": bool} — the
#: scorer folds it and can only downgrade, never upgrade.
JudgeCall = Callable[[str, GoldAtoms, str], dict[str, Any]]


def _word_count(text: str) -> int:
    return len(text.split())


def _claims(artifacts: TurnArtifacts) -> list[dict[str, Any]]:
    if not artifacts.evidence_analysis:
        return []
    return list(artifacts.evidence_analysis.get("claims") or [])


def _citations(artifacts: TurnArtifacts) -> list[dict[str, Any]]:
    if not artifacts.evidence_analysis:
        return []
    return list(artifacts.evidence_analysis.get("citations") or [])


def _coverage(artifacts: TurnArtifacts) -> dict[str, Any] | None:
    if not artifacts.evidence_analysis:
        return None
    return artifacts.evidence_analysis.get("coverage")


def _is_boundary_shaped(artifacts: TurnArtifacts) -> tuple[bool, str]:
    """Whether the turn looks like a typed capability boundary (§13.6)."""
    if artifacts.evidence_analysis is not None:
        return False, "evidence attachment present"
    if _word_count(artifacts.message_text) > BOUNDARY_WORD_CAP:
        return False, f"boundary response exceeds {BOUNDARY_WORD_CAP} words"
    if artifacts.entities:
        return False, "boundary response carries entity chips"
    return True, ""


def _forbidden_hits(artifacts: TurnArtifacts, resolution: Resolution) -> list[str]:
    """Id- and marker-level scan of everything the turn surfaced."""
    hits: list[str] = []
    surfaces: list[str] = [artifacts.message_text or ""]
    for citation in _citations(artifacts):
        surfaces.append(str(citation.get("source_id") or ""))
        surfaces.append(str(citation.get("source_title") or ""))
    for entity in artifacts.entities or []:
        surfaces.append(str(entity.get("id") or ""))
        surfaces.append(str(entity.get("label") or entity.get("name") or ""))
    haystack = "\n".join(surfaces)
    haystack_lower = haystack.lower()
    for key, (ids, markers) in resolution.forbidden.items():
        for entity_id in ids:
            if entity_id and entity_id in haystack:
                hits.append(f"{key}:{entity_id}")
        for marker in markers:
            if marker and marker.lower() in haystack_lower:
                hits.append(f"{key}:{marker}")
    return hits


# --------------------------------------------------------------------------- #
# Layers 1-6 (deterministic)                                                   #
# --------------------------------------------------------------------------- #
def _layer_service(case: ScenarioCase, artifacts: TurnArtifacts) -> LayerResult:
    allowed = case.service.allowed_statuses
    if artifacts.http_status in allowed:
        return LayerResult(1, "service_completion", "pass")
    return LayerResult(
        1,
        "service_completion",
        "fail",
        f"status {artifacts.http_status} not in {allowed}",
    )


def _layer_intent(
    turn: ScenarioTurn, artifacts: TurnArtifacts, expected_behavior: str
) -> LayerResult:
    workflow = str(artifacts.response_body.get("workflow_used") or "")
    if turn.expected_intent in ANALYSIS_INTENTS and workflow in DIAGNOSTIC_WORKFLOWS:
        return LayerResult(2, "intent_and_rail", "fail", f"analysis intent routed to {workflow}")
    if expected_behavior == "capability_boundary":
        boundary, why = _is_boundary_shaped(artifacts)
        if not boundary:
            return LayerResult(2, "intent_and_rail", "fail", f"expected boundary: {why}")
        return LayerResult(2, "intent_and_rail", "pass", "capability boundary")
    if turn.expected_intent:
        route = artifacts.route or {}
        routed_intent = str(route.get("task_intent") or "")
        if not routed_intent:
            return LayerResult(
                2,
                "intent_and_rail",
                "skip",
                "task_intent not exposed by this deployment; workflow gate only",
            )
        if routed_intent != turn.expected_intent:
            return LayerResult(
                2,
                "intent_and_rail",
                "fail",
                f"intent {routed_intent!r} != expected {turn.expected_intent!r}",
            )
    return LayerResult(2, "intent_and_rail", "pass")


def _layer_scope(artifacts: TurnArtifacts, resolution: Resolution) -> LayerResult:
    problems: list[str] = []
    attachment_scope = (artifacts.evidence_analysis or {}).get("active_scope") or {}
    if (
        artifacts.expected_scope_version is not None
        and attachment_scope.get("version") is not None
        and int(attachment_scope["version"]) != int(artifacts.expected_scope_version)
    ):
        problems.append(
            f"answer stamped scope v{attachment_scope['version']} != bound v{artifacts.expected_scope_version}"
        )
    if resolution.scope_ids:
        for entity in artifacts.entities or []:
            entity_id = str(entity.get("id") or "")
            if entity_id and entity_id not in resolution.scope_ids:
                problems.append(f"chip outside scope: {entity_id}")
    hits = _forbidden_hits(artifacts, resolution)
    problems.extend(f"forbidden entity surfaced: {hit}" for hit in hits)
    if problems:
        return LayerResult(3, "scope_purity", "fail", "; ".join(problems))
    return LayerResult(3, "scope_purity", "pass")


def _layer_coverage(turn: ScenarioTurn, artifacts: TurnArtifacts) -> LayerResult:
    coverage = _coverage(artifacts)
    if turn.complete_population_required:
        if coverage is None:
            return LayerResult(
                4, "coverage_validity", "fail", "complete population required but no coverage stamp"
            )
        # A validated partial NEVER satisfies a complete-population gate.
        if artifacts.response_state == "partial" or not coverage.get("complete_population"):
            return LayerResult(
                4,
                "coverage_validity",
                "fail",
                "complete_population not satisfied "
                f"(state={artifacts.response_state}, coverage={coverage.get('complete_population')})",
            )
    if coverage is not None:
        population = coverage.get("population_count")
        returned = coverage.get("returned_count")
        if population is not None and returned is not None and int(population) < int(returned):
            return LayerResult(
                4, "coverage_validity", "fail", f"population {population} < returned {returned}"
            )
    return LayerResult(4, "coverage_validity", "pass")


def _layer_sources(artifacts: TurnArtifacts, gold: GoldAtoms | None) -> LayerResult:
    problems: list[str] = []
    citations = _citations(artifacts)
    ordinals = {citation.get("ordinal") for citation in citations}
    for claim in _claims(artifacts):
        for ordinal in claim.get("citation_ordinals") or []:
            if ordinal not in ordinals:
                problems.append(f"claim {claim.get('claim_id')} cites missing ordinal {ordinal}")
    for citation in citations:
        if citation.get("available") is False:
            continue
        if not citation.get("source_id"):
            problems.append(f"citation [{citation.get('ordinal')}] has no source_id")
    if gold and gold.accepted_revisions:
        for citation in citations:
            revision = citation.get("source_revision")
            if revision and str(revision) not in gold.accepted_revisions:
                problems.append(
                    f"citation [{citation.get('ordinal')}] cites revision {revision!r} "
                    f"outside gold {gold.accepted_revisions}"
                )
    claim_refs = {
        str(ref) for claim in _claims(artifacts) for ref in claim.get("entity_refs") or []
    }
    if claim_refs:
        for entity in artifacts.entities or []:
            ref = str(entity.get("ref") or entity.get("id") or "")
            if ref and ref not in claim_refs:
                problems.append(f"chip {ref} outside validated claim evidence")
    if problems:
        return LayerResult(5, "source_citation_entity_validity", "fail", "; ".join(problems))
    return LayerResult(5, "source_citation_entity_validity", "pass")


def _layer_boundary_assertions(
    case: ScenarioCase, artifacts: TurnArtifacts, expected_behavior: str
) -> LayerResult:
    problems: list[str] = []
    for name in case.required_assertions:
        if name == "scope_persisted":
            if (
                artifacts.expected_scope_version is not None
                and artifacts.post_scope_version is not None
                and artifacts.post_scope_version != artifacts.expected_scope_version
            ):
                problems.append(
                    f"scope version moved {artifacts.expected_scope_version} -> "
                    f"{artifacts.post_scope_version} without a user edit"
                )
        elif name == "evidence_entails_claims":
            if expected_behavior in ("capability_boundary", "refuse", "abstain"):
                continue
            for claim in _claims(artifacts):
                if claim.get("claim_role") and claim.get("claim_role") != "answer":
                    continue
                if not claim.get("citation_ordinals"):
                    problems.append(f"claim {claim.get('claim_id')} has no citations")
                if not claim.get("evidence_classification"):
                    problems.append(f"claim {claim.get('claim_id')} has no classification")
        elif name == "no_governed_effect" and artifacts.proposal_ids_delta:
            problems.append(
                f"{artifacts.proposal_ids_delta} proposal row(s) appeared during a read-only case"
            )
    if expected_behavior == "refuse" and _word_count(artifacts.message_text) > BOUNDARY_WORD_CAP:
        problems.append(f"refusal exceeds {BOUNDARY_WORD_CAP} words (Q86 gate)")
    if problems:
        return LayerResult(6, "boundary_assertions", "fail", "; ".join(problems))
    return LayerResult(6, "boundary_assertions", "pass")


# --------------------------------------------------------------------------- #
# Layers 7-8                                                                   #
# --------------------------------------------------------------------------- #
def _layer_semantic(
    turn: ScenarioTurn,
    artifacts: TurnArtifacts,
    gold: GoldAtoms | None,
    judge_verdict: dict[str, Any] | None,
) -> LayerResult:
    if gold is None:
        return LayerResult(7, "semantic_correctness", "not_scored", "no gold authored")
    if judge_verdict is None:
        return LayerResult(
            7, "semantic_correctness", "not_scored", "judge uncalibrated or unavailable"
        )
    required = dict(judge_verdict.get("required_claims_present") or {})
    facet_credit = tuple(
        (facet, bool(required.get(facet))) for facet in gold.required_facets
    ) or tuple((claim, bool(present)) for claim, present in required.items())
    missing = [claim for claim, present in required.items() if not present]
    problems: list[str] = []
    if missing:
        problems.append(f"missing required claims: {missing}")
    if judge_verdict.get("forbidden_claims_absent") is False:
        problems.append("a forbidden claim is present")
    if gold.calculations and judge_verdict.get("calculations_within_tolerance") is False:
        problems.append("a calculation is outside gold tolerance")
    if not problems:
        return LayerResult(7, "semantic_correctness", "pass", facet_credit=facet_credit)
    if required and any(required.values()) and "a forbidden claim is present" not in problems:
        return LayerResult(
            7, "semantic_correctness", "partial", "; ".join(problems), facet_credit=facet_credit
        )
    return LayerResult(
        7, "semantic_correctness", "fail", "; ".join(problems), facet_credit=facet_credit
    )


def _layer_uncertainty(
    artifacts: TurnArtifacts, judge_verdict: dict[str, Any] | None
) -> LayerResult:
    # Deterministic half: state honesty.
    if artifacts.response_state == "partial":
        reasons = (artifacts.evidence_analysis or {}).get("incomplete_reasons") or []
        if not reasons:
            return LayerResult(
                8,
                "uncertainty_discipline",
                "fail",
                "partial state without typed incomplete_reasons",
            )
    # Prose half: judged only when a verdict exists.
    if judge_verdict is not None and judge_verdict.get("no_overclaim") is False:
        return LayerResult(
            8, "uncertainty_discipline", "fail", "answer overclaims beyond stated coverage"
        )
    if judge_verdict is None:
        return LayerResult(
            8, "uncertainty_discipline", "pass", "deterministic half only (no judge)"
        )
    return LayerResult(8, "uncertainty_discipline", "pass")


# --------------------------------------------------------------------------- #
# The fold                                                                     #
# --------------------------------------------------------------------------- #
def score_turn(
    *,
    case: ScenarioCase,
    turn: ScenarioTurn,
    turn_index: int,
    artifacts: TurnArtifacts,
    tier: int,
    resolution: Resolution | None = None,
    gold: GoldAtoms | None = None,
    judge: JudgeCall | None = None,
    question_text: str = "",
) -> TurnScore:
    """Score one turn through the eight layers, deterministic-first."""
    resolution = resolution or Resolution()
    expected_behavior = turn.behavior_for_tier(tier)

    deterministic = [
        _layer_service(case, artifacts),
        _layer_intent(turn, artifacts, expected_behavior),
        _layer_scope(artifacts, resolution),
        _layer_coverage(turn, artifacts),
        _layer_sources(artifacts, gold),
        _layer_boundary_assertions(case, artifacts, expected_behavior),
    ]
    failed = [result for result in deterministic if result.status == "fail"]
    if failed:
        # Structural precedence: the judge is never invoked on a
        # deterministic failure (and spends no request budget).
        layers = (
            *deterministic,
            LayerResult(7, "semantic_correctness", "skip", "deterministic failure"),
            LayerResult(8, "uncertainty_discipline", "skip", "deterministic failure"),
        )
        return TurnScore(
            case_id=case.id,
            turn_index=turn_index,
            layers=layers,
            deterministic_pass=False,
            substantive=False,
            outcome="fail",
        )

    judge_verdict: dict[str, Any] | None = None
    if judge is not None and gold is not None:
        judge_verdict = judge(question_text, gold, artifacts.message_text)

    semantic = _layer_semantic(turn, artifacts, gold, judge_verdict)
    uncertainty = _layer_uncertainty(artifacts, judge_verdict)
    layers = (*deterministic, semantic, uncertainty)

    if expected_behavior == "capability_boundary":
        # A tier-disabled pass is never substantive completion (§13.6).
        return TurnScore(
            case_id=case.id,
            turn_index=turn_index,
            layers=layers,
            deterministic_pass=True,
            substantive=False,
            outcome="boundary_pass",
        )

    if uncertainty.status == "fail" or semantic.status == "fail":
        outcome = "fail"
    elif semantic.status == "partial" or artifacts.response_state == "partial":
        outcome = "partial"
    else:
        outcome = "pass"
    return TurnScore(
        case_id=case.id,
        turn_index=turn_index,
        layers=layers,
        deterministic_pass=True,
        substantive=outcome in ("pass", "partial"),
        outcome=outcome,
    )


def resolution_from_manifest(
    manifest: dict[str, dict[str, Any]],
    case: ScenarioCase,
    turn: ScenarioTurn,
    resolved_ids: dict[str, tuple[str, ...]] | None = None,
) -> Resolution:
    """Build a :class:`Resolution` from the fixture-key manifest.

    ``resolved_ids`` maps fixture keys to live entity ids (the runner's
    preflight resolution); marker keys contribute their literal markers.
    """
    resolved_ids = resolved_ids or {}
    scope_ids: set[str] = set()
    for key in case.scope_machine_fixture_keys:
        scope_ids.update(resolved_ids.get(key, ()))
    forbidden: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for key in turn.forbidden_entity_fixture_keys:
        descriptor = manifest.get(key, {})
        markers = tuple(str(marker) for marker in descriptor.get("markers") or ())
        forbidden[key] = (tuple(resolved_ids.get(key, ())), markers)
    return Resolution(scope_ids=frozenset(scope_ids), forbidden=forbidden)


__all__ = [
    "ANALYSIS_INTENTS",
    "BOUNDARY_WORD_CAP",
    "DIAGNOSTIC_WORKFLOWS",
    "LAYERS",
    "JudgeCall",
    "LayerResult",
    "Resolution",
    "TurnArtifacts",
    "TurnScore",
    "resolution_from_manifest",
    "score_turn",
]
