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
      Dictionary with 'work_orders' (the returned page; each row has
      work_order_id, reference, title, lifecycle_status, work_order_type,
      priority, machine and due_date), 'returned_count' (rows in this page),
      'population_count' (ALL matching work orders — use this number, never
      the page length, when stating how many exist), and a 'retrieval'
      envelope whose coverage says whether the page is complete.
    """

    from ai.core.analysis.scope_context import current_turn_scope

    scope = current_turn_scope()
    scope_kwargs: dict[str, Any] = {}
    if scope is not None and scope.explicit:
        # S5 (WP-A3): the thread's analysis scope narrows the reader ON TOP
        # of authorization — enforced when the flag is on, counted (shadow)
        # otherwise. Plain kwargs: the reader never imports ai.*.
        scope_kwargs = {
            "scope_machine_ids": scope.machine_ids,
            "scope_date_from": scope.date_from,
            "scope_date_to": scope.date_to,
            "enforce": scope.enforce,
        }

    @sync_to_async
    def _query():
        from tasks import ai_read

        empty = {
            "rows": [],
            "population_count": 0,
            "returned_count": 0,
            "complete_population": True,
            "display_truncated": False,
            "out_of_scope_count": 0,
            "applied_filters": {},
            "high_watermark": None,
        }
        user = _current_user()
        if user is None:
            return empty, []
        result = ai_read.work_orders_page(user, query=query, limit=limit, **scope_kwargs)
        if scope_kwargs:
            # Shadow evidence for the S5 rollout soak (content-free).
            from aichat.services.retrieval_misses import record_search

            record_search(
                user=user,
                query=query or "",
                hit_count=result["returned_count"],
                top_score=None,
                machine_filter="scope_applied" if scope.enforce else "not_applied",
                document_class=None,
                scope_key="",
                corpus="reader",
                scope_hash=scope.scope_hash,
                scope_mode=scope.mode,
                scope_enforced=scope.enforce,
                out_of_scope_hits=result["out_of_scope_count"],
            )
        return result, [ai_read.work_order_row(work_order) for work_order in result["rows"]]

    page, work_orders = await _query()
    from ai.core.contracts.retrieval import build_envelope, coverage, record_envelope

    warnings: tuple[str, ...] = ()
    if scope_kwargs and scope.enforce:
        warnings = (f"narrowed_to_analysis_scope:{len(scope.machine_ids)}_machines",)
    envelope = build_envelope(
        source_class="work_order",
        population_type="work_orders",
        operation="search",
        filters=page["applied_filters"],
        coverage=coverage(
            population_count=page["population_count"],
            returned_count=page["returned_count"],
            complete_population=page["complete_population"],
            display_truncated=page["display_truncated"],
        ),
        source_revision={"high_watermark": page["high_watermark"]},
        warnings=warnings,
    )
    record_envelope("search_work_orders", envelope, out_of_scope_count=page["out_of_scope_count"])
    return {
        "returned_count": page["returned_count"],
        "population_count": page["population_count"],
        "complete_population": page["complete_population"],
        "display_truncated": page["display_truncated"],
        "work_orders": work_orders,
        "retrieval": envelope,
    }


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
        from ai.core.analysis.scope_context import scope_miss_for_machine

        miss = scope_miss_for_machine(work_order.machine_id)
        if miss is not None:
            return miss
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
        from ai.core.analysis.scope_context import scope_miss_for_machine

        miss = scope_miss_for_machine(work_order.machine_id)
        if miss is not None:
            return miss
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
        from ai.core.analysis.scope_context import scope_miss_for_machine

        miss = scope_miss_for_machine(work_order.machine_id)
        if miss is not None:
            return miss
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
        from ai.core.analysis.scope_context import scope_miss_for_machine

        miss = scope_miss_for_machine(machine.pk)
        if miss is not None:
            return miss
        return ai_read.open_repairs_for_machine(user, machine)

    result = await _fetch()
    return result if result is not None else {"error": _NOT_FOUND}


@ai_function
async def get_work_order_history(work_order_id: int, limit: int = 15) -> dict[str, Any]:
    """
    Get the audit history of one work order: lifecycle events, actors and timestamps.

    Answers "what happened on this job", "who moved it and when" and "why was
    it put on hold". Requires the work-order audit grant; without it the
    history is reported unavailable exactly like a missing record.

    Args:
      work_order_id: The work order's ID, typically from search_work_orders.
      limit: Maximum events to return, newest first (default 15, max 50).

    Returns:
      Dictionary with 'events' (the returned page, newest first; each with
      event_type, from_status, to_status, actor, reason and created_at),
      'returned_count', and 'population_count' (ALL events on the job — use
      this, never the page length, when stating how many happened), or an
      error if not available.
    """

    @sync_to_async
    def _fetch():
        from tasks import ai_read

        user, work_order = _resolve(work_order_id)
        if work_order is None:
            return None
        from ai.core.analysis.scope_context import scope_miss_for_machine

        miss = scope_miss_for_machine(work_order.machine_id)
        if miss is not None:
            return miss
        # S5b (Q15): event sequences need DISTINCTION ("the same person
        # moved it twice"), so actors render as thread-stable pseudonyms.
        from ai.core.analysis.pseudonyms import thread_pseudonymizer
        from ai.core.analysis.scope_context import current_turn_scope

        scope = current_turn_scope()
        identity = thread_pseudonymizer(scope.thread_pk if scope is not None else None)
        return ai_read.work_order_history_page(user, work_order, limit=limit, identity=identity)

    page = await _fetch()
    if page is None:
        return {"error": _NOT_FOUND}
    if page.get("scope_miss"):
        return page
    from ai.core.contracts.retrieval import build_envelope, coverage, record_envelope

    envelope = build_envelope(
        source_class="work_order",
        population_type="work_order_events",
        operation="list",
        filters={"work_order_id": work_order_id},
        coverage=coverage(
            population_count=page["population_count"],
            returned_count=page["returned_count"],
            complete_population=page["population_count"] == page["returned_count"],
            display_truncated=page["display_truncated"],
        ),
    )
    record_envelope("get_work_order_history", envelope)
    return {
        "returned_count": page["returned_count"],
        "population_count": page["population_count"],
        "display_truncated": page["display_truncated"],
        "events": page["events"],
        "retrieval": envelope,
    }


@ai_function
async def get_work_order_closeout(work_order_id: int) -> dict[str, Any]:
    """
    Get the verified structured closeout of one completed work order: cause, action, result and verification.

    Answers "what was actually wrong", "what was done" and "was it verified".
    Returns the EFFECTIVE closeout (applied amendments supersede the original
    writeup). Treat each stage separately: an action is not proof of cause,
    and an administrative "done" is not proof of sustained operation. Only
    rely on cause/verification statements when 'verified' is true.

    Args:
      work_order_id: The work order's ID, typically from search_work_orders.

    Returns:
      Dictionary with cause, action, result, verification_summary,
      downtime_minutes, follow_up fields, completed_at, verified/verified_at,
      amended/amendment_count and version — or 'no_closeout': true when the
      job has no structured closeout, or an error if not available.
    """

    @sync_to_async
    def _fetch():
        from tasks import ai_read

        _user, work_order = _resolve(work_order_id)
        if work_order is None:
            return None
        from ai.core.analysis.scope_context import scope_miss_for_machine

        miss = scope_miss_for_machine(work_order.machine_id)
        if miss is not None:
            return miss
        closeout = ai_read.work_order_closeout(work_order)
        if closeout is None:
            return {"work_order_id": work_order.pk, "no_closeout": True}
        return closeout

    result = await _fetch()
    return result if result is not None else {"error": _NOT_FOUND}


MAINTENANCE_READ_TOOLS = [
    search_work_orders,
    get_work_order_overview,
    get_work_order_readiness,
    get_work_order_repair_state,
    get_open_repairs_for_machine,
    get_work_order_history,
    get_work_order_closeout,
]

__all__ = [
    "MAINTENANCE_READ_TOOLS",
    "get_open_repairs_for_machine",
    "get_work_order_closeout",
    "get_work_order_history",
    "get_work_order_overview",
    "get_work_order_readiness",
    "get_work_order_repair_state",
    "search_work_orders",
]
