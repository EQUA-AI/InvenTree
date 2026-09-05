"""Terminal stage: canonical enrichment, freeze, and the ONE persist (S47).

Every terminal path (COMPLETE, CANCELED, INCOMPLETE, FAILED) funnels through
``persist_terminal`` — pre-extraction the four handlers rebuilt near-identical
canonical dicts and metadata blobs by hand. Byte-shape invariants:

- ``state`` is passed through EXACTLY as each caller supplies it (the string
  ``response_state`` on the complete path, the ``TurnState`` enum members on
  the failure paths) — both serialize identically, and the repository
  contract sees the same values as before.
- Failure canonicals carry ``spoken_summary: ""`` and none of the optional
  keys, so deriving metadata from the canonical emits the same dicts the
  hand-written handlers did.
- The metadata now shares the canonical's frozen events list instead of
  coalescing twice; equal content, one list identity — JSON output unchanged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ai.core.turn.events import coalesce_text_deltas
from ai.core.turn.responses import _canonical_terminal_response
from ai.core.usage import drain_turn_usage
from aichat.models import TurnState

if TYPE_CHECKING:
    from ai.core.turn.state import TurnRun
    from ai.core.turn.types import CanonicalTurn
    from ai.core.turn_service import NormalizedTurnService


#: The finalize stage keeps the pre-extraction logger identity (S47 convention).
logger = logging.getLogger("ai.core.turn_service")


def _terminal_output_metadata(base: dict[str, Any]) -> dict[str, Any]:
    """Attach the resolved model-version stamp to a terminal turn (S17 A10).

    Deployment names are aliases; the stamp records which concrete model
    identities the provider reported during this process, so a post-hoc audit
    of any persisted turn can name the models that served it. S24 adds the
    turn's provider usage through the same funnel, behind its kill switch.
    """
    from ai.core.config import get_settings
    from ai.core.integrations.model_pins import resolved_model_versions

    metadata = dict(base)
    versions = resolved_model_versions()
    if versions:
        metadata["model_versions"] = versions
    try:
        settings = get_settings()
        if settings.feature_turn_usage_persistence:
            usage = drain_turn_usage()
            if usage:
                metadata["usage"] = usage
    except Exception:  # pragma: no cover - telemetry must never fail a turn
        pass
    return metadata


async def enrich_canonical(
    service: NormalizedTurnService, run: TurnRun, canonical: CanonicalTurn
) -> CanonicalTurn:
    """Run the post-execution seams that mutate the canonical, in order."""

    # S22 arming choke point: a producer proposed a question via the
    # promotion ContextVar; the turn service owns the invariants, so
    # the record save, the persisted QUESTION event, and the canonical
    # audit copy all happen here — before events are frozen into the
    # canonical.
    canonical = await service._arm_pending_question(
        canonical,
        thread_id=run.thread.pk,
        turn_id=run.turn.pk,
        content=run.content,
        modality=run.modality,
        emitter=run.emitter,
    )
    if run.question_resolution is not None:
        canonical["question_resolution"] = run.question_resolution.audit_payload()
    # Live alias: the arming and manifest seams append to capture.events
    # and must land in the canonical; the coalesced freeze happens
    # immediately before the terminal write.
    canonical["events"] = run.capture.events
    canonical = await service._transform_proposals(
        canonical,
        actor=run.actor,
        trusted_context=run.trusted_context,
    )
    # S28: server-observed entity manifest, after proposals so the
    # manifest reflects the final canonical. The event lands in the
    # live stream AND capture.events (same list as canonical["events"]),
    # so replay reproduces the chips.
    canonical = await service._attach_entity_manifest(
        canonical,
        diagnostic_context=run.diagnostic_context,
        thread_id=run.thread.pk,
        turn_id=run.turn.pk,
        emitter=run.emitter,
    )
    canonical = await service._attach_media_evidence(
        canonical,
        thread_id=run.thread.pk,
        turn_id=run.turn.pk,
        emitter=run.emitter,
    )
    return await _attach_context_used(run, canonical)


async def _attach_context_used(run: TurnRun, canonical: CanonicalTurn) -> CanonicalTurn:
    """M1 PR G (§9.11 / GR-16): the bounded Context used record.

    Computed from the turn's ContextBundle and the retrieval snapshot
    nothing read before; ids and counts only. Rides the canonical (so it
    persists with the turn) and one STATE_DELTA on the live stream, the
    evidence_analysis idiom. Fail-soft: a record failure never fails a turn.
    """
    bundle = getattr(run, "context_bundle", None)
    if bundle is None:
        return canonical
    try:
        record = bundle.context_used(getattr(run, "retrieval_snapshot", None))
    except Exception:  # pragma: no cover - telemetry must never fail a turn
        logger.warning("context_used record failed", exc_info=False)
        return canonical
    canonical["context_used"] = record
    if run.emitter is not None:
        from ai.core.streaming import AGUIEvent, EventType

        try:
            await run.emitter.emit(
                AGUIEvent(
                    event_type=EventType.STATE_DELTA,
                    data={"kind": "context_used", **record},
                    thread_id=run.thread.pk,
                    run_id=f"contextUsed:{run.turn.pk}",
                )
            )
        except Exception:  # pragma: no cover - emission is fail-soft
            logger.warning("context_used emission failed", exc_info=False)
    return canonical


async def complete(
    service: NormalizedTurnService, run: TurnRun, canonical: CanonicalTurn
) -> tuple[Any, str, str]:
    """Freeze the events and persist the COMPLETE terminal state."""

    message = str(canonical.get("message") or "")
    response_state = str(canonical.get("response_state") or TurnState.COMPLETE)
    # S45: final freeze — every seam has run; collapse streamed
    # deltas for durable storage (replay byte-compatibility).
    canonical["events"] = coalesce_text_deltas(run.capture.events)
    finalized = await persist_terminal(
        service, run, canonical, state=response_state, output_content=message
    )
    return finalized, message, response_state


def failure_canonical(run: TurnRun, *, state: Any, message: str) -> CanonicalTurn:
    """The canonical for a CANCELED/INCOMPLETE/FAILED terminal state."""

    response = _canonical_terminal_response(state, message)
    return {
        "thread_id": run.thread.pk,
        "turn_id": run.turn.pk,
        "message": response.detailed_response,
        "agent": "root_workflow",
        "workflow_used": run.capture.workflow_id if run.capture else None,
        "response_state": state,
        "canonical_response": response.model_dump(mode="json"),
        "spoken_summary": "",
        "reasoning_provenance": None,
        "route": None,
        "events": coalesce_text_deltas(run.capture.events if run.capture else []),
    }


def _task_intent_value(run: TurnRun) -> str | None:
    """The typed task intent as its enum value, or None when unclassified."""
    intent = getattr(run.task_intent, "intent", None)
    value = getattr(intent, "value", None)
    return str(value) if value else None


async def persist_terminal(
    service: NormalizedTurnService,
    run: TurnRun,
    canonical: CanonicalTurn,
    *,
    state: Any,
    output_content: str,
) -> Any:
    """The single ``repository.terminal`` call every terminal path uses."""

    return await service._call_sync(
        run.repository.terminal,
        run.turn.pk,
        state=state,
        canonical_result=canonical,
        output_content=output_content,
        output_metadata=_terminal_output_metadata({
            "response_state": state,
            "events": canonical["events"],
            "spoken_summary": str(canonical.get("spoken_summary") or ""),
            # D0 (M1 entry baseline): content-free route facts for the battery
            # runner's layer-2 assertion — the intent enum value and whether
            # the classifier received a thread summary. Always written (null /
            # False are facts too); owner-only on the /threads projection.
            "task_intent": _task_intent_value(run),
            "conversation_summary_present": bool(
                run.extras.get("conversation_summary_present", False)
            ),
            # S22: the card and its resolution ride message metadata so
            # the /threads projection can reproduce them on reload.
            **({"question": canonical["question"]} if canonical.get("question") else {}),
            **(
                {"question_resolution": canonical["question_resolution"]}
                if canonical.get("question_resolution")
                else {}
            ),
            # S27: the grounding assessment persists with the turn so
            # the shadow soak can be audited from stored data alone.
            **({"grounding": canonical["grounding"]} if canonical.get("grounding") else {}),
            # S28: chips reload from the same metadata on /threads.
            **({"entities": canonical["entities"]} if canonical.get("entities") else {}),
            **(
                {"media_evidence": canonical["media_evidence"]}
                if canonical.get("media_evidence")
                else {}
            ),
            # S10: the consolidated evidence attachment reloads byte-faithfully
            # (same object shape as the live wires), and the gate's verdict
            # blob persists so the shadow soak is auditable from stored data.
            **(
                {"evidence_analysis": canonical["evidence_analysis"]}
                if canonical.get("evidence_analysis")
                else {}
            ),
            **(
                {"evidence_gate": canonical["evidence_gate"]}
                if canonical.get("evidence_gate")
                else {}
            ),
            # M1 PR G (§9.11 / GR-16): the bounded Context used record
            # persists with the turn so /threads can project it on reload.
            **(
                {"context_used": canonical["context_used"]} if canonical.get("context_used") else {}
            ),
        }),
        workflow_id=(run.capture.workflow_id if run.capture else None) or "",
        # S10: evidence-set rows ride the SAME transaction as the terminal
        # write — failed/canceled turns pass nothing and leave no orphans.
        evidence_sets=run.extras.get("evidence_sets") or None,
    )
