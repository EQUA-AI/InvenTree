"""Per-call authorization tests for the capability invocation guard."""

# ruff: noqa: E402

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.auth import AIPrincipal, principal_context
from ai.core.tools import invocation_guard
from ai.core.tools.capabilities import capability_catalog
from ai.core.tools.invocation_guard import (
    NON_ENUMERATING_DENIAL,
    CapabilityAuthorizationError,
    CapabilityInvocationMiddleware,
    authorize_invocation,
    bind_capability_run,
    capability_run_context,
)


def _principal(user_pk: str = "7") -> AIPrincipal:
    return AIPrincipal(
        subject=f"user:{user_pk}",
        actor=f"user:{user_pk}",
        user_pk=user_pk,
        username=f"user-{user_pk}",
        authentication_method="session",
        scope="chat",
        policy_version="test",
        is_staff=False,
        is_superuser=False,
    )


def _tool(tool_id: str):
    return next(entry.tool for entry in capability_catalog() if entry.tool_id == tool_id)


def _profile(monkeypatch, *permissions):
    fresh_permission_profile = AsyncMock(return_value=frozenset(permissions))

    monkeypatch.setattr(
        invocation_guard,
        "fresh_permission_profile",
        fresh_permission_profile,
    )


@pytest.mark.asyncio
async def test_selected_native_tool_is_freshly_authorized(monkeypatch):
    _profile(monkeypatch, ("part", "view"))
    token = principal_context.set(_principal())
    try:
        with bind_capability_run(
            workflow="wf8",
            modality="text",
            selected_tools=[_tool("search_parts")],
        ):
            entry = await authorize_invocation("search_parts", {})
    finally:
        principal_context.reset(token)

    assert entry.tool is _tool("search_parts")
    assert capability_run_context.get() is None


@pytest.mark.asyncio
async def test_missing_run_context_fails_closed():
    token = principal_context.set(_principal())
    try:
        with pytest.raises(CapabilityAuthorizationError) as error:
            await authorize_invocation("search_parts", {})
    finally:
        principal_context.reset(token)

    assert str(error.value) == NON_ENUMERATING_DENIAL
    assert error.value.reason_code == "missing_run_context"


@pytest.mark.asyncio
async def test_missing_principal_fails_closed(monkeypatch):
    _profile(monkeypatch, ("part", "view"))

    with (
        bind_capability_run(
            workflow="wf8",
            modality="text",
            selected_tools=[_tool("search_parts")],
        ),
        pytest.raises(CapabilityAuthorizationError) as error,
    ):
        await authorize_invocation("search_parts", {})

    assert error.value.reason_code == "missing_principal"


@pytest.mark.asyncio
async def test_principal_change_between_selection_and_invocation_is_denied(monkeypatch):
    _profile(monkeypatch, ("part", "view"))
    first_token = principal_context.set(_principal("7"))
    try:
        with bind_capability_run(
            workflow="wf8",
            modality="text",
            selected_tools=[_tool("search_parts")],
        ):
            second_token = principal_context.set(_principal("8"))
            try:
                with pytest.raises(CapabilityAuthorizationError) as error:
                    await authorize_invocation("search_parts", {})
            finally:
                principal_context.reset(second_token)
    finally:
        principal_context.reset(first_token)

    assert error.value.reason_code == "principal_mismatch"


@pytest.mark.asyncio
async def test_unselected_tool_is_denied(monkeypatch):
    _profile(monkeypatch, ("part", "view"), ("stock", "view"))
    token = principal_context.set(_principal())
    try:
        with (
            bind_capability_run(
                workflow="wf8",
                modality="text",
                selected_tools=[_tool("search_parts")],
            ),
            pytest.raises(CapabilityAuthorizationError) as error,
        ):
            await authorize_invocation("get_stock_levels", {})
    finally:
        principal_context.reset(token)

    assert error.value.reason_code == "tool_not_selected"


@pytest.mark.asyncio
async def test_native_tool_requires_its_fresh_permission(monkeypatch):
    _profile(monkeypatch, ("part", "view"))
    token = principal_context.set(_principal())
    try:
        with (
            bind_capability_run(
                workflow="wf8",
                modality="text",
                selected_tools=[_tool("send_email")],
            ),
            pytest.raises(CapabilityAuthorizationError) as error,
        ):
            await authorize_invocation(
                "send_email",
                {"to": "recipient@example.com", "subject": "x", "body": "y"},
            )
    finally:
        principal_context.reset(token)

    assert error.value.reason_code == "required_permission_missing"

    _profile(monkeypatch, ("email", "send"))
    token = principal_context.set(_principal())
    try:
        with bind_capability_run(
            workflow="wf8",
            modality="text",
            selected_tools=[_tool("send_email")],
        ):
            entry = await authorize_invocation(
                "send_email",
                {"to": "recipient@example.com", "subject": "x", "body": "y"},
            )
    finally:
        principal_context.reset(token)

    assert entry.tool is _tool("send_email")


@pytest.mark.asyncio
async def test_permission_revocation_is_observed_at_invocation(monkeypatch):
    _profile(monkeypatch)
    token = principal_context.set(_principal())
    try:
        with (
            bind_capability_run(
                workflow="wf8",
                modality="text",
                selected_tools=[_tool("search_parts")],
            ),
            pytest.raises(CapabilityAuthorizationError) as error,
        ):
            await authorize_invocation("search_parts", {})
    finally:
        principal_context.reset(token)

    assert error.value.reason_code == "required_permission_missing"


@pytest.mark.asyncio
async def test_sql_guard_requires_view_permission_but_keeps_relation_check_internal(monkeypatch):
    _profile(monkeypatch, ("stock", "view"))
    token = principal_context.set(_principal())
    try:
        with bind_capability_run(
            workflow="wf8",
            modality="text",
            selected_tools=[_tool("query_database")],
        ):
            entry = await authorize_invocation(
                "query_database",
                {"sql": "SELECT * FROM stock_stockitem"},
            )
    finally:
        principal_context.reset(token)

    assert entry.authorization.authorizer == "database_relation_access"


@pytest.mark.asyncio
async def test_attachment_guard_rejects_invalid_parent_identifier(monkeypatch):
    _profile(monkeypatch, ("part", "view"))
    token = principal_context.set(_principal())
    try:
        with (
            bind_capability_run(
                workflow="wf8",
                modality="text",
                selected_tools=[_tool("get_part_attachments")],
            ),
            pytest.raises(CapabilityAuthorizationError) as error,
        ):
            await authorize_invocation("get_part_attachments", {"part_id": 0})
    finally:
        principal_context.reset(token)

    assert error.value.reason_code == "invalid_parent_resource"


@pytest.mark.asyncio
async def test_middleware_stops_dispatch_on_denial(monkeypatch):
    _profile(monkeypatch)
    middleware = CapabilityInvocationMiddleware()
    context = SimpleNamespace(
        function=SimpleNamespace(name="search_parts"),
        arguments={},
    )
    call_next = AsyncMock()

    token = principal_context.set(_principal())
    try:
        with (
            bind_capability_run(
                workflow="wf8",
                modality="text",
                selected_tools=[_tool("search_parts")],
            ),
            pytest.raises(CapabilityAuthorizationError),
        ):
            await middleware.process(context, call_next)
    finally:
        principal_context.reset(token)

    call_next.assert_not_awaited()


@pytest.mark.asyncio
async def test_middleware_dispatches_once_after_authorization(monkeypatch):
    _profile(monkeypatch, ("part", "view"))
    middleware = CapabilityInvocationMiddleware()
    context = SimpleNamespace(
        function=SimpleNamespace(name="search_parts"),
        arguments={},
    )
    call_next = AsyncMock()

    token = principal_context.set(_principal())
    try:
        with bind_capability_run(
            workflow="wf8",
            modality="text",
            selected_tools=[_tool("search_parts")],
        ):
            await middleware.process(context, call_next)
    finally:
        principal_context.reset(token)

    call_next.assert_awaited_once_with(context)


def test_fresh_profile_bypasses_request_role_cache(monkeypatch):
    from ai.core.tools import rbac
    from django.contrib import auth
    from users import permissions

    rule = SimpleNamespace(name="part", can_view=True)
    group = SimpleNamespace(prefetched_rule_sets=[rule])
    user = SimpleNamespace(is_active=True, is_superuser=False)

    class UserModel:
        class DoesNotExist(Exception):
            pass

        objects = SimpleNamespace(get=lambda **_kwargs: user)

    def cached_profile_must_not_run(_user):
        raise AssertionError("request-local permission cache was used")

    monkeypatch.setattr(auth, "get_user_model", lambda: UserModel)
    monkeypatch.setattr(permissions, "prefetch_rule_sets", lambda _user: [group])
    monkeypatch.setattr(rbac, "_all_pairs", lambda: frozenset({("part", "view")}))
    monkeypatch.setattr(rbac, "permission_profile", cached_profile_must_not_run)

    granted = invocation_guard._fresh_permission_profile_sync("7")
    rule.can_view = False
    revoked = invocation_guard._fresh_permission_profile_sync("7")

    assert granted == frozenset({("part", "view")})
    assert revoked == frozenset()
