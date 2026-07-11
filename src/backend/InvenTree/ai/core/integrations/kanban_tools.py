"""
Kanban Card AI-Function Tools

@ai_function decorated tools for the AI chat agent to manage Kanban cards.
Provides full CRUD: list, get, create, update, move (status change), and delete/archive.

Uses Django ORM via sync_to_async for database access.
"""

from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import sync_to_async

from ai.core.maf_compat import ai_function

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _card_to_dict(card, include_parts: bool = True) -> dict[str, Any]:
    """Serialize a KanbanCard model instance to a plain dict."""
    data = {
        "id": card.id,
        "title": card.title,
        "description": card.description,
        "status": card.status,
        "priority": card.priority,
        "due_date": card.due_date.isoformat() if card.due_date else None,
        "assignee": card.assignee,
        "tags": list(card.tags) if card.tags else [],
        "company": card.company,
        "company_contact_name": card.company_contact_name,
        "company_contact_phone": card.company_contact_phone,
        "job_number": card.job_number,
        "service_quote": card.service_quote,
        "is_active": card.is_active,
        "created_at": card.created_at.isoformat() if card.created_at else None,
        "updated_at": card.updated_at.isoformat() if card.updated_at else None,
    }
    if include_parts:
        data["parts"] = [_card_part_to_dict(cp) for cp in card.card_parts.all().select_related('part')]
    return data


def _card_part_to_dict(card_part) -> dict[str, Any]:
    """Serialize a KanbanCardPart to a plain dict."""
    return {
        "id": card_part.id,
        "part_id": card_part.part_id,
        "part_name": card_part.part.name if card_part.part else "",
        "quantity": float(card_part.quantity),
        "allocated_quantity": float(card_part.allocated_quantity),
        "allocation_status": card_part.allocation_status,
        "allocation_note": card_part.allocation_note,
    }


def _get_model():
    """Lazy import to avoid Django app-not-ready errors."""
    from tasks.models import KanbanCard
    return KanbanCard


def _get_card_part_model():
    """Lazy import for KanbanCardPart."""
    from tasks.models import KanbanCardPart
    return KanbanCardPart


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
    KanbanCard = _get_model()

    @sync_to_async
    def _query():
        qs = KanbanCard.objects.all()

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
            qs = qs.filter(tags__contains=[tag])
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

    cards = await _query()
    return {"count": len(cards), "cards": cards}


@ai_function
async def get_kanban_card(card_id: int) -> dict[str, Any]:
    """
    Get full details of a single Kanban card by its ID.

    Args:
      card_id: The primary key of the card.

    Returns:
      Card details dict, or error if not found.
    """
    KanbanCard = _get_model()

    @sync_to_async
    def _fetch():
        try:
            card = KanbanCard.objects.get(pk=card_id)
            return _card_to_dict(card)
        except KanbanCard.DoesNotExist:
            return None

    card = await _fetch()
    if card is None:
        return {"error": f"Kanban card {card_id} not found."}
    return card


# ---------------------------------------------------------------------------
# CREATE tool
# ---------------------------------------------------------------------------

@ai_function
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
    KanbanCard = _get_model()
    KanbanCardPart = _get_card_part_model()
    valid_statuses = {"backlog", "in-progress", "review", "done"}
    valid_priorities = {"low", "medium", "high"}

    if status not in valid_statuses:
        return {"error": f"Invalid status '{status}'. Must be one of {sorted(valid_statuses)}."}
    if priority not in valid_priorities:
        return {"error": f"Invalid priority '{priority}'. Must be one of {sorted(valid_priorities)}."}

    @sync_to_async
    def _create():
        import datetime
        from decimal import Decimal

        parsed_due = None
        if due_date:
            try:
                parsed_due = datetime.date.fromisoformat(due_date)
            except ValueError:
                return {"error": f"Invalid due_date format '{due_date}'. Use YYYY-MM-DD."}

        card = KanbanCard.objects.create(
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

        if parts:
            from part.models import Part

            for part_entry in parts:
                part_id = part_entry.get("part_id")
                qty = Decimal(str(part_entry.get("quantity", 1)))

                try:
                    part_obj = Part.objects.get(pk=part_id)
                except Part.DoesNotExist:
                    allocation_warnings.append(
                        f"Part ID {part_id} not found — skipped."
                    )
                    continue

                card_part, created = KanbanCardPart.objects.get_or_create(
                    card=card, part=part_obj,
                    defaults={"quantity": qty},
                )
                if not created:
                    card_part.quantity = qty
                    card_part.save(update_fields=["quantity", "updated_at"])

                result = card_part.check_and_allocate()

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

        result = _card_to_dict(card)
        if allocation_warnings:
            result["stock_warnings"] = allocation_warnings
        return result

    return await _create()


# ---------------------------------------------------------------------------
# UPDATE tool
# ---------------------------------------------------------------------------

@ai_function
async def update_kanban_card(
    card_id: int,
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
    KanbanCard = _get_model()
    valid_statuses = {"backlog", "in-progress", "review", "done"}
    valid_priorities = {"low", "medium", "high"}

    if status is not None and status not in valid_statuses:
        return {"error": f"Invalid status '{status}'. Must be one of {sorted(valid_statuses)}."}
    if priority is not None and priority not in valid_priorities:
        return {"error": f"Invalid priority '{priority}'. Must be one of {sorted(valid_priorities)}."}

    @sync_to_async
    def _update():
        import datetime

        try:
            card = KanbanCard.objects.get(pk=card_id)
        except KanbanCard.DoesNotExist:
            return {"error": f"Kanban card {card_id} not found."}

        if title is not None:
            card.title = title
        if description is not None:
            card.description = description
        if status is not None:
            card.status = status
        if priority is not None:
            card.priority = priority
        if assignee is not None:
            card.assignee = assignee
        if due_date is not None:
            if due_date == "":
                card.due_date = None
            else:
                try:
                    card.due_date = datetime.date.fromisoformat(due_date)
                except ValueError:
                    return {"error": f"Invalid due_date format '{due_date}'. Use YYYY-MM-DD."}
        if tags is not None:
            # De-duplicate while preserving order
            seen = set()
            card.tags = [t for t in tags if not (t in seen or seen.add(t))]
        if company is not None:
            card.company = company
        if company_contact_name is not None:
            card.company_contact_name = company_contact_name
        if company_contact_phone is not None:
            card.company_contact_phone = company_contact_phone
        if job_number is not None:
            card.job_number = job_number
        if service_quote is not None:
            card.service_quote = service_quote

        card.save()
        return _card_to_dict(card)

    return await _update()


# ---------------------------------------------------------------------------
# MOVE (status change shortcut)
# ---------------------------------------------------------------------------

@ai_function
async def move_kanban_card(
    card_id: int,
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

    return await update_kanban_card(card_id=card_id, status=new_status)


# ---------------------------------------------------------------------------
# DELETE / ARCHIVE tools
# ---------------------------------------------------------------------------

@ai_function
async def archive_kanban_card(card_id: int) -> dict[str, Any]:
    """
    Archive (soft-delete) a Kanban card. The card is marked inactive
    and hidden from the default board view but can be restored later.

    Args:
      card_id: The card ID to archive.

    Returns:
      Confirmation dict or error.
    """
    KanbanCard = _get_model()

    @sync_to_async
    def _archive():
        try:
            card = KanbanCard.objects.get(pk=card_id)
        except KanbanCard.DoesNotExist:
            return {"error": f"Kanban card {card_id} not found."}

        if not card.is_active:
            return {"message": f"Card {card_id} is already archived.", "card": _card_to_dict(card)}

        card.is_active = False
        card.save(update_fields=["is_active", "updated_at"])
        return {"message": f"Card {card_id} ('{card.title}') archived.", "card": _card_to_dict(card)}

    return await _archive()


@ai_function
async def restore_kanban_card(card_id: int) -> dict[str, Any]:
    """
    Restore a previously archived Kanban card, making it active again.

    Args:
      card_id: The card ID to restore.

    Returns:
      The restored card dict or error.
    """
    KanbanCard = _get_model()

    @sync_to_async
    def _restore():
        try:
            card = KanbanCard.objects.get(pk=card_id)
        except KanbanCard.DoesNotExist:
            return {"error": f"Kanban card {card_id} not found."}

        if card.is_active:
            return {"message": f"Card {card_id} is already active.", "card": _card_to_dict(card)}

        card.is_active = True
        card.save(update_fields=["is_active", "updated_at"])
        return {"message": f"Card {card_id} ('{card.title}') restored.", "card": _card_to_dict(card)}

    return await _restore()


@ai_function
async def delete_kanban_card(card_id: int) -> dict[str, Any]:
    """
    Permanently delete a Kanban card. This cannot be undone.
    Prefer archive_kanban_card for soft-deletion.

    Args:
      card_id: The card ID to permanently delete.

    Returns:
      Confirmation dict or error.
    """
    KanbanCard = _get_model()

    @sync_to_async
    def _delete():
        try:
            card = KanbanCard.objects.get(pk=card_id)
        except KanbanCard.DoesNotExist:
            return {"error": f"Kanban card {card_id} not found."}

        title = card.title
        card.delete()
        return {"message": f"Card {card_id} ('{title}') permanently deleted."}

    return await _delete()


# ---------------------------------------------------------------------------
# PARTS & STOCK tools
# ---------------------------------------------------------------------------

@ai_function
async def add_parts_to_kanban_card(
    card_id: int,
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
    KanbanCard = _get_model()
    KanbanCardPart = _get_card_part_model()

    @sync_to_async
    def _add():
        from decimal import Decimal
        from part.models import Part

        try:
            card = KanbanCard.objects.get(pk=card_id)
        except KanbanCard.DoesNotExist:
            return {"error": f"Kanban card {card_id} not found."}

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

            card_part, created = KanbanCardPart.objects.get_or_create(
                card=card, part=part_obj,
                defaults={"quantity": qty},
            )
            if not created:
                card_part.quantity = qty
                card_part.save(update_fields=["quantity", "updated_at"])

            alloc = card_part.check_and_allocate()
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
            "card_id": card_id,
            "parts_added": len(results),
            "allocation_results": results,
            "warnings": warnings,
            "all_allocated": len(warnings) == 0,
        }

    return await _add()


@ai_function
async def check_kanban_card_stock(card_id: int) -> dict[str, Any]:
    """
    Re-check stock availability for all parts on a Kanban card.

    Useful to refresh allocation status after stock changes.

    Args:
      card_id: The card ID.

    Returns:
      Dict with allocation results per part and any warnings.
    """
    KanbanCard = _get_model()

    @sync_to_async
    def _check():
        try:
            card = KanbanCard.objects.get(pk=card_id)
        except KanbanCard.DoesNotExist:
            return {"error": f"Kanban card {card_id} not found."}

        card_parts = card.card_parts.all().select_related("part")
        if not card_parts.exists():
            return {
                "card_id": card_id,
                "message": "No parts attached to this card.",
                "parts": [],
                "warnings": [],
            }

        results = []
        warnings = []

        for cp in card_parts:
            alloc = cp.check_and_allocate()
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
            "card_id": card_id,
            "parts": results,
            "warnings": warnings,
            "all_allocated": len(warnings) == 0,
        }

    return await _check()


@ai_function
async def remove_part_from_kanban_card(
    card_id: int,
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
    KanbanCardPart = _get_card_part_model()

    @sync_to_async
    def _remove():
        try:
            cp = KanbanCardPart.objects.get(card_id=card_id, part_id=part_id)
        except KanbanCardPart.DoesNotExist:
            return {"error": f"Part {part_id} is not attached to card {card_id}."}

        part_name = cp.part.name
        cp.delete()
        return {"message": f"Part '{part_name}' (ID {part_id}) removed from card {card_id}."}

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
    KanbanCard = _get_model()

    @sync_to_async
    def _summarize():
        import datetime
        from django.db.models import Count

        active = KanbanCard.objects.filter(is_active=True)

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

KANBAN_TOOLS = [
    list_kanban_cards,
    get_kanban_card,
    create_kanban_card,
    update_kanban_card,
    move_kanban_card,
    archive_kanban_card,
    restore_kanban_card,
    delete_kanban_card,
    get_kanban_summary,
    add_parts_to_kanban_card,
    check_kanban_card_stock,
    remove_part_from_kanban_card,
]

__all__ = ["KANBAN_TOOLS"]
