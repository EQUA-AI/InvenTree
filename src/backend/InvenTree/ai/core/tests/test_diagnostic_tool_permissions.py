"""Per-call authorization tests for the diagnostic read facade."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from ai.core.auth import AIPrincipal
from ai.core.tools.diagnostics import (
    MACHINE_READ_CAPABILITY,
    NON_ENUMERATING_DENIAL,
    PACKET_READ_CAPABILITY,
    DiagnosticAuthorizationError,
    DiagnosticRecordRoot,
    ReadAuthorization,
    ReaderResult,
    build_diagnostic_context,
    get_diagnostic_tool_registry,
)

NOW = datetime(2026, 7, 15, 5, 0, tzinfo=UTC)


def _principal() -> AIPrincipal:
    return AIPrincipal(
        subject="user:7",
        actor="user:7",
        user_pk="7",
        username="reader",
        authentication_method="django_session",
        scope="boundary-policy",
        policy_version="policy-v1",
        is_staff=False,
        is_superuser=False,
    )


def _machine_context(*, capabilities=(MACHINE_READ_CAPABILITY,), issued_at=NOW):
    return build_diagnostic_context(
        _principal(),
        server_record_roots=(DiagnosticRecordRoot("machine", 11, "machine-r3"),),
        server_allowed_capabilities=capabilities,
        issued_at=issued_at,
    )


def _packet_context():
    return build_diagnostic_context(
        _principal(),
        server_record_roots=(
            DiagnosticRecordRoot("repair_packet", 23, "packet-r8", linked_machine_id=11),
        ),
        server_allowed_capabilities=(PACKET_READ_CAPABILITY,),
        issued_at=NOW,
    )


class _PermissionReader:
    def __init__(self, *, override=None, actor=True):
        self.override = override or {}
        self.actor = object() if actor else None
        self.log = []
        self.check_ids = []

    def rehydrate_actor(self, _principal):
        self.log.append("rehydrate")
        return self.actor

    def authorize(self, *, context, capability, root, check_id, **_kwargs):
        self.log.append("authorize")
        self.check_ids.append(check_id)
        grant = ReadAuthorization(
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
        return replace(grant, **self.override)

    def read(self, **_kwargs):
        self.log.append("read")
        return ReaderResult(abstention_reason="No matching evidence.")


def _execute_machine(reader, *, context=None, arguments=None):
    registry = get_diagnostic_tool_registry(reader=reader, clock=lambda: NOW)
    return registry.execute(
        "get_machine_context",
        arguments or {"machine_id": 11, "expected_revision": "machine-r3"},
        context=context or _machine_context(),
    )


@pytest.mark.parametrize(
    "override",
    [
        {"actor_id": "user:99"},
        {"scoped": False},
        {"capability": PACKET_READ_CAPABILITY},
        {"entity_id": 12},
        {"entity_type": "repair_packet"},
        {"current_revision": "stale-r2"},
        {"authorization_class": "unexpected_acl"},
        {"check_id": "replayed-check"},
        {"checked_at": NOW - timedelta(minutes=1)},
    ],
)
def test_grant_owner_scope_capability_entity_revision_and_freshness_denials(
    override,
) -> None:
    reader = _PermissionReader(override=override)

    with pytest.raises(DiagnosticAuthorizationError) as error:
        _execute_machine(reader)

    assert str(error.value) == NON_ENUMERATING_DENIAL
    assert reader.log == ["rehydrate", "authorize"]


def test_linked_edge_is_rechecked_for_packet_reads() -> None:
    reader = _PermissionReader(override={"linked_machine_id": 88})
    registry = get_diagnostic_tool_registry(reader=reader, clock=lambda: NOW)

    with pytest.raises(DiagnosticAuthorizationError) as error:
        registry.execute(
            "get_repair_packet",
            {"repair_packet_id": 23, "expected_revision": "packet-r8"},
            context=_packet_context(),
        )

    assert str(error.value) == NON_ENUMERATING_DENIAL
    assert reader.log == ["rehydrate", "authorize"]


def test_context_owner_capability_entity_and_expected_revision_fail_before_acl() -> None:
    cases = [
        (
            replace(_machine_context(), actor="user:99"),
            {"machine_id": 11, "expected_revision": "machine-r3"},
        ),
        (
            _machine_context(capabilities=(PACKET_READ_CAPABILITY,)),
            {"machine_id": 11, "expected_revision": "machine-r3"},
        ),
        (
            _machine_context(),
            {"machine_id": 99, "expected_revision": "machine-r3"},
        ),
        (
            _machine_context(),
            {"machine_id": 11, "expected_revision": "machine-r2"},
        ),
    ]
    for context, arguments in cases:
        reader = _PermissionReader()
        with pytest.raises(DiagnosticAuthorizationError) as error:
            _execute_machine(reader, context=context, arguments=arguments)
        assert str(error.value) == NON_ENUMERATING_DENIAL
        assert reader.log == []


def test_actor_is_rehydrated_and_acl_precedes_content_on_every_call() -> None:
    reader = _PermissionReader()
    registry = get_diagnostic_tool_registry(reader=reader, clock=lambda: NOW)
    arguments = {"machine_id": 11, "expected_revision": "machine-r3"}

    registry.execute("get_machine_context", arguments, context=_machine_context())
    registry.execute("get_machine_context", arguments, context=_machine_context())

    assert reader.log == [
        "rehydrate",
        "authorize",
        "read",
        "rehydrate",
        "authorize",
        "read",
    ]
    assert len(set(reader.check_ids)) == 2


def test_missing_actor_and_stale_context_are_non_enumerating() -> None:
    missing_actor = _PermissionReader(actor=False)
    with pytest.raises(DiagnosticAuthorizationError) as actor_error:
        _execute_machine(missing_actor)
    assert str(actor_error.value) == NON_ENUMERATING_DENIAL
    assert missing_actor.log == ["rehydrate"]

    stale_reader = _PermissionReader()
    stale_context = _machine_context(issued_at=NOW - timedelta(seconds=61))
    with pytest.raises(DiagnosticAuthorizationError) as stale_error:
        _execute_machine(stale_reader, context=stale_context)
    assert str(stale_error.value) == NON_ENUMERATING_DENIAL
    assert stale_reader.log == []
