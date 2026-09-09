"""P0-11b/c: approvals-side sandbox seams (island test; no database access).

- ``registry.replace_for_tests`` swaps an executor for the block and restores
  the previous one afterwards, even when the body raises.
- ``EmailExecutor`` refuses recipients outside ``AIMMS_EMAIL_RECIPIENT_ALLOWLIST``
  with ``blocked_by_policy`` and never reports success for them.
"""

from __future__ import annotations

# ruff: noqa: E402
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.integrations.email.policy import ENV_VAR
from approvals.executors import (
    ApprovalExecutor,
    DriftReport,
    EffectResult,
    EmailExecutor,
    registry,
)


class _RecordingApprovalExecutor(ApprovalExecutor):
    action_type = "email"

    def __init__(self) -> None:
        self.calls: list[tuple[dict, str]] = []

    def validate(self, payload: dict) -> list[str]:
        return []

    def check_preconditions(self, payload: dict, baseline_context: dict) -> DriftReport:
        return DriftReport(has_drift=False)

    def execute(self, payload: dict, idempotency_key: str) -> EffectResult:
        self.calls.append((payload, idempotency_key))
        return EffectResult(success=True, effect_ref="recorded")


def test_replace_for_tests_swaps_and_restores():
    previous = registry.get("email")
    fake = _RecordingApprovalExecutor()
    with registry.replace_for_tests(fake) as active:
        assert active is fake
        assert registry.get("email") is fake
        registry.get("email").execute({"to": "x@y.test"}, "key-1")
    assert fake.calls == [({"to": "x@y.test"}, "key-1")]
    assert registry.get("email") is previous


def test_replace_for_tests_restores_after_an_exception():
    previous = registry.get("email")
    with pytest.raises(RuntimeError), registry.replace_for_tests(_RecordingApprovalExecutor()):
        raise RuntimeError("boom")
    assert registry.get("email") is previous


def test_replace_for_tests_adds_and_removes_an_unregistered_type():
    class _Novel(_RecordingApprovalExecutor):
        action_type = "voice_test_only_action"

    assert not registry.has("voice_test_only_action")
    with registry.replace_for_tests(_Novel()):
        assert registry.has("voice_test_only_action")
    assert not registry.has("voice_test_only_action")


def test_email_executor_blocks_recipients_outside_the_allowlist(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "@sandbox.test")
    result = EmailExecutor().execute(
        {"to": ["ops@sandbox.test", "boss@example.com"], "subject": "hi"}, "k1"
    )
    assert result.success is False
    assert result.result_payload == {
        "blocked_by_policy": True,
        "blocked_recipients": ["boss@example.com"],
    }
    assert result.effect_ref is None


def test_email_executor_with_empty_allowlist_blocks_everything(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "")
    result = EmailExecutor().execute({"to": "ops@sandbox.test", "subject": "hi"}, "k2")
    assert result.success is False
    assert result.result_payload["blocked_by_policy"] is True


def test_email_executor_unchanged_when_policy_inactive(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    result = EmailExecutor().execute({"to": "ops@sandbox.test", "subject": "hi"}, "k3")
    # Still the Phase 1 stub: reports a stub effect (fail-closed change is OD-2 / C6).
    assert result.success is True
    assert result.result_payload == {"stub": True}
