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


def _site_key(monkeypatch, value: str):
    """Pin the deployment's single-site policy key as the guard reads it."""
    from ai.core import config

    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: SimpleNamespace(single_site_policy_key=value),
    )


@pytest.mark.asyncio
async def test_corpus_guard_rejects_a_blank_query(monkeypatch):
    _profile(monkeypatch, ("work_order", "view"))
    _site_key(monkeypatch, "site-a")
    token = principal_context.set(_principal())
    try:
        with (
            bind_capability_run(
                workflow="wf8",
                modality="text",
                selected_tools=[_tool("search_manuals")],
            ),
            pytest.raises(CapabilityAuthorizationError) as error,
        ):
            await authorize_invocation("search_manuals", {"query": "   "})
    finally:
        principal_context.reset(token)

    assert error.value.reason_code == "invalid_document_query"


@pytest.mark.asyncio
async def test_corpus_guard_denies_an_unconfigured_site_scope(monkeypatch):
    """A blank policy key would mean an unscoped index query; refuse instead."""
    _profile(monkeypatch, ("work_order", "view"))
    _site_key(monkeypatch, "")
    token = principal_context.set(_principal())
    try:
        with (
            bind_capability_run(
                workflow="wf8",
                modality="text",
                selected_tools=[_tool("search_manuals")],
            ),
            pytest.raises(CapabilityAuthorizationError) as error,
        ):
            await authorize_invocation("search_manuals", {"query": "seal replacement"})
    finally:
        principal_context.reset(token)

    assert error.value.reason_code == "site_scope_unconfigured"


@pytest.mark.asyncio
async def test_corpus_guard_authorizes_a_scoped_query(monkeypatch):
    _profile(monkeypatch, ("work_order", "view"))
    _site_key(monkeypatch, "site-a")
    token = principal_context.set(_principal())
    try:
        with bind_capability_run(
            workflow="wf8",
            modality="text",
            selected_tools=[_tool("search_manuals")],
        ):
            entry = await authorize_invocation("search_manuals", {"query": "seal replacement"})
    finally:
        principal_context.reset(token)

    assert entry.authorization.authorizer == "controlled_corpus_access"


@pytest.mark.asyncio
async def test_corpus_guard_still_requires_the_work_order_role(monkeypatch):
    """The authorizer refines work_order:view; it must never replace it."""
    _profile(monkeypatch, ("part", "view"))
    _site_key(monkeypatch, "site-a")
    token = principal_context.set(_principal())
    try:
        with (
            bind_capability_run(
                workflow="wf8",
                modality="text",
                selected_tools=[_tool("search_manuals")],
            ),
            pytest.raises(CapabilityAuthorizationError) as error,
        ):
            await authorize_invocation("search_manuals", {"query": "seal replacement"})
    finally:
        principal_context.reset(token)

    assert error.value.reason_code == "required_permission_missing"


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


# ---------------------------------------------------------------------------
# attachment_corpus_access (R2): per-call flag re-check, query/site inputs,
# maintenance scope, and any_of role exposure.
# ---------------------------------------------------------------------------

from contextlib import contextmanager

from ai.core import config as _config


@contextmanager
def _attachment_catalog(monkeypatch, *, flag=True, site_key="site-a"):
    """Build the catalog under the given flag/site settings; always restore.

    The catalog caches the policy branch, so both directions clear it -- and
    the teardown clear keeps a lit catalog from leaking into other tests.
    """
    monkeypatch.setattr(
        _config,
        "get_settings",
        lambda: SimpleNamespace(
            single_site_policy_key=site_key,
            feature_attachment_rag_retrieval=flag,
        ),
    )
    capability_catalog.cache_clear()
    try:
        yield
    finally:
        capability_catalog.cache_clear()


def _scope_resolvable(monkeypatch, value: bool):
    monkeypatch.setattr(invocation_guard, "_has_maintenance_scope", AsyncMock(return_value=value))


async def _invoke_attachment(arguments):
    token = principal_context.set(_principal())
    try:
        with bind_capability_run(
            workflow="wf8",
            modality="text",
            selected_tools=[_tool("search_attachment_docs")],
        ):
            return await authorize_invocation("search_attachment_docs", arguments)
    finally:
        principal_context.reset(token)


@pytest.mark.asyncio
async def test_attachment_guard_denies_while_the_flag_is_dark(monkeypatch):
    """Default catalog: the policy itself is DISABLED, so dispatch never runs."""
    _profile(monkeypatch, ("part", "view"))
    with (
        _attachment_catalog(monkeypatch, flag=False),
        pytest.raises(CapabilityAuthorizationError) as error,
    ):
        await _invoke_attachment({"query": "seal replacement"})

    assert error.value.reason_code == "policy_disabled"


@pytest.mark.asyncio
async def test_attachment_guard_recheck_catches_a_mid_process_flag_flip(monkeypatch):
    """Catalog lit, settings flipped dark afterwards: the per-call arm denies."""
    _profile(monkeypatch, ("part", "view"))
    _scope_resolvable(monkeypatch, True)
    with _attachment_catalog(monkeypatch, flag=True):
        capability_catalog()  # build the lit catalog
        monkeypatch.setattr(
            _config,
            "get_settings",
            lambda: SimpleNamespace(
                single_site_policy_key="site-a",
                feature_attachment_rag_retrieval=False,
            ),
        )
        with pytest.raises(CapabilityAuthorizationError) as error:
            await _invoke_attachment({"query": "seal replacement"})

    assert error.value.reason_code == "attachment_retrieval_disabled"


@pytest.mark.asyncio
async def test_attachment_guard_rejects_a_blank_query(monkeypatch):
    _profile(monkeypatch, ("part", "view"))
    _scope_resolvable(monkeypatch, True)
    with _attachment_catalog(monkeypatch), pytest.raises(CapabilityAuthorizationError) as error:
        await _invoke_attachment({"query": "   "})

    assert error.value.reason_code == "invalid_document_query"


@pytest.mark.asyncio
async def test_attachment_guard_denies_an_unconfigured_site_scope(monkeypatch):
    _profile(monkeypatch, ("part", "view"))
    _scope_resolvable(monkeypatch, True)
    with (
        _attachment_catalog(monkeypatch, site_key=""),
        pytest.raises(CapabilityAuthorizationError) as error,
    ):
        await _invoke_attachment({"query": "seal replacement"})

    assert error.value.reason_code == "site_scope_unconfigured"


@pytest.mark.asyncio
async def test_attachment_guard_denies_an_unresolvable_scope(monkeypatch):
    """client_codes derive from scope_for_actor, so no scope means no corpus."""
    _profile(monkeypatch, ("part", "view"))
    _scope_resolvable(monkeypatch, False)
    with _attachment_catalog(monkeypatch), pytest.raises(CapabilityAuthorizationError) as error:
        await _invoke_attachment({"query": "seal replacement"})

    assert error.value.reason_code == "maintenance_scope_unresolved"


@pytest.mark.asyncio
@pytest.mark.parametrize("granted", [("part", "view"), ("work_order", "view")])
async def test_attachment_guard_authorizes_either_single_role(monkeypatch, granted):
    """any_of exposure: either arm's role alone admits the call."""
    _profile(monkeypatch, granted)
    _scope_resolvable(monkeypatch, True)
    with _attachment_catalog(monkeypatch):
        entry = await _invoke_attachment({"query": "seal replacement"})

    assert entry.authorization.authorizer == "attachment_corpus_access"


@pytest.mark.asyncio
async def test_attachment_guard_requires_at_least_one_arm_role(monkeypatch):
    _profile(monkeypatch, ("stock", "view"))
    _scope_resolvable(monkeypatch, True)
    with _attachment_catalog(monkeypatch), pytest.raises(CapabilityAuthorizationError) as error:
        await _invoke_attachment({"query": "seal replacement"})

    assert error.value.reason_code == "alternative_permission_missing"
