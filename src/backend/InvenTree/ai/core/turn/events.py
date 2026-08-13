"""Per-turn event capture and durable-event normalization (S47).

Moved verbatim from ``ai.core.turn_service``. The stored event dialect is
FROZEN: ``_EventCapture`` stores ``AGUIEvent.to_dict()`` bytes and
``_event_from_record`` must reproduce them exactly on idempotency replay.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ai.core.streaming import AGUIEvent, EventType


class _EventCapture:
    """Capture exactly the events emitted by one isolated turn emitter."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.events: list[dict[str, Any]] = []
        self.workflow_id: str | None = None

    async def handle(self, event: AGUIEvent) -> None:
        if event.thread_id and event.thread_id != self.thread_id:
            return
        record = event.to_dict()
        self.events.append(record)
        if event.event_type == EventType.WORKFLOW_STARTED:
            workflow_id = event.data.get("workflow_id")
            if workflow_id:
                self.workflow_id = str(workflow_id)


def coalesce_text_deltas(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse streamed text deltas for durable storage (S45).

    Token streaming multiplies TEXT_MESSAGE_CONTENT records; stored turns
    must keep TODAY'S single-delta shape so idempotency replay stays
    byte-compatible with every existing turn and stale client, and canonical
    JSON growth stays zero. Consecutive TEXT_MESSAGE_CONTENT records with
    the same messageId merge into one. When a MESSAGES_SNAPSHOT (the S45
    reconciliation event) carries the final assistant text, it supersedes
    EVERY delta of the final message — S46 tool records split the deltas
    into non-adjacent groups, and rewriting only the last group would
    replay superseded text alongside the final one. If no delta exists at
    all, the snapshot record is KEPT: it is then the only carrier of the
    turn's text, and the client renders it via its MESSAGES_SNAPSHOT case.
    """
    snapshot_content: str | None = None
    snapshot_record: dict[str, Any] | None = None
    for record in events:
        if record.get("type") == EventType.MESSAGES_SNAPSHOT.value:
            for entry in record.get("messages") or []:
                if isinstance(entry, dict) and entry.get("role") == "assistant":
                    snapshot_content = str(entry.get("content") or "")
                    snapshot_record = record

    coalesced: list[dict[str, Any]] = []
    for record in events:
        if record.get("type") == EventType.MESSAGES_SNAPSHOT.value:
            continue
        if (
            record.get("type") == EventType.TEXT_MESSAGE_CONTENT.value
            and coalesced
            and coalesced[-1].get("type") == EventType.TEXT_MESSAGE_CONTENT.value
            and coalesced[-1].get("messageId") == record.get("messageId")
        ):
            merged = dict(coalesced[-1])
            merged["delta"] = str(merged.get("delta") or "") + str(record.get("delta") or "")
            coalesced[-1] = merged
            continue
        coalesced.append(record)

    if snapshot_content is None:
        return coalesced

    text_indices = [
        index
        for index, record in enumerate(coalesced)
        if record.get("type") == EventType.TEXT_MESSAGE_CONTENT.value
    ]
    if not text_indices:
        if snapshot_record is not None:
            coalesced.append(snapshot_record)
        return coalesced

    final_message_id = coalesced[text_indices[-1]].get("messageId")
    superseded = [
        index for index in text_indices if coalesced[index].get("messageId") == final_message_id
    ]
    replaced = dict(coalesced[superseded[0]])
    replaced["delta"] = snapshot_content
    coalesced[superseded[0]] = replaced
    for index in reversed(superseded[1:]):
        del coalesced[index]
    return coalesced


def _event_from_record(record: dict[str, Any]) -> AGUIEvent:
    """Rehydrate a persisted event without changing its public SSE payload."""

    base_keys = {
        "type",
        "timestamp",
        "threadId",
        "runId",
        "agentName",
        "eventId",
    }
    timestamp = record.get("timestamp")
    parsed_timestamp = datetime.fromisoformat(str(timestamp)) if timestamp else None
    kwargs: dict[str, Any] = {
        "event_type": EventType(str(record["type"])),
        "data": {key: value for key, value in record.items() if key not in base_keys},
        "thread_id": str(record.get("threadId") or ""),
        "run_id": str(record.get("runId") or ""),
        "agent_name": str(record.get("agentName") or ""),
        "event_id": str(record.get("eventId") or ""),
    }
    if parsed_timestamp is not None:
        kwargs["timestamp"] = parsed_timestamp
    return AGUIEvent(**kwargs)
