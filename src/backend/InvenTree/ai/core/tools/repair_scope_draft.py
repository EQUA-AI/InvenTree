"""Actor-bound, Work-focused repair proposals on the native confirmation rail."""

import hashlib
import json
from typing import Any

from ai.core.maf_compat import ai_function
from ai.core.tools.read_only import guard_write_tool
from ai.core.tools.repair_scope_schema import (
    ProposedRepairScope,
    scope_planning_requirements,
)
from asgiref.sync import sync_to_async

_PRIOR_PROPOSAL_NOTICE = (
    "Any prior proposal is reconciliation information, not approved source evidence. "
    "Its scope body is omitted from this planning context. Generate a fresh complete "
    "proposal from the current prepared evidence, actual manual passages and planning "
    "requirements. Only an explicit request to review the saved draft should use "
    "get_focused_repair_scope_draft to retrieve its full body."
)


def _planning_draft_metadata(draft):
    """Identify the complete saved record without exposing its proposal body."""
    if draft is None:
        return None
    identity = {
        key: draft[key]
        for key in ("id", "revision", "actor_id", "saved_at", "status", "lifecycle_version")
        if key in draft
    }
    sources = draft.get("sources") or {}
    references = {
        "e03": {
            key: sources["e03"][key] for key in ("id", "sha256") if key in sources.get("e03", {})
        },
        "manuals": [
            {key: row[key] for key in ("id", "sha256") if key in row}
            for row in sources.get("manuals", [])
        ],
    }
    return {
        **identity,
        "draft_sha256": hashlib.sha256(
            json.dumps(draft, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest(),
        "reconciliation": {
            **{
                key: draft[key]
                for key in (
                    "work_order_id",
                    "machine_id",
                    "repair_packet_id",
                    "source_event_id",
                    "component_code",
                )
                if key in draft
            },
            "source_references": references,
        },
        "scope_body_omitted": True,
    }


def _focused_actor(work_order_id=None):
    from ai.core.auth import get_current_principal
    from ai.core.focus.context import current_turn_focus
    from django.contrib.auth import get_user_model
    from django.core.exceptions import PermissionDenied

    principal = get_current_principal()
    focus = current_turn_focus()
    if (
        principal is None
        or focus is None
        or focus.kind != "work_order"
        or focus.work_order_id is None
        or focus.id != focus.work_order_id
        or (work_order_id is not None and work_order_id != focus.work_order_id)
    ):
        raise PermissionDenied("Confirm the exact Work focus before preparing a repair scope.")
    actor = get_user_model().objects.filter(pk=principal.user_pk, is_active=True).first()
    if actor is None:
        raise PermissionDenied("The authenticated Work actor is unavailable.")
    return actor, focus


def _failure(exc):
    return {
        "success": False,
        "executed": False,
        "error": getattr(exc, "code", "repair_scope_unavailable"),
        "detail": "The proposed scope was not saved. Recheck the current Work focus, permissions, version and source references.",
    }


@ai_function
async def get_focused_repair_scope_context() -> dict[str, Any]:
    """Read confirmed Work, current source/E03/manual IDs and prior draft identity.

    Read this before proposing a repair scope. E03 is explicitly prepared exercise
    evidence, not a live inspection. Manuals are related references with unverified
    installed-model/cooler applicability. Never infer that physical work occurred.
    The planning_requirements are source-owned requirements for the proposed
    future plan, not observed facts or approved maintenance instructions. Cover
    every listed post-service category when drafting; an older incomplete draft
    does not waive a category. Preserve unknown applicability and all authority gates.
    Follow manual_method_fidelity for every proposed method and its supplies;
    planning_energy_control_verification is a future prerequisite, not a field result.
    Prior draft metadata is only for reconciliation, never approved source evidence.
    Generate a fresh complete proposal from current sources and planning requirements;
    retrieve the full prior body only for an explicit saved-draft review request.
    """

    def read():
        from django.core.exceptions import PermissionDenied
        from tasks.services.repair_scope_drafts import repair_scope_draft_context
        from tasks.services.work_orders import WorkOrderCommandError

        try:
            actor, focus = _focused_actor()
            current = repair_scope_draft_context(focus.work_order_id, actor)
            return {
                "success": True,
                **current,
                "draft": _planning_draft_metadata(current.get("draft")),
                "prior_proposal_notice": _PRIOR_PROPOSAL_NOTICE,
                "planning_requirements": scope_planning_requirements(),
            }
        except (PermissionDenied, WorkOrderCommandError) as exc:
            return _failure(exc)

    return await sync_to_async(read, thread_sensitive=True)()


@ai_function
async def get_focused_repair_scope_draft() -> dict[str, Any]:
    """Read back the actual saved draft ID/revision/actor/time for confirmed Work.

    A null draft means nothing has been saved. Report its real pending review and
    physical-work gates; never describe this proposal as approved or executed.
    For a foreground voice recap, use one short paragraph of about 45-80 words:
    identify the actual draft ID/revision, briefly summarize the saved scope and
    key tools, zero parts and conditional source applicability, state supervisor
    and site-safety authorization are pending, and identify confirming the fitted
    cooler/manual match as the crew's next planning step before authorization.
    Do not recite every saved bullet unless the user requests the full details.
    This voice presentation guidance never changes the returned full record or
    ordinary text detail; do not invent a shorter replacement scope or approval.
    """

    def read():
        from django.core.exceptions import PermissionDenied
        from tasks.services.repair_scope_drafts import read_repair_scope_draft
        from tasks.services.work_orders import WorkOrderCommandError

        try:
            actor, focus = _focused_actor()
            return {"success": True, **read_repair_scope_draft(focus.work_order_id, actor)}
        except (PermissionDenied, WorkOrderCommandError) as exc:
            return _failure(exc)

    return await sync_to_async(read, thread_sensitive=True)()


@ai_function
async def search_focused_work_manual(
    query: str,
    attachment_id: int | None = None,
    limit: int = 3,
) -> dict[str, Any]:
    """Read literal PDF passages from a real manual attached to confirmed Work/equipment.

    This reads bounded source pages directly; it does not create a corpus entry,
    fitted-model claim or approved procedure. Return the actual source URL, PDF
    page and hash with any quotation. Keep related-reference applicability
    explicitly unverified. Choose a real attachment ID from context when needed.
    Use focused queries for the actual topic, such as heat-exchanger fouling or
    air/air heat-exchanger cleaning. A contents entry or a commissioning/bearing
    passage is not evidence for a cooling-cleaning procedure. Cite only support
    actually present in the returned excerpt; otherwise state the source gap.
    Read all method-specific conditions, restrictions and follow-up handling.
    If the bounded excerpt is incomplete, make a focused follow-up search;
    never fill a missing qualifier from assumption or generic site approval.
    """

    def read():
        from django.core.exceptions import PermissionDenied
        from tasks.services.repair_scope_drafts import search_related_work_manuals
        from tasks.services.work_orders import WorkOrderCommandError

        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 1000
            or type(limit) is not int
            or not 1 <= limit <= 5
            or (attachment_id is not None and (type(attachment_id) is not int or attachment_id < 1))
        ):
            return {
                "success": False,
                "results": [],
                "detail": "Use a bounded manual query and an actual attachment ID.",
            }
        try:
            actor, focus = _focused_actor()
            result = search_related_work_manuals(
                focus.work_order_id, actor, query, attachment_id, limit
            )
            results = [
                {key: value for key, value in row.items() if key != "as_of"}
                for row in result["results"]
            ]
            return {
                "success": True,
                "work_order_id": focus.work_order_id,
                "results": results[:limit],
                "returned_count": min(len(results), limit),
                "applicability": "related_reference_unverified",
                "detail": "Literal attached-PDF excerpts; fitted model and cooler applicability remain unverified.",
            }
        except (PermissionDenied, WorkOrderCommandError, OSError, ValueError) as exc:
            return {**_failure(exc), "results": []}

    return await sync_to_async(read, thread_sensitive=True)()


@ai_function
@guard_write_tool
async def save_proposed_repair_scope_draft(
    work_order_id: int,
    expected_version: int,
    idempotency_key: str,
    machine_id: int,
    repair_packet_id: int,
    source_event_id: int,
    component_code: str,
    e03_attachment_id: int,
    manual_attachment_ids: list[int],
    scope: ProposedRepairScope,
) -> dict[str, Any]:
    """Save a proposed repair scope for the exact confirmed Work after confirmation.

    First read get_focused_repair_scope_context; copy its real IDs and current
    lifecycle_version as expected_version. Use one stable idempotency_key on retries.
    Generate scope from the technician request and cited evidence, with EXACT keys:
    suspected_cause (text), steps, tools_and_supplies, required_ppe,
    isolation_checks, post_work_checks, open_questions (each a nonempty string list),
    replacement_parts (empty list), parts_decision (text). Include alternatives
    and unknown fitted-cooler applicability in suspected_cause/open_questions.
    Parts remain conditional: none are justified by the prepared E03 alone.
    The first planned step must explicitly match the fitted motor and cooler
    construction to the manual. An open question about applicability is not a
    substitute for that prerequisite. Confirm authorized site isolation before
    physical access, then inspect before selecting a cleaning method. Keep each
    method conditional on the verified cooler/material and actual cited passage;
    do not transfer a procedure between different cooler types.
    Preserve ALL source method-specific triggers, material restrictions,
    prohibitions and follow-up handling cautions in the sequence AND its
    tools/supplies entries. Never turn a method permitted for one condition
    into general alternatives. Generic site or chemical approval does not
    replace the manual's conditions. Read complete relevant instructions with
    a focused follow-up search when a bounded excerpt is incomplete; otherwise
    omit that method and record the source gap as an open question.
    All steps are proposed future actions with site authorization and isolation
    checks required before physical work. Supervisor review, site safety,
    physical execution and post-repair verification remain pending/not started.
    Plan identification of applicable energy sources and authorized verification
    of effective energy control and absence of hazardous energy under the
    approved site procedure before access. Permit or lockout/tagout presence
    alone is not verification; never assert that this verification occurred.
    Link only passages that support the specific claim. Do not apply generic
    commissioning, solvent or bearing instructions to a fitted cooler without
    supporting evidence. Tools, supplies, PPE and isolation checks not stated in
    an applicable passage must be labelled proposed planning requirements to be
    confirmed against the site procedure; never claim the manual specifies them.
    Include proposed post-service checks for guards/protection refitted, a valid
    measurement and protection path, and comparison of the winding channels and
    available peers already established in this Work at comparable load. Plan an
    authorized stability check against applicable criteria after service. Do not
    invent channels, measurements, test durations, PASS results or physical work.
    Do not request prepared return-to-service E05/E06 results to build this scope;
    those future exercise checks belong to their separate continuation stage.
    This never approves repair scope, buys parts, records inspection or closes Work.
    Only claim saved after success=True and an actual draft ID/revision is returned.
    """

    def save():
        from django.core.exceptions import PermissionDenied
        from tasks.services.repair_scope_drafts import (
            read_repair_scope_draft,
            save_repair_scope_draft,
        )
        from tasks.services.work_orders import WorkOrderCommandError

        try:
            actor, _ = _focused_actor(work_order_id)
            receipt = save_repair_scope_draft(
                work_order_id=work_order_id,
                actor=actor,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
                machine_id=machine_id,
                repair_packet_id=repair_packet_id,
                source_event_id=source_event_id,
                component_code=component_code,
                e03_attachment_id=e03_attachment_id,
                manual_attachment_ids=manual_attachment_ids,
                scope=scope,
            )
            readback = read_repair_scope_draft(work_order_id, actor)
            return {
                "success": True,
                "executed": True,
                "id": receipt["id"],
                "draft": receipt,
                "readback": readback,
                "detail": "Proposed scope saved; supervisor review and site-safety authorization remain pending. Physical work has not started.",
            }
        except (PermissionDenied, WorkOrderCommandError) as exc:
            return _failure(exc)

    return await sync_to_async(save, thread_sensitive=True)()


REPAIR_SCOPE_READ_TOOLS = [
    get_focused_repair_scope_context,
    get_focused_repair_scope_draft,
    search_focused_work_manual,
]
REPAIR_SCOPE_DRAFT_TOOLS = [*REPAIR_SCOPE_READ_TOOLS, save_proposed_repair_scope_draft]
