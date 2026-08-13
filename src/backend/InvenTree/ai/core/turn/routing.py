"""Routing stage: content assembly → diagnostic context → route span (S47)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai.core.turn.state import TurnRun
    from ai.core.turn_service import NormalizedTurnService


async def build_route(service: NormalizedTurnService, run: TurnRun) -> None:
    """Assemble routing content, diagnostic context, and the route decision."""

    # An accepted selection re-routes the ORIGINAL intent enriched by
    # the selected option label; both parts come from the persisted
    # record, never from anything the client echoed back. The raw
    # answer text stays the persisted user message untouched.
    run.routing_content = run.content
    if run.question_resolution is not None and run.question_resolution.outcome == "selected":
        run.routing_content = run.question_resolution.routing_content

    run.diagnostic_context = await service._build_diagnostic_context(
        actor=run.actor,
        trusted_context=run.trusted_context,
        content=run.routing_content,
        modality=run.modality,
    )
    from ai.core.tracing import set_span_attrs as _set_span_attrs
    from ai.core.tracing import turn_span as _turn_span

    with _turn_span("aimms.route", correlation_id=run.correlation_id) as route_span:
        run.route = service._route_turn(
            actor=run.actor,
            trusted_context=run.trusted_context,
            content=run.routing_content,
            modality=run.modality,
            modality_metadata=run.metadata,
            diagnostic_context=run.diagnostic_context,
        )
        _set_span_attrs(
            route_span,
            route_mode=getattr(getattr(run.route, "mode", None), "value", None),
            workflow_id=getattr(run.route, "target_workflow_id", None),
        )
