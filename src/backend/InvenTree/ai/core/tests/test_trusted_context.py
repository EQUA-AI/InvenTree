"""Trusted unscoped turn context isolation tests."""

from __future__ import annotations

import json

import pytest
from ai.core.auth import AIPrincipal
from ai.core.trusted_context import (
    TrustedContextConfigurationError,
    build_trusted_turn_context,
)


def _principal() -> AIPrincipal:
    return AIPrincipal(
        subject="user:7",
        actor="user:7",
        user_pk="7",
        username="context-user",
        authentication_method="django_session",
        scope="pilot-site",
        policy_version="policy-v1",
        is_staff=False,
        is_superuser=False,
    )


def test_trusted_body_ids_and_capabilities_cannot_enter_envelope() -> None:
    browser = {
        "actor": "user:999",
        "scope": "other-customer",
        "machine_id": 44,
        "repair_packet_id": 123,
        "record_id": 456,
        "current_route": "/repair/packets/123/",
        "allowed_capabilities": ["inventory.write", "repair.delete"],
        "tool": "generic_http",
    }
    context = build_trusted_turn_context(
        _principal(),
        single_site_policy_key="pilot-site",
        policy_version="policy-v1",
        server_route_hints=("/ai/chat",),
        browser_context=browser,
        correlation_id="1f0809d4-884c-421b-9257-d942a1dcfa55",
    )

    assert context.actor == "user:7"
    assert context.server_policy_key == "pilot-site"
    assert context.thread_namespace == "unscoped"
    assert context.server_route_hints == ("/ai/chat",)
    assert context.allowed_capabilities == ("chat.unscoped.read",)
    assert json.loads(context.untrusted_content) == browser
    assert not hasattr(context, "machine_id")
    assert not hasattr(context, "repair_packet_id")
    assert not hasattr(context, "tool")


def test_trusted_context_is_immutable_and_hashes_server_policy() -> None:
    context = build_trusted_turn_context(
        _principal(),
        single_site_policy_key="pilot-site",
        policy_version="policy-v1",
    )
    assert len(context.server_policy_hash) == 64
    with pytest.raises(AttributeError):
        context.actor = "user:9"


def test_unscoped_context_rejects_write_capabilities_and_policy_drift() -> None:
    with pytest.raises(TrustedContextConfigurationError):
        build_trusted_turn_context(
            _principal(),
            single_site_policy_key="pilot-site",
            policy_version="policy-v1",
            server_allowed_capabilities=("repair.write",),
        )

    with pytest.raises(TrustedContextConfigurationError):
        build_trusted_turn_context(
            _principal(),
            single_site_policy_key="changed-site",
            policy_version="policy-v1",
        )
