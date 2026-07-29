"""
Kanban Card AI-Function Tools

@ai_function decorated tools for the AI chat agent to manage Kanban cards.
Provides full CRUD: list, get, create, update, move (status change), and delete/archive.

Uses Django ORM via sync_to_async for database access.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function
from ai.core.tools.read_only import guard_write_tool
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _card_to_dict(work_order, include_parts: bool = True) -> dict[str, Any]:
    """Serialize a WorkOrder model instance to a plain dict."""
    data = {
        "id": work_order.id,
        "title": work_order.title,
        "description": work_order.description,
        "status": work_order.status,
        "priority": work_order.priority,
        "due_date": work_order.due_date.isoformat() if work_order.due_date else None,
        "assignee": work_order.assignee,
        "tags": list(work_order.tags) if work_order.tags else [],
        "company": work_order.company,
        "company_contact_name": work_order.company_contact_name,
        "company_contact_phone": work_order.company_contact_phone,
        "job_number": work_order.job_number,
        "service_quote": work_order.service_quote,
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
      Dictionary with 'count' and 'cards' list.
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

        qs = qs.order_by("-created_at")[:limit]
        return [_card_to_dict(c) for c in qs]

    work_orders = await _query()
    return {"count": len(work_orders), "cards": work_orders}


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
# CREATE tool
# ---------------------------------------------------------------------------


@ai_function
@guard_write_tool
async def create_kanban_card(
    title: str,
    status: str = "backlog",
    priority: str = "medium",
    description: str = "",
    assignee: str = "",
    due_date: str | None = None,
    tags: list[str] | None = None,
    company: str = "",
    company_contact_name: str = "",
    company_contact_phone: str = "",
    job_number: str = "",
    service_quote: str = "",
    parts: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Create a new Kanban card, optionally with parts that need stock allocation.

    Args:
      title: Card title (required).
      status: One of 'backlog', 'in-progress', 'review', 'done'. Default 'backlog'.
      priority: One of 'low', 'medium', 'high'. Default 'medium'.
      description: Longer description text.
      assignee: Person assigned to the card.
      due_date: Due date in ISO format (YYYY-MM-DD), or null.
      tags: List of tag strings.
      company: Company name related to this card.
      company_contact_name: Contact person at the company.
      company_contact_phone: Contact phone number.
      job_number: Job or work-order number.
      service_quote: Service quote reference.
      parts: Optional list of parts needed. Each item is a dict with 'part_id' (int)
             and optional 'quantity' (float, default 1). Stock will be checked
             automatically and allocation status returned.

    Returns:
      The newly created card as a dict, including parts allocation results
      and any stock warnings.
    """
    WorkOrder = _get_model()
    WorkOrderPart = _get_card_part_model()
    valid_statuses = {"backlog", "in-progress", "review", "done"}
    valid_priorities = {"low", "medium", "high"}

    if status not in valid_statuses:
        return {"error": f"Invalid status '{status}'. Must be one of {sorted(valid_statuses)}."}
    if priority not in valid_priorities:
        return {
            "error": f"Invalid priority '{priority}'. Must be one of {sorted(valid_priorities)}."
        }

    @sync_to_async
    def _create():
        import datetime
        from decimal import Decimal, InvalidOperation

        from django.db import transaction

        parsed_due = None
        if due_date:
            try:
                parsed_due = datetime.date.fromisoformat(due_date)
            except ValueError:
                return {"error": f"Invalid due_date format '{due_date}'. Use YYYY-MM-DD."}

        # Validate the parts payload up front - the model may pass malformed
        # entries, and failing after the card row is committed would leave an
        # orphan card behind
        normalized_parts = []
        for part_entry in parts or []:
            if not isinstance(part_entry, dict):
                return {
                    "error": f"Invalid parts entry {part_entry!r}: expected an object with part_id/quantity."
                }
            try:
                qty = Decimal(str(part_entry.get("quantity", 1)))
            except InvalidOperation:
                return {
                    "error": f"Invalid quantity {part_entry.get('quantity')!r} for part {part_entry.get('part_id')!r}."
                }
            normalized_parts.append((part_entry.get("part_id"), qty))

        # Card + part links commit together - a failure while linking parts
        # must not leave an orphan card behind
        with transaction.atomic():
            work_order = WorkOrder.objects.create(
                title=title,
                status=status,
                priority=priority,
                description=description,
                assignee=assignee,
                due_date=parsed_due,
                tags=tags or [],
                company=company,
                company_contact_name=company_contact_name,
                company_contact_phone=company_contact_phone,
                job_number=job_number,
                service_quote=service_quote,
            )

            allocation_warnings = []

            if normalized_parts:
                from part.models import Part

                for part_id, qty in normalized_parts:
                    try:
                        part_obj = Part.objects.get(pk=part_id)
                    except Part.DoesNotExist:
                        allocation_warnings.append(f"Part ID {part_id} not found — skipped.")
                        continue

                    work_order_part, created = WorkOrderPart.objects.get_or_create(
                        work_order=work_order,
                        part=part_obj,
                        defaults={"quantity": qty},
                    )
                    if not created:
                        work_order_part.quantity = qty
                        work_order_part.save(update_fields=["quantity", "updated_at"])

                    result = work_order_part.check_and_allocate()

                    if result["allocation_status"] == "insufficient":
                        allocation_warnings.append(
                            f"⚠ Part '{result['part_name']}' (ID {part_id}): "
                            f"need {result['quantity_needed']}, NO stock available"
                        )
                    elif result["allocation_status"] == "partial":
                        allocation_warnings.append(
                            f"⚠ Part '{result['part_name']}' (ID {part_id}): "
                            f"only {result['quantity_available']} of "
                            f"{result['quantity_needed']} available"
                        )

        result = _card_to_dict(work_order)
        if allocation_warnings:
            result["stock_warnings"] = allocation_warnings
        return result

    return await _create()


# ---------------------------------------------------------------------------
# UPDATE tool
# ---------------------------------------------------------------------------


@ai_function
@guard_write_tool
async def update_kanban_card(
    work_order_id: int,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    assignee: str | None = None,
    due_date: str | None = None,
    tags: list[str] | None = None,
    company: str | None = None,
    company_contact_name: str | None = None,
    company_contact_phone: str | None = None,
    job_number: str | None = None,
    service_quote: str | None = None,
) -> dict[str, Any]:
    """
    Update one or more fields on an existing Kanban card.

    Only the fields you provide will be changed; omitted fields are left as-is.

    Args:
      card_id: The ID of the card to update (required).
      title: New title.
      description: New description.
      status: New status ('backlog', 'in-progress', 'review', 'done').
      priority: New priority ('low', 'medium', 'high').
      assignee: New assignee name.
      due_date: New due date (YYYY-MM-DD) or empty string to clear.
      tags: Replace tags list entirely.
      company: New company name.
      company_contact_name: New contact name.
      company_contact_phone: New contact phone.
      job_number: New job number.
      service_quote: New service quote reference.

    Returns:
      The updated card as a dict, or error.
    """
    WorkOrder = _get_model()
    valid_statuses = {"backlog", "in-progress", "review", "done"}
    valid_priorities = {"low", "medium", "high"}

    if status is not None and status not in valid_statuses:
        return {"error": f"Invalid status '{status}'. Must be one of {sorted(valid_statuses)}."}
    if priority is not None and priority not in valid_priorities:
        return {
            "error": f"Invalid priority '{priority}'. Must be one of {sorted(valid_priorities)}."
        }

    @sync_to_async
    def _update():
        import datetime

        try:
            work_order = WorkOrder.objects.get(pk=work_order_id)
        except WorkOrder.DoesNotExist:
            return {"error": f"Kanban card {work_order_id} not found."}

        if title is not None:
            work_order.title = title
        if description is not None:
            work_order.description = description
        if status is not None:
            work_order.status = status
        if priority is not None:
            work_order.priority = priority
        if assignee is not None:
            work_order.assignee = assignee
        if due_date is not None:
            if due_date == "":
                work_order.due_date = None
            else:
                try:
                    work_order.due_date = datetime.date.fromisoformat(due_date)
                except ValueError:
                    return {"error": f"Invalid due_date format '{due_date}'. Use YYYY-MM-DD."}
        if tags is not None:
            # De-duplicate while preserving order
            seen = set()
            work_order.tags = [t for t in tags if not (t in seen or seen.add(t))]
        if company is not None:
            work_order.company = company
        if company_contact_name is not None:
            work_order.company_contact_name = company_contact_name
        if company_contact_phone is not None:
            work_order.company_contact_phone = company_contact_phone
        if job_number is not None:
            work_order.job_number = job_number
        if service_quote is not None:
            work_order.service_quote = service_quote

        work_order.save()
        return _card_to_dict(work_order)

    return await _update()


# ---------------------------------------------------------------------------
# MOVE (status change shortcut)
# ---------------------------------------------------------------------------


@ai_function
@guard_write_tool
async def move_kanban_card(
    work_order_id: int,
    new_status: str,
) -> dict[str, Any]:
    """
    Move a Kanban card to a different column/status.

    This is a convenience shortcut for changing just the status.

    Args:
      card_id: The card ID.
      new_status: Target status — 'backlog', 'in-progress', 'review', or 'done'.

    Returns:
      The updated card, or error.
    """
    valid = {"backlog", "in-progress", "review", "done"}
    if new_status not in valid:
        return {"error": f"Invalid status '{new_status}'. Must be one of {sorted(valid)}."}

    return await update_kanban_card(work_order_id=work_order_id, status=new_status)


# ---------------------------------------------------------------------------
# DELETE / ARCHIVE tools
# ---------------------------------------------------------------------------


@ai_function
@guard_write_tool
async def archive_kanban_card(work_order_id: int) -> dict[str, Any]:
    """
    Archive (soft-delete) a Kanban card. The card is marked inactive
    and hidden from the default board view but can be restored later.

    Args:
      card_id: The card ID to archive.

    Returns:
      Confirmation dict or error.
    """
    WorkOrder = _get_model()

    @sync_to_async
    def _archive():
        try:
            work_order = WorkOrder.objects.get(pk=work_order_id)
        except WorkOrder.DoesNotExist:
            return {"error": f"Kanban card {work_order_id} not found."}

        if not work_order.is_active:
            return {
                "message": f"Card {work_order_id} is already archived.",
                "card": _card_to_dict(work_order),
            }

        work_order.is_active = False
        work_order.save(update_fields=["is_active", "updated_at"])
        return {
            "message": f"Card {work_order_id} ('{work_order.title}') archived.",
            "card": _card_to_dict(work_order),
        }

    return await _archive()


@ai_function
@guard_write_tool
async def restore_kanban_card(work_order_id: int) -> dict[str, Any]:
    """
    Restore a previously archived Kanban card, making it active again.

    Args:
      card_id: The card ID to restore.

    Returns:
      The restored card dict or error.
    """
    WorkOrder = _get_model()

    @sync_to_async
    def _restore():
        try:
            work_order = WorkOrder.objects.get(pk=work_order_id)
        except WorkOrder.DoesNotExist:
            return {"error": f"Kanban card {work_order_id} not found."}

        if work_order.is_active:
            return {
                "message": f"Card {work_order_id} is already active.",
                "card": _card_to_dict(work_order),
            }

        work_order.is_active = True
        work_order.save(update_fields=["is_active", "updated_at"])
        return {
            "message": f"Card {work_order_id} ('{work_order.title}') restored.",
            "card": _card_to_dict(work_order),
        }

    return await _restore()


@ai_function
@guard_write_tool
async def delete_kanban_card(work_order_id: int) -> dict[str, Any]:
    """
    Permanently delete a Kanban card. This cannot be undone.
    Prefer archive_kanban_card for soft-deletion.

    Args:
      card_id: The card ID to permanently delete.

    Returns:
      Confirmation dict or error.
    """
    WorkOrder = _get_model()

    @sync_to_async
    def _delete():
        try:
            work_order = WorkOrder.objects.get(pk=work_order_id)
        except WorkOrder.DoesNotExist:
            return {"error": f"Kanban card {work_order_id} not found."}

        title = work_order.title
        work_order.delete()
        return {"message": f"Card {work_order_id} ('{title}') permanently deleted."}

    return await _delete()


# ---------------------------------------------------------------------------
# PARTS & STOCK tools
# ---------------------------------------------------------------------------


@ai_function
@guard_write_tool
async def add_parts_to_kanban_card(
    work_order_id: int,
    parts: list[dict],
) -> dict[str, Any]:
    """
    Add one or more parts to an existing Kanban card and check stock availability.

    Args:
      card_id: The card ID.
      parts: List of parts to add. Each is a dict with:
        - part_id (int): The Part primary key (required).
        - quantity (float): How many are needed (default 1).

    Returns:
      Dict with added parts, allocation results, and any warnings.
    """
    WorkOrder = _get_model()
    WorkOrderPart = _get_card_part_model()

    @sync_to_async
    def _add():
        from decimal import Decimal

        from part.models import Part

        try:
            work_order = WorkOrder.objects.get(pk=work_order_id)
        except WorkOrder.DoesNotExist:
            return {"error": f"Kanban card {work_order_id} not found."}

        results = []
        warnings = []

        for entry in parts:
            part_id = entry.get("part_id")
            qty = Decimal(str(entry.get("quantity", 1)))

            try:
                part_obj = Part.objects.get(pk=part_id)
            except Part.DoesNotExist:
                warnings.append(f"Part ID {part_id} not found — skipped.")
                continue

            work_order_part, created = WorkOrderPart.objects.get_or_create(
                work_order=work_order,
                part=part_obj,
                defaults={"quantity": qty},
            )
            if not created:
                work_order_part.quantity = qty
                work_order_part.save(update_fields=["quantity", "updated_at"])

            alloc = work_order_part.check_and_allocate()
            results.append(alloc)

            if alloc["allocation_status"] == "insufficient":
                warnings.append(
                    f"⚠ Part '{alloc['part_name']}' (ID {part_id}): "
                    f"need {alloc['quantity_needed']}, NO stock available"
                )
            elif alloc["allocation_status"] == "partial":
                warnings.append(
                    f"⚠ Part '{alloc['part_name']}' (ID {part_id}): "
                    f"only {alloc['quantity_available']} of "
                    f"{alloc['quantity_needed']} available"
                )

        return {
            "card_id": work_order_id,
            "parts_added": len(results),
            "allocation_results": results,
            "warnings": warnings,
            "all_allocated": len(warnings) == 0,
        }

    return await _add()


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
@guard_write_tool
async def remove_part_from_kanban_card(
    work_order_id: int,
    part_id: int,
) -> dict[str, Any]:
    """
    Remove a part from a Kanban card.

    Args:
      card_id: The card ID.
      part_id: The Part primary key to remove.

    Returns:
      Confirmation or error.
    """
    WorkOrderPart = _get_card_part_model()

    @sync_to_async
    def _remove():
        try:
            cp = WorkOrderPart.objects.get(work_order_id=work_order_id, part_id=part_id)
        except WorkOrderPart.DoesNotExist:
            return {"error": f"Part {part_id} is not attached to card {work_order_id}."}

        part_name = cp.part.name
        cp.delete()
        return {"message": f"Part '{part_name}' (ID {part_id}) removed from card {work_order_id}."}

    return await _remove()


# ---------------------------------------------------------------------------
# SUMMARY tool
# ---------------------------------------------------------------------------


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
        overdue_cards = [_card_to_dict(c) for c in overdue[:20]]

        return {
            "total_active": active.count(),
            "status_counts": status_counts,
            "priority_counts": priority_counts,
            "overdue_count": len(overdue_cards),
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

# ``delete_kanban_card`` is deliberately absent. It hard-deletes a work order, and
# ``WorkOrder`` cascades to ``WorkOrderEvent``, ``WorkOrderCommand``,
# ``WorkOrderCloseout``, ``WorkOrderDeviation``, ``CloseoutPartUsage`` and
# ``CloseoutReading`` -- so one call destroys the governance and closeout history of
# completed work. The tool also applies no customer scope, unlike the REST
# work-order surface. It stays defined (and admin/ORM deletion is unaffected) but is
# withheld from the agent until deletion returns as a governed command carrying
# permission, scope, expected-version and a durable audit record.
# ``archive_kanban_card`` is the correct soft-delete and remains available.
# ``ai.core.tools.capabilities._WITHHELD_TOOLS`` is the fail-closed backstop: if this
# tool is ever re-added here, it is still denied exposure and invocation.
KANBAN_TOOLS = [
    list_kanban_cards,
    get_kanban_card,
    create_kanban_card,
    update_kanban_card,
    move_kanban_card,
    archive_kanban_card,
    restore_kanban_card,
    get_kanban_summary,
    add_parts_to_kanban_card,
    check_kanban_card_stock,
    remove_part_from_kanban_card,
]

__all__ = ["KANBAN_READ_TOOLS", "KANBAN_TOOLS"]
