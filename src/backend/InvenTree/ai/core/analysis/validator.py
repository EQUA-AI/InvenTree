"""The deterministic final-answer validator for evidence analysis (S10, §8.6).

Fourteen checks, C01..C14, all pure functions over the turn's own artifacts:
claims, the typed evidence store, the rendered answer, the capture-ledger
join keys, the bound scope context, the candidate entity chips, and the
events already emitted. No model judges anything here — an optional
semantic audit may *flag*, but source membership, scope, coverage, and
calculation validity are decided by these checks alone, and nothing
overrides a deterministic failure.

Outcomes: ``pass`` renders; ``downgrade`` drops the offending claims (the
executor re-renders survivors plus a deterministic limitation and re-runs
the closure checks once); ``abstain`` withholds the conclusion; and
``fail_closed`` returns the safe template — always for safety-audit
outages, authorization failures, and pre-validation wire disclosure.

``shadow_scan_legacy`` is the soak half: the cheap prose-mode subset run
over legacy wf8 answers on ANALYSIS-intent turns, logged and persisted
content-free so the enforce flip can be judged on real false-positive
rates (the S27 grounding-soak precedent).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ai.core.analysis.wire import ANALYSIS_PROGRESS_STAGES
from ai.core.grounding import ungrounded_identifiers

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ai.core.analysis.evidence import EvidenceStore
    from ai.core.analysis.renderer import RenderedAnswer
    from ai.core.analysis.schemas import AnalysisClaim, AnalysisFacet
    from ai.core.analysis.scope_context import TurnScopeContext

#: C12 output bounds (Tier-1). Length is characters of rendered text.
MAX_DETAILED_CHARS = 4_000
MAX_CLAIMS = 32

#: Digit-bearing runs: every visible number, date, duration, or ordinal.
_DIGIT_RUN_RE = re.compile(r"\S*\d\S*")
#: Punctuation that may legitimately wrap an inserted value in prose.
_WRAP_CHARS = "()\"'.,;:!?"

#: Conservative safety-sensitivity markers (C11). Presence requires an
#: affirmative safety audit; the Tier-1 template catalog can't author
#: these, so a hit means model paraphrase or poisoned input.
_SAFETY_MARKER_RE = re.compile(
    r"\b(?:isolat\w+|lock[- ]?out|lockout|tag[- ]?out|stored[- ]energy|"
    r"interlock\w*|de-?energi[sz]\w+|bypass\w*|live[- ]work|arc[- ]flash)\b",
    re.IGNORECASE,
)

#: Categorical absence phrasings the contradiction scan looks for in
#: legacy prose (shadow mode only; v2 absence claims are typed).
_ABSENCE_PHRASE_RE = re.compile(
    r"\b(?:no records? exist|there are no|never (?:failed|occurred|happened)|"
    r"no \w+ (?:were|was|have been|has been) (?:found|recorded|logged))\b",
    re.IGNORECASE,
)

#: The only events that may precede validation on the analysis rail (C14).
_ALLOWED_PRE_VALIDATION_TYPES = frozenset({"RUN_STARTED", "WORKFLOW_STARTED", "RUN_CANCELLED"})
_ALLOWED_PROGRESS_KEYS = frozenset({"type", "timestamp", "threadId", "runId", "kind", "stage"})


class CheckOutcome(StrEnum):
    """Severity-ordered validation outcomes (§8.6)."""

    PASS = "pass"
    DOWNGRADE = "downgrade"
    ABSTAIN = "abstain"
    FAIL_CLOSED = "fail_closed"


_SEVERITY = {
    CheckOutcome.PASS: 0,
    CheckOutcome.DOWNGRADE: 1,
    CheckOutcome.ABSTAIN: 2,
    CheckOutcome.FAIL_CLOSED: 3,
}


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One check's finding. ``code`` is content-free by construction."""

    check: str
    outcome: CheckOutcome
    code: str
    claim_ids: tuple[str, ...] = ()


@dataclass
class ValidationVerdict:
    """The aggregate: worst outcome, per-check results, and the drops."""

    outcome: CheckOutcome
    results: list[CheckResult] = field(default_factory=list)
    dropped_claim_ids: tuple[str, ...] = ()
    allowed_entities: list[dict[str, Any]] = field(default_factory=list)

    def codes(self) -> tuple[str, ...]:
        return tuple(
            result.code for result in self.results if result.outcome is not CheckOutcome.PASS
        )


def _normalized_candidates(token: str) -> set[str]:
    stripped = token.strip(_WRAP_CHARS)
    return {token, stripped}


def _digit_runs(text: str) -> list[str]:
    return _DIGIT_RUN_RE.findall(text or "")


def _claims_for_text(
    offender: str, rendered: RenderedAnswer, claims: Sequence[AnalysisClaim]
) -> tuple[str, ...]:
    """Map an offending token to the claims whose segments contain it."""
    hits = tuple(segment.claim_id for segment in rendered.segments if offender in segment.text)
    return hits or tuple(claim.claim_id for claim in claims)


# --- the fourteen checks --------------------------------------------------


def check_scope(
    claims: Sequence[AnalysisClaim],
    store: EvidenceStore,
    scope: TurnScopeContext | None,
) -> list[CheckResult]:
    """C01: every referenced machine belongs to the turn's explicit scope."""
    if scope is None or not scope.explicit:
        return [CheckResult("C01", CheckOutcome.PASS, "scope_not_explicit")]
    allowed = set(scope.machine_ids or ())
    results: list[CheckResult] = []
    for claim in claims:
        out_of_scope = False
        for ref in claim.fact_refs:
            fact = store.facts.get(ref)
            if fact is not None and fact.machine_id is not None and fact.machine_id not in allowed:
                out_of_scope = True
        for entity_ref in claim.entity_refs:
            kind, _, raw_pk = entity_ref.partition(":")
            if kind == "machine" and raw_pk.isdigit() and int(raw_pk) not in allowed:
                out_of_scope = True
        if out_of_scope:
            results.append(
                CheckResult(
                    "C01", CheckOutcome.DOWNGRADE, "out_of_scope_reference", (claim.claim_id,)
                )
            )
    return results or [CheckResult("C01", CheckOutcome.PASS, "in_scope")]


def check_ledger(
    claims: Sequence[AnalysisClaim],
    store: EvidenceStore,
    *,
    ledger_retrieval_ids: frozenset[str],
    ledger_chunk_ids: frozenset[str] | None,
) -> list[CheckResult]:
    """C02: every reference resolves to this turn's retrieval gateway."""
    known_retrievals = store.retrieval_ids() | ledger_retrieval_ids
    results: list[CheckResult] = []
    for claim in claims:
        codes: list[str] = []
        for ref in claim.fact_refs:
            fact = store.facts.get(ref)
            if fact is None:
                codes.append("unresolved_fact_ref")
                continue
            if fact.retrieval_id and fact.retrieval_id not in known_retrievals:
                codes.append("unledgered_retrieval")
            if (
                fact.kind == "manual_passage"
                and ledger_chunk_ids is not None
                and str(fact.locator.get("chunk") or "") not in ledger_chunk_ids
            ):
                codes.append("unledgered_chunk")
        for ref in claim.calculation_output_refs:
            if ref not in store.calculations:
                codes.append("unresolved_calculation_ref")
        for ref in claim.evidence_refs:
            if ref.startswith("set_") and ref not in store.evidence_sets:
                codes.append("unresolved_evidence_set")
            elif ref.startswith("ret_") and ref not in known_retrievals:
                codes.append("unledgered_retrieval")
        for code in dict.fromkeys(codes):
            results.append(CheckResult("C02", CheckOutcome.DOWNGRADE, code, (claim.claim_id,)))
    return results or [CheckResult("C02", CheckOutcome.PASS, "ledgered")]


def check_typed_basis(claims: Sequence[AnalysisClaim], store: EvidenceStore) -> list[CheckResult]:
    """C03: runtime half — refs resolve to the right kinds, inference is labeled."""
    from ai.core.analysis.renderer import RENDER_TEMPLATES

    results: list[CheckResult] = []
    for claim in claims:
        template = RENDER_TEMPLATES.get(claim.render_template)
        if template is None:
            results.append(
                CheckResult("C03", CheckOutcome.DOWNGRADE, "unknown_template", (claim.claim_id,))
            )
            continue
        classification = str(claim.evidence_classification)
        if classification == "documented" and not any(
            ref in store.facts for ref in claim.fact_refs
        ):
            results.append(
                CheckResult(
                    "C03", CheckOutcome.DOWNGRADE, "documented_without_fact", (claim.claim_id,)
                )
            )
        if classification == "calculated" and not any(
            ref in store.calculations for ref in claim.calculation_output_refs
        ):
            results.append(
                CheckResult(
                    "C03",
                    CheckOutcome.DOWNGRADE,
                    "calculated_without_calculation",
                    (claim.claim_id,),
                )
            )
        if classification == "inferred" and not template.calibrated:
            results.append(
                CheckResult(
                    "C03", CheckOutcome.DOWNGRADE, "uncalibrated_inference", (claim.claim_id,)
                )
            )
    return results or [CheckResult("C03", CheckOutcome.PASS, "typed_basis")]


def check_identifier_closure(
    claims: Sequence[AnalysisClaim], rendered: RenderedAnswer
) -> list[CheckResult]:
    """C04: every code-shaped identifier in visible text was server-inserted."""
    text = " ".join((
        rendered.detailed_response,
        rendered.spoken_summary,
        rendered.reasoning_summary,
    ))
    offenders = ungrounded_identifiers(text, rendered.inserted_values)
    return [
        CheckResult(
            "C04",
            CheckOutcome.DOWNGRADE,
            "unclosed_identifier",
            _claims_for_text(offender, rendered, claims),
        )
        for offender in offenders
    ] or [CheckResult("C04", CheckOutcome.PASS, "identifier_closure")]


def check_value_closure(
    claims: Sequence[AnalysisClaim], rendered: RenderedAnswer
) -> list[CheckResult]:
    """C05: every digit-bearing token in visible text was server-inserted."""
    inserted = rendered.inserted_values
    results: list[CheckResult] = []
    for source_text in (
        rendered.detailed_response,
        rendered.spoken_summary,
        rendered.reasoning_summary,
    ):
        for run in _digit_runs(source_text):
            if _normalized_candidates(run) & inserted:
                continue
            results.append(
                CheckResult(
                    "C05",
                    CheckOutcome.DOWNGRADE,
                    "unclosed_value",
                    _claims_for_text(run, rendered, claims),
                )
            )
    return results or [CheckResult("C05", CheckOutcome.PASS, "value_closure")]


def check_population(claims: Sequence[AnalysisClaim], store: EvidenceStore) -> list[CheckResult]:
    """C06: population-demanding templates cite complete populations only."""
    from ai.core.analysis.renderer import RENDER_TEMPLATES

    results: list[CheckResult] = []
    for claim in claims:
        template = RENDER_TEMPLATES.get(claim.render_template)
        if template is None or not template.requires_complete_population:
            continue
        complete = False
        for ref in claim.calculation_output_refs:
            calculation = store.calculations.get(ref)
            if calculation is not None and calculation.complete_population:
                complete = True
        for ref in claim.evidence_refs:
            pending = store.evidence_sets.get(ref)
            if pending is not None and pending.complete_population:
                complete = True
        for ref in claim.fact_refs:
            fact = store.facts.get(ref)
            # `dataset_profile` (S7) carries the same honest counts a
            # coverage fact does — both may vouch for completeness.
            if (
                fact is not None
                and fact.kind in ("coverage", "dataset_profile")
                and fact.rendered_values().get("complete_population") == "yes"
            ):
                complete = True
        if not complete:
            results.append(
                CheckResult(
                    "C06", CheckOutcome.DOWNGRADE, "incomplete_population", (claim.claim_id,)
                )
            )
    return results or [CheckResult("C06", CheckOutcome.PASS, "population")]


def check_applicability(claims: Sequence[AnalysisClaim], store: EvidenceStore) -> list[CheckResult]:
    """C07: procedural/manual claims require controlled current evidence."""
    from ai.core.analysis.renderer import RENDER_TEMPLATES

    results: list[CheckResult] = []
    for claim in claims:
        template = RENDER_TEMPLATES.get(claim.render_template)
        if template is None or not template.requires_controlled_source:
            continue
        if not any(store.facts[ref].controlled for ref in claim.fact_refs if ref in store.facts):
            results.append(
                CheckResult("C07", CheckOutcome.DOWNGRADE, "uncontrolled_source", (claim.claim_id,))
            )
    return results or [CheckResult("C07", CheckOutcome.PASS, "applicability")]


def check_facets(
    facets: Sequence[AnalysisFacet],
    claims: Sequence[AnalysisClaim],
    dropped: frozenset[str],
) -> list[CheckResult]:
    """C08: every facet resolved; answered facets keep a surviving claim."""
    surviving = {claim.claim_id for claim in claims} - dropped
    results: list[CheckResult] = []
    for facet in facets:
        if str(facet.status) == "answered" and not (set(facet.claim_ids) & surviving):
            results.append(CheckResult("C08", CheckOutcome.ABSTAIN, "facet_unanswered"))
    return results or [CheckResult("C08", CheckOutcome.PASS, "facets")]


def check_contradiction(claims: Sequence[AnalysisClaim], store: EvidenceStore) -> list[CheckResult]:
    """C09: categorical absence/count claims agree with retrieval facts."""
    from ai.core.analysis.renderer import RENDER_TEMPLATES

    results: list[CheckResult] = []
    for claim in claims:
        template = RENDER_TEMPLATES.get(claim.render_template)
        if template is None or template.categorical_kind is None:
            continue
        referenced_counts = [
            calculation.rendered_values().get("count")
            for ref in claim.calculation_output_refs
            if (calculation := store.calculations.get(ref)) is not None
        ]
        if template.categorical_kind == "absence" and any(
            count not in (None, "0") for count in referenced_counts
        ):
            results.append(
                CheckResult(
                    "C09",
                    CheckOutcome.DOWNGRADE,
                    "absence_contradicts_counts",
                    (claim.claim_id,),
                )
            )
    return results or [CheckResult("C09", CheckOutcome.PASS, "contradiction")]


def check_entity_manifest(
    entities: Sequence[Mapping[str, Any]],
    claims: Sequence[AnalysisClaim],
    scope: TurnScopeContext | None,
    dropped: frozenset[str],
) -> tuple[list[CheckResult], list[dict[str, Any]]]:
    """C10: chips ⊆ surviving claims' entity refs (+ explicit-scope machines)."""
    allowed_refs: set[str] = set()
    for claim in claims:
        if claim.claim_id in dropped:
            continue
        allowed_refs.update(claim.entity_refs)
    if scope is not None and scope.explicit:
        allowed_refs.update(f"machine:{pk}" for pk in scope.machine_ids or ())
    results: list[CheckResult] = []
    kept: list[dict[str, Any]] = []
    for entity in entities:
        ref = str(entity.get("ref") or "")
        if ref in allowed_refs:
            kept.append(dict(entity))
        else:
            results.append(CheckResult("C10", CheckOutcome.DOWNGRADE, "uncited_entity"))
    return (
        results or [CheckResult("C10", CheckOutcome.PASS, "entity_manifest")],
        kept,
    )


def check_safety(
    rendered: RenderedAnswer,
    safety_audit: Callable[[], bool] | None,
) -> list[CheckResult]:
    """C11: safety-sensitive text needs an affirmative audit; outage fails closed."""
    text = " ".join((
        rendered.detailed_response,
        rendered.spoken_summary,
    ))
    if not _SAFETY_MARKER_RE.search(text):
        return [CheckResult("C11", CheckOutcome.PASS, "no_safety_content")]
    if safety_audit is None:
        return [CheckResult("C11", CheckOutcome.FAIL_CLOSED, "safety_audit_unavailable")]
    try:
        verdict = bool(safety_audit())
    except Exception:
        return [CheckResult("C11", CheckOutcome.FAIL_CLOSED, "safety_audit_unavailable")]
    if not verdict:
        return [CheckResult("C11", CheckOutcome.FAIL_CLOSED, "safety_audit_rejected")]
    return [CheckResult("C11", CheckOutcome.PASS, "safety_audited")]


def check_output_bounds(
    claims: Sequence[AnalysisClaim], rendered: RenderedAnswer
) -> list[CheckResult]:
    """C12: length, claim budget, Tier-1 read-only, uncertainty labeling."""
    from ai.core.analysis.renderer import RENDER_TEMPLATES

    results: list[CheckResult] = []
    if len(claims) > MAX_CLAIMS:
        results.append(CheckResult("C12", CheckOutcome.ABSTAIN, "claim_budget_exceeded"))
    if len(rendered.detailed_response) > MAX_DETAILED_CHARS:
        results.append(CheckResult("C12", CheckOutcome.DOWNGRADE, "output_too_long"))
    for claim in claims:
        template = RENDER_TEMPLATES.get(claim.render_template)
        if template is None:
            continue
        if str(claim.evidence_classification) == "insufficient" and not (
            claim.claim_role == "limitation" or template.calibrated
        ):
            results.append(
                CheckResult(
                    "C12",
                    CheckOutcome.DOWNGRADE,
                    "insufficient_without_limitation",
                    (claim.claim_id,),
                )
            )
    return results or [CheckResult("C12", CheckOutcome.PASS, "output_bounds")]


def check_final_authorization(
    reauthorize: Callable[[], bool],
) -> list[CheckResult]:
    """C13: the actor still authorizes everything, at emission time.

    A failure is indistinguishable from nonexistence downstream — the
    caller renders the fail-closed template with a content-free code.
    """
    try:
        authorized = bool(reauthorize())
    except Exception:
        authorized = False
    if not authorized:
        return [CheckResult("C13", CheckOutcome.FAIL_CLOSED, "reauthorization_failed")]
    return [CheckResult("C13", CheckOutcome.PASS, "reauthorized")]


def check_wire_disclosure(
    emitted_events: Sequence[Mapping[str, Any]],
) -> list[CheckResult]:
    """C14: only content-free progress preceded validation on any channel."""
    for event in emitted_events:
        event_type = str(event.get("type") or "")
        if event_type in _ALLOWED_PRE_VALIDATION_TYPES:
            continue
        if event_type == "STATE_DELTA":
            if (
                str(event.get("kind") or "") == "analysis_progress"
                and str(event.get("stage") or "") in ANALYSIS_PROGRESS_STAGES
                and set(event) <= _ALLOWED_PROGRESS_KEYS
            ):
                continue
            return [CheckResult("C14", CheckOutcome.FAIL_CLOSED, "pre_validation_disclosure")]
        return [CheckResult("C14", CheckOutcome.FAIL_CLOSED, "pre_validation_disclosure")]
    return [CheckResult("C14", CheckOutcome.PASS, "wire_disclosure")]


# --- aggregation ----------------------------------------------------------


def validate_analysis(
    *,
    claims: Sequence[AnalysisClaim],
    facets: Sequence[AnalysisFacet],
    store: EvidenceStore,
    rendered: RenderedAnswer,
    entities: Sequence[Mapping[str, Any]] = (),
    scope: TurnScopeContext | None = None,
    ledger_retrieval_ids: frozenset[str] = frozenset(),
    ledger_chunk_ids: frozenset[str] | None = None,
    emitted_events: Sequence[Mapping[str, Any]] = (),
    reauthorize: Callable[[], bool],
    safety_audit: Callable[[], bool] | None = None,
) -> ValidationVerdict:
    """Run every check; aggregate to the worst outcome plus the drops."""
    results: list[CheckResult] = []
    results.extend(check_scope(claims, store, scope))
    results.extend(
        check_ledger(
            claims,
            store,
            ledger_retrieval_ids=ledger_retrieval_ids,
            ledger_chunk_ids=ledger_chunk_ids,
        )
    )
    results.extend(check_typed_basis(claims, store))
    results.extend(check_identifier_closure(claims, rendered))
    results.extend(check_value_closure(claims, rendered))
    results.extend(check_population(claims, store))
    results.extend(check_applicability(claims, store))
    results.extend(check_contradiction(claims, store))

    dropped = frozenset(
        claim_id
        for result in results
        if result.outcome is CheckOutcome.DOWNGRADE
        for claim_id in result.claim_ids
    )

    results.extend(check_facets(facets, claims, dropped))
    entity_results, allowed_entities = check_entity_manifest(entities, claims, scope, dropped)
    results.extend(entity_results)
    results.extend(check_safety(rendered, safety_audit))
    results.extend(check_output_bounds(claims, rendered))
    results.extend(check_final_authorization(reauthorize))
    results.extend(check_wire_disclosure(emitted_events))

    dropped = frozenset(
        claim_id
        for result in results
        if result.outcome is CheckOutcome.DOWNGRADE
        for claim_id in result.claim_ids
    )
    answer_ids = {claim.claim_id for claim in claims if claim.claim_role == "answer"}
    outcome = max((result.outcome for result in results), key=_SEVERITY.__getitem__)
    if outcome is CheckOutcome.DOWNGRADE and answer_ids and answer_ids <= dropped:
        outcome = CheckOutcome.ABSTAIN
        results.append(CheckResult("C08", CheckOutcome.ABSTAIN, "no_answer_survives"))
    return ValidationVerdict(
        outcome=outcome,
        results=results,
        dropped_claim_ids=tuple(sorted(dropped)),
        allowed_entities=allowed_entities,
    )


# --- shadow soak over legacy prose ---------------------------------------


def shadow_scan_legacy(
    *,
    message: str,
    known_values: frozenset[str],
    envelopes: Sequence[Mapping[str, Any]] = (),
    intent: str = "",
) -> dict[str, Any] | None:
    """Prose-mode C04/C05/C06/C09 over a legacy wf8 answer. Content-free.

    ``known_values`` is the server-shown closure (capture-ledger observed
    values); envelopes are the §7.4 metas the turn recorded. The result is
    the compact blob persisted for the enforce-flip soak review — codes
    and counts only, never tokens or text.
    """
    if not message:
        return None
    would_fail: list[str] = []
    identifier_count = len(ungrounded_identifiers(message, known_values))
    if identifier_count:
        would_fail.append("unclosed_identifier")
    unclosed_values = 0
    for run in _digit_runs(message):
        if not (_normalized_candidates(run) & known_values):
            unclosed_values += 1
    if unclosed_values:
        would_fail.append("unclosed_value")
    incomplete = any(
        not envelope.get("coverage", {}).get("complete_population", False) for envelope in envelopes
    )
    if _ABSENCE_PHRASE_RE.search(message) and (incomplete or not envelopes):
        would_fail.append("absence_without_complete_population")
    return {
        "scan": "prose-v1",
        "intent": intent,
        "would_fail": would_fail,
        "counts": {
            "unclosed_identifiers": identifier_count,
            "unclosed_values": unclosed_values,
            "envelopes": len(envelopes),
        },
    }


__all__ = [
    "MAX_CLAIMS",
    "MAX_DETAILED_CHARS",
    "CheckOutcome",
    "CheckResult",
    "ValidationVerdict",
    "check_applicability",
    "check_contradiction",
    "check_entity_manifest",
    "check_facets",
    "check_final_authorization",
    "check_identifier_closure",
    "check_ledger",
    "check_output_bounds",
    "check_population",
    "check_safety",
    "check_scope",
    "check_typed_basis",
    "check_value_closure",
    "check_wire_disclosure",
    "shadow_scan_legacy",
    "validate_analysis",
]
