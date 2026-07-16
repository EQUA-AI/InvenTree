"""Safety-P0 registry gate and raw-status result tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from ai.core.auth import AIPrincipal
from ai.core.tools.diagnostics import (
    LIVE_SAFETY_TOOL_NAME,
    PACKET_READ_CAPABILITY,
    SAFETY_P0_CAPABILITY,
    DiagnosticAuthorizationError,
    DiagnosticRecordRoot,
    DiagnosticToolNotFoundError,
    EvidenceClaim,
    ReadAuthorization,
    ReaderResult,
    build_diagnostic_context,
    get_diagnostic_tool_registry,
)

NOW = datetime(2026, 7, 15, 7, 0, tzinfo=UTC)


def _principal() -> AIPrincipal:
    return AIPrincipal(
        subject="user:7",
        actor="user:7",
        user_pk="7",
        username="status-reader",
        authentication_method="django_session",
        scope="boundary-policy",
        policy_version="policy-v1",
        is_staff=False,
        is_superuser=False,
    )


def _context(*, capability=SAFETY_P0_CAPABILITY):
    return build_diagnostic_context(
        _principal(),
        server_record_roots=(
            DiagnosticRecordRoot("repair_packet", 23, "packet-r8", linked_machine_id=11),
        ),
        server_allowed_capabilities=(capability,),
        issued_at=NOW,
    )


def _raw_claim(**extra):
    value = {
        "packet_status": "executing",
        "gate_statuses": [{"id": 2, "status": "pending"}],
        "lockout_point_statuses": [{"id": 8, "status": "locked"}],
        "coverage": {"gate_count": 1, "lockout_point_count": 1},
        "caveat": "Raw recorded states only; verify field conditions before action.",
        **extra,
    }
    return json.dumps(value, sort_keys=True)


class _SafetyReader:
    def __init__(self, claim):
        self.claim = claim

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
        return ReaderResult(
            evidence=(
                EvidenceClaim(
                    source_type="repair_packet_command_status",
                    id="23",
                    revision="packet-r8",
                    locator="/repair/packets/23/command-status",
                    as_of=NOW,
                    authorization_class="maintenance_scope",
                    claim=self.claim,
                    untrusted=False,
                ),
            )
        )


def _arguments():
    return {"repair_packet_id": 23, "expected_revision": "packet-r8"}


def test_live_status_is_absent_until_safety_p0_registry_gate_is_true() -> None:
    reader = _SafetyReader(_raw_claim())
    disabled = get_diagnostic_tool_registry(reader=reader, clock=lambda: NOW)
    enabled = get_diagnostic_tool_registry(reader=reader, safety_p0_enabled=True, clock=lambda: NOW)

    assert LIVE_SAFETY_TOOL_NAME not in disabled.names
    assert LIVE_SAFETY_TOOL_NAME in enabled.names
    with pytest.raises(DiagnosticToolNotFoundError):
        disabled.execute(LIVE_SAFETY_TOOL_NAME, _arguments(), context=_context())


def test_exposed_live_status_still_requires_per_call_safety_capability() -> None:
    registry = get_diagnostic_tool_registry(
        reader=_SafetyReader(_raw_claim()),
        safety_p0_enabled=True,
        clock=lambda: NOW,
    )

    with pytest.raises(DiagnosticAuthorizationError):
        registry.execute(
            LIVE_SAFETY_TOOL_NAME,
            _arguments(),
            context=_context(capability=PACKET_READ_CAPABILITY),
        )


def test_live_status_returns_raw_coverage_caveat_revision_and_time() -> None:
    registry = get_diagnostic_tool_registry(
        reader=_SafetyReader(_raw_claim()),
        safety_p0_enabled=True,
        clock=lambda: NOW,
    )

    result = registry.execute(LIVE_SAFETY_TOOL_NAME, _arguments(), context=_context())

    assert result["status"] == "ok"
    citation = result["evidence"][0]
    status = json.loads(citation["claim"])
    assert status["coverage"] == {"gate_count": 1, "lockout_point_count": 1}
    assert status["caveat"]
    assert status["gate_statuses"][0]["status"] == "pending"
    assert citation["revision"] == "packet-r8"
    assert citation["as_of"] == NOW.isoformat()
    assert status.get("safe") is not True


def test_positive_safety_inference_is_rejected_not_emitted() -> None:
    registry = get_diagnostic_tool_registry(
        reader=_SafetyReader(_raw_claim(safe=True)),
        safety_p0_enabled=True,
        clock=lambda: NOW,
    )

    result = registry.execute(LIVE_SAFETY_TOOL_NAME, _arguments(), context=_context())

    assert result["status"] == "abstain"
    assert result["evidence"] == []
    assert '"safe":true' not in json.dumps(result, separators=(",", ":")).lower()


@pytest.mark.parametrize(
    "verdict",
    (
        {"cleared": True},
        {"safety_status": "safe to operate"},
        {"approved_for_operation": True},
    ),
)
def test_safety_verdict_synonyms_are_rejected(verdict) -> None:
    registry = get_diagnostic_tool_registry(
        reader=_SafetyReader(_raw_claim(**verdict)),
        safety_p0_enabled=True,
        clock=lambda: NOW,
    )

    result = registry.execute(LIVE_SAFETY_TOOL_NAME, _arguments(), context=_context())

    assert result["status"] == "abstain"
    assert result["evidence"] == []


def test_safety_result_never_truncates_into_malformed_raw_status() -> None:
    claim = json.loads(_raw_claim())
    claim["gate_statuses"] = [{"id": index, "status": "pending"} for index in range(100)]
    claim["coverage"]["gate_count"] = 100
    registry = get_diagnostic_tool_registry(
        reader=_SafetyReader(json.dumps(claim, sort_keys=True)),
        safety_p0_enabled=True,
        max_result_bytes=1024,
        clock=lambda: NOW,
    )

    result = registry.execute(LIVE_SAFETY_TOOL_NAME, _arguments(), context=_context())

    assert result["status"] == "abstain"
    assert result["evidence"] == []
    assert "bounded result" in result["abstention_reason"]


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("packet_status",), "safe"),
        (("packet_status",), "cleared"),
        (("gate_statuses", 0, "status"), "approved"),
        (("lockout_point_statuses", 0, "status"), "safe"),
        (("coverage", "gate_count"), 99),
    ),
)
def test_raw_status_enums_and_coverage_are_reconciled(path, value) -> None:
    claim = json.loads(_raw_claim())
    target = claim
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    registry = get_diagnostic_tool_registry(
        reader=_SafetyReader(json.dumps(claim, sort_keys=True)),
        safety_p0_enabled=True,
        clock=lambda: NOW,
    )

    result = registry.execute(LIVE_SAFETY_TOOL_NAME, _arguments(), context=_context())

    assert result["status"] == "abstain"
    assert result["evidence"] == []
