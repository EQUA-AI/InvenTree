"""Staff tool exposure stays separate from general identity-minimized reads."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from ai.core.decisions.resolver import parse_work_order_intent
from ai.core.integrations.staff_tools import search_staff_candidates
from ai.core.tools import invocation_guard
from ai.core.tools.capabilities import (
    PolicyKind,
    capability_catalog,
    select_capabilities,
)
from ai.core.tools.rbac import _native_pairs, filter_tools, read_tools, tool_requirement


def test_staff_tool_is_catalogued_as_a_named_permission_read():
    entry = next(entry for entry in capability_catalog() if entry.tool is search_staff_candidates)
    assert entry.pack_id == "staff.read"
    assert entry.authorization.kind is PolicyKind.NATIVE_PERMISSION
    assert entry.authorization.all_of == (("staff_directory", "view"),)
    assert tool_requirement(search_staff_candidates) == ("staff_directory", "view")
    assert read_tools([search_staff_candidates]) == (search_staff_candidates,)


def test_named_directory_permission_does_not_depend_on_user_admin_rights():
    actor = SimpleNamespace(is_active=True, is_superuser=False)
    actor.has_perm = Mock(side_effect=lambda permission: permission == "users.view_staff_directory")
    profile = _native_pairs(actor)
    assert profile == frozenset({("staff_directory", "view")})
    assert filter_tools([search_staff_candidates], profile) == [search_staff_candidates]
    assert filter_tools([search_staff_candidates], frozenset({("work_order", "view")})) == []


def test_directory_selection_is_identical_on_text_and_voice(monkeypatch):
    from ai.core.tools import capabilities

    monkeypatch.setattr(capabilities, "category_lexicon", frozenset)
    monkeypatch.setattr(capabilities, "machine_lexicon", frozenset)
    selected = select_capabilities(
        "Find qualified staff for this repair",
        profile=frozenset({("staff_directory", "view")}),
        authenticated=True,
    )
    entry = next(entry for entry in capability_catalog() if entry.tool is search_staff_candidates)
    assert entry.modalities == frozenset({"text", "voice"})
    assert "search_staff_candidates" in selected.tool_ids
    assert search_staff_candidates in read_tools(selected.tools)


@pytest.mark.parametrize(
    "content",
    [
        "Automatically assign work order 12",
        "Auto assign work order 12",
        "Assign work order 12 automatically",
    ],
)
def test_explicit_voice_auto_assignment_remains_a_governed_intent(content):
    intent = parse_work_order_intent(content)
    assert intent is not None
    assert intent.action == "work_order.assign"
    assert intent.reference == "12"
    assert intent.parameters == {"auto_assign": True}


@pytest.mark.asyncio
async def test_revocation_denies_a_previously_selected_staff_tool(monkeypatch):
    from unittest.mock import AsyncMock

    from ai.core.auth import AIPrincipal, principal_context

    principal = AIPrincipal(
        subject="user:7",
        actor="user:7",
        user_pk="7",
        username="staff-reader",
        authentication_method="session",
        scope="chat",
        policy_version="test",
        is_staff=False,
        is_superuser=False,
    )
    monkeypatch.setattr(
        invocation_guard,
        "fresh_permission_profile",
        AsyncMock(return_value=frozenset({("work_order", "view")})),
    )
    token = principal_context.set(principal)
    try:
        with (
            invocation_guard.bind_capability_run(
                workflow="wf8",
                modality="voice",
                selected_tools=[search_staff_candidates],
            ),
            pytest.raises(invocation_guard.CapabilityAuthorizationError) as caught,
        ):
            await invocation_guard.authorize_invocation(
                "search_staff_candidates",
                {"work_order_id": 12},
            )
    finally:
        principal_context.reset(token)
    assert caught.value.reason_code == "required_permission_missing"
