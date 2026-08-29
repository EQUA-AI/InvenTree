"""
Kanban Card AI-Function Tools (read-only).

@ai_function decorated READ tools for the AI chat agent: list, get, summary
and stock check. The direct-ORM write tools were retired (execution-plan S12
step 3) — board mutations from chat/voice go through the governed proposal
rail and the REST surface only.

Uses Django ORM via sync_to_async for database access.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: S5b: fields deliberately withheld from the card projection, mirroring the
#: ``tasks.ai_read.EXCLUDED_FIELDS`` decisions — this module historically
#: dumped raw model fields and was the one AI-exposed work-order projection
#: outside the allow-list discipline (recon finding, WP-A4). Pinned by
#: ``tasks/tests/test_kanban_tool_scope.py``.
EXCLUDED_FIELDS = {
    "WorkOrder.company": "tenant identity (mirrors Client.name exclusion)",
    "WorkOrder.company_contact_name": "personal data",
    "WorkOrder.company_contact_phone": "personal data",
    "WorkOrder.service_quote": "commercial value",
    "WorkOrder.assignee": "identity; presence/role only (Q15)",
}


def _card_to_dict(work_order, include_parts: bool = True) -> dict[str, Any]:
    """Serialize a WorkOrder to the ALLOW-LISTED card projection (S5b).

    Free text rides the shared fence; commercial/contact/identity fields are
    withheld per ``EXCLUDED_FIELDS``.
    """
    from tasks.ai_read import fence

    data = {
        "id": work_order.id,
        "title": fence(work_order.title, limit=255),
        # A16/Q14: fenced and capped; embedded instructions stay data.
        "description": fence(work_order.description) or None,
        "status": work_order.status,
        "priority": work_order.priority,
        "due_date": work_order.due_date.isoformat() if work_order.due_date else None,
        # S5b (Q15): presence, never the recorded free-text name.
        "assigned": bool((work_order.assignee or "").strip()),
        "tags": list(work_order.tags) if work_order.tags else [],
        "job_number": work_order.job_number,
        "is_active": work_order.is_active,
        "created_at": work_order.created_at.isoformat() if work_order.created_at else None,
        "updated_at": work_order.updated_at.isoformat() if work_order.updated_at else None,
    }
    if include_parts:
        data["parts"] = [
            _card_part_to_dict(cp)
            for cp in work_order.work_order_parts.all().select_related("part")
        ]
    return data


def _card_part_to_dict(work_order_part) -> dict[str, Any]:
    """Serialize a WorkOrderPart to a plain dict."""
    return {
        "id": work_order_part.id,
        "part_id": work_order_part.part_id,
        "part_name": work_order_part.part.name if work_order_part.part else "",
        "quantity": float(work_order_part.quantity),
        "allocated_quantity": float(work_order_part.allocated_quantity),
        "allocation_status": work_order_part.allocation_status,
        "allocation_note": work_order_part.allocation_note,
    }


def _get_model():
    """Lazy import to avoid Django app-not-ready errors."""
    from tasks.models import WorkOrder

    return WorkOrder


def _get_card_part_model():
    """Lazy import for WorkOrderPart."""
    from tasks.models import WorkOrderPart

    return WorkOrderPart


def _scoped_cards():
    """Return the acting user's work orders, never everyone's.

    These tools historically read ``WorkOrder.objects.all()`` behind a global
    ``work_order:view`` grant -- a cross-tenant read. Every read tool now
    starts from the same fail-closed predicate the canonical work-order API
    applies. An actor whose maintenance scope cannot be resolved sees an
    empty board rather than the whole plant's.
    """
    from ai.core.auth import get_current_principal
    from django.contrib.auth import get_user_model
    from tasks.scope import ScopeError, work_order_scope_filter

    WorkOrder = _get_model()
    principal = get_current_principal()
    if principal is None:
        return WorkOrder.objects.none()
    user = get_user_model().objects.filter(pk=principal.user_pk).first()
    if user is None:
        return WorkOrder.objects.none()
    try:
        return WorkOrder.objects.filter(work_order_scope_filter(user))
    except ScopeError:
        return WorkOrder.objects.none()


# ---------------------------------------------------------------------------
# READ tools
# ---------------------------------------------------------------------------


@ai_function
async def list_kanban_cards(
    status: str | None = None,
    priority: str | None = None,
    assignee: str | None = None,
    company: str | None = None,
    job_number: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    include_archived: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """
    List Kanban cards with optional filters.

    Filters:
      - status: 'backlog', 'in-progress', 'review', or 'done'
      - priority: 'low', 'medium', or 'high'
      - assignee: name of the person assigned
      - company: company name
      - job_number: job/work-order number
      - tag: a single tag to filter by
      - search: free-text search across title, description, assignee, company
      - include_archived: if True, also return archived (inactive) cards
      - limit: max number of cards to return (default 50)

    Returns:
      Dictionary with 'cards' (the returned page), 'returned_count', and
      'population_count' (ALL matching cards — use this, never the page
      length, when stating how many exist), plus a 'retrieval' envelope.
    """

    @sync_to_async
    def _query():
        qs = _scoped_cards()

        if not include_archived:
            qs = qs.filter(is_active=True)

        if status:
            qs = qs.filter(status=status)
        if priority:
            qs = qs.filter(priority=priority)
        if assignee:
            qs = qs.filter(assignee__icontains=assignee)
        if company:
            qs = qs.filter(company__icontains=company)
        if job_number:
            qs = qs.filter(job_number__icontains=job_number)
        if tag:
            from tasks.json_lookups import filter_json_array_contains

            qs = filter_json_array_contains(qs, "tags", tag)
        if search:
            from django.db.models import Q

            qs = qs.filter(
                Q(title__icontains=search)
                | Q(description__icontains=search)
                | Q(assignee__icontains=search)
                | Q(company__icontains=search)
                | Q(job_number__icontains=search)
            )

        population_count = qs.count()
        page = qs.order_by("-created_at")[:limit]
        return population_count, [_card_to_dict(c) for c in page]

    population_count, cards = await _query()
    from ai.core.contracts.retrieval import build_envelope, coverage, record_envelope

    envelope = build_envelope(
        source_class="kanban_card",
        population_type="work_orders",
        operation="list",
        filters={"query_applied": bool(search or status or priority or assignee or company)},
        coverage=coverage(
            population_count=population_count,
            returned_count=len(cards),
            complete_population=population_count == len(cards),
        ),
    )
    record_envelope("list_kanban_cards", envelope)
    return {
        "returned_count": len(cards),
        "population_count": population_count,
        "display_truncated": population_count > len(cards),
        "cards": cards,
        "retrieval": envelope,
    }


@ai_function
async def get_kanban_card(work_order_id: int) -> dict[str, Any]:
    """
    Get full details of a single Kanban card by its ID.

    Args:
      card_id: The primary key of the card.

    Returns:
      Card details dict, or error if not found.
    """
    WorkOrder = _get_model()

    @sync_to_async
    def _fetch():
        try:
            work_order = _scoped_cards().get(pk=work_order_id)
            return _card_to_dict(work_order)
        except WorkOrder.DoesNotExist:
            return None

    work_order = await _fetch()
    if work_order is None:
        # One message whether the card is missing or another tenant's.
        return {"error": f"Kanban card {work_order_id} not found."}
    return work_order


# ---------------------------------------------------------------------------
# Stock / summary READ tools
# ---------------------------------------------------------------------------


@ai_function
async def check_kanban_card_stock(work_order_id: int) -> dict[str, Any]:
    """
    Re-check stock availability for all parts on a Kanban card.

    Useful to refresh allocation status after stock changes.

    Args:
      card_id: The card ID.

    Returns:
      Dict with allocation results per part and any warnings.
    """
    WorkOrder = _get_model()

    @sync_to_async
    def _check():
        try:
            work_order = _scoped_cards().get(pk=work_order_id)
        except WorkOrder.DoesNotExist:
            return {"error": f"Kanban card {work_order_id} not found."}

        work_order_parts = work_order.work_order_parts.all().select_related("part")
        if not work_order_parts.exists():
            return {
                "card_id": work_order_id,
                "message": "No parts attached to this card.",
                "parts": [],
                "warnings": [],
            }

        results = []
        warnings = []

        for cp in work_order_parts:
            # Compute-only: this tool is registered read-only (voice Tier-1
            # lane, kanban.read pack), so it must not persist allocation state
            alloc = cp.check_and_allocate(persist=False)
            results.append(alloc)

            if alloc["allocation_status"] == "insufficient":
                warnings.append(
                    f"⚠ Part '{alloc['part_name']}' (ID {alloc['part_id']}): "
                    f"need {alloc['quantity_needed']}, NO stock available"
                )
            elif alloc["allocation_status"] == "partial":
                warnings.append(
                    f"⚠ Part '{alloc['part_name']}' (ID {alloc['part_id']}): "
                    f"only {alloc['quantity_available']} of "
                    f"{alloc['quantity_needed']} available"
                )

        return {
            "card_id": work_order_id,
            "parts": results,
            "warnings": warnings,
            "all_allocated": len(warnings) == 0,
        }

    return await _check()


@ai_function
async def get_kanban_summary() -> dict[str, Any]:
    """
    Get a summary of the Kanban board: counts by status and priority,
    plus a list of overdue cards.

    Returns:
      Dictionary with status_counts, priority_counts, total_active, and overdue_cards.
    """

    @sync_to_async
    def _summarize():
        import datetime

        from django.db.models import Count

        active = _scoped_cards().filter(is_active=True)

        status_counts = dict(
            active.values_list("status").annotate(c=Count("id")).values_list("status", "c")
        )
        priority_counts = dict(
            active.values_list("priority").annotate(c=Count("id")).values_list("priority", "c")
        )

        today = datetime.date.today()
        overdue = active.filter(due_date__lt=today).exclude(status="done")
        overdue_count = overdue.count()
        overdue_cards = [_card_to_dict(c) for c in overdue[:20]]

        return {
            "total_active": active.count(),
            "status_counts": status_counts,
            "priority_counts": priority_counts,
            # S5 coverage vocabulary: the POPULATION count, not the page.
            "overdue_count": overdue_count,
            "overdue_returned": len(overdue_cards),
            "overdue_cards": overdue_cards,
        }

    return await _summarize()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

# Read-only subset — safe for hands-free voice (Tier-1). No card mutations.
KANBAN_READ_TOOLS = [
    list_kanban_cards,
    get_kanban_card,
    get_kanban_summary,
    check_kanban_card_stock,
]

# The seven direct-ORM write tools (create/update/move/archive/restore/
# add_parts/remove_part) and the withheld hard delete were REMOVED
# (execution-plan S12 step 3, after the governed-flag soak): chat/voice board
# mutations go exclusively through the governed proposal rail and the REST
# surface, which carry scope, expected-version fencing, confirmation and
# durable audit. The invariant is enforced by absence — do not re-add a write
# tool here; add a governed command instead.
KANBAN_TOOLS = list(KANBAN_READ_TOOLS)

__all__ = ["KANBAN_READ_TOOLS", "KANBAN_TOOLS"]
