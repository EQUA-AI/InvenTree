"""Registry isolation and strict public-contract tests for diagnostic tools."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import ai.core.tools as tool_package
import pytest
from ai.core.auth import AIPrincipal
from ai.core.tools import diagnostics
from ai.core.tools.diagnostics import (
    BASE_DIAGNOSTIC_TOOL_NAMES,
    DIAGNOSTIC_TOOL_NAMES,
    LIVE_SAFETY_TOOL_NAME,
    MACHINE_READ_CAPABILITY,
    DiagnosticArgumentsError,
    DiagnosticAuthorizationError,
    DiagnosticRecordRoot,
    DiagnosticToolNotFoundError,
    ReadAuthorization,
    ReaderResult,
    build_diagnostic_context,
    get_diagnostic_tool_registry,
)
from ai.core.trusted_context import build_trusted_turn_context

NOW = datetime(2026, 7, 15, 4, 0, tzinfo=UTC)


def _principal() -> AIPrincipal:
    return AIPrincipal(
        subject="user:7",
        actor="user:7",
        user_pk="7",
        username="diagnostic-user",
        authentication_method="django_session",
        scope="boundary-policy-not-maintenance-scope",
        policy_version="policy-v1",
        is_staff=False,
        is_superuser=False,
    )


class _Reader:
    """Small valid reader used to reach registry validation boundaries."""

    def rehydrate_actor(self, _principal):
        return object()

    def authorize(self, *, context, capability, root, check_id, **_kwargs):
        return ReadAuthorization(
            check_id=check_id,
            actor_id=context.actor,
            capability=capability,
            entity_type=root.entity_type,
            entity_id=root.entity_id,
            current_revision=root.expected_revision,
            authorization_class=root.authorization_class,
            scoped=True,
            linked_machine_id=root.linked_machine_id,
            checked_at=NOW,
        )

    def read(self, **_kwargs):
        return ReaderResult(abstention_reason="No matching evidence.")


def _context():
    return build_diagnostic_context(
        _principal(),
        server_record_roots=(
            DiagnosticRecordRoot(
                entity_type="machine", entity_id=11, expected_revision="machine-r3"
            ),
        ),
        server_allowed_capabilities=(MACHINE_READ_CAPABILITY,),
        issued_at=NOW,
    )


def test_registry_snapshot_is_exact_and_separate() -> None:
    registry = get_diagnostic_tool_registry(reader=_Reader(), clock=lambda: NOW)

    assert BASE_DIAGNOSTIC_TOOL_NAMES == (
        "get_machine_context",
        "get_repair_packet",
        "get_recent_maintenance_history",
        "get_machine_health_summary",
        "get_machine_health_anomalies",
        "search_approved_manuals",
        "find_published_repair_playbooks",
        "get_parts_availability",
    )
    assert registry.snapshot() == BASE_DIAGNOSTIC_TOOL_NAMES
    assert DIAGNOSTIC_TOOL_NAMES == BASE_DIAGNOSTIC_TOOL_NAMES
    assert LIVE_SAFETY_TOOL_NAME not in registry.names
    assert "create_diagnostic_draft" not in registry.names
    assert "generic_http" not in registry.names
    assert all("write" not in name for name in registry.names)
    assert len(registry.definitions) == len(BASE_DIAGNOSTIC_TOOL_NAMES)
    assert not hasattr(tool_package, "INVENTREE_TOOLS")


def test_safety_tool_requires_explicit_registry_gate() -> None:
    disabled = get_diagnostic_tool_registry(reader=_Reader(), clock=lambda: NOW)
    enabled = get_diagnostic_tool_registry(
        reader=_Reader(), safety_p0_enabled=True, clock=lambda: NOW
    )

    assert LIVE_SAFETY_TOOL_NAME not in disabled.names
    assert enabled.names == (*BASE_DIAGNOSTIC_TOOL_NAMES, LIVE_SAFETY_TOOL_NAME)
    with pytest.raises(TypeError):
        get_diagnostic_tool_registry(reader=_Reader(), safety_p0_enabled=1)


def test_exact_name_and_strict_pydantic_arguments() -> None:
    registry = get_diagnostic_tool_registry(reader=_Reader(), clock=lambda: NOW)

    with pytest.raises(DiagnosticToolNotFoundError, match="unavailable"):
        registry.execute(
            "GET_MACHINE_CONTEXT",
            {"machine_id": 11, "expected_revision": "machine-r3"},
            context=_context(),
        )
    with pytest.raises(DiagnosticArgumentsError, match="arguments were invalid"):
        registry.execute(
            "get_machine_context",
            {"machine_id": "11", "expected_revision": "machine-r3"},
            context=_context(),
        )
    with pytest.raises(DiagnosticArgumentsError, match="arguments were invalid"):
        registry.execute(
            "get_machine_context",
            {
                "machine_id": 11,
                "expected_revision": "machine-r3",
                "url": "https://example.invalid",
            },
            context=_context(),
        )


def test_provider_definitions_are_strict_and_context_filtered() -> None:
    registry = get_diagnostic_tool_registry(reader=_Reader(), clock=lambda: NOW)

    tools = registry.provider_tools(context=_context())

    assert [tool["name"] for tool in tools] == ["get_machine_context"]
    assert tools[0]["type"] == "function"
    assert tools[0]["strict"] is True
    assert tools[0]["parameters"]["additionalProperties"] is False
    assert set(tools[0]["parameters"]["required"]) == {
        "machine_id",
        "expected_revision",
    }


def test_current_unscoped_context_unlocks_no_record_tool() -> None:
    unscoped = build_trusted_turn_context(
        _principal(),
        single_site_policy_key="boundary-policy-not-maintenance-scope",
        policy_version="policy-v1",
    )
    registry = get_diagnostic_tool_registry(reader=_Reader(), clock=lambda: NOW)

    with pytest.raises(DiagnosticAuthorizationError):
        registry.execute(
            "get_machine_context",
            {"machine_id": 11, "expected_revision": "machine-r3"},
            context=unscoped,
        )
    assert registry.provider_tools(context=unscoped) == []


def test_tool_module_has_no_data_access_or_transport_escape_hatch() -> None:
    source = Path(diagnostics.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}

    assert not any(
        module.startswith(("django", "rest_framework", "requests", "httpx")) for module in imported
    )
    assert "INVENTORY_TOOLS" not in source
    assert "INVENTREE_TOOLS" not in source
    assert ".objects" not in source
    assert "create_diagnostic_draft" not in source
    assert "generic_http" not in source
    assert "serializer" not in source.lower()
    assert "unrestricted_mcp" not in source.lower()
