"""P02/P33: the Work-focus receipt travels the chat wires and stays honest.

The repository verification itself (signature, expiry, owner, live Work role
and boundary) is tested against real rows in
``aichat/tests/test_work_focus.py``. These tests pin what the HTTP adapters
and the turn pipeline do with it: refuse before any model call, pass the
receipt only to ``begin_turn``, keep it out of the request fingerprint, give
the model ids only, save the ids on the answer and reload them for the owner.
"""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import ai.core

# As in test_typed_chat_regression: load only workflows.root, not every
# provider workflow the package would import eagerly.
_workflows_package = types.ModuleType("ai.core.workflows")
_workflows_package.__path__ = [str(Path(ai.core.__file__).resolve().parent / "workflows")]
sys.modules.setdefault("ai.core.workflows", _workflows_package)

import pytest
from ai.core.agui.models import RunAgentInput
from ai.core.analysis import wire
from ai.core.app import (
    ChatRequest,
    WorkFocusAcknowledgeRequest,
    acknowledge_work_focus,
    chat,
    chat_stream,
    get_thread,
    get_turn_work_focus,
)
from ai.core.auth import AIPrincipal, principal_context
from ai.core.streaming import AGUIEvent, EventType
from ai.core.trusted_context import TrustedTurnContext
from ai.core.turn_service import NormalizedTurnService
from aichat.services import BeginTurnResult, ThreadNotFound
from aichat.services.work_focus import WorkFocusError, public_focus
from fastapi import HTTPException
from pydantic import ValidationError

SNAPSHOT = {
    "schema_version": 1,
    "kind": "work_order",
    "work_order_id": 73,
    "lifecycle_version": 4,
    "machine_id": 12,
    "boundary_hash": "b" * 64,
}
PUBLIC = {key: value for key, value in SNAPSHOT.items() if key != "boundary_hash"}


class _Workflow:
    """Record the trusted context the model-facing workflow receives."""

    def __init__(self) -> None:
        self.call: dict[str, object] | None = None

    async def run_stream(self, **kwargs):
        self.call = kwargs
        emitter = kwargs["emitter"]
        for event_type, data in (
            (EventType.RUN_STARTED, {"message": "Starting run"}),
            (EventType.TEXT_MESSAGE_START, {"messageId": "m", "role": "assistant"}),
            (EventType.TEXT_MESSAGE_CONTENT, {"messageId": "m", "delta": "Work 73 is open."}),
            (EventType.TEXT_MESSAGE_END, {"messageId": "m"}),
            (EventType.RUN_FINISHED, {"result": None}),
        ):
            await emitter.emit(
                AGUIEvent(
                    event_type=event_type,
                    data=data,
                    thread_id=str(kwargs["thread_id"]),
                    run_id="focus-run",
                    agent_name="root_workflow",
                )
            )
        yield "Work 73 is open."


class _Turn:
    pk = "turn-focus"
    is_terminal = False
    canonical_result = None


class _Repository:
    """A turn store that verifies receipts the way the real one reports them."""

    def __init__(self, *, intake_error: Exception | None = None) -> None:
        self.thread = SimpleNamespace(pk="thread-focus")
        self.turn = _Turn()
        self.begin_calls: list[dict[str, object]] = []
        self.verified: list[tuple[str, str]] = []
        self.terminal_kwargs: dict[str, object] | None = None
        self.intake_error = intake_error

    def get_or_create(self, thread_id=None, *, title=""):
        self.thread.pk = thread_id or self.thread.pk
        return self.thread, False

    def verify_work_focus(self, thread_id, receipt):
        self.verified.append((thread_id, receipt))
        if receipt != "receipt-ok":
            raise WorkFocusError("work_focus_rejected")
        return dict(SNAPSHOT)

    def begin_turn(self, thread_id, **kwargs):
        self.begin_calls.append(kwargs)
        if self.intake_error is not None:
            raise self.intake_error
        focused = kwargs.get("work_focus_receipt") is not None
        return BeginTurnResult(
            self.turn, False, work_focus_snapshot=dict(SNAPSHOT) if focused else None
        )

    def terminal(self, turn_id, **kwargs):
        self.terminal_kwargs = kwargs
        self.turn.is_terminal = True
        self.turn.canonical_result = kwargs["canonical_result"]
        return self.turn


def _principal() -> AIPrincipal:
    return AIPrincipal(
        subject="user:1",
        actor="user:1",
        user_pk="1",
        username="focus",
        authentication_method="django_session",
        scope="site:focus",
        policy_version="1",
        is_staff=False,
        is_superuser=False,
    )


def _context() -> TrustedTurnContext:
    return TrustedTurnContext(
        actor="user:1",
        server_policy_key="site:focus",
        server_policy_hash="0" * 64,
        thread_namespace="unscoped",
        server_route_hints=("/chat",),
        allowed_capabilities=("chat.unscoped.read",),
        correlation_id="00000000-0000-0000-0000-000000000001",
        policy_version="1",
        untrusted_content="{}",
    )


def _service(workflow: _Workflow, repository: _Repository) -> NormalizedTurnService:
    class Service(NormalizedTurnService):
        @staticmethod
        async def _call_sync(function, *args, **kwargs):
            return function(*args, **kwargs)

    return Service(
        workflow_factory=lambda: workflow,
        repository_factory=lambda actor, context: repository,  # noqa: ARG005
    )


async def _with_principal(awaitable):
    token = principal_context.set(_principal())
    try:
        return await awaitable
    finally:
        principal_context.reset(token)


def _run_chat(repository: _Repository, workflow: _Workflow, **request):
    async def exercise():
        with (
            patch("ai.core.app.get_turn_service", return_value=_service(workflow, repository)),
            patch("ai.core.app.build_trusted_turn_context", return_value=_context()),
            patch("ai.core.app._repository", return_value=repository),
        ):
            return await _with_principal(
                chat(ChatRequest(message="What is left on this?", **request))
            )

    return asyncio.run(exercise())


def _sse_records(payload: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in payload.splitlines()
        if line.startswith("data: ")
    ]


# --- request models -----------------------------------------------------------


def test_chat_request_receipt_is_optional_and_bounded() -> None:
    assert ChatRequest(message="hi").work_focus_receipt is None
    assert ChatRequest(message="hi", work_focus_receipt="r").work_focus_receipt == "r"
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", work_focus_receipt="")
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", work_focus_receipt="r" * 4097)


def test_agui_forwarded_props_accept_the_receipt_by_either_name() -> None:
    for key in ("workFocusReceipt", "work_focus_receipt"):
        run_input = RunAgentInput.model_validate({"runId": "r", "forwardedProps": {key: "rc"}})
        assert run_input.forwarded_props.work_focus_receipt == "rc"
    assert RunAgentInput.model_validate({"runId": "r"}).forwarded_props.work_focus_receipt is None


@pytest.mark.parametrize(
    "body",
    [
        {"work_order_id": "73", "expected_version": 4},
        {"work_order_id": True, "expected_version": 4},
        {"work_order_id": 73, "expected_version": 4.0},
        {"work_order_id": 0, "expected_version": 4},
        {"work_order_id": 73, "expected_version": -1},
        {"work_order_id": 73},
    ],
)
def test_acknowledge_request_takes_strict_positive_integers(body) -> None:
    with pytest.raises(ValidationError):
        WorkFocusAcknowledgeRequest.model_validate(body)


# --- the turn path --------------------------------------------------------------


def test_receipt_reaches_intake_and_the_model_gets_ids_only() -> None:
    repository = _Repository()
    workflow = _Workflow()
    response = _run_chat(
        repository, workflow, thread_id="thread-focus", work_focus_receipt="receipt-ok"
    )

    assert response.message == "Work 73 is open."
    # Checked once before the turn, then handed to the durable intake.
    assert repository.verified == [("thread-focus", "receipt-ok")]
    assert repository.begin_calls[0]["work_focus_receipt"] == "receipt-ok"
    # The model-facing context names the Work by id; the boundary hash and
    # the bearer receipt never reach it.
    assert workflow.call is not None
    context = workflow.call["context"]
    assert context["work_focus"] == PUBLIC
    assert "receipt-ok" not in json.dumps(context, default=str)
    assert "b" * 64 not in json.dumps(context, default=str)


def test_receipt_is_not_part_of_the_request_fingerprint() -> None:
    focused = _Repository()
    _run_chat(focused, _Workflow(), thread_id="thread-focus", work_focus_receipt="receipt-ok")
    plain = _Repository()
    _run_chat(plain, _Workflow(), thread_id="thread-focus")

    assert (
        focused.begin_calls[0]["request_fingerprint"] == plain.begin_calls[0]["request_fingerprint"]
    )
    # Only the dedicated argument carries it: not the fingerprint, the
    # trusted context or the stored metadata.
    others = {k: v for k, v in focused.begin_calls[0].items() if k != "work_focus_receipt"}
    assert "receipt-ok" not in json.dumps(others, default=str)


def test_unfocused_turn_keeps_its_exact_intake_call_and_result() -> None:
    repository = _Repository()
    workflow = _Workflow()
    _run_chat(repository, workflow, thread_id="thread-focus")

    assert repository.verified == []
    assert "work_focus_receipt" not in repository.begin_calls[0]
    assert workflow.call is not None
    assert "work_focus" not in workflow.call["context"]
    assert repository.terminal_kwargs is not None
    assert "work_focus" not in repository.terminal_kwargs["canonical_result"]
    assert "work_focus" not in repository.terminal_kwargs["output_metadata"]


def test_answer_and_its_message_carry_the_verified_ids() -> None:
    repository = _Repository()
    _run_chat(repository, _Workflow(), thread_id="thread-focus", work_focus_receipt="receipt-ok")

    assert repository.terminal_kwargs is not None
    assert repository.terminal_kwargs["canonical_result"]["work_focus"] == PUBLIC
    assert repository.terminal_kwargs["output_metadata"]["work_focus"] == PUBLIC


def test_refused_receipt_fails_before_any_turn() -> None:
    repository = _Repository()
    with pytest.raises(HTTPException) as refused:
        _run_chat(repository, _Workflow(), thread_id="thread-focus", work_focus_receipt="stale")
    assert refused.value.status_code == 400
    assert refused.value.detail == "work_focus_rejected"
    assert repository.begin_calls == []


def test_receipt_without_a_thread_is_refused() -> None:
    repository = _Repository()
    with pytest.raises(HTTPException) as refused:
        _run_chat(repository, _Workflow(), work_focus_receipt="receipt-ok")
    assert refused.value.status_code == 400
    assert refused.value.detail == "work_focus_rejected"
    assert repository.verified == []
    assert repository.begin_calls == []


def test_receipt_for_a_thread_not_owned_is_not_found() -> None:
    repository = _Repository()

    def missing(thread_id, receipt):
        raise ThreadNotFound("Thread not found")

    repository.verify_work_focus = missing
    with pytest.raises(HTTPException) as refused:
        _run_chat(repository, _Workflow(), thread_id="thread-x", work_focus_receipt="receipt-ok")
    assert refused.value.status_code == 404
    assert repository.begin_calls == []


def test_stream_refuses_before_opening() -> None:
    repository = _Repository()

    async def exercise():
        with (
            patch("ai.core.app.get_turn_service", return_value=_service(_Workflow(), repository)),
            patch("ai.core.app.build_trusted_turn_context", return_value=_context()),
            patch("ai.core.app._repository", return_value=repository),
        ):
            return await _with_principal(
                chat_stream(
                    ChatRequest(message="hi", thread_id="thread-focus", work_focus_receipt="bad")
                )
            )

    with pytest.raises(HTTPException) as refused:
        asyncio.run(exercise())
    assert refused.value.detail == "work_focus_rejected"
    assert repository.begin_calls == []


def test_stream_reports_a_revocation_at_intake_as_a_typed_refusal() -> None:
    repository = _Repository(intake_error=WorkFocusError("work_focus_rejected"))

    async def exercise():
        with (
            patch("ai.core.app.get_turn_service", return_value=_service(_Workflow(), repository)),
            patch("ai.core.app.build_trusted_turn_context", return_value=_context()),
            patch("ai.core.app._repository", return_value=repository),
        ):
            token = principal_context.set(_principal())
            try:
                response = await chat_stream(
                    ChatRequest(
                        message="hi", thread_id="thread-focus", work_focus_receipt="receipt-ok"
                    )
                )
                return "".join([chunk async for chunk in response.body_iterator])
            finally:
                principal_context.reset(token)

    records = _sse_records(asyncio.run(exercise()))
    errors = [record for record in records if record.get("type") == "RUN_ERROR"]
    assert [error["code"] for error in errors] == ["work_focus_rejected"]
    assert [error["failure_class"] for error in errors] == ["work_focus_rejected"]
    assert repository.terminal_kwargs is None


# --- acknowledgement and saved-focus endpoints -----------------------------------


class _AckRepository:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple] = []
        self.error = error

    def get_or_create(self, thread_id=None, *, title=""):
        self.calls.append(("get_or_create", thread_id))
        return SimpleNamespace(pk=thread_id), True

    def acknowledge_work_focus(self, thread_id, work_order_id, *, expected_version):
        self.calls.append(("acknowledge", thread_id, work_order_id, expected_version))
        if self.error is not None:
            raise self.error
        return {
            "schema_version": 1,
            "thread_id": thread_id,
            "focus": dict(SNAPSHOT),
            "receipt": "signed",
            "expires_in": 300,
        }


def _acknowledge(repository, **body):
    async def exercise():
        with patch("ai.core.app._repository", return_value=repository):
            return await _with_principal(
                acknowledge_work_focus(
                    "thread-new", WorkFocusAcknowledgeRequest.model_validate(body)
                )
            )

    return asyncio.run(exercise())


def test_acknowledgement_materializes_the_thread_and_returns_ids_only() -> None:
    repository = _AckRepository()
    payload = _acknowledge(repository, work_order_id=73, expected_version=4)

    assert repository.calls == [
        ("get_or_create", "thread-new"),
        ("acknowledge", "thread-new", 73, 4),
    ]
    assert payload["focus"] == PUBLIC
    assert payload["receipt"] == "signed"
    wire.WorkFocusAcknowledgement.model_validate(payload)


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (WorkFocusError("work_focus_rejected"), 400, "work_focus_rejected"),
        (ThreadNotFound("Thread not found"), 404, "Thread not found"),
    ],
)
def test_acknowledgement_refusals_are_value_free(error, status, detail) -> None:
    with pytest.raises(HTTPException) as refused:
        _acknowledge(_AckRepository(error), work_order_id=73, expected_version=4)
    assert refused.value.status_code == status
    assert refused.value.detail == detail


class _SavedRepository:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def get_turn_work_focus(self, thread_id, turn_id):
        if self.error is not None:
            raise self.error
        return self.result


def _saved(repository):
    async def exercise():
        with patch("ai.core.app._repository", return_value=repository):
            return await _with_principal(get_turn_work_focus("thread-focus", "turn-focus"))

    return asyncio.run(exercise())


def test_saved_focus_returns_ids_and_staleness() -> None:
    payload = _saved(
        _SavedRepository({
            "thread_id": "thread-focus",
            "turn_id": "turn-focus",
            "focus": dict(SNAPSHOT),
            "stale": True,
        })
    )
    assert payload == {
        "thread_id": "thread-focus",
        "turn_id": "turn-focus",
        "focus": PUBLIC,
        "stale": True,
    }
    wire.SavedTurnWorkFocus.model_validate(payload)


@pytest.mark.parametrize(
    "repository",
    [
        _SavedRepository(None),
        _SavedRepository(error=WorkFocusError("work_focus_rejected")),
        _SavedRepository(error=ThreadNotFound("Thread not found")),
    ],
)
def test_saved_focus_absent_revoked_or_unknown_is_one_not_found(repository) -> None:
    with pytest.raises(HTTPException) as missing:
        _saved(repository)
    assert missing.value.status_code == 404
    assert missing.value.detail == "Not found"


# --- reload projection -------------------------------------------------------------


class _Turns:
    def count(self) -> int:
        return 1


class _ReadRepository:
    def __init__(self, *, shared: bool, focused: bool = True) -> None:
        self.shared = shared
        self.focused = focused
        self.state_calls: list[list[str]] = []
        self.thread = SimpleNamespace(
            pk="thread-focus",
            title="Focus",
            turns=_Turns(),
            last_workflow=None,
            summary=None,
            created_at=datetime(2026, 10, 19, tzinfo=UTC),
            updated_at=datetime(2026, 10, 19, tzinfo=UTC),
        )

    def get_readable(self, thread_id):
        return self.thread, self.shared

    def readable_message_page(self, thread_id, *, limit, before_sequence):
        message = SimpleNamespace(
            pk="message-1",
            sequence=2,
            role="assistant",
            content="Work 73 is open.",
            created_at=datetime(2026, 10, 19, tzinfo=UTC),
            metadata={"work_focus": dict(PUBLIC)} if self.focused else {},
        )
        return [message], False

    def work_focus_states(self, thread_id, message_ids):
        self.state_calls.append(list(message_ids))
        return {"message-1": "changed"}

    def scope_summary(self, thread):
        return {"mode": "legacy_unscoped", "version": 0, "display_label": ""}


@pytest.mark.parametrize(("shared", "expected"), [(False, PUBLIC), (True, None)])
def test_reload_shows_the_focus_to_the_owner_only(shared, expected) -> None:
    async def exercise():
        with patch("ai.core.app._repository", return_value=_ReadRepository(shared=shared)):
            return await _with_principal(get_thread("thread-focus"))

    payload = asyncio.run(exercise())
    assert payload["messages"][0]["work_focus"] == expected


@pytest.mark.parametrize(
    ("shared", "focused", "state", "calls"),
    [
        (False, True, "changed", [["message-1"]]),
        (True, True, None, []),
        (False, False, None, []),
    ],
)
def test_reload_reports_how_the_work_stands_now_for_the_owner(
    shared, focused, state, calls
) -> None:
    repository = _ReadRepository(shared=shared, focused=focused)

    async def exercise():
        with patch("ai.core.app._repository", return_value=repository):
            return await _with_principal(get_thread("thread-focus"))

    payload = asyncio.run(exercise())
    assert payload["messages"][0]["work_focus_state"] == state
    # One batched lookup for the owner's focused answers; none otherwise.
    assert repository.state_calls == calls


# --- wire contract pins -------------------------------------------------------------


def test_public_focus_is_the_wire_identity() -> None:
    assert public_focus(SNAPSHOT) == PUBLIC
    assert public_focus(None) is None
    assert set(wire.WorkFocusIdentity.model_fields) == set(PUBLIC) | {
        "request_id",
        "request_version",
        "component",
        "evidence",
    }
    assert wire.WorkFocusIdentity.model_validate(PUBLIC).model_dump(exclude_none=True) == PUBLIC
    with pytest.raises(ValidationError):
        wire.WorkFocusIdentity.model_validate(SNAPSHOT)


def test_state_vocabulary_is_the_served_one() -> None:
    from aichat.services.work_focus import WORK_FOCUS_STATES

    assert wire.WORK_FOCUS_STATES == WORK_FOCUS_STATES


def test_acknowledge_request_mirror_matches_the_route_model() -> None:
    from ai.core.app import WorkFocusAcknowledgeRequest as route_model

    assert set(wire.WorkFocusAcknowledgeRequest.model_fields) == set(route_model.model_fields)


def test_error_code_and_capability_are_served() -> None:
    import ai.core.agui.routes as agui_routes
    import ai.core.app as app_module

    source = inspect.getsource(app_module)
    for code in wire.WORK_FOCUS_ERROR_CODES:
        assert f'"{code}"' in source
        assert f'"{code}"' in inspect.getsource(agui_routes)
    assert '"work_focus": True' in source


# --- wf8 model input ------------------------------------------------------------------


def test_lookup_rail_appends_an_ids_only_focus_note() -> None:
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    messages = T1LookupWorkflow._with_work_focus("What is left?", {"work_focus": PUBLIC})
    texts = [content.text for message in messages for content in message.contents]
    assert texts[0] == "What is left?"
    note = texts[-1]
    assert note.startswith("[work focus] This question is about work order 73 on machine 12.")
    assert "work_order_id=73" in note
    assert "b" * 64 not in note


@pytest.mark.parametrize(
    "context",
    [
        None,
        {},
        {"work_focus": None},
        {"work_focus": {**PUBLIC, "kind": "machine"}},
        {"work_focus": {**PUBLIC, "work_order_id": "73"}},
        {"work_focus": {**PUBLIC, "work_order_id": True}},
        {"work_focus": {**PUBLIC, "work_order_id": 0}},
        {"untrusted_client_context": {"work_focus": PUBLIC}},
    ],
)
def test_lookup_rail_ignores_absent_or_malformed_focus(context) -> None:
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    assert T1LookupWorkflow._with_work_focus("What is left?", context) == "What is left?"


def test_lookup_rail_omits_an_unknown_machine() -> None:
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    messages = T1LookupWorkflow._with_work_focus(
        "What is left?", {"work_focus": {**PUBLIC, "machine_id": None}}
    )
    note = messages[-1].contents[-1].text
    assert "This question is about work order 73. " in note


@pytest.mark.parametrize("field", ["request_id", "request_version"])
@pytest.mark.parametrize("value", [True, "401", 0, -1, 1.5])
def test_request_focus_ids_are_strict_positive_integers(field, value):
    """The request extension cannot coerce hints into canonical identity."""
    body = {"work_order_id": 73, "expected_version": 4, "request_id": 401, "request_version": 3}
    body[field] = value
    with pytest.raises(ValidationError):
        WorkFocusAcknowledgeRequest.model_validate(body)


def test_request_focus_wire_keeps_ids_and_hides_the_signed_boundary():
    """All result wires can retain the same request identity as the receipt."""
    snapshot = {**SNAPSHOT, "schema_version": 2, "request_id": 401, "request_version": 3}
    public = public_focus(snapshot)
    assert public["request_id"] == 401
    assert public["request_version"] == 3
    assert "boundary_hash" not in public
    assert wire.WorkFocusIdentity.model_validate(public).model_dump(exclude_unset=True) == public


def test_request_uploads_stay_in_existing_assistant_and_labels_have_no_path():
    """A file can be proposed without handing a local path or invented facts to the model."""
    from ai.core.turn.execution import _effective_pinned_workflow
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    focus = {**PUBLIC, "schema_version": 2, "request_id": 401, "request_version": 3}
    run = SimpleNamespace(
        server_pinned_workflow=None,
        modality="text",
        work_focus=focus,
        metadata={
            "uploaded_files": [
                {
                    "file_id": "thread/report.pdf",
                    "filename": "report.pdf",
                    "path": "/private/server/path",
                    "size": 123,
                    "extension": ".pdf",
                }
            ]
        },
    )
    assert _effective_pinned_workflow(run) == "wf8"
    messages = T1LookupWorkflow._with_work_focus(
        "Record this", {"work_focus": focus, **run.metadata}
    )
    note = messages[-1].contents[-1].text
    assert "propose_investigation_artifact" in note
    assert "thread/report.pdf" in note
    assert "/private/server/path" not in note
    assert "File content has not been interpreted" in note
    run.work_focus = PUBLIC
    assert _effective_pinned_workflow(run) == "wf6"
    run.server_pinned_workflow = "wf7"
    assert _effective_pinned_workflow(run) == "wf7"


def test_investigation_tool_source_is_isolated_across_concurrent_turns():
    """Model calls inherit only the exact executing turn, with no leaked next-turn binding."""
    from ai.core.integrations.investigation_tools import _source, bind_investigation_source

    async def turn(thread, identifier):
        with bind_investigation_source(thread, identifier):
            await asyncio.sleep(0)
            return _source.get()

    async def exercise():
        return await asyncio.gather(turn("thread_1", "turn_1"), turn("thread_2", "turn_2"))

    assert asyncio.run(exercise()) == [("thread_1", "turn_1"), ("thread_2", "turn_2")]
    assert _source.get() is None


@pytest.mark.parametrize("reference", ["component", "evidence"])
def test_reference_uploads_reach_file_analysis_and_text_stays_on_lookup(reference):
    """Optional reference focus must not swallow an otherwise supported upload."""
    from ai.core.turn.execution import _effective_pinned_workflow

    run = SimpleNamespace(
        server_pinned_workflow=None,
        modality="text",
        work_focus={
            **PUBLIC,
            "schema_version": 3,
            reference: (
                {
                    "machine_id": 18,
                    "rule": "reference_positions.v1",
                    "position_id": "bearing",
                    "slot_code": None,
                    "revision": "a" * 64,
                }
                if reference == "component"
                else {"kind": "closeout_reading", "id": 51, "revision": "b" * 64}
            ),
        },
        metadata={},
    )
    assert _effective_pinned_workflow(run) == "wf8"
    run.metadata["uploaded_files"] = [{"file_id": "thread/report.pdf"}]
    assert _effective_pinned_workflow(run) == "wf6"
    run.work_focus.update(request_id=401, request_version=3)
    assert _effective_pinned_workflow(run) == "wf8"
    run.server_pinned_workflow = "wf7"
    assert _effective_pinned_workflow(run) == "wf7"
