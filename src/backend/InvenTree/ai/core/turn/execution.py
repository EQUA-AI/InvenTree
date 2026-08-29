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


def _effective_pinned_workflow(run: TurnRun) -> str | None:
    """Return the server-owned workflow pin for this turn."""

    if run.server_pinned_workflow is not None:
        return run.server_pinned_workflow
    uploaded_files = run.metadata.get("uploaded_files")
    if isinstance(uploaded_files, list) and uploaded_files:
        # `_turn_metadata` authorized these paths against the durable thread.
        # Client context is nested separately and can never reach this branch.
        return "wf6"
    return None


async def build_canonical(service: NormalizedTurnService, run: TurnRun) -> dict[str, Any]:
    """Dispatch the routed turn to exactly one canonical-producing branch."""

    route_mode = getattr(getattr(run.route, "mode", None), "value", None)
    pinned_workflow = _effective_pinned_workflow(run)
    if run.injection_canonical is not None:
        # Refusal wins over every other branch, including a pending write.
        return run.injection_canonical
    if run.safety_response is not None:
        # S4: the unsafe-shortcut refusal is final — nothing follows it.
        return run.safety_response
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
    if pinned_workflow is None and route_mode == "analysis":
        # S3/S10: the analysis rail. Reachable only under the routing enforce
        # flag. The evidence gate (AIMMS_EVIDENCE_GATE_MODE) decides what runs:
        # off keeps the deterministic abstention byte-identically (also the
        # incident-rollback posture); shadow dark-rehearses the full executor
        # and still serves the abstention; enforce serves the validated v2.
        return await _run_analysis_branch(service, run)
    if (
        pinned_workflow is None
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
    if pinned_workflow is None and route_mode == "advisory_intent":
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


async def _run_analysis_branch(service: NormalizedTurnService, run: TurnRun) -> dict[str, Any]:
    """Dispatch RouteMode.ANALYSIS per the evidence gate mode (S10)."""
    from ai.core.config import get_settings

    locale = getattr(run.trusted_context, "locale", "en")
    gate_mode = str(getattr(get_settings(), "evidence_gate_mode", "off") or "off")
    intent_value = run.task_intent.intent.value if run.task_intent is not None else "general"
    from ai.core.analysis.executor import TIER1_INTENTS

    shadow_gate_blob: dict[str, Any] | None = None
    if gate_mode != "off" and intent_value in TIER1_INTENTS:
        # The executor needs the same per-turn bindings as the legacy rail.
        await _bind_turn_capture_and_scope(service, run)
        from ai.core.analysis.executor import run_analysis

        if gate_mode == "enforce":
            outcome = await run_analysis(service, run)
            run.validation_result = outcome
            run.extras["evidence_sets"] = outcome.evidence_set_specs
            response = outcome.response
            await service._emit_canonical_events(
                emitter=run.emitter,
                thread_id=run.thread.pk,
                run_id=f"analysis:{run.turn.pk}",
                workflow_id="analysis_executor",
                workflow_name="ANALYSIS_EXECUTOR",
                message=response.detailed_response,
                response_state=str(response.response_state),
            )
            if run.emitter is not None:
                # The consolidated attachment: ONE object shape on every
                # envelope (STATE_DELTA kind / aimms.evidenceAnalysis /
                # persisted metadata). Unknown kinds are inert on old clients.
                try:
                    await run.emitter.emit(
                        AGUIEvent(
                            event_type=EventType.STATE_DELTA,
                            data={"kind": "evidence_analysis", **outcome.attachment},
                            thread_id=run.thread.pk,
                            run_id=f"analysis:{run.turn.pk}",
                        )
                    )
                except Exception:  # pragma: no cover - emission is fail-soft
                    logger.warning("evidence_analysis emission failed", exc_info=False)
            return {
                "thread_id": run.thread.pk,
                "turn_id": run.turn.pk,
                "message": response.detailed_response,
                "agent": "analysis_executor",
                "workflow_used": "analysis_executor",
                # Durable lifecycle value: wire "partial" maps to INCOMPLETE.
                "response_state": outcome.turn_state,
                "canonical_response": response.model_dump(mode="json"),
                "spoken_summary": response.spoken_summary,
                "reasoning_provenance": None,
                "route": run.route.to_dict(),
                "evidence_analysis": outcome.attachment,
                "evidence_gate": outcome.gate,
                "entities": outcome.entities,
            }
        # shadow: full dark rehearsal — run everything, persist the verdict,
        # serve the abstention unchanged.
        try:
            outcome = await run_analysis(service, run, shadow=True)
            run.validation_result = outcome
            shadow_gate_blob = {**outcome.gate, "mode": "shadow_rehearsal"}
            logger.info(
                "evidence_gate.shadow rehearsal verdict=%s intent=%s",
                outcome.gate.get("verdict"),
                intent_value,
            )
        except Exception:  # pragma: no cover - shadow must never fail a turn
            logger.warning("evidence gate shadow rehearsal failed", exc_info=False)

    from ai.core.turn.responses import (
        _canonical_analysis_capability_boundary,
        _canonical_analysis_unavailable,
    )

    tier23 = intent_value in ("fleet_aggregate", "trend_analysis", "manual_wo_comparison")
    if gate_mode != "off" and tier23:
        response = _canonical_analysis_capability_boundary(locale=locale)
        workflow_used = "analysis_capability_boundary"
    else:
        response = _canonical_analysis_unavailable(locale=locale)
        workflow_used = "analysis_unavailable"
    await service._emit_canonical_events(
        emitter=run.emitter,
        thread_id=run.thread.pk,
        run_id=f"analysis:{run.turn.pk}",
        workflow_id=workflow_used,
        workflow_name="ANALYSIS_EXECUTOR",
        message=response.detailed_response,
        response_state=response.response_state.value,
    )
    canonical = {
        "thread_id": run.thread.pk,
        "turn_id": run.turn.pk,
        "message": response.detailed_response,
        "agent": "analysis_executor",
        "workflow_used": workflow_used,
        "response_state": response.response_state.value,
        "canonical_response": response.model_dump(mode="json"),
        "spoken_summary": response.spoken_summary,
        "reasoning_provenance": None,
        "route": run.route.to_dict(),
    }
    if shadow_gate_blob is not None:
        canonical["evidence_gate"] = shadow_gate_blob
    return canonical


async def _bind_turn_capture_and_scope(service: NormalizedTurnService, run: TurnRun):
    """Bind the capture ledger + analysis-scope context for this turn.

    Shared by the legacy rail and the analysis executor so the two branches
    cannot drift (same ContextVar discipline, same serial resolution, same
    fail-soft posture). Logger identity is preserved (S47 review finding).
    """
    from ai.core.tools.capture_ledger import bind_tool_captures

    capture_ledger = bind_tool_captures()
    from ai.core.analysis.scope_context import bind_turn_scope, resolve_scope_serials

    scope_context = bind_turn_scope(
        run.analysis_scope, thread_pk=run.thread.pk, turn_pk=run.turn.pk
    )
    if scope_context is not None and scope_context.active:
        try:
            actor_user = await service._call_sync(service._rehydrate_user_for_grounding, run.actor)
            serials: frozenset[str] = frozenset()
            if actor_user is not None:
                serials = await service._call_sync(
                    resolve_scope_serials, actor_user, sorted(scope_context.machine_ids)
                )
            scope_context = bind_turn_scope(
                run.analysis_scope,
                thread_pk=run.thread.pk,
                turn_pk=run.turn.pk,
                serials=serials,
            )
        except Exception:  # pragma: no cover - scope carry must fail soft
            logger.warning("scope serial resolution failed", exc_info=False)
    return capture_ledger, scope_context


def _assemble_workflow_context(service: NormalizedTurnService, run: TurnRun) -> WorkflowContext:
    """Build the trusted workflow context; the pin-override rules live here."""

    workflow_context: WorkflowContext = dict(run.trusted)
    workflow_context["modality"] = run.modality
    pinned_workflow = _effective_pinned_workflow(run)
    if pinned_workflow is not None:
        # A trusted in-process caller selected the workflow itself;
        # or server-authorized uploads selected the document workflow.
        # Either pin wins over routing the same way the voice pin does.
        workflow_context["pinned_workflow_id"] = pinned_workflow
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
    # S5: the per-turn analysis-scope context binds beside it (same
    # ContextVar discipline — rebind every turn, propagate through
    # sync_to_async into every tool body and corpus call). Serial resolution
    # runs only when an explicit scope could actually be consulted; a
    # resolution failure binds a serial-less context, which narrows rather
    # than widens (the corpus treats it as applicability-unresolved).
    # S10: the binding is shared with the analysis executor so the two
    # branches cannot drift.
    capture_ledger, scope_context = await _bind_turn_capture_and_scope(service, run)
    workflow_context = _assemble_workflow_context(service, run)
    # Server-derived (owner-scoped rows from our own store), so it sits
    # alongside the other trusted fields. Its *content* is still
    # user-authored text and must be read as data, never instructions.
    history = await service._conversation_history(run.repository, run.thread.pk)
    if history:
        workflow_context["conversation_history"] = history
    # S3: the typed task intent rides the trusted context so wf8's
    # capability selection can prefer the matching packs (and skip the
    # history-subject carryover for analysis intents). Server-derived,
    # content-free — an enum value, never text.
    if run.task_intent is not None:
        workflow_context["task_intent"] = run.task_intent.intent.value
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
    # S5: the per-turn retrieval snapshot — the scope identity plus every
    # internal envelope meta the tools recorded. Server-only (feeds evidence
    # records and telemetry, A15); it never enters the canonical payload.
    try:
        run.retrieval_snapshot = {
            "snapshot_id": getattr(scope_context, "snapshot_id", None),
            "scope_hash": getattr(scope_context, "scope_hash", None),
            "scope_version": getattr(scope_context, "scope_version", None),
            "envelopes": capture_ledger.retrieval_metas(),
        }
    except Exception:  # pragma: no cover - bookkeeping must fail soft
        logger.warning("retrieval snapshot assembly failed", exc_info=False)
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
            from ai.core.questions.promotion import promote_captured_manual_question
            from ai.core.workflows.wf8_lookup import apply_question_replacement

            promote_captured_manual_question(modality="text")
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
    # S10 (WP-A7): the evidence-gate SHADOW soak. The analysis route gets no
    # traffic while the routing enforce flag is dark, so the soak that
    # decides the enforce flip runs HERE, over real legacy wf8 answers on
    # ANALYSIS-intent turns: the cheap deterministic prose scans (closure,
    # absence-vs-coverage), logged content-free and persisted beside the
    # grounding blob. Telemetry only — the answer is never changed.
    evidence_gate_blob: dict[str, Any] | None = None
    try:
        from ai.core.config import get_settings as _settings_for_gate

        if str(getattr(_settings_for_gate(), "evidence_gate_mode", "off")) != "off" and (
            run.task_intent is not None and getattr(run.task_intent, "intent", None) is not None
        ):
            from ai.core.analysis.intent import ANALYSIS_INTENTS

            if run.task_intent.intent in ANALYSIS_INTENTS:
                from ai.core.analysis.validator import shadow_scan_legacy

                known_values = frozenset(str(value) for value in capture_ledger.observed_values())
                envelopes = capture_ledger.retrieval_metas()
                scan = shadow_scan_legacy(
                    message=message,
                    known_values=known_values,
                    envelopes=envelopes,
                    intent=run.task_intent.intent.value,
                )
                if scan is not None:
                    evidence_gate_blob = scan
                    if scan["would_fail"]:
                        logger.info(
                            "evidence_gate.shadow would_fail=%s intent=%s",
                            ",".join(scan["would_fail"]),
                            run.task_intent.intent.value,
                        )
    except Exception:  # pragma: no cover - the soak must never fail a turn
        logger.warning("evidence gate shadow scan failed", exc_info=False)
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
    if evidence_gate_blob is not None:
        canonical["evidence_gate"] = evidence_gate_blob
    return canonical
