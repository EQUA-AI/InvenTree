"""The evidence-analysis executor (S10): retrieve → synthesize → render → validate.

Everything is buffered: until ``validate_analysis`` passes, the only wire
traffic is the content-free ``analysis_progress`` stages. The executor
returns an ``AnalysisOutcome``; the execution branch owns emission and the
canonical dict, and ``finalize`` persists the attachment + evidence sets
atomically with the terminal result.

Tier-1 serves ``record_retrieval``, ``manual_fact``, and
``source_inventory``. The synthesis model only ever ORGANIZES facts (via
the value-less ``SynthesisClaimSet`` schema) and any model failure falls
back to ``deterministic_claims`` — the rail never depends on a model for
availability.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ai.core.analysis.evidence import (
    EvidenceStore,
    FactValue,
    coverage_fact,
    fact_from_manual_citation,
    facts_from_inventory_rows,
    facts_from_work_order_row,
)
from ai.core.analysis.intent import ANALYSIS_ROUTED_INTENTS
from ai.core.analysis.renderer import (
    RenderError,
    assign_ordinals,
    render_answer,
)
from ai.core.analysis.schemas import (
    AnalysisClaim,
    AnalysisFacet,
    CanonicalEvidenceAnalysisResponseV2,
)
from ai.core.analysis.synthesis import (
    FACET_PLANS,
    deterministic_claims,
    synthesize_claims,
)
from ai.core.analysis.validator import (
    CheckOutcome,
    validate_analysis,
)
from ai.core.i18n_templates import (
    ANALYSIS_ABSTAIN,
    ANALYSIS_FAIL_CLOSED,
    deterministic_template,
)

if TYPE_CHECKING:
    from ai.core.analysis.renderer import RenderedAnswer
    from ai.core.analysis.scope_context import TurnScopeContext
    from ai.core.turn.state import TurnRun
    from ai.core.turn_service import NormalizedTurnService

logger = logging.getLogger(__name__)

#: String view of the shipped-executor set (single source of truth in
#: ``analysis.intent`` — routing and dispatch can never drift).
TIER1_INTENTS = frozenset(member.value for member in ANALYSIS_ROUTED_INTENTS)

_SAFETY_BOUNDARY = "Advisory; verify the cited source before acting. No safety status was inferred."


@dataclass
class AnalysisOutcome:
    """Everything the execution branch and finalize need from one run."""

    response: CanonicalEvidenceAnalysisResponseV2
    turn_state: str  # durable TurnState value (partial -> "incomplete")
    attachment: dict[str, Any]  # the consolidated evidence_analysis payload
    evidence_set_specs: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=dict)
    emitted_events: list[dict[str, Any]] = field(default_factory=list)


async def _emit_progress(run: TurnRun, stage: str, emitted: list[dict[str, Any]]) -> None:
    """One content-free progress stage; fail-soft, never before-validation text."""
    event_record = {"type": "STATE_DELTA", "kind": "analysis_progress", "stage": stage}
    emitted.append(event_record)
    emitter = getattr(run, "emitter", None)
    if emitter is None:
        return
    try:
        from ai.core.streaming import AGUIEvent, EventType

        await emitter.emit(
            AGUIEvent(
                event_type=EventType.STATE_DELTA,
                data={"kind": "analysis_progress", "stage": stage},
                thread_id=run.thread.pk,
                run_id=f"analysis:{run.turn.pk}",
            )
        )
    except Exception:  # pragma: no cover - progress must never fail a turn
        logger.warning("analysis progress emission failed", exc_info=False)


# --- retrieval (sync bodies, called through service._call_sync) -------------


def _retrieve_records(
    user: Any, store: EvidenceStore, *, scope: TurnScopeContext | None
) -> dict[str, Any]:
    """record_retrieval: the scope-driven work-order page → typed facts."""
    from django.utils import timezone
    from tasks.ai_read import work_orders_page

    as_of = timezone.now().isoformat()
    machine_ids = sorted(scope.machine_ids) if scope is not None and scope.explicit else None
    page = work_orders_page(
        user,
        query=None,
        limit=10,
        scope_machine_ids=machine_ids,
        scope_date_from=getattr(scope, "date_from", None),
        scope_date_to=getattr(scope, "date_to", None),
        enforce=bool(scope is not None and scope.enforce and scope.explicit),
    )
    retrieval_id = f"ret_records_{getattr(scope, 'snapshot_id', None) or 'unscoped'}"
    snapshot_label = getattr(scope, "snapshot_id", None)
    for row in page.get("rows") or ():
        facts_from_work_order_row(
            store,
            row,
            retrieval_id=retrieval_id,
            as_of=as_of,
            source_revision=str(snapshot_label or ""),
        )
    coverage_fact(store, page, retrieval_id=retrieval_id, source_class="work_order", as_of=as_of)
    pending = store.open_evidence_set(
        source_class="work_order",
        filters=dict(page.get("applied_filters") or {}),
        population_count=int(page.get("population_count") or 0),
        evaluated_count=int(page.get("population_count") or 0),
        complete_population=bool(page.get("complete_population")),
        snapshot_hash=str(snapshot_label or ""),
        high_watermarks={"updated_at": page.get("high_watermark")},
        calculation={
            "operation": "count",
            "result": str(int(page.get("population_count") or 0)),
        },
    )
    for row in page.get("rows") or ():
        pending.add_member(
            "work_order",
            str(row.get("work_order_id")),
            str(row.get("updated_at") or ""),
        )
    pending.displayed_count = len(page.get("rows") or ())
    store.add_calculation(
        operation="count",
        input_refs=(),
        values={"count": FactValue("int", int(page.get("population_count") or 0))},
        evidence_set_handle=pending.handle,
        complete_population=bool(page.get("complete_population")),
    )
    store.record_envelope({
        "retrieval_id": retrieval_id,
        "source_class": "work_order",
        "operation": "page",
        "coverage": {
            "population_count": int(page.get("population_count") or 0),
            "returned_count": int(page.get("returned_count") or 0),
            "complete_population": bool(page.get("complete_population")),
        },
    })
    store.set_primary_coverage({
        "population_count": int(page.get("population_count") or 0),
        "returned_count": int(page.get("returned_count") or 0),
        "complete_population": bool(page.get("complete_population")),
        "display_truncated": bool(page.get("display_truncated")),
        "date_field": (page.get("applied_filters") or {}).get("date_field"),
        "timezone": timezone.get_current_timezone_name(),
        "filters": [
            f"{key}: {value}" for key, value in (page.get("applied_filters") or {}).items()
        ],
        "as_of": as_of,
        "snapshot_label": snapshot_label,
        "excluded_null_date_count": None,
        "incomplete_reason": None
        if page.get("complete_population")
        else "not_all_records_evaluated",
    })
    return page


def _retrieve_manual(user: Any, store: EvidenceStore, *, query: str) -> dict[str, Any]:
    """manual_fact: the §8.4 fallback orchestrator → controlled facts."""
    from ai.core.analysis.source_gateway import retrieve_manual_fact
    from django.utils import timezone

    def _attachment_search(**kwargs):
        from ai.core.integrations.attachment_corpus import search_corpus_attachments

        # The corpus derives its scope floor from the bound scope context.
        return search_corpus_attachments(user=kwargs["user"], query=kwargs["query"])

    as_of = timezone.now().isoformat()
    result = retrieve_manual_fact(user, query=query, attachment_search=_attachment_search)
    retrieval_id = str((result.get("retrieval") or {}).get("retrieval_id") or "ret_manual")
    for chunk in result.get("chunks") or ():
        citation = chunk.get("citation") or {}
        fact_from_manual_citation(store, citation, retrieval_id=retrieval_id, as_of=as_of)
    if result.get("retrieval"):
        store.record_envelope(result["retrieval"])
    store.set_primary_coverage({
        "population_count": len(result.get("chunks") or ()),
        "returned_count": len(result.get("chunks") or ()),
        "complete_population": False,
        "display_truncated": False,
        "date_field": None,
        "timezone": timezone.get_current_timezone_name(),
        "filters": [f"step: {a['step']} ({a['outcome']})" for a in result.get("attempts") or ()],
        "as_of": as_of,
        "snapshot_label": None,
        "excluded_null_date_count": None,
        "incomplete_reason": "semantic_retrieval_never_evaluates_a_population",
    })
    return result


def _retrieve_inventory(user: Any, store: EvidenceStore) -> dict[str, Any]:
    """source_inventory: the registry gateway → inventory facts."""
    from ai.core.analysis.source_gateway import inventory
    from django.utils import timezone

    as_of = timezone.now().isoformat()
    result = inventory(user)
    section = (result.get("sections") or {}).get("controlled_documents") or {}
    rows = []
    for entry in section.get("documents") or ():
        current = entry.get("current") or {}
        rows.append({
            "document_id": entry.get("document_id"),
            "title": entry.get("title"),
            "document_class": entry.get("document_class"),
            "revision": current.get("revision"),
            "state": current.get("state"),
            "current": bool(entry.get("source_state", {}).get("current")),
            "searchable_now": bool(entry.get("source_state", {}).get("searchable_now")),
            "association": entry.get("association"),
        })
    retrieval_id = str((section.get("retrieval") or {}).get("retrieval_id") or "ret_inventory")
    facts_from_inventory_rows(store, rows, retrieval_id=retrieval_id, as_of=as_of)
    if section.get("retrieval"):
        store.record_envelope(section["retrieval"])
    population = int(section.get("population_count") or 0)
    store.add_calculation(
        operation="count",
        input_refs=(),
        values={"count": FactValue("int", population)},
        complete_population=True,
    )
    store.set_primary_coverage({
        "population_count": population,
        "returned_count": int(section.get("returned_count") or 0),
        "complete_population": True,
        "display_truncated": bool(section.get("display_truncated")),
        "date_field": None,
        "timezone": timezone.get_current_timezone_name(),
        "filters": [],
        "as_of": as_of,
        "snapshot_label": None,
        "excluded_null_date_count": None,
        "incomplete_reason": None,
    })
    return result


def _reauthorize(user: Any, store: EvidenceStore) -> bool:
    """C13: at emission time, the actor still reads every cited record."""
    try:
        work_order_ids = {
            fact.source_id
            for fact in store.facts.values()
            if fact.source_class == "work_order" and fact.kind == "record_field"
        }
        if work_order_ids:
            from tasks.ai_read import authorized_work_order

            for work_order_id in work_order_ids:
                if authorized_work_order(user, work_order_id) is None:
                    return False
        machine_ids = {
            fact.machine_id for fact in store.facts.values() if fact.machine_id is not None
        }
        if machine_ids:
            from assets.ai_read import authorized_machine

            for machine_id in machine_ids:
                if authorized_machine(user, machine_id) is None:
                    return False
        return True
    except Exception:
        return False


# --- claim assembly ---------------------------------------------------------


def _claims_from_synthesis(
    view: dict[str, Any], intent: str, store: EvidenceStore, *, timeout_s: float
) -> tuple[list[AnalysisFacet], list[AnalysisClaim], str]:
    """Model organization when it parses; the deterministic set otherwise."""
    synthesized = synthesize_claims(view, intent, timeout_s=timeout_s)
    if synthesized is not None and synthesized.claims:
        try:
            claims = [
                AnalysisClaim.model_validate_json(slot.model_dump_json())
                for slot in synthesized.claims
            ]
            return list(synthesized.facets), claims, "model"
        except Exception:
            logger.warning("synthesis claim adaptation degraded", exc_info=False)
    facets, claims = deterministic_claims(intent, store)
    return facets, claims, "deterministic"


def _wire_claims(claims: list[AnalysisClaim], ordinals: Any) -> list[dict[str, Any]]:
    """The wire claim projection: citation ORDINALS, never internal refs."""
    return [
        {
            "claim_id": claim.claim_id,
            "claim_role": claim.claim_role,
            "claim_type": str(claim.claim_type),
            "evidence_classification": str(claim.evidence_classification),
            "citation_ordinals": list(ordinals.by_claim.get(claim.claim_id, ())),
            "entity_refs": list(claim.entity_refs),
        }
        for claim in claims
    ]


def _no_data_reason(intent: str, store: EvidenceStore, retrieval_failed: bool) -> str | None:
    if retrieval_failed:
        return "retrieval_failure"
    coverage = store.coverage_meta() or {}
    if intent == "record_retrieval":
        if int(coverage.get("population_count") or 0) == 0 and coverage.get("complete_population"):
            return "complete_population_no_matches"
        if not coverage.get("complete_population") and not store.facts:
            return "incomplete_coverage"
    if intent == "manual_fact" and not any(
        fact.kind == "manual_passage" for fact in store.facts.values()
    ):
        return None  # the no_relevant_passage claim states it in prose
    return None


def _template_response(
    *, kind_state: str, message: str, reasoning: str, incomplete_reasons: list[dict[str, str]]
) -> CanonicalEvidenceAnalysisResponseV2:
    """A deterministic abstention/fail-closed v2 response."""
    payload = {
        "kind": "evidence_analysis",
        "response_version": 2,
        "response_state": kind_state,
        "detailed_response": message,
        "spoken_summary": "",
        "reasoning_summary": reasoning,
        "evidence": [],
        "next_questions": [],
        "recommended_actions": [],
        "safety_boundary": _SAFETY_BOUNDARY,
        "speak": False,
        "payload": {
            "payload_type": "evidence_analysis_v2",
            "facets": [],
            "claims": [],
            "assumptions": [],
            "inclusion_rules": [],
            "exclusion_rules": [],
            "unknowns": [],
        },
        "incomplete_reasons": incomplete_reasons,
    }
    return CanonicalEvidenceAnalysisResponseV2.model_validate_json(json.dumps(payload))


def _evidence_entries(rendered: RenderedAnswer) -> list[dict[str, Any]]:
    """§7.5 evidence entries derived from the citation manifest."""
    entries: list[dict[str, Any]] = []
    for entry in rendered.citation_manifest:
        locator: dict[str, Any] = {}
        raw_locator = entry.get("locator") or {}
        for key in ("field", "page", "chunk"):
            if raw_locator.get(key) is not None:
                locator[key] = raw_locator[key]
        if not locator:
            locator = {"field": "citation"}
        entries.append({
            "source_type": str(entry.get("source_type") or "retrieval"),
            "source_id": str(entry.get("source_id") or "unknown"),
            "source_revision": str(entry.get("source_revision") or "unversioned"),
            "locator": locator,
            "as_of": entry.get("as_of") or "1970-01-01T00:00:00+00:00",
            "authorization_class": "maintenance_authorized",
            "claim": ",".join(entry.get("claim_ids", ())) or "unattributed",
        })
    return entries


async def run_analysis(
    service: NormalizedTurnService, run: TurnRun, *, shadow: bool = False
) -> AnalysisOutcome:
    """Execute one Tier-1 evidence-analysis turn, fully buffered."""
    from ai.core.analysis.scope_context import current_turn_scope
    from ai.core.config import get_settings

    settings = get_settings()
    deadline_s = float(getattr(settings, "analysis_turn_deadline_s", 45.0) or 45.0)
    synthesis_timeout_s = float(getattr(settings, "analysis_synthesis_timeout_s", 20.0) or 20.0)
    locale = getattr(run.trusted_context, "locale", "en")
    intent = run.task_intent.intent.value if run.task_intent is not None else "general"
    emitted: list[dict[str, Any]] = []
    scope = current_turn_scope()
    store = EvidenceStore()
    retrieval_failed = False
    timed_out = False

    if not shadow:
        await _emit_progress(run, "confirming_scope", emitted)

    user = await service._call_sync(service._rehydrate_user_for_grounding, run.actor)

    if not shadow:
        await _emit_progress(run, "reviewing_records", emitted)
    try:
        async with asyncio.timeout(deadline_s):
            if intent == "record_retrieval":
                await service._call_sync(_retrieve_records, user, store, scope=scope)
            elif intent == "manual_fact":
                await service._call_sync(_retrieve_manual, user, store, query=run.routing_content)
            elif intent == "source_inventory":
                await service._call_sync(_retrieve_inventory, user, store)
    except TimeoutError:
        timed_out = True
    except Exception:
        logger.warning("analysis retrieval failed", exc_info=True)
        retrieval_failed = True

    if not shadow:
        await _emit_progress(run, "validating_evidence", emitted)

    facet_plan = FACET_PLANS.get(intent, ("records",))
    incomplete_reasons: list[dict[str, str]] = []

    if retrieval_failed or (timed_out and not store.facts):
        code = "retrieval_timeout" if timed_out else "capability_boundary"
        incomplete_reasons = [
            {"code": code if timed_out else "retrieval_timeout", "facet": facet}
            for facet in facet_plan
        ]
        response = _template_response(
            kind_state="incomplete",
            message=deterministic_template(ANALYSIS_ABSTAIN, locale),
            reasoning=(
                "Retrieval did not complete; no conclusion was produced and nothing was estimated."
            ),
            incomplete_reasons=incomplete_reasons,
        )
        return _outcome_from_response(
            response,
            turn_state="incomplete",
            store=store,
            scope=scope,
            gate={"verdict": "abstain", "codes": ["retrieval_unavailable"]},
            entities=[],
            emitted=emitted,
            claims=[],
            ordinals=None,
            no_data_reason="retrieval_failure",
            persist_evidence=False,
        )

    # Synthesis (model organizes; deterministic is the floor).
    from ai.core.analysis.renderer import RENDER_TEMPLATES

    view = store.synthesis_view(template_keys=tuple(RENDER_TEMPLATES))
    facets, claims, synthesis_source = await asyncio.to_thread(
        _claims_from_synthesis, view, intent, store, timeout_s=synthesis_timeout_s
    )

    state = "complete"
    if timed_out:
        state = "partial"
        answered = {facet.name for facet in facets}
        incomplete_reasons = [
            {"code": "retrieval_timeout", "facet": facet}
            for facet in facet_plan
            if facet not in answered
        ] or [{"code": "retrieval_timeout", "facet": facet_plan[-1]}]

    ledger_ids, ledger_chunks = _ledger_join_keys()

    verdict = None
    rendered = None
    for attempt in range(2):
        try:
            ordinals = assign_ordinals(
                claims, store, default_as_of=(store.coverage_meta() or {}).get("as_of", "")
            )
            rendered = render_answer(
                claims,
                store,
                ordinals=ordinals,
                locale=locale,
                state=state,
                incomplete_reasons=[
                    _incomplete_reason_obj(reason) for reason in incomplete_reasons
                ],
            )
        except RenderError:
            logger.warning("analysis render failed; abstaining", exc_info=False)
            verdict = None
            rendered = None
            break

        from ai.core.entities import build_analysis_entity_manifest

        candidate_entities = build_analysis_entity_manifest(claims, store, scope)
        # Off-loop: the C13 reauthorization closure touches the ORM.
        verdict = await asyncio.to_thread(
            lambda claims=claims, facets=facets, rendered=rendered, candidate_entities=candidate_entities: (
                validate_analysis(
                    claims=claims,
                    facets=facets,
                    store=store,
                    rendered=rendered,
                    entities=candidate_entities,
                    scope=scope,
                    ledger_retrieval_ids=ledger_ids,
                    ledger_chunk_ids=ledger_chunks,
                    emitted_events=emitted,
                    reauthorize=lambda: _reauthorize(user, store),
                    safety_audit=None,
                )
            )
        )
        if verdict.outcome is CheckOutcome.PASS:
            break
        if verdict.outcome is CheckOutcome.DOWNGRADE and attempt == 0:
            dropped = set(verdict.dropped_claim_ids)
            claims = [claim for claim in claims if claim.claim_id not in dropped]
            claims.append(
                AnalysisClaim.model_validate_json(
                    json.dumps({
                        "claim_id": "c_downgrade",
                        "claim_role": "limitation",
                        "claim_type": "limitation",
                        "evidence_classification": "insufficient",
                        "fact_refs": [],
                        "calculation_output_refs": [],
                        "evidence_refs": [],
                        "entity_refs": [],
                        "render_template": "analysis.downgrade_limitation",
                        "paraphrase": "",
                    })
                )
            )
            facets = [
                AnalysisFacet.model_validate_json(
                    json.dumps({
                        "name": facet.name,
                        "status": str(facet.status),
                        "claim_ids": [
                            claim_id for claim_id in facet.claim_ids if claim_id not in dropped
                        ],
                    })
                )
                for facet in facets
            ]
            continue
        break

    if verdict is not None and verdict.outcome is CheckOutcome.FAIL_CLOSED:
        response = _template_response(
            kind_state="failed",
            message=deterministic_template(ANALYSIS_FAIL_CLOSED, locale),
            reasoning="A safety or authorization invariant failed; the answer was withheld.",
            incomplete_reasons=[],
        )
        return _outcome_from_response(
            response,
            turn_state="failed",
            store=store,
            scope=scope,
            gate={"verdict": "fail_closed", "codes": list(verdict.codes())},
            entities=[],
            emitted=emitted,
            claims=[],
            ordinals=None,
            no_data_reason="unauthorized_or_unavailable",
            persist_evidence=False,
        )

    if (
        rendered is None
        or verdict is None
        or verdict.outcome in (CheckOutcome.ABSTAIN,)
        or (verdict.outcome is CheckOutcome.DOWNGRADE)
    ):
        codes = list(verdict.codes()) if verdict is not None else ["render_error"]
        response = _template_response(
            kind_state="incomplete",
            message=deterministic_template(ANALYSIS_ABSTAIN, locale),
            reasoning=(
                "The evidence gate could not verify a reliable answer; no conclusion was produced."
            ),
            incomplete_reasons=[{"code": "facet_budget_exhausted", "facet": facet_plan[0]}],
        )
        return _outcome_from_response(
            response,
            turn_state="incomplete",
            store=store,
            scope=scope,
            gate={"verdict": "abstain", "codes": codes},
            entities=[],
            emitted=emitted,
            claims=[],
            ordinals=None,
            no_data_reason=_no_data_reason(intent, store, retrieval_failed),
            persist_evidence=False,
        )

    # PASS: assemble the validated v2 response.
    payload = {
        "kind": "evidence_analysis",
        "response_version": 2,
        "response_state": state,
        "detailed_response": rendered.detailed_response,
        "spoken_summary": rendered.spoken_summary if state == "complete" else "",
        "reasoning_summary": rendered.reasoning_summary,
        "evidence": _evidence_entries(rendered),
        "next_questions": [],
        "recommended_actions": [],
        "safety_boundary": _SAFETY_BOUNDARY,
        "speak": False,
        "payload": {
            "payload_type": "evidence_analysis_v2",
            "facets": [json.loads(facet.model_dump_json()) for facet in facets],
            "claims": [json.loads(claim.model_dump_json()) for claim in claims],
            "assumptions": [],
            "inclusion_rules": [],
            "exclusion_rules": [],
            "unknowns": [],
        },
        "incomplete_reasons": incomplete_reasons,
    }
    response = CanonicalEvidenceAnalysisResponseV2.model_validate_json(json.dumps(payload))
    turn_state = "complete" if state == "complete" else "incomplete"
    outcome = _outcome_from_response(
        response,
        turn_state=turn_state,
        store=store,
        scope=scope,
        gate={
            "verdict": str(verdict.outcome),
            "codes": list(verdict.codes()),
            "synthesis": synthesis_source,
        },
        entities=[dict(entity) for entity in verdict.allowed_entities],
        emitted=emitted,
        claims=claims,
        ordinals=ordinals,
        no_data_reason=_no_data_reason(intent, store, retrieval_failed),
    )
    return outcome


def _incomplete_reason_obj(reason: dict[str, str]):
    from ai.core.analysis.schemas import IncompleteReason

    return IncompleteReason.model_validate_json(json.dumps(reason))


def _ledger_join_keys() -> tuple[frozenset[str], frozenset[str] | None]:
    try:
        from ai.core.tools.capture_ledger import current_tool_captures

        ledger = current_tool_captures()
        if ledger is None:
            return frozenset(), None
        ids = frozenset(
            str(meta.get("retrieval_id") or "")
            for meta in ledger.retrieval_metas()
            if meta.get("retrieval_id")
        )
        chunks = frozenset(
            str(citation.get("chunk_id") or "")
            for citation in ledger.manuals_citations()
            if citation.get("chunk_id")
        )
        return ids, (chunks or None)
    except Exception:  # pragma: no cover - join keys are best-effort inputs
        return frozenset(), None


def _outcome_from_response(
    response: CanonicalEvidenceAnalysisResponseV2,
    *,
    turn_state: str,
    store: EvidenceStore,
    scope: TurnScopeContext | None,
    gate: dict[str, Any],
    entities: list[dict[str, Any]],
    emitted: list[dict[str, Any]],
    claims: list[AnalysisClaim],
    ordinals: Any,
    no_data_reason: str | None,
    persist_evidence: bool = True,
) -> AnalysisOutcome:
    """Build the wire attachment + persistence specs for one response."""
    citations = ordinals.wire() if ordinals is not None else []
    attachment = {
        "response_version": 2,
        "response_state": str(response.response_state),
        "incomplete_reasons": [
            {"code": reason.code, "facet": reason.facet} for reason in response.incomplete_reasons
        ],
        "no_data_reason": no_data_reason,
        "active_scope": (
            {
                "display_label": scope.display_label
                or f"{len(scope.machine_ids or ())} selected assets",
                "version": int(scope.scope_version or 0),
            }
            if scope is not None and scope.active
            else None
        ),
        "claims": _wire_claims(claims, ordinals) if ordinals is not None else [],
        "citations": citations,
        "coverage": store.coverage_meta(),
    }
    # Withheld/abstained answers persist NO evidence sets: §7.6 membership
    # is written only for claims a validated answer actually made.
    specs = store.persistence_specs() if persist_evidence else []
    if scope is not None:
        for spec in specs:
            spec["analysis_scope_hash"] = scope.scope_hash or ""
    # Chips: strip the internal join ref before anything client-visible.
    wire_entities = [
        {key: value for key, value in entity.items() if key != "ref"} for entity in entities
    ]
    return AnalysisOutcome(
        response=response,
        turn_state=turn_state,
        attachment=attachment,
        evidence_set_specs=specs,
        entities=wire_entities,
        gate=gate,
        emitted_events=emitted,
    )


__all__ = ["TIER1_INTENTS", "AnalysisOutcome", "run_analysis"]
