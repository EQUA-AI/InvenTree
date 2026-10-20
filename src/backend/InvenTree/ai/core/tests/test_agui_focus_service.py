"""RV07 follow-up: the AG-UI focus stamp at the REAL consuming boundary.

Unlike the wire/translation suite, every control here crosses the real
``run_agui`` handler, the real ``NormalizedTurnService.process`` and the real
``ThreadRepository`` ORM on owned rows. The ONLY scripted seam is the
workflow/model side-effect sink (``workflow_factory`` -> ``run_stream``): no
real AI call is made, and nothing else is replaced — not ``service.process``,
not ``intake.begin``, not ``begin_turn``'s expected-focus enforcement, not
repository authorization, not the ORM.

Durable regression contract:

* a stale stamp (older than the thread's genuinely stored ``focus_version``)
  emits the in-band SSE ``RUN_ERROR`` code ``focus_version_conflict`` BEFORE
  any workflow invocation and before ANY new persisted turn/message effect;
* a fresh stamp (equal to the stored version) pairs with an actual persisted
  successful scripted reply;
* legacy omission stays a no-op, camelCase/snake_case aliases both land, a
  malformed stamp is rejected before processing, and a cross-owner attempt is
  denied by the real repository boundary with zero effects.

Fixtures disclosed (per the accepted RV07 plan): ordinary real Django users
(is_staff/is_superuser False); the principal boundary and trusted server
context are supplied as disclosed test fixtures (no authentication proof is
attempted) and mounted through the real ``principal_context`` variable.
One state-setup write is disclosed: the pre-existing stored focus payload on
the owned thread row is seeded through the ORM so that the version advance
itself can run through the real ``ThreadRepository.set_focus`` clear path.
"""

# ruff: noqa: E402
from __future__ import annotations

import asyncio
import json
import os
import uuid
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")
# The real trusted-context builder fails closed on an unconfigured policy key;
# pin the test plane's single site (both env aliases) so every lane resolves
# the identical server-owned policy the real principal factory binds to.
os.environ["AIMMS_SINGLE_SITE_POLICY_KEY"] = (
    os.environ.get("AIMMS_SINGLE_SITE_POLICY_KEY") or "site:pilot"
)
os.environ["FEATURE_AGUI_ENDPOINT"] = "true"
os.environ["AIMMS_FEATURE_AGUI_ENDPOINT"] = "true"

import django

django.setup()

from unittest.mock import AsyncMock, patch

import pytest
from ai.core.agui.models import RunAgentInput
from ai.core.agui.routes import run_agui
from ai.core.auth import AIPrincipal, principal_context
from ai.core.config import get_settings
from ai.core.focus import contract as focus_contract
from ai.core.streaming import AGUIEvent, EventType
from ai.core.tests.focus_test_db import ensure_island_tables
from ai.core.trusted_context import TrustedTurnContext
from ai.core.turn_service import NormalizedTurnService
from aichat.models import ChatMessage, ChatThread, ChatTurn, ThreadNamespace
from aichat.services.threads import ThreadRepository
from fastapi import HTTPException
from starlette.requests import Request


@pytest.fixture(scope="module", autouse=True)
def _tables() -> None:
    # Owned disposable SQLite carrier: real schema, no migration fixture.
    ensure_island_tables()


# --------------------------------------------------------------------------- #
# Allowed scripted seam: the workflow/model side-effect sink ONLY              #
# --------------------------------------------------------------------------- #
class _ScriptedWorkflow:
    """Yield one scripted reply per call and count invocations.

    This stands in for the real RootWorkflow's model/tool side effects (the
    permitted sink). Everything around it — intake, admission, persistence,
    canonical event emission — is the production code.
    """

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    async def run_stream(self, **kwargs):
        self.calls += 1
        # Real native lifecycle/text events (ai.core.streaming types), emitted
        # through the real emitter exactly as the working _Workflow fixture in
        # test_normalized_turn_service does: ai/core/agui/translate.py turns
        # TEXT_MESSAGE_START/CONTENT/END into spec frames carrying messageId and
        # delta, and RUN_FINISHED into the terminal spec frame. RUN_STARTED is
        # already synthesized by the transport (translate.run_started_frame),
        # so the sink does not re-emit it.
        emitter = kwargs["emitter"]
        thread_id = kwargs.get("thread_id") or ""
        run_id = kwargs.get("run_id") or "run-scripted"
        message_id = f"msg_{uuid.uuid4().hex[:8]}"
        await emitter.emit(
            AGUIEvent(
                event_type=EventType.TEXT_MESSAGE_START,
                data={"messageId": message_id},
                thread_id=thread_id,
                run_id=run_id,
            )
        )
        await emitter.emit(
            AGUIEvent(
                event_type=EventType.TEXT_MESSAGE_CONTENT,
                data={"messageId": message_id, "delta": self.reply},
                thread_id=thread_id,
                run_id=run_id,
            )
        )
        await emitter.emit(
            AGUIEvent(
                event_type=EventType.TEXT_MESSAGE_END,
                data={"messageId": message_id},
                thread_id=thread_id,
                run_id=run_id,
            )
        )
        yield self.reply
        await emitter.emit(
            AGUIEvent(
                event_type=EventType.RUN_FINISHED,
                thread_id=thread_id,
                run_id=run_id,
            )
        )


# --------------------------------------------------------------------------- #
# Real owned ORM fixtures                                                     #
# --------------------------------------------------------------------------- #
_SCOPE_KEY = "site:pilot"


def _principal_fixture(user) -> AIPrincipal:
    """Disclosed principal boundary fixture (real dataclass, real user row)."""
    return AIPrincipal(
        subject=f"user:{user.pk}",
        actor=f"user:{user.pk}",
        user_pk=str(user.pk),
        username=user.username,
        authentication_method="django_session",
        scope=_SCOPE_KEY,
        policy_version="1",
        is_staff=False,
        is_superuser=False,
    )


def _context_fixture(correlation_id: str) -> TrustedTurnContext:
    """Disclosed trusted server context fixture (real dataclass instance)."""
    return TrustedTurnContext(
        actor="fixture",
        server_policy_key=_SCOPE_KEY,
        server_policy_hash="0" * 64,
        thread_namespace="unscoped",
        server_route_hints=("/agui",),
        allowed_capabilities=("chat.unscoped.read",),
        correlation_id=correlation_id,
        policy_version="1",
        untrusted_content="",
    )


def _user():
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username=f"rv07-{uuid.uuid4().hex[:8]}",
        password="unused",
        is_staff=False,
        is_superuser=False,
    )


def _owned_setup(user) -> tuple[ThreadRepository, str]:
    """A real owner-bound repository and one real owned thread row."""
    repository = ThreadRepository(
        actor=str(user.pk),
        scope_key=_SCOPE_KEY,
        namespace=ThreadNamespace.UNSCOPED,
    )
    thread_id = f"rv07_{uuid.uuid4().hex[:12]}"
    thread, created = repository.get_or_create(thread_id, title="RV07 focus fixture")
    assert created and str(thread.owner_id) == str(user.pk)
    return repository, thread_id


def _advance_real_focus_version(repository: ThreadRepository, thread_id: str) -> int:
    """Make the stored stamp genuinely AHEAD of a would-be stale client stamp.

    Disclosed setup write: one stored focus payload is seeded on the owned row
    through the ORM (no reader is consulted on the clear path). The version
    advance itself then runs through the real ``ThreadRepository.set_focus``
    optimistic-concurrency clear — production code increments the version.
    """
    stored = focus_contract.focus_to_stored(
        SimpleNamespace(
            kind=focus_contract.FOCUS_KINDS[0],
            id=1,
            label="RV07 fixture focus",
            machine_id=None,
            work_order_id=None,
        )
    )
    ChatThread.objects.filter(pk=thread_id, owner_id=repository.actor_id).update(
        conversation_focus=stored
    )
    repository.set_focus(thread_id, None, expected_focus_version=0)
    thread = ChatThread.objects.get(pk=thread_id)
    assert thread.focus_version == 1
    return thread.focus_version


# --------------------------------------------------------------------------- #
# Real transport + real handler driver                                        #
# --------------------------------------------------------------------------- #
_OMIT = object()


def _body(
    thread_id: str,
    *,
    stamp: object = _OMIT,
    spelling: str = "camel",
    content: str = "How many parts are on file?",
) -> dict:
    props: dict = {}
    if stamp is not _OMIT:
        key = "expectedFocusVersion" if spelling == "camel" else "expected_focus_version"
        props[key] = stamp
    return {
        "threadId": thread_id,
        "runId": f"run_{uuid.uuid4().hex[:6]}",
        "messages": [{"role": "user", "content": content}],
        "forwardedProps": props,
    }


def _request(payload: dict, *, headers: dict | None = None, method: str = "POST") -> Request:
    raw = json.dumps(payload).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": "/agui",
        "raw_path": b"/agui",
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    }

    receive = AsyncMock(
        side_effect=lambda: {"type": "http.request", "body": raw, "more_body": False}
    )

    return Request(scope, receive)


def _run(user, payload: dict, workflow: _ScriptedWorkflow, *, headers=None) -> list[str]:
    """Drive the REAL handler to completion and consume the real stream.

    ``get_turn_service`` is patched only to return a REAL
    ``NormalizedTurnService`` instance whose workflow sink is scripted and
    whose repository is the class's own real default; ``process`` itself is
    the production method. Draining the actual ``StreamingResponse``
    ``body_iterator`` is what executes the background processing.
    """
    service = NormalizedTurnService(workflow_factory=lambda: workflow)
    # Disclosed fixtures: the principal boundary and trusted server context.
    # Everything else in the handler is production code; the flag is pinned on
    # a real Settings copy because the carrier env carries no feature config.
    flagged = get_settings().model_copy(update={"feature_agui_endpoint": True})

    def _context(principal, **kwargs):
        return _context_fixture(kwargs.get("correlation_id") or str(uuid.uuid4()))

    async def scenario() -> list[str]:
        token = principal_context.set(_principal_fixture(user))
        try:
            with (
                patch("ai.core.config.get_settings", return_value=flagged),
                patch(
                    "ai.core.trusted_context.build_trusted_turn_context",
                    side_effect=_context,
                ),
                patch("ai.core.trusted_context.resolve_actor_locale", return_value="en"),
                patch("ai.core.app.get_turn_service", return_value=service),
            ):
                response = await run_agui(_request(payload, headers=headers))
                assert response.media_type == "text/event-stream"
                return [chunk async for chunk in response.body_iterator]
        finally:
            principal_context.reset(token)

    return asyncio.run(scenario())


def _turn_count(thread_id: str) -> int:
    return ChatTurn.objects.filter(thread_id=thread_id).count()


def _messages(thread_id: str) -> list[ChatMessage]:
    return list(ChatMessage.objects.filter(thread_id=thread_id).order_by("sequence"))


# --------------------------------------------------------------------------- #
# The durable stale/fresh regression at the real boundary                     #
# --------------------------------------------------------------------------- #
def test_stale_focus_stamp_conflicts_before_any_turn_effect() -> None:
    """Stale stamp => in-band conflict BEFORE workflow work or any new row."""
    user = _user()
    repository, thread_id = _owned_setup(user)
    stale = _advance_real_focus_version(repository, thread_id) - 1  # genuinely old
    assert stale == 0

    workflow = _ScriptedWorkflow("Must never be produced.")
    frames = _run(user, _body(thread_id, stamp=stale), workflow)
    blob = "".join(frames)

    # The conflict is in-band SSE after the stream began — never HTTP 409.
    assert '"code":"focus_version_conflict"' in blob
    assert "Conversation focus changed" in blob
    for model_work in ("TEXT_MESSAGE", "RUN_FINISHED", "delta"):
        assert model_work not in blob

    # BEFORE any workflow/model/tool invocation ...
    assert workflow.calls == 0
    # ... and before any new persisted turn/message effect.
    assert _turn_count(thread_id) == 0
    assert _messages(thread_id) == []

    # The stored version itself is untouched, still genuinely ahead.
    assert ChatThread.objects.get(pk=thread_id).focus_version == 1


def test_fresh_focus_stamp_pairs_with_persisted_scripted_reply() -> None:
    """Fresh stamp (snake_case spelling) => real persisted successful reply."""
    user = _user()
    repository, thread_id = _owned_setup(user)
    fresh = _advance_real_focus_version(repository, thread_id)  # 1, genuinely current

    workflow = _ScriptedWorkflow("Fresh scripted reply.")
    frames = _run(
        user,
        _body(thread_id, stamp=fresh, spelling="snake", content="Fresh question."),
        workflow,
    )
    blob = "".join(frames)

    assert '"code":"focus_version_conflict"' not in blob
    assert '"type":"RUN_FINISHED"' in blob
    assert '"delta"' in blob
    assert "Fresh scripted reply." in blob
    assert workflow.calls == 1

    # Owned row readbacks: exactly one turn, user + assistant messages.
    assert _turn_count(thread_id) == 1
    turn = ChatTurn.objects.get(thread_id=thread_id)
    messages = _messages(thread_id)
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].content == "Fresh question."
    assistant = messages[1]
    persisted = (
        (assistant.content or "")
        + json.dumps(assistant.metadata or {})
        + json.dumps(turn.canonical_result or {})
    )
    assert "Fresh scripted reply." in persisted


def test_legacy_omitted_stamp_is_a_no_op_and_persists() -> None:
    """Legacy clients never send a stamp: the turn runs and persists."""
    user = _user()
    _repository, thread_id = _owned_setup(user)

    workflow = _ScriptedWorkflow("Legacy scripted reply.")
    frames = _run(user, _body(thread_id), workflow)  # no forwardedProps stamp
    blob = "".join(frames)

    assert '"code":"focus_version_conflict"' not in blob
    assert '"type":"RUN_FINISHED"' in blob
    assert workflow.calls == 1
    assert _turn_count(thread_id) == 1
    assert [message.role for message in _messages(thread_id)] == ["user", "assistant"]
    assert ChatThread.objects.get(pk=thread_id).focus_version == 0


def test_cross_owner_denial_is_thread_not_found_with_zero_effects() -> None:
    """A stranger's fresh stamp buys nothing: the real boundary denies first."""
    owner = _user()
    repository, thread_id = _owned_setup(owner)
    current = _advance_real_focus_version(repository, thread_id)
    stranger = _user()
    assert stranger.pk != owner.pk

    workflow = _ScriptedWorkflow("Must never be produced.")
    frames = _run(stranger, _body(thread_id, stamp=current), workflow)
    blob = "".join(frames)

    assert '"code":"thread_not_found"' in blob
    assert "focus_version_conflict" not in blob
    assert workflow.calls == 0
    assert _turn_count(thread_id) == 0
    assert _messages(thread_id) == []
    # The stranger owns no thread rows at all; the owned row is intact.
    assert not ChatThread.objects.filter(owner_id=stranger.pk).exists()
    thread = ChatThread.objects.get(pk=thread_id)
    assert str(thread.owner_id) == str(owner.pk)
    assert thread.focus_version == current


def test_malformed_focus_stamp_is_rejected_before_processing() -> None:
    """Non-strict stamps never reach the turn service (422, zero effects)."""
    user = _user()
    _repository, thread_id = _owned_setup(user)

    workflow = _ScriptedWorkflow("Must never be produced.")
    with pytest.raises(HTTPException) as excinfo:
        _run(user, _body(thread_id, stamp="7"), workflow)
    assert excinfo.value.status_code == 422
    assert workflow.calls == 0
    assert _turn_count(thread_id) == 0
    assert _messages(thread_id) == []


def test_focus_stamp_aliases_and_optional_legacy_shape() -> None:
    """Control: both spellings land, omission stays None (ChatRequest parity)."""
    base = {"threadId": "t", "runId": "r", "messages": [{"role": "user", "content": "q"}]}
    camel = RunAgentInput.model_validate({**base, "forwardedProps": {"expectedFocusVersion": 5}})
    snake = RunAgentInput.model_validate({**base, "forwardedProps": {"expected_focus_version": 5}})
    assert camel.forwarded_props.expected_focus_version == 5
    assert snake.forwarded_props.expected_focus_version == 5
    legacy = RunAgentInput.model_validate({**base, "forwardedProps": {}})
    assert legacy.forwarded_props.expected_focus_version is None
