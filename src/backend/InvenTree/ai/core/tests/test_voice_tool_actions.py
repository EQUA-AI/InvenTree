"""Voice parity for text-chat tools with proposal-time and execution-time RBAC."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest  # noqa: E402
from ai.core.agents.voice_routing import VoiceComplexityRouter  # noqa: E402
from ai.core.auth import AIPrincipal, principal_context  # noqa: E402
from ai.core.integrations.email.tools import (  # noqa: E402
    generate_and_send_document,
    list_emails,
    send_email,
)
from ai.core.integrations.inventory_tools import create_part  # noqa: E402
from ai.core.integrations.kanban_tools import create_kanban_card  # noqa: E402
from ai.core.tools.capabilities import tool_name  # noqa: E402
from ai.core.tools.inventree.write.purchase_orders import (  # noqa: E402
    issue_purchase_order,
)
from ai.core.voice.tool_actions import (  # noqa: E402
    TextToolRBACVoicePermission,
    TextToolVoiceExecutor,
    VoiceToolActionResolver,
    _action_candidates,
    capability_for_tool,
    text_chat_action_tools,
)
from ai.core.voice.write_gate import ExecutableWrite  # noqa: E402


def _principal() -> AIPrincipal:
    return AIPrincipal(
        subject="user:7",
        actor="user:7",
        user_pk="7",
        username="voice-user",
        authentication_method="session",
        scope="chat",
        policy_version="test",
        is_staff=False,
        is_superuser=False,
    )


def test_action_catalog_covers_inventory_procurement_email_and_kanban():
    actions = set(text_chat_action_tools())

    assert create_part in actions
    assert issue_purchase_order in actions
    assert send_email in actions
    assert generate_and_send_document in actions
    assert create_kanban_card in actions
    assert list_emails not in actions
    assert len({tool.__name__ for tool in actions}) == len(actions)
    assert all(capability_for_tool(tool) for tool in actions)


def test_router_recognizes_every_text_chat_action():
    router = VoiceComplexityRouter()
    missed = [
        tool_name(tool)
        for tool in text_chat_action_tools()
        if not router._is_effect_intent(tool_name(tool).replace("_", " "))
    ]

    assert missed == []


def test_action_candidates_narrow_high_confidence_domains_and_preserve_fallback():
    actions = text_chat_action_tools()

    rfq = _action_candidates("Generate and send an RFQ", actions)
    ambiguous = _action_candidates("Do the requested change", actions)

    assert {tool_name(tool) for tool in rfq} == {
        "mark_email_processed",
        "send_email",
        "generate_and_send_document",
    }
    assert ambiguous == actions


class _CaptureAgent:
    def __init__(self) -> None:
        self.tool_names: list[str] = []

    async def run(self, content, *, tools):
        self.tool_names = [tool.__name__ for tool in tools]
        proposal = next(tool for tool in tools if tool.__name__ == "send_email")
        await proposal(
            to="buyer@example.com",
            subject="RFQ for bearings",
            body="Sensitive commercial detail",
        )
        return object()


@pytest.mark.asyncio
async def test_resolver_captures_authorized_action_without_executing_it():
    agent = _CaptureAgent()

    async def authorized_tools():  # noqa: RUF029
        return [list_emails, send_email]

    resolver = VoiceToolActionResolver(
        agent=agent,
        authorized_tool_loader=authorized_tools,
    )
    actor = _principal()
    token = principal_context.set(actor)
    try:
        with patch(
            "ai.core.integrations.email.tools.get_gmail_client",
            side_effect=AssertionError("planning must not send email"),
        ):
            resolved = await resolver.resolve(
                "Send the bearing RFQ to buyer@example.com",
                actor=actor,
                trusted_context=object(),
            )
    finally:
        principal_context.reset(token)

    assert resolved is not None
    assert resolved.executable.tool_name == "send_email"
    assert resolved.executable.arguments["to"] == "buyer@example.com"
    assert "Sensitive commercial detail" not in resolved.action.summary
    assert "list_emails" not in agent.tool_names


class _ReadThenCaptureAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, content, *, tools):
        self.calls += 1
        names = [tool.__name__ for tool in tools]
        if self.calls == 1:
            assert names == ["send_email"]
            return object()
        assert "list_emails" in names
        proposal = next(tool for tool in tools if tool.__name__ == "send_email")
        await proposal(to="buyer@example.com", subject="RFQ", body="Details")
        return object()


@pytest.mark.asyncio
async def test_resolver_retries_with_authorized_reads_for_record_resolution():
    agent = _ReadThenCaptureAgent()

    async def authorized_tools():  # noqa: RUF029
        return [list_emails, send_email]

    resolver = VoiceToolActionResolver(
        agent=agent,
        authorized_tool_loader=authorized_tools,
    )
    actor = _principal()
    token = principal_context.set(actor)
    try:
        resolved = await resolver.resolve(
            "Send the RFQ from the latest supplier email",
            actor=actor,
            trusted_context=object(),
        )
    finally:
        principal_context.reset(token)

    assert resolved is not None
    assert resolved.executable.tool_name == "send_email"
    assert agent.calls == 2


class _DomainFallbackAgent:
    def __init__(self) -> None:
        self.tool_sets: list[set[str]] = []

    async def run(self, content, *, tools):
        names = {tool.__name__ for tool in tools}
        self.tool_sets.append(names)
        if "create_part" in names:
            proposal = next(tool for tool in tools if tool.__name__ == "create_part")
            await proposal(name="Fallback part", category_id=1)
        return object()


@pytest.mark.asyncio
async def test_domain_shortlist_falls_back_to_all_authorized_actions():
    agent = _DomainFallbackAgent()

    async def authorized_tools():  # noqa: RUF029
        return [send_email, create_part]

    resolver = VoiceToolActionResolver(
        agent=agent,
        authorized_tool_loader=authorized_tools,
    )
    actor = _principal()
    token = principal_context.set(actor)
    try:
        resolved = await resolver.resolve(
            "Email the requested part change",
            actor=actor,
            trusted_context=object(),
        )
    finally:
        principal_context.reset(token)

    assert resolved is not None
    assert resolved.executable.tool_name == "create_part"
    assert agent.tool_sets[0] == {"send_email"}
    assert {"send_email", "create_part"} in agent.tool_sets


@pytest.mark.asyncio
async def test_resolver_fails_closed_on_principal_mismatch():
    resolver = VoiceToolActionResolver(
        agent=_CaptureAgent(),
        authorized_tool_loader=AsyncMock(return_value=[send_email]),
    )
    actor = _principal()

    assert (
        await resolver.resolve(
            "Send an email",
            actor=actor,
            trusted_context=object(),
        )
        is None
    )


@pytest.mark.asyncio
async def test_permission_uses_fresh_text_chat_profile():
    permission = TextToolRBACVoicePermission()
    with patch(
        "ai.core.voice.tool_actions.permission_profile_for_user_pk",
        AsyncMock(return_value=frozenset({("purchase_order", "add")})),
    ):
        assert await permission.allows(_principal(), "purchase_order:add")
        assert not await permission.allows(_principal(), "purchase_order:delete")


@pytest.mark.asyncio
async def test_executor_rechecks_capability_and_runs_exact_arguments():
    calls: list[dict] = []

    async def fake_action(quantity: int) -> dict:  # noqa: RUF029
        calls.append({"quantity": quantity})
        return {"success": True}

    permission = AsyncMock()
    permission.allows.return_value = True
    executor = TextToolVoiceExecutor(tools=[fake_action], permission=permission)
    executable = ExecutableWrite(
        tool_name="fake_action",
        capability="stock:change",
        arguments={"quantity": 4},
    )

    with patch(
        "ai.core.voice.tool_actions.capability_for_tool",
        return_value="stock:change",
    ):
        result = await executor.execute(
            executable,
            actor=_principal(),
            trusted_context=object(),
        )

    assert result.ok is True
    assert calls == [{"quantity": 4}]
    permission.allows.assert_awaited_once_with(_principal(), "stock:change")


@pytest.mark.asyncio
async def test_executor_rejects_capability_mismatch_without_calling_tool():
    calls = 0

    async def fake_action() -> dict:  # noqa: RUF029
        nonlocal calls
        calls += 1
        return {"success": True}

    executor = TextToolVoiceExecutor(tools=[fake_action])

    with patch(
        "ai.core.voice.tool_actions.capability_for_tool",
        return_value="stock:change",
    ):
        result = await executor.execute(
            ExecutableWrite(
                tool_name="fake_action",
                capability="stock:delete",
                arguments={},
            ),
            actor=_principal(),
            trusted_context=object(),
        )

    assert result.ok is False
    assert result.detail == "capability_mismatch"
    assert calls == 0
