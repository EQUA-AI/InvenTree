"""Golden regression tests for the existing typed AI chat transport.

These tests intentionally freeze the public REST and SSE contracts without
making the legacy caller-supplied identity or context authoritative.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import ai.core  # noqa: E402
from django.test import SimpleTestCase  # noqa: E402

# The legacy workflows package eagerly imports every provider workflow.  The
# golden transport suite needs only ``workflows.root`` and deliberately avoids
# loading provider SDK implementations.
_workflows_package = types.ModuleType("ai.core.workflows")
_workflows_package.__path__ = [str(Path(ai.core.__file__).resolve().parent / "workflows")]
sys.modules.setdefault("ai.core.workflows", _workflows_package)

from ai.core.agents.routing import RoutingDecision, WorkflowType  # noqa: E402
from ai.core.app import (  # noqa: E402
    ChatRequest,
    ChatResponse,
    _is_audio_upload,
    chat,
    chat_stream,
)
from ai.core.auth import AIPrincipal, principal_context  # noqa: E402
from ai.core.streaming import AGUIEvent, EventType  # noqa: E402
from ai.core.trusted_context import TrustedTurnContext  # noqa: E402
from ai.core.turn_service import NormalizedTurnService  # noqa: E402
from aichat.services import BeginTurnResult  # noqa: E402


class _GoldenWorkflow:
    """Small deterministic workflow which exercises the transport adapter."""

    def __init__(self) -> None:
        self.call: dict[str, object] | None = None

    async def run_stream(self, **kwargs):
        self.call = kwargs
        emitter = kwargs["emitter"]
        thread_id = str(kwargs["thread_id"])
        run_id = "golden-run"

        events = (
            (EventType.RUN_STARTED, {"message": "Starting run"}),
            (
                EventType.WORKFLOW_STARTED,
                {"workflow_id": "wf1", "workflow_name": "T6_DIAGNOSTICS"},
            ),
            (
                EventType.TEXT_MESSAGE_START,
                {"messageId": "golden-message", "role": "assistant"},
            ),
            (
                EventType.TEXT_MESSAGE_CONTENT,
                {"messageId": "golden-message", "delta": "Golden response"},
            ),
            (EventType.TEXT_MESSAGE_END, {"messageId": "golden-message"}),
            (EventType.RUN_FINISHED, {"result": None}),
        )

        for event_type, data in events:
            await emitter.emit(
                AGUIEvent(
                    event_type=event_type,
                    data=data,
                    thread_id=thread_id,
                    run_id=run_id,
                    agent_name="root_workflow",
                )
            )

        yield "Golden "
        yield "response"


class _GoldenTurn:
    pk = "turn-golden"
    is_terminal = False
    canonical_result = None


class _GoldenRepository:
    def __init__(self) -> None:
        self.thread = SimpleNamespace(pk="thread-golden")
        self.turn = _GoldenTurn()

    def get_or_create(self, thread_id=None, *, title=""):  # noqa: ARG002
        self.thread.pk = thread_id or self.thread.pk
        return self.thread, False

    def begin_turn(self, thread_id, **kwargs):  # noqa: ARG002
        return BeginTurnResult(self.turn, False)

    def terminal(self, turn_id, **kwargs):  # noqa: ARG002
        self.turn.is_terminal = True
        self.turn.canonical_result = kwargs["canonical_result"]
        return self.turn


def _golden_principal() -> AIPrincipal:
    return AIPrincipal(
        subject="user:1",
        actor="user:1",
        user_pk="1",
        username="golden",
        authentication_method="django_session",
        scope="site:golden",
        policy_version="1",
        is_staff=False,
        is_superuser=False,
    )


def _golden_context() -> TrustedTurnContext:
    return TrustedTurnContext(
        actor="user:1",
        server_policy_key="site:golden",
        server_policy_hash="0" * 64,
        thread_namespace="unscoped",
        server_route_hints=("/chat",),
        allowed_capabilities=("chat.unscoped.read",),
        correlation_id="00000000-0000-0000-0000-000000000001",
        policy_version="1",
        untrusted_content="{}",
    )


def _golden_service(workflow: _GoldenWorkflow) -> NormalizedTurnService:
    repository = _GoldenRepository()

    class GoldenService(NormalizedTurnService):
        @staticmethod
        async def _call_sync(function, *args, **kwargs):
            return function(*args, **kwargs)

    return GoldenService(
        workflow_factory=lambda: workflow,
        repository_factory=lambda actor, context: repository,  # noqa: ARG005
    )


def _parse_sse_events(payload: str) -> list[dict[str, object]]:
    """Return JSON data records from an SSE response."""
    records: list[dict[str, object]] = []
    for line in payload.splitlines():
        if line.startswith("data: "):
            records.append(json.loads(line.removeprefix("data: ")))
    return records


class TypedChatGoldenTests(SimpleTestCase):
    """Freeze the typed chat behavior which WS1 must preserve."""

    def test_upload_guard_rejects_audio_even_with_document_name(self) -> None:
        self.assertTrue(_is_audio_upload(b"RIFF\x00\x00\x00\x00WAVEfmt ", "application/pdf"))
        self.assertTrue(_is_audio_upload(b"ID3\x04\x00\x00audio", "application/pdf"))
        self.assertTrue(_is_audio_upload(b"%PDF-1.7", "audio/wav"))

    def test_upload_guard_allows_normal_document_header(self) -> None:
        self.assertFalse(_is_audio_upload(b"%PDF-1.7\n", "application/pdf"))

    def test_legacy_request_fields_remain_parseable(self) -> None:
        """Legacy identity/context fields remain syntactically compatible."""
        request = ChatRequest(
            message="Inspect the pump",
            thread_id="thread-golden",
            user_id="legacy-client-claim",
            context={"machine_id": 44, "workflow_hint": "wf7"},
            file_ids=["thread-golden/manual.pdf"],
        )

        self.assertEqual(request.message, "Inspect the pump")
        self.assertEqual(request.thread_id, "thread-golden")
        self.assertEqual(request.user_id, "legacy-client-claim")
        self.assertEqual(request.context["workflow_hint"], "wf7")
        self.assertEqual(request.file_ids, ["thread-golden/manual.pdf"])

    def test_rest_response_shape_upload_metadata_and_workflow(self) -> None:
        """REST keeps its response fields and passes bounded upload metadata."""

        async def exercise() -> tuple[ChatResponse, _GoldenWorkflow, str]:
            workflow = _GoldenWorkflow()
            with TemporaryDirectory() as directory:
                upload = Path(directory) / "manual.pdf"
                upload.write_bytes(b"golden-pdf")

                async def metadata(principal, request):  # noqa: RUF029
                    return {
                        "untrusted_client_context": dict(request.context or {}),
                        "uploaded_files": [
                            {
                                "file_id": "thread-golden/manual.pdf",
                                "path": str(upload),
                                "filename": "manual.pdf",
                                "extension": ".pdf",
                                "size": 10,
                            }
                        ],
                    }

                with (
                    patch("ai.core.app.get_turn_service", return_value=_golden_service(workflow)),
                    patch("ai.core.app.build_trusted_turn_context", return_value=_golden_context()),
                    patch("ai.core.app._turn_metadata", side_effect=metadata),
                ):
                    token = principal_context.set(_golden_principal())
                    try:
                        response = await chat(
                            ChatRequest(
                                message="Inspect the pump",
                                thread_id="thread-golden",
                                user_id="attacker-selected-owner",
                                context={
                                    "display_preference": "concise",
                                    "actor": "user:999",
                                    "allowed_capabilities": ["inventory.write"],
                                },
                                file_ids=["thread-golden/manual.pdf"],
                            )
                        )
                    finally:
                        principal_context.reset(token)
            return response, workflow, str(upload)

        response, workflow, upload_path = asyncio.run(exercise())

        self.assertEqual(
            response.model_dump(),
            {
                "thread_id": "thread-golden",
                "message": "Golden response",
                "agent": "root_workflow",
                "workflow_used": "wf1",
            },
        )
        self.assertIsNotNone(workflow.call)
        context = workflow.call["context"]
        self.assertEqual(workflow.call["user_id"], "1")
        self.assertEqual(context["actor"], "user:1")
        self.assertEqual(context["allowed_capabilities"], ["chat.unscoped.read"])
        self.assertNotIn("display_preference", context)
        self.assertEqual(
            context["untrusted_client_context"],
            {
                "display_preference": "concise",
                "actor": "user:999",
                "allowed_capabilities": ["inventory.write"],
            },
        )
        self.assertEqual(
            context["uploaded_files"],
            [
                {
                    "file_id": "thread-golden/manual.pdf",
                    "path": upload_path,
                    "filename": "manual.pdf",
                    "extension": ".pdf",
                    "size": 10,
                }
            ],
        )

    def test_sse_headers_and_event_order(self) -> None:
        """Streaming preserves the current AG-UI ordering and media contract."""

        async def exercise() -> tuple[object, str]:
            workflow = _GoldenWorkflow()
            with (
                patch("ai.core.app.get_turn_service", return_value=_golden_service(workflow)),
                patch("ai.core.app.build_trusted_turn_context", return_value=_golden_context()),
            ):
                token = principal_context.set(_golden_principal())
                try:
                    response = await chat_stream(
                        ChatRequest(
                            message="Inspect the pump",
                            thread_id="thread-golden",
                        )
                    )
                    chunks = [chunk async for chunk in response.body_iterator]
                finally:
                    principal_context.reset(token)
            return response, "".join(chunks)

        response, payload = asyncio.run(exercise())
        events = _parse_sse_events(payload)

        self.assertEqual(response.media_type, "text/event-stream")
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertEqual(
            [event["type"] for event in events],
            [
                "RUN_STARTED",
                "WORKFLOW_STARTED",
                "TEXT_MESSAGE_START",
                "TEXT_MESSAGE_CONTENT",
                "TEXT_MESSAGE_END",
                "RUN_FINISHED",
            ],
        )
        self.assertTrue(all(event["threadId"] == "thread-golden" for event in events))
        self.assertEqual(events[3]["delta"], "Golden response")

    def test_stream_disconnect_cancels_background_work(self) -> None:
        """Closing the response iterator cancels in-flight workflow work."""

        class BlockingWorkflow:
            def __init__(self) -> None:
                self.cancelled = asyncio.Event()

            async def run_stream(self, **kwargs):
                await kwargs["emitter"].emit(
                    AGUIEvent(
                        event_type=EventType.RUN_STARTED,
                        thread_id=str(kwargs["thread_id"]),
                    )
                )
                try:
                    await asyncio.Event().wait()
                finally:
                    self.cancelled.set()
                yield "unreachable"

        async def exercise() -> bool:
            workflow = BlockingWorkflow()
            with (
                patch("ai.core.app.get_turn_service", return_value=_golden_service(workflow)),
                patch("ai.core.app.build_trusted_turn_context", return_value=_golden_context()),
            ):
                token = principal_context.set(_golden_principal())
                try:
                    response = await chat_stream(
                        ChatRequest(message="Wait", thread_id="thread-cancel")
                    )
                    iterator = response.body_iterator
                    await iterator.__anext__()
                    await iterator.aclose()
                    await asyncio.wait_for(workflow.cancelled.wait(), timeout=1)
                finally:
                    principal_context.reset(token)
            return workflow.cancelled.is_set()

        self.assertTrue(asyncio.run(exercise()))

    def test_diagnostic_routing_name_mismatch_is_frozen(self) -> None:
        """WS1 must not accidentally change the current wf1/wf7 routing map."""
        decision = RoutingDecision(
            workflow_type=WorkflowType.T6_DIAGNOSTICS,
            confidence=1.0,
            reasoning="golden",
        )

        self.assertEqual(WorkflowType.T6_DIAGNOSTICS.value, "wf1_diagnostics")
        self.assertEqual(decision.get_workflow_id(), "wf1")
