"""The S9 cross-source comparison eligibility gate (§8.5).

Before any manual/work-order comparison synthesis, ALL of the gate's
checks must hold — and an insufficient candidate is never "used anyway":

1. a SPECIFIC in-scope work order (an explicit ``WO-…`` reference) or the
   deterministic candidate-selection rule (``tasks.ai_analytics``);
2. the fields the comparison requires (a real completion timestamp);
3. structured procedure evidence preferred — the applied revision
   snapshot — else a VERIFIED-applicable manual revision historically
   effective at ``actual_completed_at`` (the S8b effective window);
4. manual passages with coordinates (retrieved by the executor for the
   manual route; zero passages fails the gate there, not here);
5. complete record coverage for any frequency premise — v1 does not have
   that coverage wired, so a frequency/interval premise is an honest
   ``comparison_gate_unmet`` naming the missing facet, never a partial
   compliance story;
6. ONE snapshot manifest spanning record evidence and document pins —
   assembled by the executor from this gate's version rows.

The candidate loop tries the rule's next candidate when one lacks a
required facet; every skip carries its typed reason into the wire.

Module-level code is island-safe: Django and tasks imports happen inside
``evaluate_comparison_gate`` only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Frequency/interval premises need complete-coverage math (§8.5 check 5).
_FREQUENCY_PREMISE = re.compile(
    r"\b(?:every|how often|frequency|intervals?|overdue|on schedule|"
    r"per (?:day|week|month|quarter|year))\b",
    re.IGNORECASE,
)

_WO_REFERENCE = re.compile(r"\bWO[-\s]?0*(\d{1,7})\b", re.IGNORECASE)


def explicit_work_order_pk(text: str) -> int | None:
    """Parse an explicit ``WO-000123`` reference (``reference`` == zero-padded pk)."""
    match = _WO_REFERENCE.search(str(text or ""))
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class ComparisonCandidate:
    """One work order that passed every gate check, with its evidence."""

    work_order_id: int
    route: str  # "structured" | "verified_manual"
    evidence: dict[str, Any]
    application: dict[str, Any] | None
    steps: dict[str, Any] | None
    deviations: dict[str, Any]
    manual_document: Any | None
    drift: bool
    completed_at: str


@dataclass(frozen=True)
class ComparisonSelection:
    """The gate's outcome: a candidate, or the named missing facets."""

    candidate: ComparisonCandidate | None
    rule: str | None
    explicit_reference: bool
    skipped: tuple[tuple[int, str], ...] = ()
    missing_facets: tuple[str, ...] = ()
    version_rows: tuple[tuple[Any, Any], ...] = ()
    document_pins: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def _gate_unmet(
    *,
    rule: str | None,
    explicit: bool,
    skipped: list[tuple[int, str]],
    facets: tuple[str, ...],
) -> ComparisonSelection:
    return ComparisonSelection(
        candidate=None,
        rule=rule,
        explicit_reference=explicit,
        skipped=tuple(skipped),
        missing_facets=facets,
    )


def evaluate_comparison_gate(user: Any, *, query: str, scope: Any = None) -> ComparisonSelection:
    """Run the §8.5 gate; Django-side (call through the executor's sync seam)."""
    from tasks.ai_analytics import select_comparison_candidate
    from tasks.ai_read import (
        authorized_work_order,
        work_order_deviations,
        work_order_procedure_application,
        work_order_step_executions,
    )

    if _FREQUENCY_PREMISE.search(str(query or "")):
        # Check 5: no complete frequency coverage is wired in v1 — the
        # honest outcome is the named unmet facet, never a partial story.
        return _gate_unmet(
            rule=None,
            explicit=False,
            skipped=[],
            facets=("complete_frequency_coverage",),
        )

    explicit_pk = explicit_work_order_pk(query)
    rule: str | None = None
    if explicit_pk is not None:
        candidates: list[int] = [explicit_pk]
    else:
        machine_id = None
        machine_ids = tuple(getattr(scope, "machine_ids", ()) or ())
        if (
            getattr(scope, "explicit", False)
            and getattr(scope, "enforce", False)
            and len(machine_ids) == 1
        ):
            machine_id = machine_ids[0]
        selection = select_comparison_candidate(user, machine_id=machine_id)
        if not selection.get("available"):
            return _gate_unmet(
                rule=None,
                explicit=False,
                skipped=[],
                facets=("candidate_selection_unavailable",),
            )
        rule = str(selection.get("rule"))
        candidates = [int(pk) for pk in selection.get("candidates") or ()]

    skipped: list[tuple[int, str]] = []
    for candidate_pk in candidates:
        work_order = authorized_work_order(user, candidate_pk)
        if work_order is None:
            skipped.append((candidate_pk, "not_found_or_unauthorized"))
            continue
        if work_order.actual_completed_at is None:
            skipped.append((candidate_pk, "missing_completion_timestamp"))
            continue

        from tasks.ai_analytics import get_maintenance_evidence

        evidence = get_maintenance_evidence(user, candidate_pk)
        if not evidence.get("available"):
            skipped.append((candidate_pk, "evidence_unavailable"))
            continue
        deviations = work_order_deviations(work_order)
        completed_at = work_order.actual_completed_at.isoformat()

        application = work_order_procedure_application(work_order)
        if application is not None:
            from tasks.procedure_models import WorkOrderProcedureApplication

            application_obj = WorkOrderProcedureApplication.objects.get(
                pk=application["application_id"]
            )
            steps = work_order_step_executions(application_obj)
            version_rows: list[tuple[Any, Any]] = [
                (f"work_order:{work_order.pk}", work_order.updated_at.isoformat()),
                (
                    f"procedure_application:{application['application_id']}",
                    f"{application['policy_version']}:{application['applied_at']}",
                ),
            ]
            version_rows.extend(
                (f"step:{step['step_key']}", str(step["version"])) for step in steps["steps"]
            )
            return ComparisonSelection(
                candidate=ComparisonCandidate(
                    work_order_id=work_order.pk,
                    route="structured",
                    evidence=evidence,
                    application=application,
                    steps=steps,
                    deviations=deviations,
                    manual_document=None,
                    drift=str(application.get("drift_status")) != "current",
                    completed_at=completed_at,
                ),
                rule=rule,
                explicit_reference=explicit_pk is not None,
                skipped=tuple(skipped),
                version_rows=tuple(version_rows),
                document_pins=(
                    {
                        "kind": "procedure_revision",
                        "revision_id": application["revision_id"],
                        "content_hash": application.get("content_hash") or "",
                    },
                ),
            )

        # Manual route: a VERIFIED-applicable revision historically
        # effective at the completion date (check 3's second leg).
        if work_order.machine_id is None:
            skipped.append((candidate_pk, "no_procedure_or_verified_manual"))
            continue
        try:
            from aichat.services.applicability import verified_documents_for_machines

            documents = verified_documents_for_machines(
                [work_order.machine_id],
                on_date=work_order.actual_completed_at.date(),
            )
        except Exception:
            documents = []
        if not documents:
            skipped.append((candidate_pk, "no_procedure_or_verified_manual"))
            continue
        document = documents[0]
        return ComparisonSelection(
            candidate=ComparisonCandidate(
                work_order_id=work_order.pk,
                route="verified_manual",
                evidence=evidence,
                application=None,
                steps=None,
                deviations=deviations,
                manual_document=document,
                drift=False,
                completed_at=completed_at,
            ),
            rule=rule,
            explicit_reference=explicit_pk is not None,
            skipped=tuple(skipped),
            version_rows=((f"work_order:{work_order.pk}", work_order.updated_at.isoformat()),),
            document_pins=(
                {
                    "kind": "controlled_document",
                    "document_id": document.document_id,
                    "revision": document.revision,
                    "content_hash": document.source_sha256 or "",
                },
            ),
        )

    facets: tuple[str, ...] = tuple(dict.fromkeys(reason for _pk, reason in skipped)) or (
        "eligible_candidate",
    )
    return _gate_unmet(rule=rule, explicit=explicit_pk is not None, skipped=skipped, facets=facets)


#: The ONLY statuses a comparison may state (§8.5). Compliance verdicts
#: are structurally absent — no status and no template can say one.
COMPARISON_STATUSES = (
    "documented_match",
    "documented_deviation",
    "possible_documented_alignment",
    "not_recorded",
    "not_applicable",
    "cannot_determine",
)


def derive_step_statuses(candidate: ComparisonCandidate) -> dict[str, Any]:
    """Deterministic status derivation, ordered Application → Steps → Deviations.

    Pure server logic — no model choice anywhere. A ``documented_match``
    or ``documented_deviation`` can only come from a PRESENT structured
    record (a completed/failed execution or an explicit deviation row);
    a step with no recorded execution is ``not_recorded``, which is NOT
    noncompliance (the renderer pins that sentence). The prose fallback
    route derives nothing: the §8.5 fallback is deliberately
    ``cannot_determine``-heavy, so it tallies exactly one
    ``cannot_determine``.

    A drifted application snapshot keeps its statuses — they compare the
    record against the revision AS APPLIED — and the answer carries the
    drift limitation note instead of silently re-anchoring.
    """
    counts: dict[str, int] = dict.fromkeys(COMPARISON_STATUSES, 0)
    if candidate.route != "structured" or candidate.steps is None:
        counts["cannot_determine"] = 1
        return {"rows": [], "counts": counts, "total_steps": 0}

    deviation_steps = {
        entry["step_key"]
        for entry in candidate.deviations.get("deviations") or ()
        if entry.get("step_key")
    }
    rows: list[dict[str, Any]] = []
    for step in candidate.steps.get("steps") or ():
        if (
            step["step_key"] in deviation_steps
            or step["status"] == "failed"
            or step["passed"] is False
        ):
            status = "documented_deviation"
        elif step["status"] == "completed":
            status = "documented_match"
        elif step["status"] == "not_applicable":
            status = "not_applicable"
        else:
            status = "not_recorded"
        counts[status] += 1
        rows.append({"key": str(step["sequence"]), "status": status})
    return {"rows": rows, "counts": counts, "total_steps": len(rows)}


__all__ = [
    "COMPARISON_STATUSES",
    "ComparisonCandidate",
    "ComparisonSelection",
    "derive_step_statuses",
    "evaluate_comparison_gate",
    "explicit_work_order_pk",
]
