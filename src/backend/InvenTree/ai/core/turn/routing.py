"""Routing stage: content assembly → intent → diagnostic context → route (S47/S3)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai.core.turn.state import TurnRun
    from ai.core.turn_service import NormalizedTurnService

logger = logging.getLogger(__name__)


def _analysis_override_applies(run: TurnRun) -> bool:
    """Whether this turn's typed intent belongs on the analysis rail.

    Read-only intents WITH A SHIPPED VALIDATED EXECUTOR, on TEXT turns
    only: an effect-shaped turn keeps its ADVISORY_INTENT isolation, voice
    keeps legacy routing (the analysis rail is text-first), and an
    analysis intent whose executor has not shipped (fleet/trend/comparison
    until S7/S9) keeps the legacy full-tool rail — never a refusal.
    """
    from ai.core.analysis.intent import ANALYSIS_ROUTED_INTENTS, EffectIntent
    from aichat.models import TurnModality

    decision = run.task_intent
    return (
        decision is not None
        and decision.intent in ANALYSIS_ROUTED_INTENTS
        and decision.effect is EffectIntent.READ_ONLY
        and run.modality == TurnModality.TEXT
    )


async def build_route(service: NormalizedTurnService, run: TurnRun) -> None:
    """Assemble routing content, task intent, diagnostic context, and the route."""

    # An accepted selection re-routes the ORIGINAL intent enriched by
    # the selected option label; both parts come from the persisted
    # record, never from anything the client echoed back. The raw
    # answer text stays the persisted user message untouched.
    run.routing_content = run.content
    if run.question_resolution is not None and run.question_resolution.outcome == "selected":
        run.routing_content = run.question_resolution.routing_content

    # S3: typed task/effect intent BEFORE diagnostic-context construction.
    # Skipped for already-refused turns (no wasted classifier call). The
    # classifier reads the turn content and the scope MODE only.
    from ai.core.config import get_settings

    settings = get_settings()
    intent_shadow = getattr(settings, "feature_ai_analysis_router_shadow", False)
    intent_enforce = getattr(settings, "feature_ai_analysis_router_enforce", False)
    if (intent_shadow or intent_enforce) and run.injection_canonical is None:
        from ai.core.analysis.intent import classify
        from aichat.models import TurnModality

        snapshot_scope = (run.analysis_scope or {}).get("scope") or {}
        run.task_intent = await classify(
            run.routing_content,
            scope_mode=snapshot_scope.get("mode"),
            allow_llm=run.modality == TurnModality.TEXT,
        )

    # S3 (D5): diagnostic context stays unconditional in shadow — skipping
    # it would silently change grounding-fence seeds and entity chips for
    # turns still answered by the legacy rail. Only an ENFORCED analysis
    # turn (which never reaches those consumers' diagnostic branches)
    # skips the ~4-query construction.
    # The analysis route is taken ONLY when the evidence gate can actually
    # serve it (enforce): a gate rollback to shadow/off automatically
    # returns every analysis intent to the legacy rail instead of an
    # abstention — the no-refusal invariant survives misconfiguration.
    gate_enforce = str(getattr(settings, "evidence_gate_mode", "off") or "off") == "enforce"
    analysis_turn = intent_enforce and gate_enforce and _analysis_override_applies(run)
    if analysis_turn:
        run.diagnostic_context = None
    else:
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
        if run.task_intent is not None:
            legacy_mode = getattr(getattr(run.route, "mode", None), "value", None)
            legacy_workflow = getattr(run.route, "target_workflow_id", None)
            if _analysis_override_applies(run):
                if analysis_turn:
                    from ai.core.agents.voice_routing import (
                        ReasoningEffort,
                        RouteMode,
                        RouteReason,
                        VoiceRouteDecision,
                    )

                    run.route = VoiceRouteDecision(
                        mode=RouteMode.ANALYSIS,
                        effort=ReasoningEffort.MEDIUM,
                        reason_codes=(RouteReason.ANALYSIS_INTENT,),
                        target_workflow_id=None,
                        task_intent=run.task_intent.intent.value,
                        effect_intent=run.task_intent.effect.value,
                    )
                else:
                    # Content-free divergence record: what enforce WOULD
                    # have re-routed away from the legacy decision (reason
                    # names WHICH precondition kept the legacy route).
                    reason = "router_dark" if not intent_enforce else "gate_not_enforce"
                    logger.info(
                        "analysis_router.divergence intent=%s effect=%s "
                        "legacy_mode=%s legacy_wf=%s source=%s confidence=%.2f "
                        "reason=%s",
                        run.task_intent.intent.value,
                        run.task_intent.effect.value,
                        legacy_mode,
                        legacy_workflow,
                        run.task_intent.source,
                        run.task_intent.confidence,
                        reason,
                    )
            _set_span_attrs(
                route_span,
                task_intent=run.task_intent.intent.value,
                effect_intent=run.task_intent.effect.value,
            )
        _set_span_attrs(
            route_span,
            route_mode=getattr(getattr(run.route, "mode", None), "value", None),
            workflow_id=getattr(run.route, "target_workflow_id", None),
        )
        # S1 shadow telemetry: which analysis scope this turn was bound to.
        # Content-free by construction — mode enum, version int, an 8-char
        # hash prefix, and a count; never machine names or filters.
        if run.analysis_scope is not None:
            snapshot_scope = run.analysis_scope.get("scope") or {}
            machine_ids = snapshot_scope.get("machine_ids") or []
            _set_span_attrs(
                route_span,
                scope_mode=snapshot_scope.get("mode"),
                scope_version=run.analysis_scope.get("version"),
                scope_hash_prefix=str(run.analysis_scope.get("hash") or "")[:8],
                scope_machine_count=len(machine_ids),
            )
