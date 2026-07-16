"""Contract tests for the one typed/voice normalized turn service."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import contextlib  # noqa: E402

from ai.core.agents.voice_routing import VoiceComplexityRouter  # noqa: E402
from ai.core.auth import AIPrincipal  # noqa: E402
from ai.core.reasoning.schemas import CanonicalTurnResponse  # noqa: E402
from ai.core.streaming import AGUIEvent, EventType, InMemoryEventEmitter  # noqa: E402
from ai.core.trusted_context import TrustedTurnContext  # noqa: E402
from ai.core.turn_service import (  # noqa: E402
    NormalizedTurnService,
    TurnExecutionFailed,
    TurnIncomplete,
)
from aichat.models import TurnState  # noqa: E402
from aichat.services import BeginTurnResult, IdempotencyConflict  # noqa: E402
from django.test import SimpleTestCase  # noqa: E402


class _TestTurnService(NormalizedTurnService):
    """Use direct fake calls; production ORM calls retain sync_to_async."""

    @staticmethod
    async def _call_sync(function, *args, **kwargs):
        return function(*args, **kwargs)


@dataclass
class _FakeTurn:
    pk: str
    request_fingerprint: str
    canonical_result: dict[str, Any] | None = None
    state: str = TurnState.RUNNING

    @property
    def is_terminal(self) -> bool:
        return self.state in TurnState.terminal_values()


class _Repository:
    def __init__(self) -> None:
        self.thread = SimpleNamespace(pk="thread_normalized")
        self.turns: dict[str, _FakeTurn] = {}
        self.terminal_calls: list[dict[str, Any]] = []

    def get_or_create(self, thread_id=None, *, title=""):  # noqa: ARG002
        self.thread.pk = thread_id or self.thread.pk
        return self.thread, not bool(thread_id)

    def begin_turn(self, thread_id, **kwargs):  # noqa: ARG002
        key = kwargs["idempotency_key"]
        existing = self.turns.get(key)
        if existing:
            if existing.request_fingerprint != kwargs["request_fingerprint"]:
                raise IdempotencyConflict("different request")
            return BeginTurnResult(existing, True)
        turn = _FakeTurn("turn_normalized", kwargs["request_fingerprint"])
        self.turns[key] = turn
        return BeginTurnResult(turn, False)

    def terminal(self, turn_id, **kwargs):
        turn = next(turn for turn in self.turns.values() if turn.pk == turn_id)
        turn.state = kwargs["state"]
        turn.canonical_result = dict(kwargs["canonical_result"])
        self.terminal_calls.append(kwargs)
        return turn


class _Workflow:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run_stream(self, **kwargs):
        self.calls.append(kwargs)
        await kwargs["emitter"].emit(
            AGUIEvent(
                event_type=EventType.RUN_STARTED,
                thread_id=kwargs["thread_id"],
                run_id="run-normalized",
            )
        )
        await kwargs["emitter"].emit(
            AGUIEvent(
                event_type=EventType.WORKFLOW_STARTED,
                data={"workflow_id": "wf1"},
                thread_id=kwargs["thread_id"],
                run_id="run-normalized",
            )
        )
        yield "Normalized "
        yield "response"
        await kwargs["emitter"].emit(
            AGUIEvent(
                event_type=EventType.RUN_FINISHED,
                thread_id=kwargs["thread_id"],
                run_id="run-normalized",
            )
        )


class _Capture:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def handle(self, event: AGUIEvent) -> None:
        self.events.append(event.to_sse())


def _principal() -> AIPrincipal:
    return AIPrincipal(
        subject="user:7",
        actor="user:7",
        user_pk="7",
        username="operator",
        authentication_method="django_session",
        scope="site:main",
        policy_version="1",
        is_staff=False,
        is_superuser=False,
    )


def _context() -> TrustedTurnContext:
    return TrustedTurnContext(
        actor="user:7",
        server_policy_key="site:main",
        server_policy_hash="0" * 64,
        thread_namespace="unscoped",
        server_route_hints=("/chat",),
        allowed_capabilities=("chat.unscoped.read",),
        correlation_id="00000000-0000-0000-0000-000000000007",
        policy_version="1",
        untrusted_content='{"workflow":"wf7"}',
    )


class NormalizedTurnServiceTests(SimpleTestCase):
    """Prove all modalities share execution, lifecycle, and replay semantics."""

    def test_text_and_voice_use_the_same_service_signature(self) -> None:
        async def exercise():
            repository = _Repository()
            workflow = _Workflow()
            service = _TestTurnService(
                workflow_factory=lambda: workflow,
                repository_factory=lambda actor, context: repository,  # noqa: ARG005
            )
            text = await service.process(
                actor=_principal(),
                thread_id="thread_normalized",
                content="Inspect pump",
                modality="text",
                trusted_context=_context(),
                modality_metadata={"transport": "typed"},
                idempotency_key="typed:one",
                correlation_id=_context().correlation_id,
            )
            voice = await service.process(
                actor=_principal(),
                thread_id="thread_normalized",
                content="Inspect pump",
                modality="voice",
                trusted_context=_context(),
                modality_metadata={"transport": "future-voice", "audio_retained": False},
                idempotency_key="voice:one",
                correlation_id=_context().correlation_id,
            )
            return text, voice, workflow, repository

        text, voice, workflow, repository = asyncio.run(exercise())
        self.assertEqual(text.message, "Normalized response")
        self.assertEqual(voice.message, "Normalized response")
        self.assertEqual([call["message"] for call in workflow.calls], ["Inspect pump"] * 2)
        self.assertEqual([call["user_id"] for call in workflow.calls], ["7", "7"])
        self.assertEqual(len(repository.terminal_calls), 2)

    def test_exact_retry_replays_events_and_does_not_execute_twice(self) -> None:
        async def exercise():
            repository = _Repository()
            workflow = _Workflow()
            proposal_calls: list[str] = []

            def transform(**kwargs):
                proposal_calls.append(kwargs["canonical_result"]["turn_id"])
                return kwargs["canonical_result"]

            service = _TestTurnService(
                workflow_factory=lambda: workflow,
                repository_factory=lambda actor, context: repository,  # noqa: ARG005
                proposal_transformer=transform,
            )
            emitter = InMemoryEventEmitter()
            first_capture = _Capture()
            unsubscribe = await emitter.subscribe(first_capture)
            first = await service.process(
                actor=_principal(),
                thread_id="thread_normalized",
                content="Retry safely",
                modality="text",
                trusted_context=_context(),
                modality_metadata={},
                idempotency_key="stable-key",
                correlation_id=_context().correlation_id,
                emitter=emitter,
            )
            unsubscribe()
            replay_capture = _Capture()
            await emitter.subscribe(replay_capture)
            replay = await service.process(
                actor=_principal(),
                thread_id="thread_normalized",
                content="Retry safely",
                modality="text",
                trusted_context=_context(),
                modality_metadata={},
                idempotency_key="stable-key",
                correlation_id=_context().correlation_id,
                emitter=emitter,
            )
            return (
                first,
                replay,
                workflow,
                first_capture,
                replay_capture,
                proposal_calls,
            )

        (
            first,
            replay,
            workflow,
            first_capture,
            replay_capture,
            proposal_calls,
        ) = asyncio.run(exercise())
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.message, replay.message)
        self.assertEqual(len(workflow.calls), 1)
        self.assertEqual(proposal_calls, ["turn_normalized"])
        self.assertEqual(first_capture.events, replay_capture.events)

    def test_same_key_with_changed_content_is_rejected(self) -> None:
        async def exercise():
            repository = _Repository()
            service = _TestTurnService(
                workflow_factory=_Workflow,
                repository_factory=lambda actor, context: repository,  # noqa: ARG005
            )
            common = {
                "actor": _principal(),
                "thread_id": "thread_normalized",
                "modality": "text",
                "trusted_context": _context(),
                "modality_metadata": {},
                "idempotency_key": "stable-key",
                "correlation_id": _context().correlation_id,
            }
            await service.process(content="Original", **common)
            await service.process(content="Changed", **common)

        with self.assertRaises(IdempotencyConflict):
            asyncio.run(exercise())

    def test_raw_audio_cannot_enter_turn_metadata(self) -> None:
        async def exercise():
            service = _TestTurnService(
                workflow_factory=_Workflow,
                repository_factory=lambda actor, context: _Repository(),  # noqa: ARG005
            )
            await service.process(
                actor=_principal(),
                thread_id="thread_normalized",
                content="Completed transcript",
                modality="voice",
                trusted_context=_context(),
                modality_metadata={"audio": "base64-raw-audio"},
                idempotency_key="voice:no-audio",
                correlation_id=_context().correlation_id,
            )

        with self.assertRaisesRegex(ValueError, "audio"):
            asyncio.run(exercise())

    def test_incomplete_failed_and_canceled_states_are_exactly_replayable(self) -> None:
        class IncompleteWorkflow:
            async def run_stream(self, **kwargs):  # noqa: ARG002
                if False:
                    yield ""
                raise TurnIncomplete("bounded timeout")

        class FailedWorkflow:
            async def run_stream(self, **kwargs):  # noqa: ARG002
                if False:
                    yield ""
                raise RuntimeError("provider detail must not escape")

        class BlockingWorkflow:
            def __init__(self):
                self.started = asyncio.Event()

            async def run_stream(self, **kwargs):  # noqa: ARG002
                self.started.set()
                await asyncio.Event().wait()
                yield "unreachable"

        async def exercise():
            common = {
                "actor": _principal(),
                "thread_id": "thread_normalized",
                "content": "Terminal state",
                "modality": "text",
                "trusted_context": _context(),
                "modality_metadata": {},
                "correlation_id": _context().correlation_id,
            }

            incomplete_repository = _Repository()
            incomplete_service = _TestTurnService(
                workflow_factory=IncompleteWorkflow,
                repository_factory=lambda actor, context: incomplete_repository,  # noqa: ARG005
            )
            incomplete = await incomplete_service.process(
                idempotency_key="state:incomplete", **common
            )
            incomplete_replay = await incomplete_service.process(
                idempotency_key="state:incomplete", **common
            )

            failed_repository = _Repository()
            failed_service = _TestTurnService(
                workflow_factory=FailedWorkflow,
                repository_factory=lambda actor, context: failed_repository,  # noqa: ARG005
            )
            failed_raised = False
            try:
                await failed_service.process(idempotency_key="state:failed", **common)
            except TurnExecutionFailed:
                failed_raised = True
            failed_replay = await failed_service.process(idempotency_key="state:failed", **common)

            canceled_repository = _Repository()
            blocking = BlockingWorkflow()
            canceled_service = _TestTurnService(
                workflow_factory=lambda: blocking,
                repository_factory=lambda actor, context: canceled_repository,  # noqa: ARG005
            )
            task = asyncio.create_task(
                canceled_service.process(idempotency_key="state:canceled", **common)
            )
            await blocking.started.wait()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            canceled_replay = await canceled_service.process(
                idempotency_key="state:canceled", **common
            )
            return (
                incomplete,
                incomplete_replay,
                failed_replay,
                canceled_replay,
                failed_raised,
            )

        (
            incomplete,
            incomplete_replay,
            failed_replay,
            canceled_replay,
            failed_raised,
        ) = asyncio.run(exercise())
        self.assertEqual(incomplete.response_state, TurnState.INCOMPLETE)
        self.assertTrue(incomplete_replay.replayed)
        self.assertEqual(incomplete_replay.response_state, TurnState.INCOMPLETE)
        self.assertTrue(failed_replay.replayed)
        self.assertTrue(failed_raised)
        self.assertEqual(failed_replay.response_state, TurnState.FAILED)
        self.assertTrue(canceled_replay.replayed)
        self.assertEqual(canceled_replay.response_state, TurnState.CANCELED)

    def test_legacy_output_is_adapted_to_strict_canonical_without_rendering_change(self) -> None:
        async def exercise():
            repository = _Repository()
            workflow = _Workflow()
            service = _TestTurnService(
                workflow_factory=lambda: workflow,
                repository_factory=lambda actor, context: repository,  # noqa: ARG005
            )
            result = await service.process(
                actor=_principal(),
                thread_id="thread_normalized",
                content="Inspect pump",
                modality="text",
                trusted_context=_context(),
                modality_metadata={},
                idempotency_key="legacy:canonical",
                correlation_id=_context().correlation_id,
            )
            return result, repository

        result, repository = asyncio.run(exercise())
        self.assertEqual(result.message, "Normalized response")
        self.assertEqual(result.spoken_summary, "")
        self.assertEqual(result.canonical_response["response_version"], 1)
        self.assertEqual(result.canonical_response["detailed_response"], result.message)
        self.assertFalse(result.canonical_response["speak"])
        persisted = repository.terminal_calls[0]["canonical_result"]
        self.assertEqual(persisted["canonical_response"], result.canonical_response)
        self.assertIsInstance(persisted["spoken_summary"], str)

    def test_reasoning_route_is_shared_by_text_and_voice_and_persists_provenance(self) -> None:
        response = CanonicalTurnResponse(
            kind="repair_diagnosis",
            response_version=1,
            response_state="complete",
            detailed_response="The current evidence supports further inspection.",
            spoken_summary="",
            reasoning_summary="Current evidence was reviewed.",
            confidence="medium",
            evidence=[],
            next_questions=[],
            recommended_actions=[],
            safety_boundary="No safety status was inferred.",
            speak=False,
        )

        class Adapter:
            def __init__(self):
                self.calls = []

            async def reason(self, **kwargs):
                self.calls.append(kwargs)
                provenance = SimpleNamespace(
                    to_dict=lambda: {
                        "invocation_mode": "agent_reference",
                        "provider_request_id": "resp_fake",
                        "effort": kwargs["effort"],
                        "agent_name": "voice-agent-test",
                        "agent_version": "3",
                        "deployment": "",
                        "tool_names": [],
                        "tool_rounds": 0,
                        "response_version": 1,
                        "outcome_code": "complete",
                    }
                )
                return SimpleNamespace(response=response, provenance=provenance)

        async def exercise():
            repository = _Repository()
            adapter = Adapter()
            service = _TestTurnService(
                workflow_factory=_Workflow,
                repository_factory=lambda actor, context: repository,  # noqa: ARG005
                complexity_router=VoiceComplexityRouter(),
                reasoning_adapter=adapter,
            )
            common = {
                "actor": _principal(),
                "thread_id": "thread_normalized",
                "content": "The pump is vibrating and the bearing is hot. Diagnose it.",
                "trusted_context": _context(),
                "correlation_id": _context().correlation_id,
            }
            text = await service.process(
                modality="text",
                modality_metadata={},
                idempotency_key="reason:text",
                **common,
            )
            voice = await service.process(
                modality="voice",
                modality_metadata={"transcription_confidence": 0.91},
                idempotency_key="reason:voice",
                **common,
            )
            return text, voice, adapter, repository

        text, voice, adapter, repository = asyncio.run(exercise())
        self.assertEqual(text.message, voice.message)
        self.assertEqual(text.route["mode"], "reasoning")
        self.assertEqual(voice.route["mode"], "reasoning")
        self.assertEqual(text.reasoning_provenance["agent_version"], "3")
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual([call["envelope"].mode for call in adapter.calls], ["text", "voice"])
        self.assertEqual(len(repository.terminal_calls), 2)
        self.assertTrue(
            all(
                call["canonical_result"]["canonical_response"] == response.model_dump(mode="json")
                for call in repository.terminal_calls
            )
        )

    def test_effect_wording_is_advisory_only_and_creates_no_proposal(self) -> None:
        class ForbiddenAdapter:
            async def reason(self, **kwargs):  # noqa: ARG002
                raise AssertionError("advisory intent must not reach the provider")

        async def exercise():
            repository = _Repository()
            service = _TestTurnService(
                workflow_factory=lambda: (_ for _ in ()).throw(
                    AssertionError("advisory intent must not run a legacy workflow")
                ),
                repository_factory=lambda actor, context: repository,  # noqa: ARG005
                complexity_router=VoiceComplexityRouter(),
                reasoning_adapter=ForbiddenAdapter(),
            )
            result = await service.process(
                actor=_principal(),
                thread_id="thread_normalized",
                content="Please complete the work order",
                modality="voice",
                trusted_context=_context(),
                modality_metadata={"transcription_confidence": 0.99},
                idempotency_key="advisory:only",
                correlation_id=_context().correlation_id,
            )
            return result, repository

        result, repository = asyncio.run(exercise())
        self.assertEqual(result.route["mode"], "advisory_intent")
        self.assertFalse(result.route["proposal_creation_allowed"])
        self.assertFalse(result.route["action_execution_allowed"])
        self.assertEqual(result.canonical_response["recommended_actions"], [])
        self.assertNotIn("proposal_id", repository.terminal_calls[0]["canonical_result"])

    def test_broader_effect_commands_never_reach_legacy_or_reasoning_paths(self) -> None:
        class ForbiddenAdapter:
            async def reason(self, **_kwargs):
                raise AssertionError("effect wording reached the provider")

        async def exercise():
            repository = _Repository()
            workflow = _Workflow()
            service = _TestTurnService(
                workflow_factory=lambda: workflow,
                repository_factory=lambda _actor, _context: repository,
                complexity_router=VoiceComplexityRouter(),
                reasoning_adapter=ForbiddenAdapter(),
            )
            results = []
            for index, content in enumerate((
                "Send an email to the supervisor",
                "Archive the kanban card",
                "Delete the card permanently",
                "Order ten replacement bearings",
            )):
                results.append(
                    await service.process(
                        actor=_principal(),
                        thread_id="thread_normalized",
                        content=content,
                        modality="text",
                        trusted_context=_context(),
                        modality_metadata={},
                        idempotency_key=f"effect:broad:{index}",
                        correlation_id=_context().correlation_id,
                    )
                )
            return results, workflow

        results, workflow = asyncio.run(exercise())
        self.assertEqual(workflow.calls, [])
        self.assertTrue(all(item.workflow_used == "advisory_intent" for item in results))
