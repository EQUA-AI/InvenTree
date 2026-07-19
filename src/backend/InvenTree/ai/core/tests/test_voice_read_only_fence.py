"""Read-only fence tests: hands-free voice speech can never execute a write.

The fence is set by the normalized turn service for voice-modality turns
and enforced at the InvenTree client request funnel that every live write
tool ultimately calls (contract §0.2 / FR-VO-010).
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
# The client constructor loads InvenTree settings; the tests never reach a
# real API (the fence fires first / the host is invalid).
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest  # noqa: E402
from ai.core.integrations.inventree.client import (  # noqa: E402
    BusinessRuleError,
    InvenTreeClient,
)
from ai.core.tests.test_normalized_turn_service import (  # noqa: E402
    _context,
    _principal,
    _Repository,
    _TestTurnService,
    _Workflow,
)
from ai.core.tools.read_only import (  # noqa: E402
    read_only_tool_fence,
    read_only_tools_active,
)
from aichat.models import TurnModality  # noqa: E402


def test_fence_blocks_mutating_client_requests_before_any_network():
    client = InvenTreeClient(base_url="https://inventree.invalid", token="t")

    async def _post_under_fence():
        with read_only_tool_fence():
            await client._request("POST", "/stock/", json_data={"quantity": 1})

    with pytest.raises(BusinessRuleError) as excinfo:
        asyncio.run(_post_under_fence())
    assert "read-only" in str(excinfo.value)


def test_fence_permits_get_requests():
    client = InvenTreeClient(base_url="https://inventree.invalid", token="t")

    class _ReachedNetwork(Exception):
        """Sentinel: the request passed the fence into the transport."""

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _sentinel_transport():
        raise _ReachedNetwork()
        yield  # pragma: no cover - unreachable

    client._get_client = _sentinel_transport

    async def _get_under_fence():
        with read_only_tool_fence():
            await client._request("GET", "/part/")

    with pytest.raises(_ReachedNetwork):
        asyncio.run(_get_under_fence())


def test_fence_resets_after_scope():
    with read_only_tool_fence():
        assert read_only_tools_active()
    assert not read_only_tools_active()


class _FenceObservingWorkflow(_Workflow):
    def __init__(self) -> None:
        super().__init__()
        self.fence_active: list[bool] = []

    async def run_stream(self, **kwargs):
        self.fence_active.append(read_only_tools_active())
        async for chunk in super().run_stream(**kwargs):
            yield chunk


def _run_turn(workflow, modality: str):
    service = _TestTurnService(
        workflow_factory=lambda: workflow,
        repository_factory=lambda actor, context: _Repository(),  # noqa: ARG005
    )
    return asyncio.run(
        service.process(
            actor=_principal(),
            thread_id="thread_fence",
            content="Consume ten of part FAS-0042.",
            modality=modality,
            trusted_context=_context(),
            modality_metadata={},
            idempotency_key=f"fence-{modality}",
            correlation_id="00000000-0000-0000-0000-000000000042",
        )
    )


def test_voice_turns_execute_workflows_under_the_fence():
    workflow = _FenceObservingWorkflow()
    result = _run_turn(workflow, TurnModality.VOICE)
    assert result.response_state == "complete"
    assert workflow.fence_active == [True]
    # The fence never leaks past the turn.
    assert not read_only_tools_active()


def test_text_turns_are_not_fenced():
    workflow = _FenceObservingWorkflow()
    result = _run_turn(workflow, TurnModality.TEXT)
    assert result.response_state == "complete"
    assert workflow.fence_active == [False]
