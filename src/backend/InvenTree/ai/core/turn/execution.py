"""Execution stage: canonical-branch dispatch + the legacy workflow run (S47).

One dispatch decision (refusal → pending write → declined question →
reasoning → advisory → legacy workflow), preserved verbatim from the
pre-extraction code. The legacy path owns the capture ledger, the
token-streaming reconciliation (S45), and the grounding fence (S27/P8-W0a).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ai.core.streaming import AGUIEvent, EventType
from ai.core.turn.request import _machine_name_matches
from ai.core.turn.responses import (
    _canonical_advisory_intent,
    _canonical_response_for_legacy,
)
from aichat.models import TurnModality, TurnState

if TYPE_CHECKING:
    from ai.core.turn.state import TurnRun
    from ai.core.turn_service import NormalizedTurnService

import logging

#: The legacy path's fail-soft warnings keep their PRE-extraction logger
#: identity: ops filters, log configs, and assertLogs key on
#: "ai.core.turn_service", and a refactor must not silently move records
#: out from under them (S47 review finding).
logger = logging.getLogger("ai.core.turn_service")

#: The workflow-facing context dict. It starts as a splat of the trusted
#: turn context and gains: modality, pinned_workflow_id,
#: server_generation_target, untrusted_client_context, uploaded_files,
#: conversation_history, question_resolution. Consumed opaquely by
#: ai/core/workflows/root.py — typing it for real belongs with a root.py
#: contract change, not this extraction.
WorkflowContext = dict[str, Any]


async def build_canonical(service: NormalizedTurnService, run: TurnRun) -> dict[str, Any]:
    """Dispatch the routed turn to exactly one canonical-producing branch."""

    route_mode = getattr(getattr(run.route, "mode", None), "value", None)
    if run.injection_canonical is not None:
        # Refusal wins over every other branch, including a pending write.
        return run.injection_canonical
    if run.write_canonical is not None:
        # A pending write confirmation resolved; it supersedes routing.
        return run.write_canonical
    if run.question_resolution is not None and run.question_resolution.outcome == "declined":
        # A declined question is terminal: acknowledge and invite a
        # rephrase. It never routes and never executes anything.
        return await service._question_declined_canonical(
            thread_id=run.thread.pk,
            turn_id=run.turn.pk,
            modality=run.modality,
            route=run.route,
            emitter=run.emitter,
            locale=getattr(run.trusted_context, "locale", "en"),
        )
    if (
        run.server_pinned_workflow is None
        and route_mode == "reasoning"
        and service.reasoning_adapter is not None
    ):
        return await service._reasoning_canonical(
            actor=run.actor,
            trusted_context=run.trusted_context,
            thread_id=run.thread.pk,
            turn_id=run.turn.pk,
            content=run.routing_content,
            modality=run.modality,
            route=run.route,
            diagnostic_context=run.diagnostic_context,
            emitter=run.emitter,
        )
    if run.server_pinned_workflow is None and route_mode == "advisory_intent":
        canonical = None
        if run.modality == TurnModality.VOICE:
            from ai.core.turn_service import _log_voice_write_confirmation_shadow

            _log_voice_write_confirmation_shadow(run.content, run.thread.pk)
            # Opt-in Tier-3: try to turn this effect turn into a
            # confirmable write proposal (read-back). None -> stay
            # advisory and read-only.
            canonical = await service._begin_voice_write(
                actor=run.actor,
                trusted_context=run.trusted_context,
                content=run.content,
                thread_id=run.thread.pk,
                turn_id=run.turn.pk,
                emitter=run.emitter,
            )
        if canonical is None:
            response = _canonical_advisory_intent(
                voice=run.modality == TurnModality.VOICE,
                action_available=(
                    run.modality == TurnModality.VOICE and service._voice_write_enabled()
                ),
                locale=getattr(run.trusted_context, "locale", "en"),
            )
            message = response.detailed_response
            await service._emit_canonical_events(
                emitter=run.emitter,
                thread_id=run.thread.pk,
                run_id=f"advisory:{run.turn.pk}",
                workflow_id="advisory_intent",
                workflow_name="ADVISORY_INTENT",
                message=message,
                response_state=response.response_state.value,
            )
            canonical = {
                "thread_id": run.thread.pk,
                "turn_id": run.turn.pk,
                "message": message,
                "agent": "complexity_router",
                "workflow_used": "advisory_intent",
                "response_state": response.response_state.value,
                "canonical_response": response.model_dump(mode="json"),
                "spoken_summary": response.spoken_summary,
                "reasoning_provenance": None,
                "route": run.route.to_dict(),
            }
        return canonical
    return await _run_legacy_workflow(service, run)


def _assemble_workflow_context(service: NormalizedTurnService, run: TurnRun) -> WorkflowContext:
    """Build the trusted workflow context; the pin-override rules live here."""

    workflow_context: WorkflowContext = dict(run.trusted)
    workflow_context["modality"] = run.modality
    if run.server_pinned_workflow is not None:
        # A trusted in-process caller selected the workflow itself;
        # its pin wins over routing the same way the voice pin does.
        workflow_context["pinned_workflow_id"] = run.server_pinned_workflow
        if run.server_generation_target is not None:
            workflow_context["server_generation_target"] = dict(run.server_generation_target)
    elif run.modality == TurnModality.VOICE:
        # Pin the workflow the voice router already chose. Without
        # this the legacy router re-decides from scratch and can pick
        # a write-tier workflow for a voice turn (observed: wf4
        # procurement). Voice may only land on read workflows.
        pinned = getattr(run.route, "target_workflow_id", None) or "wf8"
        workflow_context["pinned_workflow_id"] = pinned
    # Client hints remain visibly and semantically untrusted. They
    # are nested so no caller value can overwrite a server field.
    untrusted_context = run.metadata.get("untrusted_client_context")
    if isinstance(untrusted_context, dict):
        workflow_context["untrusted_client_context"] = untrusted_context
    uploaded_files = run.metadata.get("uploaded_files")
    if isinstance(uploaded_files, list):
        workflow_context["uploaded_files"] = uploaded_files
    return workflow_context


async def _run_legacy_workflow(service: NormalizedTurnService, run: TurnRun) -> dict[str, Any]:
    """Run the routed legacy workflow and post-process its streamed text."""

    workflow = service.workflow_factory()
    # S27: fresh capture ledger for this turn; the invocation
    # middleware records every tool result into it so grounding
    # can compare the answer against what the server returned.
    from ai.core.tools.capture_ledger import bind_tool_captures

    capture_ledger = bind_tool_captures()
    workflow_context = _assemble_workflow_context(service, run)
    # Server-derived (owner-scoped rows from our own store), so it sits
    # alongside the other trusted fields. Its *content* is still
    # user-authored text and must be read as data, never instructions.
    history = await service._conversation_history(run.repository, run.thread.pk)
    if history:
        workflow_context["conversation_history"] = history
    if run.question_resolution is not None and run.question_resolution.outcome == "selected":
        # Trusted context: the selected option's server-persisted
        # ref, so wf8 can pin e.g. the machine filter exactly.
        workflow_context["question_resolution"] = run.question_resolution.context_payload()
    elif run.question_resolution is not None and run.question_resolution.outcome == "unmatched":
        # Loop guard input: producers must not re-ask the exact
        # question this reply just failed to answer.
        workflow_context["question_resolution"] = run.question_resolution.unmatched_payload()

    from ai.core.tracing import turn_span as _turn_span

    chunks: list[str] = []
    with _turn_span(
        "aimms.workflow.execute",
        workflow_id=workflow_context.get("pinned_workflow_id"),
        correlation_id=run.correlation_id,
    ):
        # S46: bind the content-free tool-event sink for this
        # turn (flag-gated). The invocation-guard middleware
        # emits through it from any agent-framework depth.
        from contextlib import ExitStack as _ExitStack

        from ai.core.config import get_settings as _get_settings
        from ai.core.tool_events import bind_tool_event_sink

        with _ExitStack() as sink_stack:
            if getattr(_get_settings(), "feature_tool_events", False):
                sink_stack.enter_context(
                    bind_tool_event_sink(run.emitter, run.thread.pk, f"run:{run.turn.pk}")
                )
            async for chunk in workflow.run_stream(
                message=run.routing_content,
                emitter=run.emitter,
                thread_id=run.thread.pk,
                user_id=run.actor.user_pk,
                context=workflow_context,
            ):
                chunks.append(str(chunk))

    streamed_text = "".join(chunks)
    # S45: the post-hoc question replacement and the snapshot
    # reconciliation run ONLY when the token-streaming rail could
    # have produced the text (flag on, non-voice). Flag off, wf8's
    # own in-workflow application already produced the final text
    # and the classic path must stay byte-identical (ships-dark);
    # voice keeps the workflow's voice-trimmed rendering — a
    # second render would re-trim an already-trimmed proposal
    # (the trim loop-guard is not idempotent).
    streaming_reconcile = (
        getattr(_get_settings(), "feature_token_streaming", False)
        and run.modality != TurnModality.VOICE
    )
    message = streamed_text
    if streaming_reconcile:
        try:
            from ai.core.workflows.wf8_lookup import apply_question_replacement

            message = apply_question_replacement(
                message,
                modality="text",
                context=workflow_context,
            )
        except Exception:  # pragma: no cover - replacement is fail-soft
            logger.warning("question replacement failed", exc_info=False)
    grounding_meta = None
    try:
        # S27 seam: after message assembly, before the canonical
        # wrapper, so a downgrade is what gets persisted AND
        # spoken. Fail-soft: a grounding error ships the answer.
        from ai.core.config import get_settings
        from ai.core.grounding import (
            enum_closure_sets,
            evaluate_manual_grounding,
            machine_serials,
        )

        grounding_mode = str(get_settings().manual_grounding_mode)
        closure: frozenset[str] = frozenset()
        serials: frozenset[str] = frozenset()
        if grounding_mode in ("shadow", "enforce"):
            machine_roots = [
                root
                for root in getattr(run.diagnostic_context, "record_roots", ())
                if getattr(root, "entity_type", "") == "machine"
            ]
            machine_ids = [root.entity_id for root in machine_roots]
            # P8-W0a: the fence seed is the machines the
            # UTTERANCE names (token match), never the whole
            # scope — record_roots holds every in-scope machine,
            # and a scope-wide seed can neither catch an
            # in-scope wrong-machine citation nor stay honest
            # under root truncation. No name match => the turn
            # does not identify a machine => fence inert.
            lowered_utterance = run.content.lower()
            matched_ids = [
                root.entity_id
                for root in machine_roots
                if _machine_name_matches(
                    str(getattr(root, "display_name", "") or ""),
                    lowered_utterance,
                )
            ]
            if machine_ids:
                actor_user = await service._call_sync(
                    service._rehydrate_user_for_grounding, run.actor
                )
                if actor_user is not None:
                    closure = await service._call_sync(enum_closure_sets, actor_user, machine_ids)
                    if matched_ids:
                        serials = await service._call_sync(machine_serials, actor_user, matched_ids)
        message, assessment = evaluate_manual_grounding(
            message=message,
            ledger=capture_ledger,
            mode=grounding_mode,
            locale=getattr(run.trusted_context, "locale", "en"),
            closure_values=closure,
            turn_machine_serials=serials,
        )
        if assessment is not None:
            grounding_meta = assessment.to_meta()
    except Exception:  # pragma: no cover - grounding must fail soft
        logger.warning("manual grounding evaluation failed", exc_info=False)
    # S45 reconciliation: whenever the FINAL message differs from
    # what was emitted as text (question replacement, grounding
    # enforce-downgrade), one MESSAGES_SNAPSHOT tells the client
    # to replace the bubble wholesale. Emitted before events
    # freeze so replay carries the truth. Same gate as the
    # replacement above: flag off, the wire and storage must stay
    # byte-identical to the pre-S45 shape (the latent
    # enforce-downgrade divergence stays latent until the flip).
    if streaming_reconcile and message != streamed_text:
        try:
            await run.emitter.emit(
                AGUIEvent(
                    event_type=EventType.MESSAGES_SNAPSHOT,
                    data={"messages": [{"role": "assistant", "content": message}]},
                    thread_id=run.thread.pk,
                )
            )
        except Exception:  # pragma: no cover - advisory event
            logger.warning("messages snapshot emit failed", exc_info=False)
    response = _canonical_response_for_legacy(message, speakable=run.modality == TurnModality.VOICE)
    canonical: dict[str, Any] = {
        "thread_id": run.thread.pk,
        "turn_id": run.turn.pk,
        "message": message,
        "agent": "root_workflow",
        "workflow_used": run.capture.workflow_id,
        "response_state": TurnState.COMPLETE,
        "canonical_response": response.model_dump(mode="json"),
        "spoken_summary": response.spoken_summary,
        "reasoning_provenance": None,
        "route": run.route.to_dict() if run.route is not None else None,
    }
    if grounding_meta is not None:
        canonical["grounding"] = grounding_meta
    return canonical
