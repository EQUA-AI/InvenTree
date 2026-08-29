"""Intake stage: validation → ambient binds → fingerprint → durable begin (S47).

Order is load-bearing and preserved from the pre-extraction code: input
validation raises BEFORE the usage ledger is rebound; the ledger and
correlation rebinds use the rebind-not-reset idiom so no state leaks across
turns even on early exit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ai.core.turn.request import _json_value, turn_request_fingerprint
from ai.core.turn.state import TurnRun
from ai.core.turn.types import TurnAlreadyRunning
from ai.core.usage import TurnUsageLedger, turn_usage_ledger
from aichat.models import TurnModality

if TYPE_CHECKING:
    from ai.core.auth import AIPrincipal
    from ai.core.trusted_context import TrustedTurnContext
    from ai.core.turn_service import NormalizedTurnService


async def begin(
    service: NormalizedTurnService,
    *,
    actor: AIPrincipal,
    thread_id: str | None,
    content: str,
    modality: str,
    trusted_context: TrustedTurnContext,
    modality_metadata: dict[str, Any] | None,
    idempotency_key: str,
    correlation_id: str,
    server_pinned_workflow: str | None,
    server_generation_target: dict[str, int] | None,
) -> TurnRun:
    """Validate the request and bind it to a durable thread + turn row."""

    if not content.strip():
        raise ValueError("turn content must not be empty")
    if modality not in TurnModality.values:
        raise ValueError("unsupported turn modality")
    if not idempotency_key.strip():
        raise ValueError("idempotency key is required")
    if server_pinned_workflow is not None and not server_pinned_workflow.strip():
        raise ValueError("server_pinned_workflow must be a workflow id when set")
    if server_generation_target is not None and (
        not isinstance(server_generation_target, dict)
        or not set(server_generation_target) <= {"machine_id", "repair_packet_id"}
        or not all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in server_generation_target.values()
        )
    ):
        raise ValueError("server_generation_target accepts int machine_id/repair_packet_id only")

    # S24: a fresh ledger per turn. Rebinding (rather than set/reset)
    # guarantees no cross-turn leakage even when a turn exits early —
    # the next turn always starts from an empty ledger.
    turn_usage_ledger.set(TurnUsageLedger())
    # S36: bind the turn's correlation id for infrastructure that logs
    # outside this call graph's arguments (reflection middleware, spans).
    # Same rebinding idiom as the ledger: never leaks across turns.
    from ai.core.correlation import bind_correlation

    bind_correlation(correlation_id)

    trusted = _json_value(trusted_context)
    metadata = _json_value(modality_metadata or {}, reject_audio=True)
    fingerprint = turn_request_fingerprint(
        content=content,
        modality=modality,
        trusted_context=trusted,
        modality_metadata=metadata,
    )

    repository = await service._call_sync(service.repository_factory, actor, trusted_context)
    thread, created = await service._call_sync(
        repository.get_or_create, thread_id, title=content.strip()[:255]
    )
    if not created and getattr(thread, "title", None) == "":
        thread = await service._call_sync(repository.rename, thread.pk, content.strip()[:255])
    begin_result = await service._call_sync(
        repository.begin_turn,
        thread.pk,
        content=content,
        modality=modality,
        trusted_context=trusted,
        modality_metadata=metadata,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        correlation_id=correlation_id,
    )

    run = TurnRun(
        actor=actor,
        trusted_context=trusted_context,
        content=content,
        modality=modality,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        server_pinned_workflow=server_pinned_workflow,
        server_generation_target=server_generation_target,
        trusted=trusted,
        metadata=metadata,
        repository=repository,
        thread=thread,
        turn=begin_result.turn,
        # S1: the scope snapshot ``begin_turn`` bound atomically with turn
        # creation. Replay carries the ORIGINAL turn's snapshot — a scope
        # change after the fact never rebinds a turn.
        analysis_scope=begin_result.scope_snapshot,
    )

    if begin_result.replayed:
        turn = begin_result.turn
        if not turn.is_terminal or not isinstance(turn.canonical_result, dict):
            raise TurnAlreadyRunning("turn with this idempotency key is running")
        run.replayed_canonical = turn.canonical_result
    return run
