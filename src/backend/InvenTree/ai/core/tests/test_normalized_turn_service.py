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

    def get_or_create(self, thread_id=None, *, title=""):
        self.thread.pk = thread_id or self.thread.pk
        return self.thread, not bool(thread_id)

    def begin_turn(self, thread_id, **kwargs):
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
            async def run_stream(self, **kwargs):
                if False:
                    yield ""
                raise TurnIncomplete("bounded timeout")

        class FailedWorkflow:
            async def run_stream(self, **kwargs):
                if False:
                    yield ""
                raise RuntimeError("provider detail must not escape")

        class BlockingWorkflow:
            def __init__(self):
                self.started = asyncio.Event()

            async def run_stream(self, **kwargs):
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
            # The reasoning rail fails closed without an authorized diagnostic
            # context and at least one exposed tool, so wire both stubs in.
            registry = SimpleNamespace(
                definitions=(
                    SimpleNamespace(
                        name="get_machine_context", capability="diagnostics.machine.read"
                    ),
                )
            )
            diagnostic_context = SimpleNamespace(
                capabilities=("diagnostics.machine.read",),
                record_roots=(),
            )
            service = _TestTurnService(
                workflow_factory=_Workflow,
                repository_factory=lambda actor, context: repository,  # noqa: ARG005
                complexity_router=VoiceComplexityRouter(),
                reasoning_adapter=adapter,
                diagnostic_tool_registry=registry,
                diagnostic_context_factory=lambda **kwargs: diagnostic_context,  # noqa: ARG005
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

    def test_reasoning_route_emits_diagnosis_provenance_event(self) -> None:
        """The chat surface receives citations + declared confidence (S10)."""
        from datetime import UTC, datetime

        from ai.core.reasoning.schemas import EvidenceEntry, EvidenceLocator

        entry = EvidenceEntry(
            source_type="machine",
            source_id="44",
            source_revision="r7",
            locator=EvidenceLocator(field="bearing_temp_c"),
            as_of=datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC),
            authorization_class="maintenance_scope",
            claim="Bearing temperature trended above baseline.",
        )
        response = CanonicalTurnResponse(
            kind="repair_diagnosis",
            response_version=1,
            response_state="complete",
            detailed_response="The current evidence supports further inspection.",
            spoken_summary="",
            reasoning_summary="Current evidence was reviewed.",
            confidence="medium",
            evidence=[entry],
            next_questions=[],
            recommended_actions=[],
            safety_boundary="No safety status was inferred.",
            speak=False,
        )

        class Adapter:
            async def reason(self, **kwargs):
                provenance = SimpleNamespace(to_dict=dict)
                return SimpleNamespace(response=response, provenance=provenance)

        captured: list[AGUIEvent] = []

        class RawCapture:
            async def handle(self, event: AGUIEvent) -> None:
                captured.append(event)

        async def exercise():
            repository = _Repository()
            registry = SimpleNamespace(
                definitions=(
                    SimpleNamespace(
                        name="get_machine_context", capability="diagnostics.machine.read"
                    ),
                )
            )
            diagnostic_context = SimpleNamespace(
                capabilities=("diagnostics.machine.read",),
                record_roots=(),
            )
            service = _TestTurnService(
                workflow_factory=_Workflow,
                repository_factory=lambda actor, context: repository,  # noqa: ARG005
                complexity_router=VoiceComplexityRouter(),
                reasoning_adapter=Adapter(),
                diagnostic_tool_registry=registry,
                diagnostic_context_factory=lambda **kwargs: diagnostic_context,  # noqa: ARG005
            )
            emitter = InMemoryEventEmitter()
            await emitter.subscribe(RawCapture())
            await service.process(
                actor=_principal(),
                thread_id="thread_normalized",
                content="The pump is vibrating and the bearing is hot. Diagnose it.",
                modality="text",
                trusted_context=_context(),
                modality_metadata={},
                idempotency_key="reason:provenance",
                correlation_id=_context().correlation_id,
                emitter=emitter,
            )

        asyncio.run(exercise())
        deltas = [
            event
            for event in captured
            if event.event_type == EventType.STATE_DELTA
            and event.data.get("kind") == "diagnosis_provenance"
        ]
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0].data["confidence"], "medium")
        self.assertEqual(deltas[0].data["evidence"][0]["source_id"], "44")
        self.assertEqual(deltas[0].data["evidence"][0]["source_revision"], "r7")

    def test_reasoning_envelope_carries_the_authorized_records_verbatim(self) -> None:
        """The model can only quote server ids/revisions it was actually given.

        Every diagnostic tool demands the exact entity_id + expected_revision
        the server resolved, and the instructions forbid inventing identifiers
        — so a turn whose envelope omits the authorized roots is structurally
        unable to ground itself (found live 2026-08-03: every grounded turn
        ended incomplete). The envelope must mirror the context's roots.
        """
        from ai.core.reasoning.schemas import CanonicalTurnResponse

        response = CanonicalTurnResponse(
            kind="repair_diagnosis",
            response_version=1,
            response_state="incomplete",
            detailed_response="Incomplete.",
            spoken_summary="",
            reasoning_summary="stub",
            confidence="low",
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
                provenance = SimpleNamespace(to_dict=dict)
                return SimpleNamespace(response=response, provenance=provenance)

        async def exercise():
            repository = _Repository()
            adapter = Adapter()
            registry = SimpleNamespace(
                definitions=(
                    SimpleNamespace(
                        name="get_machine_context", capability="diagnostics.machine.read"
                    ),
                )
            )
            diagnostic_context = SimpleNamespace(
                capabilities=("diagnostics.machine.read",),
                record_roots=(
                    SimpleNamespace(
                        entity_type="machine",
                        entity_id=44,
                        expected_revision="2026-08-01T00:00:00+00:00",
                        linked_machine_id=None,
                        display_name="Influent Pump 1",
                    ),
                    SimpleNamespace(
                        entity_type="repair_packet",
                        entity_id=7,
                        expected_revision="2026-08-02T00:00:00+00:00",
                        linked_machine_id=44,
                        display_name="",
                    ),
                ),
            )
            service = _TestTurnService(
                workflow_factory=_Workflow,
                repository_factory=lambda actor, context: repository,  # noqa: ARG005
                complexity_router=VoiceComplexityRouter(),
                reasoning_adapter=adapter,
                diagnostic_tool_registry=registry,
                diagnostic_context_factory=lambda **kwargs: diagnostic_context,  # noqa: ARG005
            )
            await service.process(
                actor=_principal(),
                thread_id="thread_normalized",
                content="The pump is vibrating and the bearing is hot. Diagnose it.",
                trusted_context=_context(),
                correlation_id=_context().correlation_id,
                modality="text",
                modality_metadata={},
                idempotency_key="reason:roots",
            )
            return adapter

        adapter = asyncio.run(exercise())
        envelope = adapter.calls[0]["envelope"]
        records = [record.model_dump() for record in envelope.authorized_records]
        self.assertEqual(
            records,
            [
                {
                    "entity_type": "machine",
                    "entity_id": 44,
                    "expected_revision": "2026-08-01T00:00:00+00:00",
                    "linked_machine_id": None,
                    "display_name": "Influent Pump 1",
                },
                {
                    "entity_type": "repair_packet",
                    "entity_id": 7,
                    "expected_revision": "2026-08-02T00:00:00+00:00",
                    "linked_machine_id": 44,
                    "display_name": "",
                },
            ],
        )

    def test_reasoning_route_without_diagnostic_tools_refuses_and_never_reaches_adapter(
        self,
    ) -> None:
        """A tool-less reasoning turn fails closed to an honest, unspeakable refusal.

        This pins the production incident shape: the diagnosis flag on with no
        capability resolver meant the adapter ran blind and an uncited answer
        could be spoken. Neither half may regress.
        """

        class ForbiddenAdapter:
            def __init__(self):
                self.calls = []

            async def reason(self, **kwargs):
                self.calls.append(kwargs)
                raise AssertionError("a tool-less reasoning turn must not reach the provider")

        async def exercise(context_factory):
            repository = _Repository()
            adapter = ForbiddenAdapter()
            service = _TestTurnService(
                workflow_factory=_Workflow,
                repository_factory=lambda actor, context: repository,  # noqa: ARG005
                complexity_router=VoiceComplexityRouter(),
                reasoning_adapter=adapter,
                diagnostic_context_factory=context_factory,
            )
            result = await service.process(
                actor=_principal(),
                thread_id="thread_normalized",
                content="The pump is vibrating and the bearing is hot. Diagnose it.",
                trusted_context=_context(),
                correlation_id=_context().correlation_id,
                modality="voice",
                modality_metadata={"transcription_confidence": 0.91},
                idempotency_key=f"reason:refusal:{id(context_factory)}",
            )
            return result, adapter

        # Case 1: no diagnostic context at all (unset capability resolver).
        # Case 2: a context whose capabilities expose zero registry tools.
        no_context = lambda **kwargs: None  # noqa: E731, ARG005
        empty_capabilities = lambda **kwargs: SimpleNamespace(  # noqa: E731, ARG005
            capabilities=(), record_roots=()
        )
        for factory in (no_context, empty_capabilities):
            result, adapter = asyncio.run(exercise(factory))
            self.assertEqual(adapter.calls, [])
            self.assertEqual(result.route["mode"], "reasoning")
            canonical = result.canonical_response
            self.assertEqual(canonical["response_state"], "incomplete")
            self.assertEqual(canonical["kind"], "repair_diagnosis")
            self.assertFalse(canonical["speak"])
            self.assertEqual(canonical["spoken_summary"], "")
            self.assertEqual(canonical["recommended_actions"], [])
            self.assertIn("Diagnostic reasoning is unavailable", result.message)

    def test_effect_wording_is_advisory_only_and_creates_no_proposal(self) -> None:
        class ForbiddenAdapter:
            async def reason(self, **kwargs):
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
                content="Create an RFQ for ten bearings",
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
        self.assertTrue(result.canonical_response["speak"])
        self.assertEqual(result.spoken_summary, result.message)
        self.assertIn("do not create or change records by voice", result.message)
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


class VoiceWriteConfirmationShadowTests(SimpleTestCase):
    """Phase 4 slice 2: shadow-observe the write-confirmation contract.

    The classifier is wired into the live voice advisory seam but, while the flag
    is off (or shadowing), it only logs -- no read-back, no pending state, no
    execution -- and the read-only fence is untouched.
    """

    _LOGGER = "ai.core.turn_service"

    def _run_voice_advisory(self, content: str, *, flag: bool):
        from unittest.mock import patch

        class ForbiddenAdapter:
            async def reason(self, **_kwargs):
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
            return await service.process(
                actor=_principal(),
                thread_id="thread_shadow",
                content=content,
                modality="voice",
                trusted_context=_context(),
                modality_metadata={"transcription_confidence": 0.99},
                idempotency_key=f"shadow:{content}",
                correlation_id=_context().correlation_id,
            )

        settings = SimpleNamespace(feature_voice_write_confirmation=flag)
        with patch("ai.core.config.get_settings", return_value=settings):
            return asyncio.run(exercise())

    def test_flag_on_shadow_logs_classification_without_changing_behavior(self) -> None:
        with self.assertLogs(self._LOGGER, level="INFO") as captured:
            result = self._run_voice_advisory("Delete the card permanently", flag=True)
        shadow = [m for m in captured.output if "voice.write_confirmation.shadow" in m]
        self.assertEqual(len(shadow), 1)
        self.assertIn("action_class=irreversible", shadow[0])
        # Behavior is unchanged: still advisory, still no execution.
        self.assertEqual(result.route["mode"], "advisory_intent")
        self.assertFalse(result.route["action_execution_allowed"])

    def test_flag_on_classifies_reversible_effect_as_confirmable(self) -> None:
        with self.assertLogs(self._LOGGER, level="INFO") as captured:
            self._run_voice_advisory("Please complete the work order", flag=True)
        shadow = [m for m in captured.output if "voice.write_confirmation.shadow" in m]
        self.assertEqual(len(shadow), 1)
        self.assertIn("action_class=confirmable", shadow[0])

    def test_flag_off_emits_no_shadow_log(self) -> None:
        from unittest.mock import patch

        from ai.core.turn_service import _log_voice_write_confirmation_shadow

        settings = SimpleNamespace(feature_voice_write_confirmation=False)
        with (
            patch("ai.core.config.get_settings", return_value=settings),
            self.assertNoLogs(self._LOGGER, level="INFO"),
        ):
            _log_voice_write_confirmation_shadow("Delete it permanently", 7)


class VoiceWriteConfirmationEnforceTests(SimpleTestCase):
    """Phase 4 slice 3: end-to-end propose -> confirm -> execute through the service."""

    @staticmethod
    def _gate_and_executor():
        from ai.core.voice.confirmation import ProposedWriteAction
        from ai.core.voice.write_gate import (
            ExecutableWrite,
            InMemoryPendingWriteStore,
            ResolvedVoiceWrite,
            VoiceWriteExecutionResult,
            VoiceWriteGate,
        )

        class _OrderResolver:
            async def resolve(self, content, *, actor, trusted_context):
                if "order" not in content.lower():
                    return None
                return ResolvedVoiceWrite(
                    action=ProposedWriteAction(
                        capability="inventory.write",
                        summary="Place a purchase order for 10 bearings",
                    ),
                    executable=ExecutableWrite(
                        tool_name="create_purchase_order",
                        capability="inventory.write",
                        arguments={"qty": 10},
                    ),
                )

        class _Allow:
            def allows(self, actor, capability):
                return True

        class _Executor:
            def __init__(self):
                self.calls = []

            async def execute(self, executable, *, actor, trusted_context):
                self.calls.append(executable)
                return VoiceWriteExecutionResult(ok=True)

        executor = _Executor()
        gate = VoiceWriteGate(
            resolver=_OrderResolver(),
            permission=_Allow(),
            executor=executor,
            store=InMemoryPendingWriteStore(),
        )
        return gate, executor

    def _service(self, gate):
        class ForbiddenAdapter:
            async def reason(self, **_kwargs):
                raise AssertionError("must not reach the provider")

        return _TestTurnService(
            workflow_factory=lambda: (_ for _ in ()).throw(
                AssertionError("write path must not run a legacy workflow")
            ),
            repository_factory=lambda actor, context: _Repository(),  # noqa: ARG005
            complexity_router=VoiceComplexityRouter(),
            reasoning_adapter=ForbiddenAdapter(),
            voice_write_gate=gate,
        )

    def test_propose_then_confirm_executes_the_resolved_write(self) -> None:
        from unittest.mock import patch

        gate, executor = self._gate_and_executor()

        async def exercise():
            # One repository shared across both turns so the thread id is stable.
            repository = _Repository()
            service = self._service(gate)
            service.repository_factory = lambda actor, context: repository  # noqa: ARG005

            propose = await service.process(
                actor=_principal(),
                thread_id="thread_write",
                content="Order 10 replacement bearings",
                modality="voice",
                trusted_context=_context(),
                modality_metadata={"transcription_confidence": 0.99},
                idempotency_key="write:propose",
                correlation_id=_context().correlation_id,
            )
            confirm = await service.process(
                actor=_principal(),
                thread_id="thread_write",
                content="yes",
                modality="voice",
                trusted_context=_context(),
                modality_metadata={"transcription_confidence": 0.99},
                idempotency_key="write:confirm",
                correlation_id=_context().correlation_id,
            )
            return propose, confirm

        settings = SimpleNamespace(feature_voice_write_confirmation=True)
        with patch("ai.core.config.get_settings", return_value=settings):
            propose, confirm = asyncio.run(exercise())

        # Turn 1: a spoken read-back (no execution -- proven by the single call
        # below being the confirm turn's).
        self.assertEqual(propose.workflow_used, "voice_write_propose")
        self.assertIn("Place a purchase order for 10 bearings", propose.message)
        # Turn 2: the confirmation executes exactly the resolved call, once.
        self.assertEqual(confirm.workflow_used, "voice_write_confirm")
        self.assertEqual(confirm.message, "Done.")
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(executor.calls[0].tool_name, "create_purchase_order")

    def test_voice_action_router_does_not_require_live_diagnosis(self) -> None:
        from unittest.mock import patch

        gate, executor = self._gate_and_executor()

        async def exercise():
            repository = _Repository()
            service = _TestTurnService(
                workflow_factory=lambda: (_ for _ in ()).throw(
                    AssertionError("voice action must not run a legacy workflow")
                ),
                repository_factory=lambda actor, context: repository,  # noqa: ARG005
                voice_write_gate=gate,
            )
            return await service.process(
                actor=_principal(),
                thread_id="thread_write",
                content="Order 10 replacement bearings",
                modality="voice",
                trusted_context=_context(),
                modality_metadata={"transcription_confidence": 0.99},
                idempotency_key="write:no-diagnosis-router",
                correlation_id=_context().correlation_id,
            )

        settings = SimpleNamespace(
            feature_voice_live_diagnosis=False,
            feature_voice_write_confirmation=True,
        )
        with patch("ai.core.config.get_settings", return_value=settings):
            result = asyncio.run(exercise())

        self.assertEqual(result.workflow_used, "voice_write_propose")
        self.assertTrue(result.canonical_response["speak"])
        self.assertEqual(executor.calls, [])

    def test_unresolved_voice_action_requests_required_details(self) -> None:
        from unittest.mock import patch

        from ai.core.voice.write_gate import VoiceWriteGate

        async def exercise():
            repository = _Repository()
            service = _TestTurnService(
                workflow_factory=lambda: (_ for _ in ()).throw(
                    AssertionError("unresolved action must not run a legacy workflow")
                ),
                repository_factory=lambda actor, context: repository,  # noqa: ARG005
                voice_write_gate=VoiceWriteGate(),
            )
            return await service.process(
                actor=_principal(),
                thread_id="thread_write",
                content="Create an RFQ",
                modality="voice",
                trusted_context=_context(),
                modality_metadata={"transcription_confidence": 0.99},
                idempotency_key="write:missing-details",
                correlation_id=_context().correlation_id,
            )

        settings = SimpleNamespace(
            feature_voice_live_diagnosis=False,
            feature_voice_write_confirmation=True,
        )
        with patch("ai.core.config.get_settings", return_value=settings):
            result = asyncio.run(exercise())

        self.assertEqual(result.workflow_used, "advisory_intent")
        self.assertIn("could not prepare that action", result.message)
        self.assertTrue(result.canonical_response["speak"])

    def test_flag_off_keeps_effect_wording_advisory(self) -> None:
        from unittest.mock import patch

        gate, executor = self._gate_and_executor()

        async def exercise():
            service = self._service(gate)
            return await service.process(
                actor=_principal(),
                thread_id="thread_write",
                content="Order 10 replacement bearings",
                modality="voice",
                trusted_context=_context(),
                modality_metadata={"transcription_confidence": 0.99},
                idempotency_key="write:off",
                correlation_id=_context().correlation_id,
            )

        settings = SimpleNamespace(feature_voice_write_confirmation=False)
        with patch("ai.core.config.get_settings", return_value=settings):
            result = asyncio.run(exercise())

        # Fence stands: advisory only, nothing resolved or executed.
        self.assertEqual(result.route["mode"], "advisory_intent")
        self.assertEqual(executor.calls, [])


class ConversationHistoryTests(SimpleTestCase):
    """The transcript a lookup turn replays, and the bounds on it."""

    def test_history_excludes_the_current_turn_and_bounds_the_window(self) -> None:
        from unittest.mock import patch

        calls: list[Any] = []

        class _Repository:
            def recent_messages(self, thread_id, limit, *, exclude_latest=0):
                calls.append((thread_id, limit, exclude_latest))
                return [
                    SimpleNamespace(role="user", content="How many fasteners are in stock?"),
                    SimpleNamespace(role="assistant", content="Four parts carry them."),
                    SimpleNamespace(role="user", content="   "),
                ]

        service = _TestTurnService(workflow_factory=lambda: None)
        settings = SimpleNamespace(
            chat_history_messages=6,
            chat_history_max_message_chars=4000,
            chat_history_max_total_chars=24000,
        )
        with patch("ai.core.config.get_settings", return_value=settings):
            history = asyncio.run(service._conversation_history(_Repository(), "thread_history"))

        # begin_turn has already stored this turn's question, so the newest row is
        # excluded rather than replayed back at the agent as if it were context.
        self.assertEqual(calls, [("thread_history", 6, 1)])
        self.assertEqual(
            history,
            [
                {"role": "user", "content": "How many fasteners are in stock?"},
                {"role": "assistant", "content": "Four parts carry them."},
            ],
        )

    def test_history_is_disabled_by_a_zero_budget(self) -> None:
        from unittest.mock import patch

        class _Repository:
            def recent_messages(self, *args, **kwargs):
                raise AssertionError("must not query when replay is disabled")

        service = _TestTurnService(workflow_factory=lambda: None)
        settings = SimpleNamespace(
            chat_history_messages=0,
            chat_history_max_message_chars=4000,
            chat_history_max_total_chars=24000,
        )
        with patch("ai.core.config.get_settings", return_value=settings):
            history = asyncio.run(service._conversation_history(_Repository(), "thread_history"))

        self.assertEqual(history, [])

    def test_history_failure_degrades_to_no_context(self) -> None:
        from unittest.mock import patch

        class _Repository:
            def recent_messages(self, *args, **kwargs):
                raise RuntimeError("transcript unavailable")

        service = _TestTurnService(workflow_factory=lambda: None)
        settings = SimpleNamespace(
            chat_history_messages=6,
            chat_history_max_message_chars=4000,
            chat_history_max_total_chars=24000,
        )
        with patch("ai.core.config.get_settings", return_value=settings):
            history = asyncio.run(service._conversation_history(_Repository(), "thread_history"))

        # A lookup answered without context beats a turn that fails outright.
        self.assertEqual(history, [])


class ServerPinnedWorkflowTests(SimpleTestCase):
    """A server-owned workflow pin bypasses routing, not the safety ordering."""

    def test_pin_forces_the_legacy_branch_over_reasoning(self) -> None:
        async def exercise():
            repository = _Repository()
            workflow = _Workflow()
            service = _TestTurnService(
                workflow_factory=lambda: workflow,
                repository_factory=lambda actor, context: repository,  # noqa: ARG005
                # Non-None sentinels: if the pin failed to bypass the reasoning
                # branch, _reasoning_canonical would fail loudly on these.
                complexity_router=object(),
                reasoning_adapter=object(),
            )
            service._route_turn = lambda **kwargs: SimpleNamespace(  # noqa: ARG005
                mode=SimpleNamespace(value="reasoning"),
                target_workflow_id="wf1",
                to_dict=lambda: {"mode": "reasoning"},
            )
            result = await service.process(
                actor=_principal(),
                thread_id="thread_normalized",
                content="Generate a repair packet for the seized pump",
                modality="text",
                trusted_context=_context(),
                modality_metadata={},
                idempotency_key="repair-gen:1:run",
                correlation_id=_context().correlation_id,
                server_pinned_workflow="wf7",
            )
            return result, workflow

        result, workflow = asyncio.run(exercise())
        self.assertEqual(result.message, "Normalized response")
        self.assertEqual(len(workflow.calls), 1)
        self.assertEqual(workflow.calls[0]["context"]["pinned_workflow_id"], "wf7")

    def test_blank_pin_is_rejected(self) -> None:
        service = _TestTurnService(
            workflow_factory=lambda: None,
            complexity_router=object(),
            reasoning_adapter=object(),
        )
        with self.assertRaises(ValueError):
            asyncio.run(
                service.process(
                    actor=_principal(),
                    thread_id=None,
                    content="Generate a packet",
                    modality="text",
                    trusted_context=_context(),
                    modality_metadata={},
                    idempotency_key="repair-gen:1:run",
                    correlation_id=_context().correlation_id,
                    server_pinned_workflow="   ",
                )
            )
