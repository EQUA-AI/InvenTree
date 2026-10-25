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


def _memory_lineage(run) -> dict[str, Any]:
    """Content-free provenance from server-built context, never client metadata."""
    bundle = getattr(run, "context_bundle", None)
    if not isinstance(getattr(bundle, "sections", None), dict):
        return {}
    items = [
        item
        for slot in ("user_preferences", "verified_entity_facts")
        for item in bundle.section(slot).items
    ]
    episodes = bundle.section("recalled_episodes").items
    if not items and not episodes:
        return {}
    return {
        "memory_informed_clients": sorted({
            code for item in (*items, *episodes) for code in item.scope.client_codes
        }),
        "memory_fact_ids": [item.item_id for item in items],
        "memory_fact_versions": {item.item_id: item.version for item in items},
        "memory_episode_sources": [item.source_pointer for item in episodes],
    }


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
    if run.work_focus is not None:
        # P02/P33: the answer names the Work it was about, so a reload or a
        # replay can return to that exact record (ids only).
        from aichat.services.work_focus import public_focus

        canonical["work_focus"] = public_focus(run.work_focus)
    if run.pre_speech_status:
        # A3: an unrelated reply set a pending write aside; the voice route
        # speaks this before the routed answer.
        canonical["pre_speech_status"] = run.pre_speech_status
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
        actor=run.actor,
        **(
            {"target_machine_id": run.extras["record_target"]["id"]}
            if (run.extras.get("record_target") or {}).get("kind") == "machine"
            else {}
        ),
    )
    canonical = await service._attach_media_evidence(
        canonical,
        thread_id=run.thread.pk,
        turn_id=run.turn.pk,
        emitter=run.emitter,
    )
    canonical = await _attach_context_used(run, canonical)
    return await _attach_presentation(run, canonical)


async def _attach_presentation(run: TurnRun, canonical: CanonicalTurn) -> CanonicalTurn:
    """Optional final attachment after the existing evidence/proposal gates."""
    from ai.core.config import get_settings
    from ai.core.presentation.builder import build_presentation
    from ai.core.presentation.datasets import normalize_datasets
    from ai.core.presentation.library import library_available
    from ai.core.streaming import AGUIEvent, EventType

    if not getattr(get_settings(), "feature_chat_presentation", False) or not library_available():
        return canonical
    try:
        if (canonical.get("evidence_gate") or {}).get("verdict") == "pass":
            datasets = normalize_datasets([
                *(run.extras.get("presentation_trend_candidate") or []),
                *(run.extras.get("presentation_chart_candidate") or []),
            ])
            if datasets:
                canonical["presentation_data"] = datasets
        elif canonical.get("workflow_used") == "chat_presentation":
            datasets = normalize_datasets(canonical.pop("presentation_data", []))
            if datasets and all(item.get("origin") == "user_supplied" for item in datasets):
                canonical["presentation_data"] = datasets
        presentation = build_presentation(canonical)
        if presentation is None:
            canonical.pop("presentation_data", None)
            return canonical
        canonical["presentation"] = presentation
        if run.emitter is not None:
            await run.emitter.emit(
                AGUIEvent(
                    event_type=EventType.STATE_DELTA,
                    thread_id=str(run.thread.pk),
                    run_id=str(run.turn.pk),
                    data={
                        "kind": "presentation",
                        "presentation": presentation,
                        "presentation_data": canonical.get("presentation_data", []),
                    },
                )
            )
    except Exception:
        # Never log the program, labels or answer. Optional rendering cannot
        # fail an otherwise valid answer or trigger another business turn.
        canonical.pop("presentation", None)
        canonical.pop("presentation_data", None)
        logger.warning("presentation attachment unavailable", exc_info=False)
    return canonical


def _context_used_is_informative(record: dict[str, Any]) -> bool:
    """Whether the record discloses anything beyond "nothing was used"."""
    recent = record.get("recent_turns") or {}
    if isinstance(recent, dict) and int(recent.get("used") or 0) > 0:
        return True
    if record.get("summary") not in (None, "none"):
        return True
    corpora = record.get("corpora") or {}
    if isinstance(corpora, dict) and any(
        (entry or {}).get("state") != "not_consulted" for entry in corpora.values()
    ):
        return True
    if record.get("preferences_used") or record.get("facts_used"):
        return True
    return bool(record.get("truncation"))


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
    if run.emitter is not None and _context_used_is_informative(record):
        # The live frame follows the entity-manifest/media idiom: only when
        # there is something to disclose. A first turn that used nothing
        # keeps the AG-UI order golden (RUN_FINISHED last); the persisted
        # record still reloads through /threads.
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
            **_memory_lineage(run),
            "response_state": state,
            "events": canonical["events"],
            "spoken_summary": str(canonical.get("spoken_summary") or ""),
            **(
                {"presentation": canonical["presentation"]} if canonical.get("presentation") else {}
            ),
            **(
                {"presentation_data": canonical["presentation_data"]}
                if canonical.get("presentation_data")
                else {}
            ),
            # D0 (M1 entry baseline): content-free route facts for the battery
            # runner's layer-2 assertion — the intent enum value and whether
            # the classifier received a thread summary. Always written (null /
            # False are facts too); owner-only on the /threads projection.
            "task_intent": _task_intent_value(run),
            "conversation_summary_present": bool(
                run.extras.get("conversation_summary_present", False)
            ),
            # M1 (GR-33): content-free capability pack ids the turn ran with;
            # the next turn's recall statement reads them back (sticky prefix).
            "tool_packs": [
                pack_id
                for pack_id in (run.extras.get("tool_packs") or ())
                if isinstance(pack_id, str)
            ],
            # M1 (E35): which workflow answered, and whether routing continuity
            # pinned it for a follow-up fragment (content-free enum values).
            "workflow_used": (run.capture.workflow_id if run.capture else None) or None,
            "routing_continuity": run.extras.get("routing_continuity") or None,
            # S22: the card and its resolution ride message metadata so
            # the /threads projection can reproduce them on reload.
            **({"question": canonical["question"]} if canonical.get("question") else {}),
            **(
                {"question_resolution": canonical["question_resolution"]}
                if canonical.get("question_resolution")
                else {}
            ),
            # P02/P33: the verified Work identity reloads with the answer.
            **({"work_focus": canonical["work_focus"]} if canonical.get("work_focus") else {}),
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
