"""Hostname guard cases use fake resolvers; no socket or vendor imports."""

import os
import socket

import pytest
from ai.core import egress


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", socket.create_connection)
    for name in ("AIMMS_EGRESS_MODE", "AIMMS_EGRESS_ALLOW", "IDENTITY_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(egress, "_INSTALLED", None)


@pytest.mark.parametrize(
    "host",
    [
        "a.search.windows.net",
        "a.cognitiveservices.azure.com",
        "us-aiplatform.googleapis.com",
        "AIMMS-FOUNDRY.OPENAI.AZURE.COM.",
    ],
)
def test_canonical_allow_list(host):
    assert egress.policy_from_environment().permits(host)


@pytest.mark.parametrize(
    "host",
    [
        "search.windows.net",
        "a.b.search.windows.net",
        "evilsearch.windows.net",
        "a.search.windows.net.evil.test",
        "169.254.169.254",
        "https://a.search.windows.net",
        "private:secret@a.search.windows.net",
        2130706433,
    ],
)
def test_suffix_confusion_and_non_host_inputs_fail_closed(host):
    assert not egress.policy_from_environment().permits(host)


def test_enforced_guard_stops_resolution_without_disclosing_host(monkeypatch, caplog):
    calls = []
    monkeypatch.setenv("AIMMS_EGRESS_MODE", "enforce")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **_k: calls.append(a))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **_k: calls.append(a))
    egress.install_guard()
    egress.install_guard()
    with pytest.raises(egress.EgressDenied, match="not permitted") as error:
        socket.getaddrinfo("private-user-data.evil.test", 443)
    with pytest.raises(egress.EgressDenied):
        socket.create_connection(("private-user-data.evil.test", 443))
    assert not calls
    assert "private-user-data" not in caplog.text + str(error.value)
    socket.getaddrinfo("aimms-foundry.openai.azure.com", 443)
    assert len(calls) == 1


def test_audit_allows_unknown_host_and_off_does_not_patch(monkeypatch):
    def resolver(*_a, **_k):
        return "resolved"

    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    egress.install_guard()
    assert socket.getaddrinfo is resolver
    monkeypatch.setenv("AIMMS_EGRESS_MODE", "audit")
    egress.install_guard()
    assert socket.getaddrinfo("unknown.test", 443) == "resolved"


def test_identity_address_only_and_policy_changes_require_restart(monkeypatch):
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://127.0.0.1:1234/msi/token")
    assert egress.policy_from_environment().permits("127.0.0.1")
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://public.evil.test/token")
    with pytest.raises(ValueError):
        egress.policy_from_environment()
    monkeypatch.delenv("IDENTITY_ENDPOINT")
    monkeypatch.setenv("AIMMS_EGRESS_MODE", "enforce")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: None)
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: None)
    egress.install_guard()
    monkeypatch.setenv("AIMMS_EGRESS_MODE", "audit")
    with pytest.raises(ValueError, match="restart"):
        egress.install_guard()


@pytest.mark.parametrize(
    "pattern", ["*", "*.com", "foo.*.com", "https://allowed.test", "foo.test/path"]
)
def test_invalid_allow_configuration_is_rejected(monkeypatch, pattern):
    monkeypatch.setenv("AIMMS_EGRESS_ALLOW", pattern)
    with pytest.raises(ValueError):
        egress.policy_from_environment()


def test_optional_engine_telemetry_is_disabled_before_import(monkeypatch):
    monkeypatch.setenv("MEM0_TELEMETRY", "true")
    monkeypatch.setenv("MEM0_DIR", "/tmp/fixture-memory")
    egress.disable_optional_memory_telemetry()
    assert os.environ["MEM0_TELEMETRY"] == "false"
    assert os.environ["MEM0_DIR"] == "/tmp/fixture-memory"
