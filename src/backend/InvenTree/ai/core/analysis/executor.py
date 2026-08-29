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
    EVIDENCE_SET_MEMBER_CAP,
    EvidenceStore,
    FactValue,
    coverage_fact,
    fact_from_dataset_profile,
    fact_from_manual_citation,
    facts_from_group_rows,
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
from ai.core.analysis.snapshot import AnalysisRetrievalIncomplete, build_manifest
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


def _scope_narrowing(scope: TurnScopeContext | None) -> dict[str, Any]:
    """The enforce-only analysis-scope narrowing kwargs for analytics ops."""
    enforce = bool(scope is not None and scope.enforce and scope.explicit)
    if not enforce:
        return {"scope_machine_ids": None, "date_from": None, "date_to": None}
    return {
        "scope_machine_ids": sorted(scope.machine_ids) if scope.machine_ids else None,
        "date_from": getattr(scope, "date_from", None),
        "date_to": getattr(scope, "date_to", None),
    }


def _snapshot_scan(scan_versions, *, compute):
    """Scan → compute → rescan, retried once; §8.3.5 snapshot v1.

    ``scan_versions()`` returns the operand-version dict from the tasks
    scans; ``compute()`` runs the aggregation. A changed operand list after
    compute retries the whole read once; a second divergence is the typed
    ``snapshot_changed``. Overflow past the membership envelope is the
    typed ``population_cap_exceeded`` (§7.6: larger exact-audit
    calculations abstain).
    """
    for _attempt in range(2):
        versions = scan_versions()
        if not versions.get("available"):
            return versions, None
        if versions.get("overflow"):
            raise AnalysisRetrievalIncomplete(
                "population_cap_exceeded",
                f"population exceeds the {EVIDENCE_SET_MEMBER_CAP} member envelope",
            )
        computed = compute()
        recheck = scan_versions()
        if recheck.get("rows") == versions.get("rows"):
            return versions, computed
    raise AnalysisRetrievalIncomplete(
        "snapshot_changed", "operand versions changed during analysis; retried once"
    )


_VOCABULARY_CODES = frozenset({"grouping_unavailable", "bucket_range_exceeded"})


def _vocabulary_incomplete(exc: Exception) -> AnalysisRetrievalIncomplete:
    """Map a tasks vocabulary error onto the wire incomplete vocabulary."""
    code = str(getattr(exc, "code", "") or "")
    if code not in _VOCABULARY_CODES:
        code = "grouping_unavailable"
    return AnalysisRetrievalIncomplete(code, str(exc))


def _retrieve_aggregate(
    user: Any, store: EvidenceStore, *, scope: TurnScopeContext | None, query: str, run: Any
) -> dict[str, Any]:
    """fleet_aggregate: profile + one grouped count over the population."""
    from ai.core.analysis.plans import build_aggregate_plan
    from django.utils import timezone
    from tasks.ai_analytics import (
        AnalyticsRequestError,
        aggregate_work_orders,
        get_work_order_dataset_profile,
        work_order_operand_versions,
    )

    as_of = timezone.now().isoformat()
    plan = build_aggregate_plan(query)
    narrowing = _scope_narrowing(scope)
    grouping = plan["grouping"]
    date_field = plan["date_field"]

    profile: dict[str, Any] = {}

    def _compute() -> dict[str, Any]:
        nonlocal profile
        profile = get_work_order_dataset_profile(user, date_field=date_field, **narrowing)
        return aggregate_work_orders(user, grouping=grouping, date_field=date_field, **narrowing)

    try:
        versions, result = _snapshot_scan(
            lambda: work_order_operand_versions(
                user,
                date_field=date_field,
                require_machine=(grouping == "machine"),
                limit=EVIDENCE_SET_MEMBER_CAP,
                **narrowing,
            ),
            compute=_compute,
        )
    except AnalyticsRequestError as exc:
        raise _vocabulary_incomplete(exc) from exc

    snapshot_label = getattr(scope, "snapshot_id", None)
    retrieval_id = f"ret_aggregate_{snapshot_label or 'unscoped'}"
    if result is None or not result.get("available"):
        store.set_primary_coverage({
            "population_count": 0,
            "returned_count": 0,
            "complete_population": False,
            "display_truncated": False,
            "date_field": date_field,
            "timezone": None,
            "filters": [],
            "as_of": as_of,
            "snapshot_label": snapshot_label,
            "excluded_null_date_count": None,
            "incomplete_reason": "analytics_unavailable",
        })
        return result or {}

    manifest = build_manifest(
        snapshot_id=str(snapshot_label or ""),
        operands=versions["rows"],
        sources={"work_order": {"high_watermark": result.get("high_watermark")}},
        plan=plan,
        as_of=as_of,
        notes=("row_pinned_only",),
    )
    run.query_plan = manifest

    profile_fact = fact_from_dataset_profile(store, profile, retrieval_id=retrieval_id, as_of=as_of)
    group_facts = facts_from_group_rows(
        store,
        result.get("groups") or (),
        retrieval_id=retrieval_id,
        as_of=as_of,
        source_class="work_order",
        source_revision=manifest.operand_hash,
        grouping=grouping,
    )
    pending = store.open_evidence_set(
        source_class="work_order",
        filters=dict(result.get("applied_filters") or {}),
        population_count=int(result.get("population_count") or 0),
        evaluated_count=int(result.get("evaluated_count") or 0),
        complete_population=bool(result.get("complete_population")),
        snapshot_hash=manifest.operand_hash,
        high_watermarks={"updated_at": result.get("high_watermark")},
        calculation={
            "operation": "group_count",
            "result": str(int(result.get("total_group_count") or 0)),
        },
    )
    for pk, version in versions["rows"]:
        pending.add_member("work_order", str(pk), version)
    pending.displayed_count = len(result.get("groups") or ())
    store.add_calculation(
        operation="group_count",
        input_refs=(profile_fact, *group_facts),
        values={
            "grouping": FactValue("enum", result.get("grouping")),
            "population_count": FactValue("int", int(result.get("population_count") or 0)),
            "total_group_count": FactValue("int", int(result.get("total_group_count") or 0)),
            "shown_group_count": FactValue("int", len(result.get("groups") or ())),
            "remainder_group_count": FactValue(
                "int", int(result.get("remainder_group_count") or 0)
            ),
            "remainder_count": FactValue("int", int(result.get("remainder_count") or 0)),
            "unassigned_machine_count": FactValue(
                "int", int(result.get("unassigned_machine_count") or 0)
            ),
        },
        evidence_set_handle=pending.handle,
        complete_population=bool(result.get("complete_population")),
    )
    store.record_envelope({
        "retrieval_id": retrieval_id,
        "source_class": "work_order",
        "operation": "aggregate",
        "coverage": {
            "population_count": int(result.get("population_count") or 0),
            "returned_count": len(result.get("groups") or ()),
            "complete_population": bool(result.get("complete_population")),
        },
    })
    store.set_primary_coverage({
        "population_count": int(result.get("population_count") or 0),
        "returned_count": len(result.get("groups") or ()),
        "complete_population": bool(result.get("complete_population")),
        "display_truncated": bool(result.get("groups_truncated")),
        "date_field": result.get("date_field"),
        "timezone": result.get("timezone"),
        "filters": [
            f"{key}: {value}" for key, value in (result.get("applied_filters") or {}).items()
        ],
        "as_of": as_of,
        "snapshot_label": snapshot_label,
        "excluded_null_date_count": None,
        "incomplete_reason": None,
    })
    return result


def _retrieve_trend(
    user: Any, store: EvidenceStore, *, scope: TurnScopeContext | None, query: str, run: Any
) -> dict[str, Any]:
    """trend_analysis: a zero-filled bucket series over one population."""
    import datetime as _datetime

    from ai.core.analysis.plans import build_trend_plan, default_trend_window
    from django.utils import timezone
    from tasks.ai_analytics import (
        AnalyticsRequestError,
        get_work_order_timeline,
        maintenance_record_operand_versions,
        plant_timezone,
        work_order_operand_versions,
    )

    as_of = timezone.now().isoformat()
    plan = build_trend_plan(query)
    narrowing = _scope_narrowing(scope)
    population = plan["population_type"]
    date_field = plan["date_field"]
    bucket = plan["bucket"]

    # An unbounded trend would refuse on the bucket cap; the domain default
    # is the last twelve full months, always echoed in the filters.
    if not narrowing.get("date_from") and not narrowing.get("date_to"):
        tz, _tzname = plant_timezone()
        today = _datetime.datetime.now(tz).date()
        narrowing["date_from"], narrowing["date_to"] = default_trend_window(today)
        plan["window_default"] = "last_12_months"

    if population == "maintenance_records":

        def _scan() -> dict[str, Any]:
            return maintenance_record_operand_versions(
                user,
                limit=EVIDENCE_SET_MEMBER_CAP,
                date_from=narrowing["date_from"],
                date_to=narrowing["date_to"],
                scope_machine_ids=narrowing["scope_machine_ids"],
            )

    else:

        def _scan() -> dict[str, Any]:
            return work_order_operand_versions(
                user, date_field=date_field, limit=EVIDENCE_SET_MEMBER_CAP, **narrowing
            )

    try:
        versions, result = _snapshot_scan(
            _scan,
            compute=lambda: get_work_order_timeline(
                user,
                bucket=bucket,
                population=population,
                date_field=date_field,
                **narrowing,
            ),
        )
    except AnalyticsRequestError as exc:
        raise _vocabulary_incomplete(exc) from exc

    snapshot_label = getattr(scope, "snapshot_id", None)
    retrieval_id = f"ret_trend_{snapshot_label or 'unscoped'}"
    source_class = "maintenance_record" if population == "maintenance_records" else "work_order"
    if result is None or not result.get("available"):
        store.set_primary_coverage({
            "population_count": 0,
            "returned_count": 0,
            "complete_population": False,
            "display_truncated": False,
            "date_field": date_field,
            "timezone": None,
            "filters": [],
            "as_of": as_of,
            "snapshot_label": snapshot_label,
            "excluded_null_date_count": None,
            "incomplete_reason": "analytics_unavailable",
        })
        return result or {}

    manifest = build_manifest(
        snapshot_id=str(snapshot_label or ""),
        operands=versions["rows"],
        sources={source_class: {"high_watermark": result.get("high_watermark")}},
        plan=plan,
        as_of=as_of,
        notes=("row_pinned_only",),
    )
    run.query_plan = manifest

    bucket_facts = facts_from_group_rows(
        store,
        result.get("buckets") or (),
        retrieval_id=retrieval_id,
        as_of=as_of,
        source_class=source_class,
        source_revision=manifest.operand_hash,
        grouping="bucket",
    )
    pending = store.open_evidence_set(
        source_class=source_class,
        filters=dict(result.get("applied_filters") or {}),
        population_count=int(result.get("population_count") or 0),
        evaluated_count=int(result.get("evaluated_count") or 0),
        complete_population=bool(result.get("complete_population")),
        snapshot_hash=manifest.operand_hash,
        high_watermarks={"updated_at": result.get("high_watermark")},
        calculation={
            "operation": "bucket_count",
            "result": str(int(result.get("bucket_count") or 0)),
        },
    )
    for pk, version in versions["rows"]:
        pending.add_member(source_class, str(pk), version)
    pending.displayed_count = len(result.get("buckets") or ())
    store.add_calculation(
        operation="bucket_count",
        input_refs=tuple(bucket_facts),
        values={
            "bucket": FactValue("enum", result.get("bucket")),
            "population_count": FactValue("int", int(result.get("population_count") or 0)),
            "bucket_count": FactValue("int", int(result.get("bucket_count") or 0)),
            "null_date_count": FactValue("int", int(result.get("null_date_count") or 0)),
        },
        evidence_set_handle=pending.handle,
        complete_population=bool(result.get("complete_population")),
    )
    store.record_envelope({
        "retrieval_id": retrieval_id,
        "source_class": source_class,
        "operation": "timeline",
        "coverage": {
            "population_count": int(result.get("population_count") or 0),
            "returned_count": len(result.get("buckets") or ()),
            "complete_population": bool(result.get("complete_population")),
        },
    })
    store.set_primary_coverage({
        "population_count": int(result.get("population_count") or 0),
        "returned_count": len(result.get("buckets") or ()),
        "complete_population": bool(result.get("complete_population")),
        "display_truncated": False,
        "date_field": result.get("date_field"),
        "timezone": result.get("timezone"),
        "filters": [
            f"{key}: {value}" for key, value in (result.get("applied_filters") or {}).items()
        ],
        "as_of": as_of,
        "snapshot_label": snapshot_label,
        "excluded_null_date_count": int(result.get("null_date_count") or 0),
        "incomplete_reason": None,
    })
    return result


def _retrieve_comparison(
    user: Any, store: EvidenceStore, *, scope: TurnScopeContext | None, query: str, run: Any
) -> dict[str, Any]:
    """manual_wo_comparison: the §8.5 gate → deterministic statuses → facts."""
    from ai.core.analysis.comparison import (
        derive_step_statuses,
        evaluate_comparison_gate,
    )
    from ai.core.analysis.evidence import (
        fact_from_applicability_claim,
        fact_from_procedure_application,
    )
    from django.utils import timezone

    as_of = timezone.now().isoformat()
    selection = evaluate_comparison_gate(user, query=query, scope=scope)
    if selection.candidate is None:
        raise AnalysisRetrievalIncomplete(
            "comparison_gate_unmet",
            "a required comparison facet is missing",
            facets=selection.missing_facets,
        )
    candidate = selection.candidate
    snapshot_label = getattr(scope, "snapshot_id", None)
    retrieval_id = f"ret_comparison_{snapshot_label or 'unscoped'}"

    statuses = derive_step_statuses(candidate)
    wo_fact = facts_from_work_order_row(
        store,
        candidate.evidence["work_order"],
        retrieval_id=retrieval_id,
        as_of=as_of,
        source_revision=str(snapshot_label or ""),
    )
    step_facts: list[str] = []
    if candidate.route == "structured":
        fact_from_procedure_application(
            store, candidate.application, retrieval_id=retrieval_id, as_of=as_of
        )
        step_facts = facts_from_group_rows(
            store,
            statuses["rows"],
            retrieval_id=retrieval_id,
            as_of=as_of,
            source_class="step_execution",
            source_revision=str(candidate.application.get("content_hash") or ""),
            grouping="comparison_step",
        )
    else:
        # Manual route: pinned passages from the VERIFIED revision, plus
        # the applicability fact the C07 extension demands.
        from ai.core.integrations.controlled_document_corpus import (
            search_pinned_document,
        )

        try:
            pinned = search_pinned_document(
                user=user,
                document_row=candidate.manual_document,
                query=query,
                top_k=3,
            )
        except Exception:
            raise AnalysisRetrievalIncomplete(
                "comparison_gate_unmet",
                "the verified manual could not be searched",
                facets=("manual_passage",),
            ) from None
        chunks = pinned.get("chunks") or ()
        if not chunks:
            raise AnalysisRetrievalIncomplete(
                "comparison_gate_unmet",
                "no relevant passage in the verified manual",
                facets=("manual_passage",),
            )
        for chunk in chunks:
            citation = chunk.get("citation") or {}
            fact_from_manual_citation(store, citation, retrieval_id=retrieval_id, as_of=as_of)
        try:
            from aichat.services.applicability import applicability_for

            claim_row = applicability_for(candidate.manual_document).first()
        except Exception:
            claim_row = None
        if claim_row is not None:
            fact_from_applicability_claim(store, claim_row, retrieval_id=retrieval_id, as_of=as_of)

    manifest = build_manifest(
        snapshot_id=str(snapshot_label or ""),
        operands=selection.version_rows,
        sources={"work_order": {"completed_at": candidate.completed_at}},
        plan={
            "plan_version": 1,
            "intent": "manual_wo_comparison",
            "route": candidate.route,
            "rule": selection.rule or "explicit_reference",
            "population_type": "work_orders",
        },
        as_of=as_of,
        document_pins=selection.document_pins,
        notes=("row_pinned_only",),
    )
    run.query_plan = manifest

    pending = store.open_evidence_set(
        source_class="work_order",
        filters={"rule": selection.rule or "explicit_reference"},
        population_count=1,
        evaluated_count=1,
        complete_population=True,
        snapshot_hash=manifest.operand_hash,
        high_watermarks={},
        calculation={
            "operation": "comparison_statuses",
            "result": str(statuses["total_steps"]),
        },
    )
    pending.add_member(
        "work_order",
        str(candidate.work_order_id),
        str(candidate.evidence["work_order"].get("updated_at") or ""),
    )
    pending.displayed_count = 1
    values = {status: FactValue("int", count) for status, count in statuses["counts"].items()}
    values["total_steps"] = FactValue("int", statuses["total_steps"])
    values["drift"] = FactValue("bool", candidate.drift)
    store.add_calculation(
        operation="comparison_statuses",
        input_refs=(wo_fact, *step_facts),
        values=values,
        evidence_set_handle=pending.handle,
        complete_population=True,
    )
    store.record_envelope({
        "retrieval_id": retrieval_id,
        "source_class": "work_order",
        "operation": "comparison",
        "coverage": {
            "population_count": 1,
            "returned_count": 1,
            "complete_population": True,
        },
    })
    store.set_primary_coverage({
        "population_count": 1,
        "returned_count": 1,
        "complete_population": True,
        "display_truncated": False,
        "date_field": "actual_completed_at",
        "timezone": timezone.get_current_timezone_name(),
        "filters": [
            f"route: {candidate.route}",
            f"rule: {selection.rule or 'explicit_reference'}",
            *[f"skipped:{pk}:{reason}" for pk, reason in selection.skipped],
        ],
        "as_of": as_of,
        "snapshot_label": snapshot_label,
        "excluded_null_date_count": None,
        "incomplete_reason": None,
    })
    return {"route": candidate.route, "work_order_id": candidate.work_order_id}


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
        record_ids = {
            fact.source_id
            for fact in store.facts.values()
            if fact.source_class == "maintenance_record" and fact.kind == "maintenance_record"
        }
        if record_ids:
            from tasks.ai_analytics import authorized_maintenance_record

            for record_id in record_ids:
                if authorized_maintenance_record(user, record_id) is None:
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
    if intent in ("record_retrieval", "fleet_aggregate", "trend_analysis"):
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
    vocabulary_code: str | None = None
    vocabulary_facets: tuple[str, ...] = ()
    try:
        async with asyncio.timeout(deadline_s):
            if intent == "record_retrieval":
                await service._call_sync(_retrieve_records, user, store, scope=scope)
            elif intent == "manual_fact":
                await service._call_sync(_retrieve_manual, user, store, query=run.routing_content)
            elif intent == "source_inventory":
                await service._call_sync(_retrieve_inventory, user, store)
            elif intent == "fleet_aggregate":
                await service._call_sync(
                    _retrieve_aggregate,
                    user,
                    store,
                    scope=scope,
                    query=run.routing_content,
                    run=run,
                )
            elif intent == "trend_analysis":
                await service._call_sync(
                    _retrieve_trend, user, store, scope=scope, query=run.routing_content, run=run
                )
            elif intent == "manual_wo_comparison":
                await service._call_sync(
                    _retrieve_comparison,
                    user,
                    store,
                    scope=scope,
                    query=run.routing_content,
                    run=run,
                )
    except TimeoutError:
        timed_out = True
    except AnalysisRetrievalIncomplete as exc:
        vocabulary_code = exc.code
        vocabulary_facets = exc.facets
    except Exception:
        logger.warning("analysis retrieval failed", exc_info=True)
        retrieval_failed = True

    if not shadow:
        await _emit_progress(run, "validating_evidence", emitted)

    facet_plan = FACET_PLANS.get(intent, ("records",))
    incomplete_reasons: list[dict[str, str]] = []

    if vocabulary_code is not None:
        # S7: a TYPED honest unavailability (unsupported grouping, series
        # past the bucket cap, population past the membership envelope, a
        # snapshot that would not hold still). Never an estimate — and each
        # code renders its own named message, not the generic abstain.
        from ai.core.i18n_templates import (
            ANALYSIS_BUCKET_RANGE,
            ANALYSIS_COMPARISON_GATE,
            ANALYSIS_GROUPING_UNAVAILABLE,
            ANALYSIS_POPULATION_CAP,
            ANALYSIS_SNAPSHOT_CHANGED,
        )

        message_key = {
            "grouping_unavailable": ANALYSIS_GROUPING_UNAVAILABLE,
            "bucket_range_exceeded": ANALYSIS_BUCKET_RANGE,
            "population_cap_exceeded": ANALYSIS_POPULATION_CAP,
            "snapshot_changed": ANALYSIS_SNAPSHOT_CHANGED,
            "comparison_gate_unmet": ANALYSIS_COMPARISON_GATE,
        }.get(vocabulary_code, ANALYSIS_ABSTAIN)
        response = _template_response(
            kind_state="incomplete",
            message=deterministic_template(message_key, locale),
            reasoning=(
                "The requested analysis is typed as unavailable "
                f"({vocabulary_code}); nothing was estimated."
            ),
            # S9: a gate-unmet outcome names the MISSING facets, not the
            # intent's answer plan.
            incomplete_reasons=[
                {"code": vocabulary_code, "facet": facet}
                for facet in (vocabulary_facets or facet_plan)
            ],
        )
        return _outcome_from_response(
            response,
            turn_state="incomplete",
            store=store,
            scope=scope,
            gate={"verdict": "abstain", "codes": [vocabulary_code]},
            entities=[],
            emitted=emitted,
            claims=[],
            ordinals=None,
            no_data_reason=vocabulary_code,
            persist_evidence=False,
        )

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
