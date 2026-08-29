"""Multi-turn isolation suite, island half (S14, §13.3 patterns 1-5, 9, 10).

Drives the REAL ``NormalizedTurnService`` pipeline with the REAL
``ThreadRepository`` on the island's file-backed SQLite and a scripted
workflow double — real thread rows, real per-turn scope snapshots, real
persistence. Assertions are on STATE and IDs, never answer prose (§13.3).

Patterns 6, 7, 8, 11 need the assets/tasks apps and live in
``aichat/tests/test_multiturn_isolation.py``; pattern 10's voice half is
pinned by ``test_voice_parity.py`` (the session scope-version binding).
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
import uuid
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest  # noqa: E402
from django.core.management import call_command  # noqa: E402

# The island has no assets app; ThreadRepository.set_scope's explicit-mode
# authorizer imports assets.ai_read.authorized_machine lazily. Install a
# stub whose allow-set each test controls — the SEAM is real, the rows are
# not needed.
_AUTHORIZED_MACHINE_IDS: set[int] = set()
_assets_pkg = types.ModuleType("assets")
_assets_ai_read = types.ModuleType("assets.ai_read")


def _authorized_machine(user, machine_id):
    if int(machine_id) in _AUTHORIZED_MACHINE_IDS:
        return SimpleNamespace(pk=int(machine_id), name=f"Machine {machine_id}")
    return None


_assets_ai_read.authorized_machine = _authorized_machine
_assets_pkg.ai_read = _assets_ai_read
sys.modules.setdefault("assets", _assets_pkg)
sys.modules.setdefault("assets.ai_read", _assets_ai_read)

from ai.core.auth import AIPrincipal  # noqa: E402
from ai.core.streaming import AGUIEvent, EventType  # noqa: E402
from ai.core.trusted_context import TrustedTurnContext  # noqa: E402
from ai.core.turn_service import NormalizedTurnService  # noqa: E402
from aichat.services.threads import (  # noqa: E402
    ANALYSIS_SCOPE_SNAPSHOT_KEY,
    ThreadNotFound,
    ThreadRepository,
)


@pytest.fixture(scope="module", autouse=True)
def _database():
    call_command("migrate", verbosity=0, interactive=False)
    yield


@pytest.fixture(autouse=True)
def _authorized():
    _AUTHORIZED_MACHINE_IDS.clear()
    _AUTHORIZED_MACHINE_IDS.update({11, 12, 21, 22})
    yield
    _AUTHORIZED_MACHINE_IDS.clear()


class ScriptedWorkflow:
    """Yields a scripted reply per call and records every invocation."""

    def __init__(self, replies: list[str] | None = None):
        self.replies = list(replies or [])
        self.calls: list[dict[str, Any]] = []

    async def run_stream(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if self.replies else "Scripted reply."
        await kwargs["emitter"].emit(
            AGUIEvent(
                event_type=EventType.RUN_STARTED,
                thread_id=kwargs["thread_id"],
                run_id=f"run-{len(self.calls)}",
            )
        )
        await kwargs["emitter"].emit(
            AGUIEvent(
                event_type=EventType.WORKFLOW_STARTED,
                data={"workflow_id": "wf8"},
                thread_id=kwargs["thread_id"],
                run_id=f"run-{len(self.calls)}",
            )
        )
        yield reply
        await kwargs["emitter"].emit(
            AGUIEvent(
                event_type=EventType.RUN_FINISHED,
                thread_id=kwargs["thread_id"],
                run_id=f"run-{len(self.calls)}",
            )
        )


def _user():
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username=f"tech-{uuid.uuid4().hex[:8]}", password="unused"
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


def _repository(user) -> ThreadRepository:
    return ThreadRepository(actor=user.pk, scope_key="site:pilot")


def _service(workflow, user) -> NormalizedTurnService:
    return NormalizedTurnService(
        workflow_factory=lambda: workflow,
        repository_factory=lambda actor, context: _repository_for(actor),  # noqa: ARG005
    )


def _repository_for(actor: AIPrincipal) -> ThreadRepository:
    return ThreadRepository(actor=int(actor.user_pk), scope_key="site:pilot")


def _set_scope(user, thread_id: str, machine_ids: list[int], expected: int = 0) -> int:
    repository = _repository(user)
    repository.get_or_create(thread_id)
    result = repository.set_scope(
        thread_id,
        {"mode": "explicit_assets", "machine_ids": machine_ids},
        expected_version=expected,
    )
    return int(result["version"])


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


def _turn_snapshots(user, thread_id: str) -> list[dict[str, Any] | None]:
    from aichat.models import ChatTurn

    return [
        (turn.trusted_context or {}).get(ANALYSIS_SCOPE_SNAPSHOT_KEY)
        for turn in ChatTurn.objects.filter(thread_id=thread_id).order_by("created_at")
    ]


# --------------------------------------------------------------------------- #
# P1: select asset A -> deixis follow-up -> prior-repair comparison            #
# --------------------------------------------------------------------------- #
def test_p1_deixis_follow_up_stays_bound_to_the_selected_scope():
    user = _user()
    thread_id = f"iso_{uuid.uuid4().hex[:12]}"
    version = _set_scope(user, thread_id, [11])
    workflow = ScriptedWorkflow(["Latest work order listed.", "Compared with its prior repair."])
    service = _service(workflow, user)

    async def run():
        await _turn(service, user, thread_id, "Show the latest work order for inverter A", "p1:1")
        await _turn(service, user, thread_id, "Compare it with its prior repair", "p1:2")

    asyncio.run(run())

    snapshots = _turn_snapshots(user, thread_id)
    assert len(snapshots) == 2
    # Every turn ran under the SAME immutable scope snapshot.
    assert [s["version"] for s in snapshots] == [version, version]
    assert snapshots[0]["scope"]["machine_ids"] == [11]
    # The follow-up turn saw the same single thread, not a new one.
    scope = _repository(user).get_scope(thread_id)
    assert scope["version"] == version


# --------------------------------------------------------------------------- #
# P2: aggregate -> select a code -> comparison, one continuous thread          #
# --------------------------------------------------------------------------- #
def test_p2_three_turn_sequence_persists_in_order_on_one_thread():
    from aichat.models import ChatTurn

    user = _user()
    thread_id = f"iso_{uuid.uuid4().hex[:12]}"
    version = _set_scope(user, thread_id, [11, 12])
    workflow = ScriptedWorkflow(["Aggregate view.", "Code E42 selected.", "Manual versus field."])
    service = _service(workflow, user)

    async def run():
        for index, question in enumerate((
            "How often did each fault code occur?",
            "Look at code E42",
            "Does the manual procedure match what the field did?",
        )):
            await _turn(service, user, thread_id, question, f"p2:{index}")

    asyncio.run(run())
    turns = list(ChatTurn.objects.filter(thread_id=thread_id).order_by("created_at"))
    assert len(turns) == 3
    assert all(turn.state == "complete" for turn in turns)
    assert [
        (turn.trusted_context or {})[ANALYSIS_SCOPE_SNAPSHOT_KEY]["version"] for turn in turns
    ] == [version] * 3


# --------------------------------------------------------------------------- #
# P3: fresh thread after a supplier conversation (the M6 contamination gate)   #
# --------------------------------------------------------------------------- #
def test_p3_a_fresh_thread_carries_zero_supplier_context():
    from aichat.models import ChatMessage

    user = _user()
    supplier_thread = f"iso_{uuid.uuid4().hex[:12]}"
    workflow = ScriptedWorkflow([
        "Supplier ACME quoted SKU 998 at a unit price with a 3-week lead time."
    ])
    service = _service(workflow, user)

    async def supplier_run():
        await _turn(service, user, supplier_thread, "Find suppliers for the gasket", "p3:1")

    asyncio.run(supplier_run())

    fresh_thread = f"iso_{uuid.uuid4().hex[:12]}"
    fresh_workflow = ScriptedWorkflow(["Inverter A tripped on DC overvoltage."])
    fresh_service = _service(fresh_workflow, user)

    async def fresh_run():
        await _turn(fresh_service, user, fresh_thread, "Why did inverter A trip?", "p3:2")

    asyncio.run(fresh_run())

    # (a) Nothing supplier-flavored was HANDED to the fresh thread's workflow.
    forbidden = ("supplier", "sku", "price", "lead time", "acme")
    handed = repr(fresh_workflow.calls[0]).lower()
    for marker in forbidden:
        assert marker not in handed, marker
    # (b) Nothing supplier-flavored PERSISTED on the fresh thread.
    fresh_content = " ".join(
        message.content.lower() for message in ChatMessage.objects.filter(thread_id=fresh_thread)
    )
    for marker in forbidden:
        assert marker not in fresh_content, marker


# --------------------------------------------------------------------------- #
# P4: two concurrent threads, different asset sets, one user                   #
# --------------------------------------------------------------------------- #
def test_p4_concurrent_threads_keep_their_own_scope_and_messages():
    from aichat.models import ChatMessage

    user = _user()
    thread_a = f"iso_{uuid.uuid4().hex[:12]}"
    thread_b = f"iso_{uuid.uuid4().hex[:12]}"
    version_a = _set_scope(user, thread_a, [11])
    version_b = _set_scope(user, thread_b, [21, 22])
    workflow = ScriptedWorkflow(["Thread A answer.", "Thread B answer."])
    service = _service(workflow, user)

    async def run():
        await asyncio.gather(
            _turn(service, user, thread_a, "Status of inverter A?", "p4:a"),
            _turn(service, user, thread_b, "Status of the pump pair?", "p4:b"),
        )

    asyncio.run(run())

    assert _repository(user).get_scope(thread_a)["version"] == version_a
    assert _repository(user).get_scope(thread_b)["version"] == version_b
    assert [s["scope"]["machine_ids"] for s in _turn_snapshots(user, thread_a)] == [[11]]
    assert [s["scope"]["machine_ids"] for s in _turn_snapshots(user, thread_b)] == [[21, 22]]
    a_contents = {m.content for m in ChatMessage.objects.filter(thread_id=thread_a)}
    b_contents = {m.content for m in ChatMessage.objects.filter(thread_id=thread_b)}
    assert not (a_contents & b_contents & {"Thread A answer.", "Thread B answer."})


# --------------------------------------------------------------------------- #
# P5: equivalent turns for two users with different boundaries                 #
# --------------------------------------------------------------------------- #
def test_p5_users_cannot_see_or_reuse_each_others_threads():
    user_a = _user()
    user_b = _user()
    thread_a = f"iso_{uuid.uuid4().hex[:12]}"
    _set_scope(user_a, thread_a, [11])

    workflow = ScriptedWorkflow(["A's answer.", "B's answer."])
    service_a = _service(workflow, user_a)
    service_b = _service(workflow, user_b)

    async def run():
        await asyncio.gather(
            _turn(service_a, user_a, thread_a, "Same question", "p5:a"),
            _turn(service_b, user_b, f"iso_{uuid.uuid4().hex[:12]}", "Same question", "p5:b"),
        )

    asyncio.run(run())

    # B's boundary cannot even see A's thread — reads fail closed.
    with pytest.raises(ThreadNotFound):
        _repository(user_b).get_scope(thread_a)


# --------------------------------------------------------------------------- #
# P9: an in-flight turn stays bound to its immutable pre-edit snapshot         #
# --------------------------------------------------------------------------- #
def test_p9_scope_edit_during_a_turn_never_rebinds_the_running_turn():
    """The revocation-abort half (final live reauthorization) is the evidence
    gate's C13 and is pinned by test_analysis_validator; this pins the
    snapshot half: an owner scope edit mid-turn leaves the RUNNING turn on
    the version it started with."""
    user = _user()
    thread_id = f"iso_{uuid.uuid4().hex[:12]}"
    version = _set_scope(user, thread_id, [11])

    class EditingWorkflow(ScriptedWorkflow):
        async def run_stream(self, **kwargs):
            self.calls.append(kwargs)
            await kwargs["emitter"].emit(
                AGUIEvent(
                    event_type=EventType.RUN_STARTED,
                    thread_id=kwargs["thread_id"],
                    run_id="run-p9",
                )
            )
            # Mid-turn, the owner edits the scope on another surface.
            from asgiref.sync import sync_to_async

            await sync_to_async(
                lambda: _set_scope(user, thread_id, [12], expected=version),
                thread_sensitive=True,
            )()
            yield "Answer bound to the original selection."
            await kwargs["emitter"].emit(
                AGUIEvent(
                    event_type=EventType.RUN_FINISHED,
                    thread_id=kwargs["thread_id"],
                    run_id="run-p9",
                )
            )

    workflow = EditingWorkflow()
    service = _service(workflow, user)

    async def run():
        await _turn(service, user, thread_id, "What is selected?", "p9:1")

    asyncio.run(run())

    snapshots = _turn_snapshots(user, thread_id)
    assert [s["version"] for s in snapshots] == [version]
    assert snapshots[0]["scope"]["machine_ids"] == [11]
    # The THREAD reflects the edit for the NEXT turn.
    assert _repository(user).get_scope(thread_id)["version"] == version + 1


# --------------------------------------------------------------------------- #
# P10: text + voice bind to one scope version                                  #
# --------------------------------------------------------------------------- #
def test_p10_voice_sessions_bind_the_same_thread_scope_version():
    """The behavioral half (409 VOICE_SCOPE_CHANGED, restart re-binds) is
    pinned by test_voice_parity; here: the binding field exists and reads
    the SAME ChatThread version the text rail persists."""
    from voice.models import VoiceSession

    assert hasattr(VoiceSession, "analysis_scope_version")
    user = _user()
    thread_id = f"iso_{uuid.uuid4().hex[:12]}"
    version = _set_scope(user, thread_id, [11])
    from ai.core.voice.routes import _thread_scope_version

    assert _thread_scope_version(thread_id, user.pk) == version
