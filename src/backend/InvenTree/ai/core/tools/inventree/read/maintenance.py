"""Maintenance work-order read tools for the unscoped voice and assistant rails.

The maintenance twin of ``machines.py``: every tool delegates to
``tasks.ai_read``, the single authorized reader for work orders and their
repair state, so this rail can never drift from the canonical scope rules.

The same three properties hold:

* **The acting user comes from the boundary, never the arguments.**
* **A ``work_order_id`` is a candidate, not a grant** -- re-authorized on
  every call; an id outside the actor's scope is indistinguishable from one
  that does not exist.
* **A spoken reference resolves inside the scope, not against it.**
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

#: One message for unknown actor / unscoped / feature off / not yours, so no
#: caller can tell which applies.
_NOT_FOUND = "No work order matching that reference is available to you."


def _current_user():
    """Resolve the acting Django user from the authenticated ASGI boundary."""
    from ai.core.auth import get_current_principal
    from django.contrib.auth import get_user_model

    principal = get_current_principal()
    if principal is None:
        return None
    return get_user_model().objects.filter(pk=principal.user_pk).first()


def _resolve(work_order_id: int):
    """Load one authorized work order, or ``None``, under the acting user."""
    from tasks import ai_read

    user = _current_user()
    if user is None:
        return None, None
    return user, ai_read.authorized_work_order(user, work_order_id)


@ai_function
async def search_work_orders(query: str | None = None, limit: int = 10) -> dict[str, Any]:
    """
    Find maintenance work orders you are authorized to see, by reference, title or machine name.

    Use this first when the user names a job (e.g. "WO-000123" or "the pump
    seal job") to turn that phrase into a work_order_id for the other tools.

    Args:
      query: Optional text matched against the work-order reference, title or
             machine name. Omit to list your most recent work orders.
      limit: Maximum rows to return (default 10, max 25).

    Returns:
      Dictionary with 'count' and 'work_orders'. Each row has work_order_id,
      reference, title, lifecycle_status, work_order_type, priority, machine
      and due_date.
    """

    @sync_to_async
    def _query():
        from tasks import ai_read

        user = _current_user()
        if user is None:
            return []
        rows = ai_read.work_orders_in_scope(user, query=query, limit=limit)
        return [ai_read.work_order_row(work_order) for work_order in rows]

    work_orders = await _query()
    return {"count": len(work_orders), "work_orders": work_orders}


@ai_function
async def get_work_order_overview(work_order_id: int) -> dict[str, Any]:
    """
    Get one maintenance work order: status, schedule, machine, and required parts with stock availability.

    Answers "what's the status of this job", "when is it scheduled" and "do we
    have the parts for it in stock".

    Args:
      work_order_id: The work order's ID, typically from search_work_orders.

    Returns:
      Dictionary with reference, title, lifecycle_status, type, priority,
      schedule window, machine, assigned_to and parts (each with quantity,
      quantity_available and allocation_status), or an error if not available.
    """

    @sync_to_async
    def _fetch():
        from tasks import ai_read

        _user, work_order = _resolve(work_order_id)
        if work_order is None:
            return None
        return ai_read.work_order_overview(work_order)

    result = await _fetch()
    return result if result is not None else {"error": _NOT_FOUND}


@ai_function
async def get_work_order_readiness(work_order_id: int, action: str = "start") -> dict[str, Any]:
    """
    Explain whether a maintenance work order is ready for an action, and what blocks it.

    Answers "is this job ready to start" and "why is it blocked". Reports only
    what the live readiness evaluator emitted; it never guesses at blockers.

    Args:
      work_order_id: The work order's ID.
      action: The action to evaluate: plan, mark_ready, start, hold, resume,
              verify, complete, cancel or assign (default start).

    Returns:
      Dictionary with ready (boolean), the evaluated action, and blockers
      (each with code, message and remediation), or an error if not available.
    """

    @sync_to_async
    def _fetch():
        from tasks import ai_read

        user, work_order = _resolve(work_order_id)
        if work_order is None:
            return None
        try:
            return ai_read.work_order_readiness(user, work_order, action=action)
        except Exception:
            logger.exception("work order readiness evaluation failed")
            return {"error": "Readiness could not be evaluated for that action."}

    result = await _fetch()
    return result if result is not None else {"error": _NOT_FOUND}


@ai_function
async def get_work_order_repair_state(work_order_id: int) -> dict[str, Any]:
    """
    Get the repair investigation attached to a work order: findings, approved repair plan and safety gates.

    Answers "what are the findings", "what is the verified cause", "what's the
    approved repair scope" and "which safety gates are outstanding".

    Args:
      work_order_id: The work order's ID.

    Returns:
      Dictionary with the repair packet (status, criticality, fault summary),
      findings (observations with values and verification state), the current
      approved_scope (verified cause, scope lines, failure codes) and gate
      status, or packet: null when no repair is attached.
    """

    @sync_to_async
    def _fetch():
        from tasks import ai_read

        _user, work_order = _resolve(work_order_id)
        if work_order is None:
            return None
        return ai_read.work_order_repair_state(work_order)

    result = await _fetch()
    return result if result is not None else {"error": _NOT_FOUND}


@ai_function
async def get_open_repairs_for_machine(machine_id: int) -> dict[str, Any]:
    """
    List the open (non-closed) repairs on one machine, each with its start readiness.

    Answers "is there already a repair underway on this machine" and "can the
    repair start yet".

    Args:
      machine_id: The machine's ID, typically from search_machines.

    Returns:
      Dictionary with 'repairs' (each with reference, status, criticality,
      fault summary, linked work order, ready flag and blockers) and 'total',
      or an error if the machine is not available to you.
    """

    @sync_to_async
    def _fetch():
        from tasks import ai_read

        user = _current_user()
        if user is None:
            return None
        machine = ai_read.authorized_machine(user, machine_id)
        if machine is None:
            return None
        return ai_read.open_repairs_for_machine(user, machine)

    result = await _fetch()
    return result if result is not None else {"error": _NOT_FOUND}


MAINTENANCE_READ_TOOLS = [
    search_work_orders,
    get_work_order_overview,
    get_work_order_readiness,
    get_work_order_repair_state,
    get_open_repairs_for_machine,
]

__all__ = [
    "MAINTENANCE_READ_TOOLS",
    "get_open_repairs_for_machine",
    "get_work_order_overview",
    "get_work_order_readiness",
    "get_work_order_repair_state",
    "search_work_orders",
]
