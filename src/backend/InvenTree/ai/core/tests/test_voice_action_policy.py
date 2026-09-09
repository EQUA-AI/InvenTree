"""A5: the per-action voice eligibility table is complete, honest and enforced behind a flag."""

# ruff: noqa: E402

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.voice import action_policy as policy_module
from ai.core.voice.action_policy import (
    PROPOSAL_POLICIES,
    ActionPolicy,
    action_policy,
    all_policies,
    render_markdown,
    tool_policies,
)
from ai.core.voice.action_severity import WriteSeverity, classified_tool_names
from ai.core.voice.confirmation import (
    BLOCKED_UNKNOWN_PHRASE,
    ProposedWriteAction,
    VoiceWriteAuditEventType,
    WriteActionClass,
    propose,
)
from ai.core.voice.tool_actions import _policy_block


def test_tool_policies_cover_every_classified_tool():
    assert set(tool_policies()) == set(classified_tool_names())


def test_proposal_policies_cover_every_proposal_action():
    from aichat.models import ProposalAction

    assert set(PROPOSAL_POLICIES) == set(ProposalAction.values)


def test_every_policy_row_is_complete():
    for policy in all_policies():
        assert isinstance(policy, ActionPolicy)
        assert policy.change_label
        if policy.action_class is WriteActionClass.IRREVERSIBLE:
            assert policy.confirm_phrase, policy.name
            assert policy.review == "full", policy.name
        else:
            assert policy.confirm_phrase == "", policy.name
        if not policy.voice_execution_allowed or not policy.voice_review_allowed:
            assert policy.ineligible_reason, policy.name
            assert policy.follow_up, policy.name
        assert len(policy.ineligible_reason) <= 80


def test_external_and_irreversible_tools_require_full_review():
    for name, policy in tool_policies().items():
        if policy.severity is not WriteSeverity.REVERSIBLE:
            assert policy.review == "full", name


def test_tool_rail_execution_is_off_until_governed_adapters_land():
    """Doc §5.2: no receipt / version guard / idempotency => unavailable by voice."""
    for name, policy in tool_policies().items():
        assert policy.voice_execution_allowed is False, name
        assert policy.follow_up in {"B6", "C7", "E5", "E6", "F-OTHER", "OD-15"}, name


def test_document_attachments_need_a_screen_review():
    policy = action_policy("generate_and_send_document")
    assert policy is not None
    assert policy.voice_review_allowed is False
    assert "screen" in policy.ineligible_reason


def test_proposal_rail_defaults():
    hold = action_policy("work_order.hold")
    assert hold is not None and hold.voice_execution_allowed and hold.review == "brief"
    delete = action_policy("work_order.delete")
    assert delete is not None and delete.confirm_phrase == "confirm delete"
    cancel = action_policy("work_order.cancel")
    assert cancel is not None and cancel.confirm_phrase == "confirm cancel"  # OD-3 fail-closed
    optimize = action_policy("schedule.optimize")
    assert optimize is not None and optimize.voice_execution_allowed is False


def test_unmapped_action_has_no_policy():
    assert action_policy("no_such_action") is None
    assert action_policy("") is None


def test_tracked_document_is_generated_from_the_table():
    path = (
        Path(__file__).resolve().parents[6]
        / "docs"
        / "docs"
        / "aimms"
        / "voice-action-eligibility.md"
    )
    assert path.read_text(encoding="utf-8") == render_markdown(), (
        "docs/docs/aimms/voice-action-eligibility.md drifted; regenerate it (see its header)"
    )


def test_blocked_reason_is_spoken_through_the_fixed_template():
    action = ProposedWriteAction(
        capability="stock:add",
        summary="Add stock with quantity 10",
        action_class=WriteActionClass.BLOCKED_UNKNOWN,
        blocked_reason=policy_module.REASON_NO_RECEIPT,
    )
    pending, spoken, event = propose(action, thread_id=1, nonce="n", has_permission=True)
    assert pending is None
    assert spoken == f"{BLOCKED_UNKNOWN_PHRASE} {policy_module.REASON_NO_RECEIPT}."
    assert event.event is VoiceWriteAuditEventType.BLOCKED


def test_blocked_without_reason_keeps_the_static_phrase():
    action = ProposedWriteAction(
        capability="stock:add", summary="x", action_class=WriteActionClass.BLOCKED_UNKNOWN
    )
    _, spoken, _ = propose(action, thread_id=1, nonce="n", has_permission=True)
    assert spoken == BLOCKED_UNKNOWN_PHRASE


@pytest.mark.parametrize("name", ("add_stock", "send_email", "no_such_tool"))
def test_enforcement_is_off_by_default(name):
    with patch(
        "ai.core.voice.tool_actions.get_settings",
        return_value=SimpleNamespace(feature_voice_action_policy_enforce=False),
    ):
        assert _policy_block(name) is None


def test_enforcement_refuses_with_the_row_reason_when_on():
    with patch(
        "ai.core.voice.tool_actions.get_settings",
        return_value=SimpleNamespace(feature_voice_action_policy_enforce=True),
    ):
        assert _policy_block("add_stock") == policy_module.REASON_NO_RECEIPT
        assert _policy_block("merge_stock") == policy_module.REASON_STOCK_NOT_YET
        assert _policy_block("no_such_tool") == policy_module.REASON_NOT_COMMITTED
        assert _policy_block("work_order.hold") is None
