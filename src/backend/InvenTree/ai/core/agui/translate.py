"""Classic-dialect → AG-UI spec event translation (S49).

The translator is TOTAL over ``EventType``: every member has an explicit
disposition (pass, remap, wrap in CUSTOM, or skip) and a parametrized test
enforces totality — adding an enum member without a disposition fails CI.

Spec rules applied here:
- bare ``data: {json}\\n\\n`` SSE framing (no ``event:`` line, no [DONE]);
- ``timestamp`` is int epoch-milliseconds;
- camelCase payloads; the classic ``agentName``/``eventId`` extras and the
  per-event threadId/runId stamps never leave — lifecycle events echo the
  REQUEST ids (stored ids differ on replay, the spec requires the echo);
- non-spec payloads ride ``CUSTOM {name, value}`` on the ``aimms.*``
  channels so the official client's zod parse cannot strip them.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ai.core.streaming import EventType

#: Every CUSTOM channel the adapter emits. The wire-contract generator
#: publishes this as the AimmsCustomChannel TS union (S43 drift check).
CUSTOM_CHANNELS = (
    "aimms.error",
    "aimms.toolStatus",
    "aimms.question",
    "aimms.entities",
    "aimms.provenance",
    "aimms.stateDelta",
    "aimms.proposalsRefresh",
    "aimms.hitl",
    "aimms.custom",
)

#: Classic base-envelope keys that are NOT part of a record's payload.
_BASE_KEYS = {"type", "timestamp", "threadId", "runId", "agentName", "eventId"}

#: Members that never reach the spec wire (internal/informational/dead).
_SKIPPED = frozenset({
    EventType.AGENT_THINKING,
    EventType.AGENT_EXECUTING,
    EventType.AGENT_WAITING,
    EventType.AGENT_HANDOFF,
    EventType.PROGRESS_UPDATE,
    EventType.WORKFLOW_STARTED,
    EventType.WORKFLOW_COMPLETED,
    EventType.WORKFLOW_STEP,
    EventType.STATE_SNAPSHOT,
    EventType.CACHE_HIT,
    EventType.CACHE_MISS,
    EventType.WARNING,
    EventType.RAW,
    EventType.TOOL_CALL_RESULT,
})


def _timestamp_ms(record: dict[str, Any]) -> int | None:
    raw = record.get("timestamp")
    if not raw:
        return None
    try:
        return int(datetime.fromisoformat(str(raw)).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in _BASE_KEYS}


class SpecTranslator:
    """Stateful per-stream translator from classic records to spec events.

    State: RUN_STARTED dedupe (the route emits the synthetic spec one),
    and the tool/step observation bit that gates the proposals-refresh
    nudge on RUN_FINISHED (the ``sawToolEvents`` gate moved server-side).
    """

    def __init__(self, *, thread_id: str, run_id: str) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.saw_tool_events = False
        self.run_started_sent = False

    def _spec(self, event_type: str, record: dict[str, Any], **fields: Any) -> dict[str, Any]:
        out: dict[str, Any] = {"type": event_type}
        timestamp = _timestamp_ms(record)
        if timestamp is not None:
            out["timestamp"] = timestamp
        out.update(fields)
        return out

    def _custom(self, name: str, value: Any, record: dict[str, Any]) -> dict[str, Any]:
        return self._spec("CUSTOM", record, name=name, value=value)

    def run_started_frame(self) -> dict[str, Any]:
        """The synthetic spec RUN_STARTED the route emits as the first frame."""
        self.run_started_sent = True
        return {"type": "RUN_STARTED", "threadId": self.thread_id, "runId": self.run_id}

    def translate(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        """Translate one classic record into zero or more spec events."""
        try:
            event_type = EventType(str(record.get("type")))
        except ValueError:
            # Forward-compat: an unknown stored type is dropped, never leaked.
            return []
        payload = _payload(record)

        if event_type in _SKIPPED:
            return []

        if event_type == EventType.RUN_STARTED:
            if self.run_started_sent:
                return []
            return [self.run_started_frame()]
        if event_type == EventType.RUN_FINISHED:
            out = []
            if self.saw_tool_events:
                # S46 nudge: only runs that carried tool/step events may
                # trigger the proposals refetch (poll stays the backstop).
                out.append(self._custom("aimms.proposalsRefresh", {}, record))
            out.append(
                self._spec("RUN_FINISHED", record, threadId=self.thread_id, runId=self.run_id)
            )
            return out
        if event_type in (EventType.RUN_ERROR, EventType.ERROR):
            message = str(payload.get("message") or payload.get("error") or "AI turn failed")
            code = str(payload.get("code") or ("error" if event_type == EventType.ERROR else ""))
            detail = {
                "message": message,
                "code": code,
                "failureClass": payload.get("failure_class"),
                "localizedMessage": payload.get("localized_message"),
            }
            spec_error = self._spec("RUN_ERROR", record, message=message)
            if code:
                spec_error["code"] = code
            # CUSTOM first — the client run terminates on RUN_ERROR.
            return [self._custom("aimms.error", detail, record), spec_error]
        if event_type == EventType.RUN_CANCELLED:
            return [self._spec("RUN_ERROR", record, message="Run cancelled", code="run_cancelled")]

        if event_type == EventType.TEXT_MESSAGE_START:
            return [
                self._spec(
                    "TEXT_MESSAGE_START",
                    record,
                    messageId=str(payload.get("messageId") or ""),
                    role="assistant",
                )
            ]
        if event_type == EventType.TEXT_MESSAGE_CONTENT:
            delta = str(payload.get("delta") or "")
            if not delta:
                return []  # the spec forbids empty deltas
            return [
                self._spec(
                    "TEXT_MESSAGE_CONTENT",
                    record,
                    messageId=str(payload.get("messageId") or ""),
                    delta=delta,
                )
            ]
        if event_type == EventType.TEXT_MESSAGE_END:
            return [
                self._spec(
                    "TEXT_MESSAGE_END",
                    record,
                    messageId=str(payload.get("messageId") or ""),
                )
            ]
        if event_type == EventType.TEXT_MESSAGE_CHUNK:
            # Real AG-UI; no internal producer exists. Defensive passthrough.
            return [self._spec("TEXT_MESSAGE_CHUNK", record, **payload)]

        if event_type == EventType.TOOL_CALL_START:
            self.saw_tool_events = True
            return [
                self._spec(
                    "TOOL_CALL_START",
                    record,
                    toolCallId=str(payload.get("toolCallId") or ""),
                    toolCallName=str(payload.get("toolCallName") or ""),
                )
            ]
        if event_type == EventType.TOOL_CALL_ARGS:
            # Pinned unemitted internally; defensive spec passthrough.
            return [
                self._spec(
                    "TOOL_CALL_ARGS",
                    record,
                    toolCallId=str(payload.get("toolCallId") or ""),
                    delta=str(payload.get("delta") or ""),
                )
            ]
        if event_type == EventType.TOOL_CALL_END:
            self.saw_tool_events = True
            tool_call_id = str(payload.get("toolCallId") or "")
            # The S46 strip extras (name/status/duration) are non-spec on
            # TOOL_CALL_END — the client's schema parse would strip them, so
            # they ride the aimms.toolStatus CUSTOM channel.
            return [
                self._spec("TOOL_CALL_END", record, toolCallId=tool_call_id),
                self._custom(
                    "aimms.toolStatus",
                    {
                        "toolCallId": tool_call_id,
                        "toolCallName": payload.get("toolCallName"),
                        "status": payload.get("status"),
                        "durationMs": payload.get("durationMs"),
                    },
                    record,
                ),
            ]

        if event_type in (EventType.STEP_STARTED, EventType.STEP_FINISHED):
            self.saw_tool_events = True
            return [
                self._spec(event_type.value, record, stepName=str(payload.get("stepName") or ""))
            ]

        if event_type == EventType.QUESTION:
            return [self._custom("aimms.question", payload, record)]
        if event_type in (
            EventType.HITL_REQUIRED,
            EventType.HITL_APPROVED,
            EventType.HITL_REJECTED,
            EventType.HITL_TIMEOUT,
        ):
            phase = event_type.value.split("_", 1)[1].lower()
            return [self._custom("aimms.hitl", {"phase": phase, **payload}, record)]

        if event_type == EventType.STATE_DELTA:
            # NEVER spec STATE_DELTA — our payloads are not RFC-6902 patches
            # and the official client would try to json-patch them.
            kind = str(payload.get("kind") or "")
            if kind == "entity_manifest":
                return [
                    self._custom(
                        "aimms.entities", {"entities": payload.get("entities") or []}, record
                    )
                ]
            if kind == "diagnosis_provenance":
                return [
                    self._custom(
                        "aimms.provenance",
                        {
                            "confidence": payload.get("confidence"),
                            "evidence": payload.get("evidence") or [],
                        },
                        record,
                    )
                ]
            return [self._custom("aimms.stateDelta", payload, record)]
        if event_type == EventType.MESSAGES_SNAPSHOT:
            messages = []
            for index, entry in enumerate(payload.get("messages") or []):
                if not isinstance(entry, dict):
                    continue
                message = dict(entry)
                if not message.get("id"):
                    # The spec Message requires an id; synthesize
                    # deterministically so replays are byte-stable.
                    message["id"] = f"msg_{self.run_id}_{index}"
                messages.append(message)
            return [self._spec("MESSAGES_SNAPSHOT", record, messages=messages)]

        if event_type == EventType.CUSTOM:
            # Dead in our dialect; forward a well-formed pair, else wrap.
            if "name" in payload and "value" in payload:
                return [
                    self._spec("CUSTOM", record, name=str(payload["name"]), value=payload["value"])
                ]
            return [self._custom("aimms.custom", payload, record)]

        # Unreachable while the totality test holds; drop, never leak.
        return []  # pragma: no cover


def encode_sse(spec_event: dict[str, Any]) -> str:
    """Bare AG-UI SSE framing: one data: line, no event: line, no [DONE]."""
    return f"data: {json.dumps(spec_event, separators=(',', ':'))}\n\n"


class SpecSSEStream:
    """The SSEEventStream skeleton with translation at the dequeue edge.

    Same queue/keepalive behavior (15s ``: ping`` comments — legal SSE the
    AG-UI parser ignores); each dequeued classic event runs through the
    translator via ``event.to_dict()`` so live and replayed turns share one
    code path with the stored dialect.
    """

    def __init__(self, emitter: Any, *, thread_id: str, translator: SpecTranslator) -> None:
        import asyncio

        from ai.core.streaming import QueueEventHandler

        self.emitter = emitter
        self.thread_id = thread_id
        self.translator = translator
        self._queue: Any = asyncio.Queue()
        self._handler_cls = QueueEventHandler
        self._unsubscribe: Any = None

    async def start(self) -> None:
        if self._unsubscribe is not None:
            return
        handler = self._handler_cls(self._queue, self.thread_id)
        self._unsubscribe = await self.emitter.subscribe(handler)

    async def stop(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        await self._queue.put(None)

    async def frames(self) -> Any:
        """Yield translated spec SSE frames (async iterator of str)."""
        import asyncio

        await self.start()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(self._queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event is None:
                    break
                for spec_event in self.translator.translate(event.to_dict()):
                    yield encode_sse(spec_event)
        finally:
            await self.stop()
