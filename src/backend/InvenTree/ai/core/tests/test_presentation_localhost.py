"""Real loopback HTTP/auth/ORM checks; all rows live in disposable SQLite.

No provider startup, model, cloud, business database or account credentials.
The normal boundary middleware verifies signed subjects against real users.
"""

# ruff: noqa: E402
from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
import django

django.setup()

import httpx
import pytest
import uvicorn
from agent_framework import AIFunction, FunctionInvocationContext
from ai.core import app as ai_app
from ai.core import config
from ai.core.auth import (
    AIBoundaryAuthMiddleware,
    AIBoundaryPolicy,
    principal_context,
    sign_interactive_subject,
)
from ai.core.streaming import InMemoryEventEmitter
from ai.core.tests.focus_test_db import ensure_island_tables, grant_work_view
from ai.core.tests.test_capability_invocation_guard import _principal
from ai.core.tools.capture_ledger import bind_tool_captures
from ai.core.tools.inventree.read.machines import get_machine_overview
from ai.core.tools.invocation_guard import CapabilityInvocationMiddleware, bind_capability_run
from ai.core.turn.finalize import _attach_presentation, complete
from ai.core.turn_service import NormalizedTurnService
from aichat.models import ChatMessage
from aichat.services.threads import ThreadRepository
from aichat.tests.memory_scope_fixtures import fixture_client_resolver
from asgiref.sync import sync_to_async
from assets.models import AssetMachine, Client, ClientScopeGrant
from django.contrib.auth import get_user_model
from django.test import override_settings


@pytest.fixture(scope="module", autouse=True)
def database():
    ensure_island_tables()


@pytest.fixture
def local_server(monkeypatch):
    suffix = os.urandom(4).hex()
    users = [
        get_user_model().objects.create_user(username=f"presentation-{suffix}-{index}")
        for index in range(3)
    ]
    owner, reader, stranger = users
    grant_work_view(owner)
    client = Client.objects.create(
        code=f"presentation-{suffix}", name=f"Presentation fixture {suffix}"
    )
    for user in [owner, reader]:
        ClientScopeGrant.objects.create(user=user, client=client)
    machine = AssetMachine.objects.create(name=f"Recorded Pump A {suffix}", client=client)
    configured = config.get_settings().model_copy(update={"feature_chat_presentation": True})
    monkeypatch.setattr(config, "get_settings", lambda: configured)
    monkeypatch.setattr(ai_app, "get_settings", lambda: configured)
    policy = AIBoundaryPolicy(
        single_site_policy_key="presentation-localhost",
        policy_version="test-v1",
        signed_subject_max_age_seconds=120,
        signed_subject_salt="presentation.localhost",
        signed_subject_audience="presentation-localhost",
        allowed_origins=("http://localhost:5173",),
    )
    repository = ThreadRepository(owner.pk, policy.single_site_policy_key)
    thread, _ = repository.get_or_create(title="Recorded presentation")
    begin = repository.begin_turn(
        thread.pk,
        content="Inspect the pump",
        modality="text",
        trusted_context={},
        modality_metadata={},
        idempotency_key="presentation-turn",
        request_fingerprint="a" * 64,
        correlation_id="",
    )
    capture = SimpleNamespace(events=[], workflow_id="local-fixture")

    class Collector:
        async def handle(self, event):
            capture.events.append(event.to_dict())

    class Service:
        @staticmethod
        async def _call_sync(function, *args, **kwargs):
            return await sync_to_async(function, thread_sensitive=True)(*args, **kwargs)

    canonical = {
        "thread_id": thread.pk,
        "turn_id": begin.turn.pk,
        "message": "Recorded inspection answer.",
        "agent": "fixture",
        "workflow_used": "fixture",
        "response_state": "complete",
        "canonical_response": None,
        "spoken_summary": "Recorded inspection answer.",
        "reasoning_provenance": None,
        "route": None,
        "events": capture.events,
    }

    async def persist():
        emitter = InMemoryEventEmitter()
        await emitter.subscribe(Collector())
        run = SimpleNamespace(
            repository=repository,
            thread=thread,
            turn=begin.turn,
            capture=capture,
            emitter=emitter,
            extras={},
            task_intent=None,
        )
        # The release SDK invokes the real authorized reader; its actual
        # result passes through invocation middleware into the ledger.
        # No injected diagnostic roots or hand-built entities/presentation.
        token = principal_context.set(_principal(str(owner.pk)))
        try:
            ledger = bind_tool_captures()
            function = AIFunction(name="get_machine_overview", func=get_machine_overview)
            invocation = FunctionInvocationContext(
                function=function,
                arguments=function.input_model.model_validate({"machine_id": machine.pk}),
            )

            async def dispatch(context):
                context.result = await context.function.invoke(arguments=context.arguments)

            with bind_capability_run(
                workflow="wf8", modality="text", selected_tools=[get_machine_overview]
            ):
                await CapabilityInvocationMiddleware().process(invocation, dispatch)
            assert ("assetmachine", machine.pk) in ledger.observed_entity_refs()
            service = NormalizedTurnService(workflow_factory=lambda: None)
            await service._attach_entity_manifest(
                canonical,
                diagnostic_context=None,
                thread_id=thread.pk,
                turn_id=begin.turn.pk,
                emitter=emitter,
                actor=_principal(str(owner.pk)),
            )
            assert canonical["entities"][0]["pk"] == machine.pk
        finally:
            principal_context.reset(token)
        await _attach_presentation(run, canonical)
        return await complete(Service(), run, canonical)

    with override_settings(
        AIMMS_MACHINE_AI_READ_ENABLED=True,
        AIMMS_RECORDED_TELEMETRY_AS_LIVE=False,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER=fixture_client_resolver,
    ):
        finalized, _, _ = asyncio.run(persist())
    with override_settings(
        FEATURE_THREAD_SHARING=True, AIMMS_MAINTENANCE_SCOPE_RESOLVER=fixture_client_resolver
    ):
        repository.share(thread.pk, grantee_id=reader.pk)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        server = uvicorn.Server(
            uvicorn.Config(
                AIBoundaryAuthMiddleware(ai_app.app, policy=policy),
                lifespan="off",
                log_level="error",
                access_log=False,
            )
        )
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        deadline = time.monotonic() + 10
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        address = f"http://127.0.0.1:{listener.getsockname()[1]}"
        with httpx.Client(base_url=address, trust_env=False, timeout=10) as http:

            def headers(user):
                return {"Authorization": f"Bearer {sign_interactive_subject(user, policy=policy)}"}

            try:
                yield SimpleNamespace(
                    http=http,
                    headers=headers,
                    owner=owner,
                    reader=reader,
                    stranger=stranger,
                    repository=repository,
                    thread=thread,
                    turn=finalized,
                    machine=machine,
                    canonical=canonical,
                    configured=configured,
                )
            finally:
                server.should_exit = True
                worker.join(timeout=10)
                listener.close()
                assert not worker.is_alive()


def test_owner_reload_replay_and_copy_sources_are_one_immutable_terminal_write(local_server):
    env = local_server
    response = env.http.get(f"/threads/{env.thread.pk}", headers=env.headers(env.owner))
    assert response.status_code == 200
    answer = response.json()["messages"][-1]
    assert answer["content"] == env.canonical["message"]
    assert answer["presentation"] == env.canonical["presentation"]
    assert env.turn.output_message.metadata["presentation"] == answer["presentation"]
    assert env.turn.output_message.metadata["spoken_summary"] == answer["content"]
    before = ChatMessage.objects.filter(thread=env.thread).count()
    replay = env.repository.begin_turn(
        env.thread.pk,
        content="Inspect the pump",
        modality="text",
        trusted_context={},
        modality_metadata={},
        idempotency_key="presentation-turn",
        request_fingerprint="a" * 64,
        correlation_id="",
    )
    assert replay.replayed
    assert replay.turn.canonical_result["presentation"] == answer["presentation"]
    assert ChatMessage.objects.filter(thread=env.thread).count() == before
    env.machine.name = "Changed current label"
    env.machine.save(update_fields=["name"])
    reloaded = env.http.get(f"/threads/{env.thread.pk}", headers=env.headers(env.owner)).json()[
        "messages"
    ][-1]
    assert reloaded["entities"] == answer["entities"]
    assert reloaded["presentation"] == answer["presentation"]


def test_shared_denied_revoked_and_disabled_projections(local_server):
    env = local_server
    path = f"/threads/{env.thread.pk}"
    assert env.http.get(path).status_code == 401
    assert env.http.get(path, headers=env.headers(env.stranger)).status_code == 404
    shared = env.http.get(path, headers=env.headers(env.reader))
    assert shared.status_code == 200 and shared.json()["shared"] is True
    answer = shared.json()["messages"][-1]
    assert answer["content"] == env.canonical["message"]
    assert answer["presentation"] is None and answer["presentation_data"] == []
    env.repository.revoke_share(env.thread.pk, grantee_id=env.reader.pk)
    assert env.http.get(path, headers=env.headers(env.reader)).status_code == 404
    env.configured.feature_chat_presentation = False
    disabled = env.http.get(path, headers=env.headers(env.owner)).json()["messages"][-1]
    assert disabled["presentation"] is None and disabled["content"] == answer["content"]


def test_revoked_client_access_does_not_preserve_a_shared_snapshot(local_server):
    env = local_server
    ClientScopeGrant.objects.filter(user=env.reader).delete()
    response = env.http.get(f"/threads/{env.thread.pk}", headers=env.headers(env.reader))
    assert response.status_code == 404
    assert "Recorded inspection" not in response.text and "presentation" not in response.text


def test_observed_entity_candidates_require_current_actor_access(local_server):
    from ai.core.entities import resolve_observed_record_roots

    env = local_server
    observed = frozenset({("assetmachine", env.machine.pk)})
    with override_settings(AIMMS_MACHINE_AI_READ_ENABLED=True):
        assert len(resolve_observed_record_roots(env.owner.pk, observed)) == 1
        assert resolve_observed_record_roots(env.stranger.pk, observed) == ()
        ClientScopeGrant.objects.filter(user=env.owner).delete()
        assert resolve_observed_record_roots(env.owner.pk, observed) == ()
    with override_settings(AIMMS_MACHINE_AI_READ_ENABLED=False):
        assert resolve_observed_record_roots(env.reader.pk, observed) == ()


def test_export_and_deletion_keep_the_existing_ledger_contract(local_server):
    env = local_server
    records = list(env.repository.export_transcript_records())
    exported = json.dumps(records)
    assert env.canonical["message"] in exported
    assert '"presentation"' not in exported
    assert "AnswerLayout" not in exported
    thread_id = env.thread.pk
    receipt = env.repository.delete(thread_id)
    from aichat.models import AIRetentionOutbox
    from aichat.services.retention import THREAD_DERIVATIVES

    assert receipt["status"] == "deleted", {
        "pending_kinds": list(
            AIRetentionOutbox.objects
            .filter(reference=thread_id)
            .exclude(state="done")
            .values("kind", "state", "last_error_code")
        ),
        "uncovered": THREAD_DERIVATIVES.uncovered_models(),
    }
    assert not ChatMessage.objects.filter(thread_id=thread_id).exists()
    response = env.http.get(f"/threads/{thread_id}", headers=env.headers(env.owner))
    assert response.status_code == 404


def test_thread_receipts_keep_foreign_work_pending_and_unknown_work_fail_closed(
    local_server, monkeypatch
):
    from aichat.models import AIRetentionOutbox
    from aichat.services import retention
    from django.utils import timezone

    env = local_server
    touched = []
    monkeypatch.setitem(
        retention.OUTBOX_KINDS,
        "memory_owner_purge",
        retention.OutboxKind(
            "memory_owner_purge",
            handler=lambda ref: touched.append(ref),
            probe=lambda _ref: (_ for _ in ()).throw(AssertionError("foreign probe")),
            thread_scoped=False,
        ),
    )
    foreign = AIRetentionOutbox.objects.create(
        kind="memory_owner_purge", reference=env.thread.pk, next_attempt_at=timezone.now()
    )
    assert env.repository.delete(env.thread.pk)["status"] == "deleted"
    foreign.refresh_from_db()
    assert foreign.state == "pending" and touched == []
    AIRetentionOutbox.objects.create(
        kind="future_unknown_kind", reference=env.thread.pk, next_attempt_at=timezone.now()
    )
    assert env.repository.delete(env.thread.pk)["status"] == "purge_incomplete"
    assert touched == []
