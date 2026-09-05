"""D0 (M1 entry baseline): content-free route facts persist and project owner-only.

Every terminal turn writes ``task_intent`` (the enum value, or null when the
router is dark) and ``conversation_summary_present`` (whether the routing
classifier's summary slot was populated — False on every turn today, which
is the baseline fact the battery journals). The ``/threads/{id}`` projection
shows them to the owner and nulls them for a granted reader.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest  # noqa: E402
from ai.core.app import ROUTE_FACT_KEYS, _route_facts  # noqa: E402
from ai.core.auth import AIPrincipal  # noqa: E402
from ai.core.evals.scenarios import TASK_INTENTS  # noqa: E402
from ai.core.streaming import AGUIEvent, EventType  # noqa: E402
from ai.core.trusted_context import TrustedTurnContext  # noqa: E402
from ai.core.turn.finalize import _task_intent_value  # noqa: E402
from ai.core.turn_service import NormalizedTurnService  # noqa: E402
from aichat.services.threads import ThreadRepository  # noqa: E402
from django.core.management import call_command  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _database():
    call_command("migrate", verbosity=0, interactive=False)
    yield


# --------------------------------------------------------------------------- #
# Projection                                                                   #
# --------------------------------------------------------------------------- #
def test_owner_sees_the_persisted_facts():
    facts = _route_facts(
        {"task_intent": "record_retrieval", "conversation_summary_present": False}, shared=False
    )
    assert facts == {"task_intent": "record_retrieval", "conversation_summary_present": False}


def test_grantee_sees_null_for_every_fact():
    facts = _route_facts(
        {"task_intent": "record_retrieval", "conversation_summary_present": True}, shared=True
    )
    assert set(facts) == set(ROUTE_FACT_KEYS)
    assert all(value is None for value in facts.values())


def test_old_images_project_null_not_a_fabricated_fact():
    """A message persisted before D0 has neither key: null, never False."""
    facts = _route_facts({"workflow_id": "wf8"}, shared=False)
    assert facts == {"task_intent": None, "conversation_summary_present": None}


def test_projection_rejects_non_canonical_shapes():
    facts = _route_facts({"task_intent": 7, "conversation_summary_present": "yes"}, shared=False)
    assert facts == {"task_intent": None, "conversation_summary_present": None}


# --------------------------------------------------------------------------- #
# Persistence — the REAL turn service, a scripted workflow double             #
# --------------------------------------------------------------------------- #
class _ScriptedWorkflow:
    """Yields one scripted reply per call and emits the wf8 envelope."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)

    async def run_stream(self, **kwargs):
        run_id = f"run-{uuid.uuid4().hex[:6]}"
        thread_id = kwargs["thread_id"]
        emitter = kwargs["emitter"]
        await emitter.emit(
            AGUIEvent(event_type=EventType.RUN_STARTED, thread_id=thread_id, run_id=run_id)
        )
        await emitter.emit(
            AGUIEvent(
                event_type=EventType.WORKFLOW_STARTED,
                data={"workflow_id": "wf8"},
                thread_id=thread_id,
                run_id=run_id,
            )
        )
        yield self.replies.pop(0) if self.replies else "Scripted reply."
        await emitter.emit(
            AGUIEvent(event_type=EventType.RUN_FINISHED, thread_id=thread_id, run_id=run_id)
        )


def _user():
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username=f"d0-{uuid.uuid4().hex[:8]}", password="unused"
    )


def _principal(user) -> AIPrincipal:
    return AIPrincipal(
        subject=f"user:{user.pk}",
        actor=f"user:{user.pk}",
        user_pk=str(user.pk),
        username=user.username,
        authentication_method="django_session",
        scope="site:pilot",
        policy_version="1",
        is_staff=False,
        is_superuser=False,
    )


def _context(user) -> TrustedTurnContext:
    return TrustedTurnContext(
        actor=f"user:{user.pk}",
        server_policy_key="site:pilot",
        server_policy_hash="0" * 64,
        thread_namespace="unscoped",
        server_route_hints=("/chat",),
        allowed_capabilities=("chat.unscoped.read",),
        correlation_id=str(uuid.uuid4()),
        policy_version="1",
        untrusted_content="",
    )


def _service(workflow) -> NormalizedTurnService:
    return NormalizedTurnService(
        workflow_factory=lambda: workflow,
        repository_factory=lambda actor, context: ThreadRepository(  # noqa: ARG005
            actor=int(actor.user_pk), scope_key="site:pilot"
        ),
    )


async def _turn(service, user, thread_id: str, content: str, key: str):
    return await service.process(
        actor=_principal(user),
        thread_id=thread_id,
        content=content,
        modality="text",
        trusted_context=_context(user),
        modality_metadata={"transport": "test"},
        idempotency_key=key,
        correlation_id=str(uuid.uuid4()),
    )


def test_task_intent_value_is_the_enum_value_or_none():
    from ai.core.analysis.intent import TaskIntent

    run = SimpleNamespace(task_intent=SimpleNamespace(intent=TaskIntent.RECORD_RETRIEVAL))
    assert _task_intent_value(run) == "record_retrieval"
    assert _task_intent_value(SimpleNamespace(task_intent=None)) is None


def test_terminal_write_persists_both_facts_on_the_assistant_message():
    from aichat.models import ChatMessage

    user = _user()
    thread_id = f"d0_{uuid.uuid4().hex[:12]}"
    service = _service(_ScriptedWorkflow(["First reply.", "Second reply."]))

    async def run():
        await _turn(service, user, thread_id, "What is on file for inverter A?", "d0:1")
        await _turn(service, user, thread_id, "And the superseded revision?", "d0:2")

    asyncio.run(run())

    assistant = list(
        ChatMessage.objects.filter(thread_id=thread_id, role="assistant").order_by("sequence")
    )
    assert len(assistant) == 2
    for message in assistant:
        # Both keys are ALWAYS written: null/False are facts the baseline journals.
        assert "task_intent" in message.metadata
        assert "conversation_summary_present" in message.metadata
        # A typed intent (the shadow rules classifier) or null when the
        # router is dark — never anything outside the contract vocabulary.
        assert message.metadata["task_intent"] in (None, *TASK_INTENTS)
        assert message.metadata["workflow_id"] == "wf8"
        # And the owner projection reproduces them byte-for-byte.
        assert _route_facts(message.metadata, shared=False) == {
            "task_intent": message.metadata["task_intent"],
            "conversation_summary_present": message.metadata["conversation_summary_present"],
        }
    # M1 PR E: the classifier's summary slot is fed by the builder — empty on
    # the first turn (no prior exchange to digest), populated from the second.
    assert assistant[0].metadata["conversation_summary_present"] is False
    assert assistant[1].metadata["conversation_summary_present"] is True
