"""S22: the turn service owns the question invariants — arming and binding."""

from __future__ import annotations

import asyncio
import inspect
import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.auth import AIPrincipal  # noqa: E402
from ai.core.questions.pending import (  # noqa: E402
    PENDING_QUESTION_SCHEMA_VERSION,
    InMemoryPendingQuestionStore,
)
from ai.core.questions.promotion import set_question_proposal  # noqa: E402
from ai.core.questions.schema import FORBIDDEN_EVENT_KEYS  # noqa: E402
from ai.core.streaming import AGUIEvent, EventType  # noqa: E402
from ai.core.trusted_context import TrustedTurnContext  # noqa: E402
from ai.core.turn_service import NormalizedTurnService  # noqa: E402
from aichat.models import TurnState  # noqa: E402
from aichat.services import BeginTurnResult, IdempotencyConflict  # noqa: E402
from django.test import SimpleTestCase  # noqa: E402

OPTIONS = [
    {
        "id": "machine:42",
        "label": "Influent Pump Station No. 1 (TC-INF-PS1-001)",
        "kind": "machine",
        "recommended": True,
        "ref": {"machine_id": 42, "serial": "TC-INF-PS1-001"},
    },
    {
        "id": "machine:43",
        "label": "Clarifier Drive 2 (TC-CLA-DR2-001)",
        "kind": "machine",
        "ref": {"machine_id": 43, "serial": "TC-CLA-DR2-001"},
    },
]


class _TestTurnService(NormalizedTurnService):
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
        self.thread = SimpleNamespace(pk="thread_q", title="t")
        self.turns: dict[str, _FakeTurn] = {}
        self.terminal_calls: list[dict[str, Any]] = []
        self._counter = 0

    def get_or_create(self, thread_id=None, *, title=""):
        return self.thread, False

    def rename(self, thread_id, title):
        return self.thread

    def begin_turn(self, thread_id, **kwargs):
        key = kwargs["idempotency_key"]
        existing = self.turns.get(key)
        if existing:
            if existing.request_fingerprint != kwargs["request_fingerprint"]:
                raise IdempotencyConflict("different request")
            return BeginTurnResult(existing, True)
        self._counter += 1
        turn = _FakeTurn(f"turn_q_{self._counter}", kwargs["request_fingerprint"])
        self.turns[key] = turn
        return BeginTurnResult(turn, False)

    def terminal(self, turn_id, **kwargs):
        turn = next(turn for turn in self.turns.values() if turn.pk == turn_id)
        turn.state = kwargs["state"]
        turn.canonical_result = dict(kwargs["canonical_result"])
        self.terminal_calls.append(kwargs)
        return turn

    def recent_messages(self, thread_id, limit, exclude_latest=1):
        return []


class _AskingWorkflow:
    """A workflow whose tool proposed a question mid-run."""

    def __init__(self, propose: bool = True) -> None:
        self.propose = propose
        self.calls: list[dict[str, Any]] = []

    async def run_stream(self, **kwargs):
        self.calls.append(kwargs)
        await kwargs["emitter"].emit(
            AGUIEvent(
                event_type=EventType.WORKFLOW_STARTED,
                data={"workflow_id": "wf8"},
                thread_id=kwargs["thread_id"],
                run_id="run-q",
            )
        )
        if self.propose:
            set_question_proposal({
                "source": "manual_search_ambiguity",
                "question_text": "Which machine do you mean?",
                "options": OPTIONS,
            })
            yield "Which machine do you mean?"
        else:
            yield f"answered: {kwargs['message']}"


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
        untrusted_content="{}",
    )


def _flag_settings(enabled: bool):
    from ai.core.config import Settings

    return lambda: Settings(_env_file=None, FEATURE_QUESTION_CARDS=enabled)


class QuestionTurnBindingTests(SimpleTestCase):
    """Arming, exactly-once answering, and every non-answer outcome."""

    def _service(self, workflow, repository, store):
        return _TestTurnService(
            workflow_factory=lambda: workflow,
            repository_factory=lambda actor, context: repository,  # noqa: ARG005
            question_store=store,
        )

    def _turn(self, service, content, key):
        return service.process(
            actor=_principal(),
            thread_id="thread_q",
            content=content,
            modality="text",
            trusted_context=_context(),
            modality_metadata={"transport": "typed"},
            idempotency_key=key,
            correlation_id=_context().correlation_id,
        )

    def test_asking_turn_arms_slot_and_persists_question(self) -> None:
        async def exercise():
            repository = _Repository()
            store = InMemoryPendingQuestionStore()
            service = self._service(_AskingWorkflow(), repository, store)
            with mock.patch("ai.core.config.get_settings", _flag_settings(True)):
                result = await self._turn(service, "manual for the pump", "ask:1")
            return result, repository, store

        result, repository, store = asyncio.run(exercise())
        canonical = repository.terminal_calls[0]["canonical_result"]
        self.assertEqual(canonical["kind"], "clarification_question")
        question = canonical["question"]
        self.assertEqual(question["question_text"], "Which machine do you mean?")
        self.assertTrue(FORBIDDEN_EVENT_KEYS.isdisjoint(question))
        self.assertTrue(all("ref" not in option for option in question["options"]))
        # The QUESTION event is persisted among the turn's events.
        question_events = [event for event in canonical["events"] if event["type"] == "QUESTION"]
        self.assertEqual(len(question_events), 1)
        self.assertEqual(question_events[0]["interrupt_id"], question["interrupt_id"])
        # The pending record holds the refs and the schema version.
        record = store.take("thread_q")
        self.assertEqual(record["schema_version"], PENDING_QUESTION_SCHEMA_VERSION)
        self.assertEqual(record["options"][0]["ref"]["machine_id"], 42)
        self.assertEqual(record["origin"]["content"], "manual for the pump")
        # The result surfaces the payload for per-turn transports (voice).
        self.assertEqual(result.pending_question, question)
        # Terminal metadata carries the card for the /threads projection.
        metadata = repository.terminal_calls[0]["output_metadata"]
        self.assertEqual(metadata["question"], question)

    def test_accept_reroutes_original_intent_with_persisted_label(self) -> None:
        async def exercise():
            repository = _Repository()
            store = InMemoryPendingQuestionStore()
            asking = _AskingWorkflow()
            service = self._service(asking, repository, store)
            with mock.patch("ai.core.config.get_settings", _flag_settings(True)):
                await self._turn(service, "manual for the pump", "ask:1")
                answering = _AskingWorkflow(propose=False)
                service.workflow_factory = lambda: answering
                await self._turn(service, "the second one", "answer:1")
            return repository, store, answering

        repository, store, answering = asyncio.run(exercise())
        run_kwargs = answering.calls[0]
        self.assertEqual(
            run_kwargs["message"],
            "manual for the pump — Clarifier Drive 2 (TC-CLA-DR2-001)",
        )
        resolution = run_kwargs["context"]["question_resolution"]
        self.assertEqual(resolution["option"]["ref"]["machine_id"], 43)
        audit = repository.terminal_calls[1]["canonical_result"]["question_resolution"]
        self.assertEqual(audit["outcome"], "selected")
        self.assertEqual(audit["selected_option_id"], "machine:43")
        self.assertEqual(audit["matched_by"], "ordinal")
        # Slot is empty: the answer cannot be replayed.
        self.assertIsNone(store.take("thread_q"))

    def test_only_the_immediately_following_turn_can_answer(self) -> None:
        async def exercise():
            repository = _Repository()
            store = InMemoryPendingQuestionStore()
            service = self._service(_AskingWorkflow(), repository, store)
            with mock.patch("ai.core.config.get_settings", _flag_settings(True)):
                await self._turn(service, "manual for the pump", "ask:1")
                unrelated = _AskingWorkflow(propose=False)
                service.workflow_factory = lambda: unrelated
                await self._turn(service, "list open work orders", "other:1")
                late = _AskingWorkflow(propose=False)
                service.workflow_factory = lambda: late
                await self._turn(service, "2", "late:1")
            return unrelated, late, repository

        unrelated, late, repository = asyncio.run(exercise())
        # The unrelated turn consumed the slot (unmatched -> routed normally).
        self.assertEqual(unrelated.calls[0]["message"], "list open work orders")
        unmatched_audit = repository.terminal_calls[1]["canonical_result"]["question_resolution"]
        self.assertEqual(unmatched_audit["outcome"], "unmatched")
        # A later ordinal is just text: no resolution, routes as-is.
        self.assertEqual(late.calls[0]["message"], "2")
        self.assertNotIn("question_resolution", repository.terminal_calls[2]["canonical_result"])

    def test_decline_is_terminal_and_never_routes(self) -> None:
        async def exercise():
            repository = _Repository()
            store = InMemoryPendingQuestionStore()
            service = self._service(_AskingWorkflow(), repository, store)
            with mock.patch("ai.core.config.get_settings", _flag_settings(True)):
                await self._turn(service, "manual for the pump", "ask:1")
                would_route = _AskingWorkflow(propose=False)
                service.workflow_factory = lambda: would_route
                result = await self._turn(service, "none of those", "decline:1")
            return repository, would_route, result

        repository, would_route, result = asyncio.run(exercise())
        self.assertEqual(would_route.calls, [])
        canonical = repository.terminal_calls[1]["canonical_result"]
        self.assertEqual(canonical["workflow_used"], "question_declined")
        self.assertEqual(canonical["question_resolution"]["outcome"], "declined")
        self.assertIn("tell me a bit more", result.message)

    def test_flag_off_never_asks_but_still_drains_records(self) -> None:
        async def exercise():
            repository = _Repository()
            store = InMemoryPendingQuestionStore()
            service = self._service(_AskingWorkflow(), repository, store)
            # Pre-existing record from before a flag flip-off.
            store.save(
                "thread_q",
                {
                    "schema_version": PENDING_QUESTION_SCHEMA_VERSION,
                    "interrupt_id": "stale",
                    "options": OPTIONS,
                    "origin": {"content": "old intent"},
                    "expires_at": "2099-01-01T00:00:00+00:00",
                },
            )
            with mock.patch("ai.core.config.get_settings", _flag_settings(False)):
                result = await self._turn(service, "2", "off:1")
            return repository, store, result

        repository, store, result = asyncio.run(exercise())
        canonical = repository.terminal_calls[0]["canonical_result"]
        # The binder ran (flag-independent): the stale record was consumed and
        # this reply selected from it — flipping the flag off drains, it does
        # not strand.
        self.assertEqual(canonical["question_resolution"]["outcome"], "selected")
        self.assertIsNone(store.take("thread_q"))
        # But the workflow's proposal was NOT armed: no new question exists.
        self.assertNotIn("question", canonical)
        self.assertIsNone(result.pending_question)

    def test_replay_reemits_the_persisted_question_byte_for_byte(self) -> None:
        async def exercise():
            repository = _Repository()
            store = InMemoryPendingQuestionStore()
            service = self._service(_AskingWorkflow(), repository, store)
            captured: list[dict[str, Any]] = []

            class _Emitter:
                async def emit(self, event):
                    captured.append(event.to_dict())

            with mock.patch("ai.core.config.get_settings", _flag_settings(True)):
                await self._turn(service, "manual for the pump", "ask:1")
                store.take("thread_q")  # simulate consumption elsewhere
                replay = await service.process(
                    actor=_principal(),
                    thread_id="thread_q",
                    content="manual for the pump",
                    modality="text",
                    trusted_context=_context(),
                    modality_metadata={"transport": "typed"},
                    idempotency_key="ask:1",
                    correlation_id=_context().correlation_id,
                    emitter=_Emitter(),
                )
            return repository, store, captured, replay

        repository, store, captured, replay = asyncio.run(exercise())
        self.assertTrue(replay.replayed)
        persisted = [
            event
            for event in repository.terminal_calls[0]["canonical_result"]["events"]
            if event["type"] == "QUESTION"
        ]
        replayed = [event for event in captured if event["type"] == "QUESTION"]
        self.assertEqual(len(replayed), 1)
        self.assertEqual(replayed[0], persisted[0])
        # Replay must NOT re-arm the slot.
        self.assertIsNone(store.take("thread_q"))

    def test_injection_branch_abandons_the_pending_question(self) -> None:
        """Source pin: the injection branch closes the question window.

        S47 moved the pending-resolution block into its stage module; the
        invariant (a refused turn closes BOTH pending windows) is unchanged.
        """
        from ai.core.turn import pending as module

        source = inspect.getsource(module.resolve_preconditions)
        injection_block = source.split("injection_canonical is not None:")[1].split("else:")[0]
        self.assertIn("_abandon_pending_voice_write", injection_block)
        self.assertIn("_abandon_pending_question", injection_block)

    def test_abandon_consumes_and_discards(self) -> None:
        store = InMemoryPendingQuestionStore()
        store.save(
            "thread_q",
            {
                "schema_version": PENDING_QUESTION_SCHEMA_VERSION,
                "interrupt_id": "q-x",
                "options": OPTIONS,
            },
        )
        service = self._service(_AskingWorkflow(), _Repository(), store)
        service._abandon_pending_question(thread_id="thread_q")
        self.assertIsNone(store.take("thread_q"))

    def test_unmatched_reply_passes_the_loop_guard_context(self) -> None:
        """The next workflow sees which question just went unanswered.

        Without this, near-miss replies re-derived the identical card
        forever (live battery 2026-08-08) — the producers key their
        never-re-ask guard on this payload.
        """

        async def exercise():
            repository = _Repository()
            store = InMemoryPendingQuestionStore()
            service = self._service(_AskingWorkflow(), repository, store)
            with mock.patch("ai.core.config.get_settings", _flag_settings(True)):
                await self._turn(service, "manual for the pump", "ask:1")
                unmatched = _AskingWorkflow(propose=False)
                service.workflow_factory = lambda: unmatched
                await self._turn(service, "list open work orders", "miss:1")
            return unmatched

        unmatched = asyncio.run(exercise())
        resolution = unmatched.calls[0]["context"]["question_resolution"]
        self.assertEqual(resolution["outcome"], "unmatched")
        self.assertEqual(resolution["option_ids"], [str(option["id"]) for option in OPTIONS])
        # No refs leak on the unmatched path.
        self.assertNotIn("option", resolution)
