"""D6 golden: a voice follow-up sees the text turn that preceded it.

The follow-up battery is text-only (``/chat``); voice needs a session, so
this is the family's voice member (named in the golden ``items.yaml``
header). Turn 0 goes through the REAL normalized turn service as TEXT;
turn 1 arrives through ``submit_voice_turn`` on a session bound to the
same thread. The voice turn's workflow input must carry turn 0 — the
builder's replay dict and the fenced routing digest — on the same thread.
Same harness as ``test_voice_parity``: real route handlers, boundary
principal, no provider network access.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import ai.core.app as ai_app  # noqa: E402
import pytest  # noqa: E402
from ai.core.auth import AIPrincipal  # noqa: E402
from ai.core.streaming import AGUIEvent, EventType  # noqa: E402
from ai.core.trusted_context import TrustedTurnContext  # noqa: E402
from ai.core.turn_service import NormalizedTurnService  # noqa: E402
from ai.core.voice.routes import (  # noqa: E402
    VoiceSessionCreateRequest,
    VoiceTurnRequest,
    create_voice_session,
    submit_voice_turn,
)
from aichat.services.threads import ThreadRepository  # noqa: E402
from django.core.management import call_command  # noqa: E402

from .test_realtime_session_api import _run, _settings, _user  # noqa: E402

TEXT_TURN = "Which surge arrester fits inverter A?"
VOICE_TURN = "and the second one"


@pytest.fixture(scope="module", autouse=True)
def _database():
    call_command("migrate", verbosity=0, interactive=False)
    yield


class _CapturingWorkflow:
    """Scripted wf8 double recording every workflow input it receives."""

    def __init__(self):
        self.calls: list[dict] = []

    async def run_stream(self, **kwargs):
        self.calls.append({key: value for key, value in kwargs.items() if key != "emitter"})
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
        yield f"Scripted reply {len(self.calls)}."
        await emitter.emit(
            AGUIEvent(event_type=EventType.RUN_FINISHED, thread_id=thread_id, run_id=run_id)
        )


def _principal(user) -> AIPrincipal:
    return AIPrincipal(
        subject=f"user:{user.pk}",
        actor=f"user:{user.pk}",
        user_pk=str(user.pk),
        username=user.username,
        authentication_method="session",
        scope="site:pilot",
        policy_version="1",
        is_staff=False,
        is_superuser=False,
    )


def _trusted(user, *, route: str) -> TrustedTurnContext:
    return TrustedTurnContext(
        actor=f"user:{user.pk}",
        server_policy_key="site:pilot",
        server_policy_hash="0" * 64,
        thread_namespace="unscoped",
        server_route_hints=(route,),
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


def test_voice_followup_carries_the_preceding_text_turn():
    user = _user()
    principal = _principal(user)
    settings = _settings([user.pk])
    thread_id = f"vf_{uuid.uuid4().hex[:12]}"
    workflow = _CapturingWorkflow()
    service = _service(workflow)

    # Turn 0: TEXT through the real service (creates the owned thread).
    asyncio.run(
        service.process(
            actor=principal,
            thread_id=thread_id,
            content=TEXT_TURN,
            modality="text",
            trusted_context=_trusted(user, route="/chat"),
            modality_metadata={"transport": "test"},
            idempotency_key="vf:text",
            correlation_id=str(uuid.uuid4()),
        )
    )
    assert workflow.calls[0]["thread_id"] == thread_id
    assert not workflow.calls[0]["context"].get("conversation_history")  # nothing before turn 0

    # Turn 1: VOICE through the real route, session bound to the same thread.
    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=thread_id)),
        settings,
    )
    assert created["thread_id"] == thread_id
    request = VoiceTurnRequest(
        transcript=VOICE_TURN, item_id="item-1", confidence=0.9, language="en-US"
    )
    with (
        patch.object(ai_app, "get_turn_service", return_value=service),
        patch(
            "ai.core.trusted_context.build_trusted_turn_context",
            return_value=_trusted(user, route="/voice/turns"),
        ),
    ):
        result = _run(principal, lambda: submit_voice_turn(created["id"], request), settings)
    assert result["response_state"] in {"complete", "partial"}

    assert len(workflow.calls) == 2
    voice_call = workflow.calls[1]
    assert voice_call["thread_id"] == thread_id
    assert voice_call["message"] == VOICE_TURN
    context = voice_call["context"]
    assert context["modality"] == "voice"
    history = context["conversation_history"]
    assert [entry["role"] for entry in history] == ["user", "assistant"]
    assert history[0]["content"] == TEXT_TURN
    assert history[1]["content"] == "Scripted reply 1."
    # The routing digest is the fenced newest exchange from the same bundle.
    assert context["thread_summary"].startswith("[UNTRUSTED-CONTENT-BEGIN]")
    assert TEXT_TURN in context["thread_summary"]
    assert context["routing_context"].splitlines()[0] == "modality=voice"
