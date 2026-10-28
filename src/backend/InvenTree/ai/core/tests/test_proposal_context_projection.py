"""Planning sees current source evidence without inheriting a prior proposal body."""

import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from ai.core.tools.repair_scope_draft import (
    REPAIR_SCOPE_DRAFT_TOOLS,
    _planning_draft_metadata,
    get_focused_repair_scope_context,
    get_focused_repair_scope_draft,
)
from ai.core.tools.repair_scope_schema import scope_planning_requirements

OLD_BODY = "PRIOR_BAD_FILTER_METHOD_AND_INCOMPLETE_ISOLATION_SENTINEL"


@pytest.fixture
def saved():
    return {
        "id": 350,
        "revision": 2,
        "actor_id": 7,
        "saved_at": "2026-07-15T04:00:00+00:00",
        "status": "proposed",
        "lifecycle_version": 9,
        "work_order_id": 84,
        "machine_id": 44,
        "repair_packet_id": 27,
        "source_event_id": 12,
        "component_code": "MOTOR:1",
        "scope": {"steps": [OLD_BODY], "isolation_checks": [OLD_BODY]},
        "unrecognized_body": OLD_BODY,
        "sources": {
            "e03": {"id": 19, "sha256": "a" * 64, "content": OLD_BODY},
            "manuals": [{"id": 23, "sha256": "b" * 64, "title": OLD_BODY}],
        },
        "gates": {"supervisor_review": "pending", "physical_execution": "not_started"},
    }


@pytest.fixture
def native(saved):
    return {
        "work_order_id": 84,
        "machine_id": 44,
        "repair_packet_id": 27,
        "lifecycle_version": 10,
        "source_event_id": 12,
        "component_code": "MOTOR:1",
        "draft": saved,
        "sources": {
            "e03": {
                "id": 29,
                "sha256": "c" * 64,
                "prepared": True,
                "content": "Current prepared evidence; not field inspection.",
            },
            "manuals": [
                {"id": 33, "sha256": "d" * 64, "applicability": "related_reference_unverified"}
            ],
        },
        "gates": {
            "supervisor_review": "pending",
            "site_safety_authorization": "pending",
            "physical_execution": "not_started",
            "post_repair_verification": "not_started",
        },
        "manual_applicability": "Unverified related reference.",
    }


def test_prior_body_and_unknown_proposal_text_are_omitted_but_authentic_identity_is_retained(saved):
    metadata = _planning_draft_metadata(saved)
    assert OLD_BODY not in json.dumps(metadata)
    assert "scope" not in metadata and "unrecognized_body" not in metadata
    for key in ("id", "revision", "actor_id", "saved_at", "status", "lifecycle_version"):
        assert metadata[key] == saved[key]
    assert metadata["scope_body_omitted"] is True
    assert metadata["reconciliation"] == {
        "work_order_id": 84,
        "machine_id": 44,
        "repair_packet_id": 27,
        "source_event_id": 12,
        "component_code": "MOTOR:1",
        "source_references": {
            "e03": {"id": 19, "sha256": "a" * 64},
            "manuals": [{"id": 23, "sha256": "b" * 64}],
        },
    }


def test_hash_authenticates_the_entire_actual_saved_snapshot_with_stable_ordering(saved):
    expected = hashlib.sha256(
        json.dumps(saved, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    assert _planning_draft_metadata(saved)["draft_sha256"] == expected
    reordered = dict(reversed(list(saved.items())))
    assert _planning_draft_metadata(reordered)["draft_sha256"] == expected
    changed = copy.deepcopy(saved)
    changed["scope"]["steps"].append("A different actual prior body.")
    assert _planning_draft_metadata(changed)["draft_sha256"] != expected


@pytest.mark.asyncio
async def test_real_decorated_context_tool_projects_only_prior_draft_and_preserves_current_native_evidence(
    native,
):
    before = copy.deepcopy(native)
    actor = object()
    with (
        patch(
            "ai.core.tools.repair_scope_draft._focused_actor",
            return_value=(actor, SimpleNamespace(work_order_id=84)),
        ),
        patch(
            "tasks.services.repair_scope_drafts.repair_scope_draft_context", return_value=native
        ) as read,
    ):
        result = await get_focused_repair_scope_context()
    read.assert_called_once_with(84, actor)
    assert result["success"] is True
    assert OLD_BODY not in json.dumps(result)
    assert result["draft"] == _planning_draft_metadata(before["draft"])
    assert result["sources"] == before["sources"]
    assert result["gates"] == before["gates"]
    assert result["lifecycle_version"] == 10
    assert result["planning_requirements"] == scope_planning_requirements()
    assert "not approved source evidence" in result["prior_proposal_notice"]
    assert "fresh complete" in result["prior_proposal_notice"]
    assert "explicit request" in result["prior_proposal_notice"]
    assert native == before


@pytest.mark.asyncio
async def test_null_prior_record_stays_null_and_current_requirements_still_arrive(native):
    native["draft"] = None
    with (
        patch(
            "ai.core.tools.repair_scope_draft._focused_actor",
            return_value=(object(), SimpleNamespace(work_order_id=84)),
        ),
        patch("tasks.services.repair_scope_drafts.repair_scope_draft_context", return_value=native),
    ):
        result = await get_focused_repair_scope_context()
    assert result["draft"] is None
    assert result["sources"] == native["sources"]
    assert result["planning_requirements"] == scope_planning_requirements()


@pytest.mark.asyncio
async def test_standalone_saved_draft_reader_retains_the_entire_actual_prior_body(native):
    actor = object()
    full = {
        key: native[key]
        for key in ("work_order_id", "machine_id", "repair_packet_id", "lifecycle_version", "draft")
    }
    before = copy.deepcopy(full)
    with (
        patch(
            "ai.core.tools.repair_scope_draft._focused_actor",
            return_value=(actor, SimpleNamespace(work_order_id=84)),
        ),
        patch(
            "tasks.services.repair_scope_drafts.read_repair_scope_draft", return_value=full
        ) as read,
    ):
        result = await get_focused_repair_scope_draft()
    read.assert_called_once_with(84, actor)
    assert result == {"success": True, **before}
    assert result["draft"]["scope"]["steps"] == [OLD_BODY]
    assert "draft_sha256" not in result["draft"]
    assert full == before


@pytest.mark.asyncio
async def test_failed_native_authority_exposes_neither_prior_body_nor_projected_identity(native):
    from django.core.exceptions import PermissionDenied

    with (
        patch(
            "ai.core.tools.repair_scope_draft._focused_actor",
            side_effect=PermissionDenied("No native actor"),
        ),
        patch(
            "tasks.services.repair_scope_drafts.repair_scope_draft_context", return_value=native
        ) as read,
    ):
        result = await get_focused_repair_scope_context()
    assert result["success"] is False
    assert "draft" not in result and "sources" not in result
    assert OLD_BODY not in json.dumps(result)
    read.assert_not_called()


@pytest.mark.asyncio
async def test_scope_only_resolver_instruction_requires_fresh_sources_without_another_provider_round():
    from ai.core.auth import principal_context
    from ai.core.tests.test_repair_scope_schema import _arguments
    from ai.core.tests.test_voice_tool_actions import _principal
    from ai.core.voice.tool_actions import VoiceToolActionResolver

    seen = []

    class Agent:
        async def run(self, content, *, tools):
            seen.append(content)
            action = next(
                tool for tool in tools if tool.__name__ == "save_proposed_repair_scope_draft"
            )
            await action(**_arguments())
            return object()

    async def authorized():  # noqa: RUF029 - Match the awaited tool-loader protocol.
        return REPAIR_SCOPE_DRAFT_TOOLS

    resolver = VoiceToolActionResolver(agent=Agent(), authorized_tool_loader=authorized)
    actor = _principal()
    token = principal_context.set(actor)
    try:
        with patch(
            "ai.core.voice.tool_actions.get_settings",
            return_value=SimpleNamespace(feature_voice_action_policy_enforce=True),
        ):
            result = await resolver.resolve(
                "Prepare the repair scope and save it as a draft.",
                actor=actor,
                trusted_context=object(),
            )
    finally:
        principal_context.reset(token)
    assert result is not None and len(seen) == 1
    assert "Prior proposals are reconciliation records, not approved source evidence." in seen[0]
    assert (
        "Generate a fresh complete scope from current sources and planning requirements." in seen[0]
    )
    assert "only for an explicit saved-draft review request" in seen[0]
