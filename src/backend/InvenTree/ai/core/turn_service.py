"""The single normalized turn boundary shared by typed chat and voice.

This module deliberately owns orchestration, idempotency, and durable turn
lifecycle.  HTTP and future realtime transports are adapters around this
service; neither transport is allowed to select an identity or a tenant scope.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ai.core.reasoning.schemas import CanonicalTurnResponse
from ai.core.streaming import (
    AGUIEvent,
    EventEmitter,
    EventType,
    InMemoryEventEmitter,
)
from aichat.models import ThreadNamespace, TurnModality, TurnState
from aichat.services import IdempotencyConflict, ThreadRepository
from asgiref.sync import sync_to_async

if TYPE_CHECKING:
    from collections.abc import Callable

    from ai.core.auth import AIPrincipal
    from ai.core.trusted_context import TrustedTurnContext

logger = logging.getLogger(__name__)


class TurnAlreadyRunning(RuntimeError):
    """Raised when an idempotency key refers to a non-terminal turn."""


class TurnExecutionFailed(RuntimeError):
    """Value-free public failure raised after a durable failed transition."""


class TurnIncomplete(RuntimeError):
    """Signal that bounded processing ended without a valid final answer."""


@dataclass(frozen=True, slots=True)
class NormalizedTurnResult:
    """Transport-independent result persisted for every normalized turn."""

    thread_id: str
    turn_id: str
    message: str
    agent: str = "root_workflow"
    workflow_used: str | None = None
    response_state: str = TurnState.COMPLETE
    replayed: bool = False
    canonical_response: dict[str, Any] | None = None
    spoken_summary: str = ""
    reasoning_provenance: dict[str, Any] | None = None
    route: dict[str, Any] | None = None


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


def _reject_durable_audio(value: Any, *, path: str = "metadata") -> None:
    """Reject raw/audio-shaped values before any durable turn write."""

    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError("raw audio must not enter normalized turn persistence")
    if isinstance(value, dict):
        forbidden = {
            "audio",
            "audio_bytes",
            "audio_data",
            "audio_payload",
            "pcm",
            "waveform",
        }
        for key, item in value.items():
            if str(key).lower() in forbidden:
                raise ValueError("raw audio metadata is not permitted")
            _reject_durable_audio(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_durable_audio(item, path=f"{path}[{index}]")


def _json_value(value: Any, *, reject_audio: bool = False) -> dict[str, Any]:
    """Convert a trusted context object to a JSON-compatible dictionary."""

    if hasattr(value, "to_dict"):
        result = value.to_dict()
    elif hasattr(value, "model_dump"):
        result = value.model_dump(mode="json")
    elif is_dataclass(value):
        result = asdict(value)
    elif isinstance(value, dict):
        result = value
    else:  # pragma: no cover - defensive misuse guard
        raise TypeError("trusted context must be serializable")

    # A round-trip both validates portability and strips exotic mapping types.
    if reject_audio:
        _reject_durable_audio(result)
    try:
        normalized = json.loads(json.dumps(result, sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise ValueError("turn metadata must contain JSON values") from exc
    if not isinstance(normalized, dict):  # pragma: no cover - guarded above
        raise TypeError("trusted context must serialize to an object")
    return normalized


def turn_request_fingerprint(
    *,
    content: str,
    modality: str,
    trusted_context: dict[str, Any],
    modality_metadata: dict[str, Any],
) -> str:
    """Return the stable fingerprint bound to an idempotency key."""

    payload = json.dumps(
        {
            "content": content,
            "modality": modality,
            "trusted_context": trusted_context,
            "modality_metadata": modality_metadata,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


def _canonical_response_for_legacy(message: str) -> CanonicalTurnResponse:
    """Adapt existing workflow text without changing its visible rendering."""
    return CanonicalTurnResponse(
        kind="legacy_chat",
        response_version=1,
        response_state="complete",
        detailed_response=message or "No response was produced.",
        spoken_summary="",
        reasoning_summary=(
            "This text was produced by the selected legacy workflow. "
            "No hidden reasoning was persisted."
        ),
        confidence="low",
        evidence=[],
        next_questions=[],
        recommended_actions=[],
        safety_boundary=("No safety status was inferred; check the authoritative safety surface."),
        speak=False,
    )


def _canonical_terminal_response(state: str, message: str) -> CanonicalTurnResponse:
    """Return a strict, non-speaking response for a non-complete lifecycle state."""
    state_value = str(getattr(state, "value", state))
    return CanonicalTurnResponse(
        kind="repair_diagnosis",
        response_version=1,
        response_state=state_value,
        detailed_response=message,
        spoken_summary="",
        reasoning_summary=("The normalized turn ended without a complete diagnostic answer."),
        confidence="low",
        evidence=[],
        next_questions=[],
        recommended_actions=[],
        safety_boundary=("No safety status was inferred; check the authoritative safety surface."),
        speak=False,
    )


def _canonical_advisory_intent() -> CanonicalTurnResponse:
    """Explain effect wording without creating a proposal or executable action."""
    return CanonicalTurnResponse(
        kind="advisory_intent",
        response_version=1,
        response_state="complete",
        detailed_response=(
            "I can discuss that requested change, but this turn cannot create a "
            "proposal or perform an effect. Use the normal authenticated action "
            "surface for an allow-listed operation."
        ),
        spoken_summary="",
        reasoning_summary=("Effect-shaped wording was isolated as advisory intent only."),
        confidence="high",
        evidence=[],
        next_questions=["Would you like read-only guidance about the normal action surface?"],
        recommended_actions=[],
        safety_boundary=("This response does not change or confirm any safety status."),
        speak=False,
    )


class NormalizedTurnService:
    """Authorize, persist, execute, and finalize one normalized turn."""

    def __init__(
        self,
        *,
        workflow_factory: Callable[[], Any],
        repository_factory: Callable[[AIPrincipal, TrustedTurnContext], ThreadRepository]
        | None = None,
        proposal_transformer: Callable[..., Any] | None = None,
        complexity_router: Any | None = None,
        reasoning_adapter: Any | None = None,
        diagnostic_tool_registry: Any | None = None,
        diagnostic_context_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.workflow_factory = workflow_factory
        self.repository_factory = repository_factory or self._default_repository
        self.proposal_transformer = proposal_transformer
        self.complexity_router = complexity_router
        self.reasoning_adapter = reasoning_adapter
        self.diagnostic_tool_registry = diagnostic_tool_registry
        self.diagnostic_context_factory = diagnostic_context_factory

        # Preserve typed chat exactly while diagnosis is disabled.  When the
        # server flag is enabled, build the Foundry adapter lazily here so both
        # REST and future Voice transports still enter this one service.
        if self.complexity_router is None and self.reasoning_adapter is None:
            from ai.core.config import get_settings

            configured = get_settings()
            if configured.feature_voice_live_diagnosis:
                from ai.core.agents.voice_routing import VoiceComplexityRouter
                from ai.core.reasoning.luna_diagnostics import LunaDiagnosticsAdapter
                from ai.core.tools.diagnostics import get_diagnostic_tool_registry

                registry = get_diagnostic_tool_registry(
                    safety_p0_enabled=configured.repair_safety_p0s_closed,
                    max_result_bytes=min(
                        configured.azure_luna_diagnosis_max_tool_data_kb * 1024,
                        64 * 1024,
                    ),
                )
                self.complexity_router = VoiceComplexityRouter()
                self.diagnostic_tool_registry = registry
                self.reasoning_adapter = LunaDiagnosticsAdapter(tool_registry=registry)

    @staticmethod
    def _default_repository(
        actor: AIPrincipal, trusted_context: TrustedTurnContext
    ) -> ThreadRepository:
        """Bind the repository to server-derived scalar boundary values."""

        scope_key = trusted_context.server_policy_key
        return ThreadRepository(
            actor=actor.user_pk,
            scope_key=scope_key,
            namespace=ThreadNamespace.UNSCOPED,
        )

    @staticmethod
    async def _call_sync(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await sync_to_async(function, thread_sensitive=True)(*args, **kwargs)

    async def _emit_replay(
        self, emitter: EventEmitter | None, canonical_result: dict[str, Any]
    ) -> None:
        if emitter is None:
            return
        for record in canonical_result.get("events", []):
            if isinstance(record, dict):
                await emitter.emit(_event_from_record(record))

    async def _transform_proposals(
        self,
        canonical: dict[str, Any],
        *,
        actor: AIPrincipal,
        trusted_context: TrustedTurnContext,
    ) -> dict[str, Any]:
        """Apply the optional server-owned proposal transformation hook once."""

        if self.proposal_transformer is None:
            return canonical
        transformed = self.proposal_transformer(
            canonical_result=dict(canonical),
            actor=actor,
            trusted_context=trusted_context,
        )
        if inspect.isawaitable(transformed):
            transformed = await transformed
        if not isinstance(transformed, dict):
            raise TypeError("proposal transformer must return a canonical object")
        return transformed

    async def _build_diagnostic_context(
        self,
        *,
        actor: AIPrincipal,
        trusted_context: TrustedTurnContext,
        content: str,
        modality: str,
    ) -> Any | None:
        """Resolve an optional server record root without consuming client hints."""
        if self.diagnostic_context_factory is None:
            return None
        context = self.diagnostic_context_factory(
            actor=actor,
            trusted_context=trusted_context,
            content=content,
            modality=modality,
        )
        if inspect.isawaitable(context):
            context = await context
        return context

    def _route_turn(
        self,
        *,
        actor: AIPrincipal,
        trusted_context: TrustedTurnContext,
        content: str,
        modality: str,
        modality_metadata: dict[str, Any],
        diagnostic_context: Any | None,
    ) -> Any | None:
        """Apply the explicit policy using only server-owned routing inputs."""
        if self.complexity_router is None:
            return None
        from ai.core.agents.voice_routing import (
            RiskLevel,
            VoiceRoutingContext,
            VoiceRoutingRequest,
        )

        allowed_tools: list[str] = []
        if diagnostic_context is not None and self.diagnostic_tool_registry is not None:
            capabilities = set(getattr(diagnostic_context, "capabilities", ()))
            for definition in getattr(self.diagnostic_tool_registry, "definitions", ()):
                if getattr(definition, "capability", None) in capabilities:
                    allowed_tools.append(str(definition.name))

        confidence = 1.0
        if modality == TurnModality.VOICE:
            candidate = modality_metadata.get("transcription_confidence")
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                confidence = max(0.0, min(1.0, float(candidate)))

        capabilities = tuple(trusted_context.allowed_capabilities)
        risk = RiskLevel.LOW
        if "diagnostics.risk.critical" in capabilities:
            risk = RiskLevel.CRITICAL
        elif "diagnostics.risk.high" in capabilities:
            risk = RiskLevel.HIGH
        elif "diagnostics.risk.medium" in capabilities:
            risk = RiskLevel.MEDIUM

        actor_role = (
            "administrator" if actor.is_superuser else "staff" if actor.is_staff else "technician"
        )
        routing_context = VoiceRoutingContext(
            actor_role=actor_role,
            actor_scope=trusted_context.server_policy_key,
            transcription_confidence=confidence,
            risk=risk,
            allowed_tools=tuple(allowed_tools),
            allowed_capabilities=capabilities,
        )
        # Authority-shaped browser fields are intentionally never copied into
        # the request. The router receives final content only.
        return self.complexity_router.route(
            VoiceRoutingRequest(final_content=content), routing_context
        )

    @staticmethod
    async def _emit_canonical_events(
        *,
        emitter: EventEmitter,
        thread_id: str,
        run_id: str,
        workflow_id: str,
        workflow_name: str,
        message: str,
        response_state: str,
    ) -> None:
        """Emit the standard AG-UI lifecycle for a non-legacy WS3 route."""
        message_id = f"{run_id}:response"
        events = (
            AGUIEvent(
                event_type=EventType.RUN_STARTED,
                data={"message": "Starting run"},
                thread_id=thread_id,
                run_id=run_id,
                agent_name="root_workflow",
            ),
            AGUIEvent(
                event_type=EventType.WORKFLOW_STARTED,
                data={
                    "workflow_id": workflow_id,
                    "workflow_name": workflow_name,
                },
                thread_id=thread_id,
                run_id=run_id,
                agent_name="root_workflow",
            ),
            AGUIEvent(
                event_type=EventType.TEXT_MESSAGE_START,
                data={"messageId": message_id, "role": "assistant"},
                thread_id=thread_id,
                run_id=run_id,
                agent_name="root_workflow",
            ),
            AGUIEvent(
                event_type=EventType.TEXT_MESSAGE_CONTENT,
                data={"messageId": message_id, "delta": message},
                thread_id=thread_id,
                run_id=run_id,
                agent_name="root_workflow",
            ),
            AGUIEvent(
                event_type=EventType.TEXT_MESSAGE_END,
                data={"messageId": message_id},
                thread_id=thread_id,
                run_id=run_id,
                agent_name="root_workflow",
            ),
            AGUIEvent(
                event_type=EventType.RUN_FINISHED,
                data={"response_state": response_state},
                thread_id=thread_id,
                run_id=run_id,
                agent_name="root_workflow",
            ),
        )
        for event in events:
            await emitter.emit(event)

    async def _reasoning_canonical(
        self,
        *,
        actor: AIPrincipal,
        trusted_context: TrustedTurnContext,
        thread_id: str,
        turn_id: str,
        content: str,
        modality: str,
        route: Any,
        diagnostic_context: Any | None,
        emitter: EventEmitter,
    ) -> dict[str, Any]:
        """Invoke the Foundry adapter and return the durable wrapper."""
        from ai.core.reasoning.luna_diagnostics import TrustedReasoningEnvelope

        machine_id: int | None = None
        repair_packet_id: int | None = None
        if diagnostic_context is not None:
            for root in getattr(diagnostic_context, "record_roots", ()):
                if getattr(root, "entity_type", None) == "machine":
                    machine_id = int(root.entity_id)
                elif getattr(root, "entity_type", None) == "repair_packet":
                    repair_packet_id = int(root.entity_id)
                    machine_id = int(root.linked_machine_id)

        allowed_tools = tuple(
            definition.name
            for definition in getattr(self.diagnostic_tool_registry, "definitions", ())
            if diagnostic_context is not None
            and definition.capability in set(getattr(diagnostic_context, "capabilities", ()))
        )
        envelope = TrustedReasoningEnvelope(
            actor_id=actor.actor,
            scope={"policy_key": trusted_context.server_policy_key},
            thread_id=thread_id,
            machine_id=machine_id,
            repair_packet_id=repair_packet_id,
            user_message=content,
            mode=modality,
            allowed_tool_names=allowed_tools,
            policy_version=trusted_context.policy_version,
            correlation_id=trusted_context.correlation_id,
        )
        outcome = await self.reasoning_adapter.reason(
            envelope=envelope,
            tool_context=diagnostic_context,
            effort=route.effort.value,
        )
        response = outcome.response
        message = response.detailed_response
        route_record = route.to_dict()
        run_id = f"reasoning:{turn_id}"
        await self._emit_canonical_events(
            emitter=emitter,
            thread_id=thread_id,
            run_id=run_id,
            workflow_id=route.target_workflow_id or "wf1",
            workflow_name="FOUNDRY_DIAGNOSTICS",
            message=message,
            response_state=response.response_state.value,
        )
        return {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "message": message,
            "agent": "foundry_voice_agent",
            "workflow_used": route.target_workflow_id or "wf1",
            "response_state": response.response_state.value,
            "canonical_response": response.model_dump(mode="json"),
            "spoken_summary": response.spoken_summary,
            "reasoning_provenance": outcome.provenance.to_dict(),
            "route": route_record,
        }

    async def process(
        self,
        *,
        actor: AIPrincipal,
        thread_id: str | None,
        content: str,
        modality: str,
        trusted_context: TrustedTurnContext,
        modality_metadata: dict[str, Any] | None,
        idempotency_key: str,
        correlation_id: str,
        emitter: EventEmitter | None = None,
    ) -> NormalizedTurnResult:
        """Process one idempotent turn through the common reasoning path."""

        if not content.strip():
            raise ValueError("turn content must not be empty")
        if modality not in TurnModality.values:
            raise ValueError("unsupported turn modality")
        if not idempotency_key.strip():
            raise ValueError("idempotency key is required")

        trusted = _json_value(trusted_context)
        metadata = _json_value(modality_metadata or {}, reject_audio=True)
        fingerprint = turn_request_fingerprint(
            content=content,
            modality=modality,
            trusted_context=trusted,
            modality_metadata=metadata,
        )

        repository = await self._call_sync(self.repository_factory, actor, trusted_context)
        thread, created = await self._call_sync(
            repository.get_or_create, thread_id, title=content.strip()[:255]
        )
        if not created and getattr(thread, "title", None) == "":
            thread = await self._call_sync(repository.rename, thread.pk, content.strip()[:255])
        begin = await self._call_sync(
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

        if begin.replayed:
            turn = begin.turn
            if not turn.is_terminal or not isinstance(turn.canonical_result, dict):
                raise TurnAlreadyRunning("turn with this idempotency key is running")
            await self._emit_replay(emitter, turn.canonical_result)
            return self._result_from_canonical(
                thread.pk, turn.pk, turn.canonical_result, replayed=True
            )

        turn = begin.turn
        isolated_emitter = emitter or InMemoryEventEmitter()
        capture = _EventCapture(thread.pk)
        unsubscribe = await isolated_emitter.subscribe(capture)
        chunks: list[str] = []

        try:
            diagnostic_context = await self._build_diagnostic_context(
                actor=actor,
                trusted_context=trusted_context,
                content=content,
                modality=modality,
            )
            route = self._route_turn(
                actor=actor,
                trusted_context=trusted_context,
                content=content,
                modality=modality,
                modality_metadata=metadata,
                diagnostic_context=diagnostic_context,
            )

            route_mode = getattr(getattr(route, "mode", None), "value", None)
            if route_mode == "reasoning" and self.reasoning_adapter is not None:
                canonical = await self._reasoning_canonical(
                    actor=actor,
                    trusted_context=trusted_context,
                    thread_id=thread.pk,
                    turn_id=turn.pk,
                    content=content,
                    modality=modality,
                    route=route,
                    diagnostic_context=diagnostic_context,
                    emitter=isolated_emitter,
                )
            elif route_mode == "advisory_intent":
                response = _canonical_advisory_intent()
                message = response.detailed_response
                await self._emit_canonical_events(
                    emitter=isolated_emitter,
                    thread_id=thread.pk,
                    run_id=f"advisory:{turn.pk}",
                    workflow_id="advisory_intent",
                    workflow_name="ADVISORY_INTENT",
                    message=message,
                    response_state=response.response_state.value,
                )
                canonical = {
                    "thread_id": thread.pk,
                    "turn_id": turn.pk,
                    "message": message,
                    "agent": "complexity_router",
                    "workflow_used": "advisory_intent",
                    "response_state": response.response_state.value,
                    "canonical_response": response.model_dump(mode="json"),
                    "spoken_summary": response.spoken_summary,
                    "reasoning_provenance": None,
                    "route": route.to_dict(),
                }
            else:
                workflow = self.workflow_factory()
                workflow_context = dict(trusted)
                workflow_context["modality"] = modality
                # Client hints remain visibly and semantically untrusted. They
                # are nested so no caller value can overwrite a server field.
                untrusted_context = metadata.get("untrusted_client_context")
                if isinstance(untrusted_context, dict):
                    workflow_context["untrusted_client_context"] = untrusted_context
                uploaded_files = metadata.get("uploaded_files")
                if isinstance(uploaded_files, list):
                    workflow_context["uploaded_files"] = uploaded_files

                async for chunk in workflow.run_stream(
                    message=content,
                    emitter=isolated_emitter,
                    thread_id=thread.pk,
                    user_id=actor.user_pk,
                    context=workflow_context,
                ):
                    chunks.append(str(chunk))

                message = "".join(chunks)
                response = _canonical_response_for_legacy(message)
                canonical = {
                    "thread_id": thread.pk,
                    "turn_id": turn.pk,
                    "message": message,
                    "agent": "root_workflow",
                    "workflow_used": capture.workflow_id,
                    "response_state": TurnState.COMPLETE,
                    "canonical_response": response.model_dump(mode="json"),
                    "spoken_summary": response.spoken_summary,
                    "reasoning_provenance": None,
                    "route": route.to_dict() if route is not None else None,
                }
            canonical["events"] = capture.events
            canonical = await self._transform_proposals(
                canonical,
                actor=actor,
                trusted_context=trusted_context,
            )
            message = str(canonical.get("message") or "")
            response_state = str(canonical.get("response_state") or TurnState.COMPLETE)
            finalized = await self._call_sync(
                repository.terminal,
                turn.pk,
                state=response_state,
                canonical_result=canonical,
                output_content=message,
                output_metadata={
                    "response_state": response_state,
                    "events": capture.events,
                    "spoken_summary": str(canonical.get("spoken_summary") or ""),
                },
                workflow_id=capture.workflow_id or "",
            )
            return self._result_from_canonical(thread.pk, finalized.pk, canonical, replayed=False)
        except asyncio.CancelledError:
            await isolated_emitter.emit(
                AGUIEvent(
                    event_type=EventType.RUN_CANCELLED,
                    data={"message": "Run cancelled"},
                    thread_id=thread.pk,
                )
            )
            response = _canonical_terminal_response(
                TurnState.CANCELED,
                "The request was canceled before a complete answer was produced.",
            )
            canonical = {
                "thread_id": thread.pk,
                "turn_id": turn.pk,
                "message": response.detailed_response,
                "agent": "root_workflow",
                "workflow_used": capture.workflow_id,
                "response_state": TurnState.CANCELED,
                "canonical_response": response.model_dump(mode="json"),
                "spoken_summary": "",
                "reasoning_provenance": None,
                "route": None,
                "events": capture.events,
            }
            await asyncio.shield(
                self._call_sync(
                    repository.terminal,
                    turn.pk,
                    state=TurnState.CANCELED,
                    canonical_result=canonical,
                    output_content=response.detailed_response,
                    output_metadata={
                        "response_state": TurnState.CANCELED,
                        "events": capture.events,
                        "spoken_summary": "",
                    },
                    workflow_id=capture.workflow_id or "",
                )
            )
            raise
        except TurnIncomplete:
            await isolated_emitter.emit(
                AGUIEvent(
                    event_type=EventType.RUN_ERROR,
                    data={
                        "message": "AI turn incomplete",
                        "code": "turn_incomplete",
                    },
                    thread_id=thread.pk,
                )
            )
            response = _canonical_terminal_response(
                TurnState.INCOMPLETE,
                "The bounded diagnostic review ended before a complete answer was produced.",
            )
            canonical = {
                "thread_id": thread.pk,
                "turn_id": turn.pk,
                "message": response.detailed_response,
                "agent": "root_workflow",
                "workflow_used": capture.workflow_id,
                "response_state": TurnState.INCOMPLETE,
                "canonical_response": response.model_dump(mode="json"),
                "spoken_summary": "",
                "reasoning_provenance": None,
                "route": None,
                "events": capture.events,
            }
            finalized = await self._call_sync(
                repository.terminal,
                turn.pk,
                state=TurnState.INCOMPLETE,
                canonical_result=canonical,
                output_content=response.detailed_response,
                output_metadata={
                    "response_state": TurnState.INCOMPLETE,
                    "events": capture.events,
                    "spoken_summary": "",
                },
                workflow_id=capture.workflow_id or "",
            )
            return self._result_from_canonical(thread.pk, finalized.pk, canonical, replayed=False)
        except (IdempotencyConflict, TurnAlreadyRunning):
            raise
        except Exception as exc:
            # Error details are deliberately absent from the durable public
            # result and logs; provider exceptions may contain credentials or
            # customer text.
            logger.error(
                "Normalized AI turn failed (turn_id=%s, correlation_id=%s, error_type=%s)",
                turn.pk,
                correlation_id,
                type(exc).__name__,
            )
            await isolated_emitter.emit(
                AGUIEvent(
                    event_type=EventType.RUN_ERROR,
                    data={"message": "AI turn failed", "code": "turn_failed"},
                    thread_id=thread.pk,
                )
            )
            response = _canonical_terminal_response(
                TurnState.FAILED,
                "The diagnostic turn failed before a complete answer was produced.",
            )
            canonical = {
                "thread_id": thread.pk,
                "turn_id": turn.pk,
                "message": response.detailed_response,
                "agent": "root_workflow",
                "workflow_used": capture.workflow_id,
                "response_state": TurnState.FAILED,
                "canonical_response": response.model_dump(mode="json"),
                "spoken_summary": "",
                "reasoning_provenance": None,
                "route": None,
                "events": capture.events,
            }
            await self._call_sync(
                repository.terminal,
                turn.pk,
                state=TurnState.FAILED,
                canonical_result=canonical,
                output_content=response.detailed_response,
                output_metadata={
                    "response_state": TurnState.FAILED,
                    "events": capture.events,
                    "spoken_summary": "",
                },
                workflow_id=capture.workflow_id or "",
            )
            raise TurnExecutionFailed("AI turn failed") from None
        finally:
            unsubscribe()

    @staticmethod
    def _result_from_canonical(
        thread_id: str,
        turn_id: str,
        canonical: dict[str, Any],
        *,
        replayed: bool,
    ) -> NormalizedTurnResult:
        canonical_response = canonical.get("canonical_response")
        reasoning_provenance = canonical.get("reasoning_provenance")
        route = canonical.get("route")
        return NormalizedTurnResult(
            thread_id=thread_id,
            turn_id=turn_id,
            message=str(canonical.get("message") or ""),
            agent=str(canonical.get("agent") or "root_workflow"),
            workflow_used=(
                str(canonical["workflow_used"]) if canonical.get("workflow_used") else None
            ),
            response_state=str(canonical.get("response_state") or TurnState.COMPLETE),
            replayed=replayed,
            canonical_response=(
                dict(canonical_response) if isinstance(canonical_response, dict) else None
            ),
            spoken_summary=str(canonical.get("spoken_summary") or ""),
            reasoning_provenance=(
                dict(reasoning_provenance) if isinstance(reasoning_provenance, dict) else None
            ),
            route=dict(route) if isinstance(route, dict) else None,
        )


__all__ = [
    "IdempotencyConflict",
    "NormalizedTurnResult",
    "NormalizedTurnService",
    "TurnAlreadyRunning",
    "TurnExecutionFailed",
    "TurnIncomplete",
    "turn_request_fingerprint",
]
