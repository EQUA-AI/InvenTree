"""Machine (asset) read tools for the unscoped voice and assistant rails.

Voice cannot use the scoped-chat rail: a voice session refuses any ``scoped_``
thread and the turn service pins every thread to ``ThreadNamespace.UNSCOPED``,
so the signed 15-minute ``ChatContext`` token has no carrier across a
hands-free session. These tools are the second surface that gap requires --
but deliberately not a second *authority*. Every one of them delegates to
``assets.ai_read``, the same module the scoped panel reads through, so the two
rails cannot drift into showing different answers or enforcing different rules.

Three properties make this safe to hand to a model:

* **The acting user comes from the boundary, never the arguments.**
  ``get_current_principal()`` reads a contextvar the authenticated ASGI
  boundary set. A model cannot name whose authority to use.
* **A ``machine_id`` is a candidate, not a grant.** It is re-authorized on
  every call through ``ai_read.authorized_machine``; an id outside the actor's
  scope is indistinguishable from one that does not exist.
* **A spoken name resolves inside the scope, not against it.**
  ``search_machines`` filters by the actor's scope first and treats the name as
  a search term, so a name that matches another tenant's asset matches nothing.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

#: Returned whenever the actor is unknown, unscoped, the feature is off, or the
#: machine is not theirs. One message for all four so that no caller can tell
#: which applies -- distinguishing them is what would turn this into an
#: asset-existence oracle.
_NOT_FOUND = "No machine matching that reference is available to you."


def _current_user():
    """Resolve the acting Django user from the authenticated ASGI boundary."""
    from ai.core.auth import get_current_principal
    from django.contrib.auth import get_user_model

    principal = get_current_principal()
    if principal is None:
        return None
    return get_user_model().objects.filter(pk=principal.user_pk).first()


def _resolve(machine_id: int):
    """Load one authorized machine, or ``None``, under the acting user."""
    from assets import ai_read

    user = _current_user()
    if user is None:
        return None, None
    return user, ai_read.authorized_machine(user, machine_id)


@ai_function
async def search_machines(query: str | None = None, limit: int = 10) -> dict[str, Any]:
    """
    Find machines (equipment assets) you are authorized to see, by name or other identifying text.

    Use this first when the user names a machine in conversation, to turn that
    spoken name into a machine_id for the other machine tools.

    Args:
      query: Optional text to match against machine name, serial, model,
             manufacturer or location. Omit to list your machines.
      limit: Maximum machines to return (default 10, max 25).

    Returns:
      Dictionary with 'machines' (the returned page; each machine has
      machine_id, name, location, manufacturer, model, serial and active —
      location disambiguates similarly named assets), 'returned_count',
      'population_count' (ALL matching machines — use this, never the page
      length, when stating how many exist), and a 'retrieval' envelope.
    """

    from ai.core.analysis.scope_context import current_turn_scope

    scope = current_turn_scope()
    scope_kwargs: dict[str, Any] = {}
    if scope is not None and scope.explicit:
        # S5 (WP-A3): analysis-scope narrowing on top of authorization.
        scope_kwargs = {"scope_machine_ids": scope.machine_ids, "enforce": scope.enforce}

    @sync_to_async
    def _query():
        from assets import ai_read

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
        result = ai_read.machines_page(user, query=query, limit=limit, **scope_kwargs)
        if scope_kwargs:
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
        return result, [ai_read.machine_search_row(machine) for machine in result["rows"]]

    page, machines = await _query()
    from ai.core.contracts.retrieval import build_envelope, coverage, record_envelope

    warnings: tuple[str, ...] = ()
    if scope_kwargs and scope.enforce:
        warnings = (f"narrowed_to_analysis_scope:{len(scope.machine_ids)}_machines",)
    envelope = build_envelope(
        source_class="machine",
        population_type="machines",
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
    record_envelope("search_machines", envelope, out_of_scope_count=page["out_of_scope_count"])
    return {
        "returned_count": page["returned_count"],
        "population_count": page["population_count"],
        "complete_population": page["complete_population"],
        "display_truncated": page["display_truncated"],
        "machines": machines,
        "retrieval": envelope,
    }


@ai_function
async def get_machine_overview(machine_id: int) -> dict[str, Any]:
    """
    Get everything known about one machine: identity, health, signals, alarms, parts, maintenance and documents.

    This is the single call that answers most spoken questions about a machine.
    Use the more specific tools below when the user asks to drill into one area.

    Args:
      machine_id: The machine's ID, typically from search_machines.

    Returns:
      Dictionary with identity, health, signals, anomalies, installed_parts,
      maintenance_history and attachments, or an error if not available.
    """

    @sync_to_async
    def _fetch():
        from assets import ai_read

        user, machine = _resolve(machine_id)
        if machine is None:
            return None
        from ai.core.analysis.scope_context import scope_miss_for_machine

        miss = scope_miss_for_machine(machine.pk)
        if miss is not None:
            return miss
        return ai_read.machine_overview(user, machine)

    result = await _fetch()
    return result if result is not None else {"error": _NOT_FOUND}


@ai_function
async def get_machine_health(machine_id: int) -> dict[str, Any]:
    """
    Get a machine's current condition, data freshness and connection status of its sources.

    Answers "how is it doing", "is it running", "is the data stale" and
    "is the connector working".

    Args:
      machine_id: The machine's ID.

    Returns:
      Health state, whether monitoring is configured, signal and stale counts,
      anomaly counts, and per-source connection status including when each
      source last succeeded or errored.
    """

    @sync_to_async
    def _fetch():
        from assets import ai_read

        _user, machine = _resolve(machine_id)
        if machine is None:
            return None
        from ai.core.analysis.scope_context import scope_miss_for_machine

        miss = scope_miss_for_machine(machine.pk)
        if miss is not None:
            return miss
        return ai_read.machine_health(machine)

    result = await _fetch()
    return result if result is not None else {"error": _NOT_FOUND}


@ai_function
async def get_machine_signals(machine_id: int) -> dict[str, Any]:
    """
    Get the current readings for every signal mapped to a machine.

    Answers "what is the temperature/pressure/vibration right now" and
    "which readings are out of range".

    Args:
      machine_id: The machine's ID.

    Returns:
      A list of signals, each with binding_id, display_name, value, unit,
      state, quality, staleness, and its configured normal/warning/critical
      limits. Use binding_id with get_machine_signal_trend for history.
    """

    @sync_to_async
    def _fetch():
        from assets import ai_read

        _user, machine = _resolve(machine_id)
        if machine is None:
            return None
        from ai.core.analysis.scope_context import scope_miss_for_machine

        miss = scope_miss_for_machine(machine.pk)
        if miss is not None:
            return miss
        return ai_read.machine_signals(machine)

    result = await _fetch()
    return result if result is not None else {"error": _NOT_FOUND}


@ai_function
async def get_machine_signal_trend(
    machine_id: int, binding_id: int, hours: int = 24
) -> dict[str, Any]:
    """
    Get how one machine signal has moved over a recent time window.

    Answers "is the temperature climbing", "has vibration got worse this week".
    Get binding_id from get_machine_signals first.

    Args:
      machine_id: The machine's ID.
      binding_id: Which mapped signal, from get_machine_signals.
      hours: How far back to look (default 24, max 168).

    Returns:
      Window bounds, sample count and first/last/min/max values. If the source
      cannot serve history, 'available' is false with a reason rather than a
      made-up trend.
    """

    @sync_to_async
    def _fetch():
        from assets import ai_read

        _user, machine = _resolve(machine_id)
        if machine is None:
            return None
        from ai.core.analysis.scope_context import scope_miss_for_machine

        miss = scope_miss_for_machine(machine.pk)
        if miss is not None:
            return miss
        return ai_read.machine_signal_trend(machine, binding_id=binding_id, hours=hours)

    result = await _fetch()
    return result if result is not None else {"error": _NOT_FOUND}


@ai_function
async def get_machine_anomalies(
    machine_id: int, include_resolved: bool = False, limit: int = 10
) -> dict[str, Any]:
    """
    Get the alarms and detected anomalies for a machine.

    Answers "what is wrong with it", "any alarms", and — with
    include_resolved — "has this happened before" and "was it ever fixed".

    Args:
      machine_id: The machine's ID.
      include_resolved: Include resolved/dismissed history (default False,
                        matching the machine page which shows active alarms).
      limit: Maximum anomalies to return (default 10, max 25).

    Returns:
      Anomalies with title, severity, status, alarm code, numeric metrics,
      first/last observed times, acknowledgement and resolution times, and any
      linked work order reference.
    """

    @sync_to_async
    def _fetch():
        from assets import ai_read

        _user, machine = _resolve(machine_id)
        if machine is None:
            return None
        from ai.core.analysis.scope_context import scope_miss_for_machine

        miss = scope_miss_for_machine(machine.pk)
        if miss is not None:
            return miss
        return ai_read.machine_anomalies(machine, include_resolved=include_resolved, limit=limit)

    result = await _fetch()
    return result if result is not None else {"error": _NOT_FOUND}


@ai_function
async def get_machine_parts(machine_id: int, limit: int = 50) -> dict[str, Any]:
    """
    Get the parts installed on a machine, with quantities.

    Answers "what spares does it take". Each row carries part_id and IPN, so
    you can chain into get_part or get_stock_levels to check availability.

    Args:
      machine_id: The machine's ID.
      limit: Maximum parts to return (default 50, max 50).

    Returns:
      Parts with part_id, part_name, ipn and quantity, plus total and whether
      the list was truncated.
    """

    @sync_to_async
    def _fetch():
        from assets import ai_read

        _user, machine = _resolve(machine_id)
        if machine is None:
            return None
        from ai.core.analysis.scope_context import scope_miss_for_machine

        miss = scope_miss_for_machine(machine.pk)
        if miss is not None:
            return miss
        return ai_read.machine_installed_parts(machine, limit=limit)

    result = await _fetch()
    return result if result is not None else {"error": _NOT_FOUND}


@ai_function
async def get_machine_maintenance_history(machine_id: int, limit: int = 25) -> dict[str, Any]:
    """
    Get the recorded maintenance history for a machine.

    Answers "when was it last serviced", "what have we done to it".

    Args:
      machine_id: The machine's ID.
      limit: Maximum records to return (default 25, max 25).

    Returns:
      Records with date, summary, who performed it, and the linked work order
      reference and title where you are authorized to see that work order.
    """

    @sync_to_async
    def _fetch():
        from assets import ai_read

        user, machine = _resolve(machine_id)
        if machine is None:
            return None
        from ai.core.analysis.scope_context import scope_miss_for_machine

        miss = scope_miss_for_machine(machine.pk)
        if miss is not None:
            return miss
        return ai_read.machine_maintenance_history(user, machine, limit=limit)

    result = await _fetch()
    return result if result is not None else {"error": _NOT_FOUND}


@ai_function
async def get_machine_attachments(machine_id: int, limit: int = 50) -> dict[str, Any]:
    """
    Get what documentation is attached to a machine.

    Answers "is there a manual", "do we have the hydraulic schematic". Returns
    labels only — never file contents or download links.

    Args:
      machine_id: The machine's ID.
      limit: Maximum attachments to return (default 50, max 50).

    Returns:
      Attachments with kind (file or link), name, operator comment, whether it
      is an image, file size and upload date.
    """

    @sync_to_async
    def _fetch():
        from assets import ai_read

        _user, machine = _resolve(machine_id)
        if machine is None:
            return None
        from ai.core.analysis.scope_context import scope_miss_for_machine

        miss = scope_miss_for_machine(machine.pk)
        if miss is not None:
            return miss
        return ai_read.machine_attachments(machine, limit=limit)

    result = await _fetch()
    return result if result is not None else {"error": _NOT_FOUND}


MACHINE_READ_TOOLS = [
    search_machines,
    get_machine_overview,
    get_machine_health,
    get_machine_signals,
    get_machine_signal_trend,
    get_machine_anomalies,
    get_machine_parts,
    get_machine_maintenance_history,
    get_machine_attachments,
]

__all__ = [
    "MACHINE_READ_TOOLS",
    "get_machine_anomalies",
    "get_machine_attachments",
    "get_machine_health",
    "get_machine_maintenance_history",
    "get_machine_overview",
    "get_machine_parts",
    "get_machine_signal_trend",
    "get_machine_signals",
    "search_machines",
]
