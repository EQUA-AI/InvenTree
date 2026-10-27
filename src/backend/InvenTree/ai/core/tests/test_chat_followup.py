"""Live-retest regressions: real scoped records and structured model candidates."""

# ruff: noqa: E402
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
import django

django.setup()

import pytest
from ai.core import config
from ai.core.analysis import executor, plans
from ai.core.analysis.schemas import SynthesisClaimSet
from ai.core.auth import principal_context
from ai.core.integrations.inventree.client import InvenTreeClient
from ai.core.streaming import InMemoryEventEmitter
from ai.core.tests.focus_test_db import ensure_island_tables, grant_work_view
from ai.core.tests.test_capability_invocation_guard import _principal
from ai.core.tests.test_normalized_turn_service import _context
from ai.core.tools import capabilities
from ai.core.tools.inventree.read import parts
from ai.core.turn_service import NormalizedTurnService
from ai.core.workflows.wf8_lookup import T1LookupWorkflow
from aichat.models import ChatTurn
from aichat.services.threads import ThreadRepository
from aichat.tests.memory_scope_fixtures import fixture_client_resolver
from assets.ai_read import machine_identity
from assets.models import AssetMachine, Client, ClientScopeGrant
from django.contrib.auth import get_user_model
from django.test import override_settings
from tasks.models import WorkOrder

PART_SEARCH = "Search the parts catalog for bearing. List the matching part names and internal part numbers. Do not create or change any records."


def test_category_followup_ignores_old_topics_and_assistant_category_guesses(monkeypatch):
    monkeypatch.setattr(
        capabilities, "category_lexicon", lambda: frozenset({"pcba", "wire", "widget"})
    )
    history = [
        {"role": "user", "content": "Show Wire stock."},
        {"role": "assistant", "content": "Wire and Widget are empty."},
        {"role": "user", "content": "How many PCBA are in stock?"},
        {"role": "assistant", "content": "90 PCBA; Wire is unrelated."},
    ]
    assert capabilities.matched_category_terms("And where are those located?", history) == ("pcba",)
    assert capabilities.matched_category_terms("Where is Wire stock?", history) == ("wire",)
    assert capabilities.matched_category_terms("Hello", history) == ()
    history.append({"role": "user", "content": "What is the status of work order 115?"})
    assert capabilities.matched_category_terms("And where is it?", history) == ()
    hint = T1LookupWorkflow._with_category_hint(
        "And where are those located?",
        "And where are those located?",
        {"conversation_history": history[:-1]},
    )[-1].text
    assert "'pcba'" in hint and "'wire'" not in hint and "'widget'" not in hint


@pytest.mark.parametrize(
    "prompt",
    [
        PART_SEARCH,
        "Search the parts catalog for the literal keyword bearing across names and descriptions. Do not use a category filter. List all matching part names and internal part numbers. Do not create or change records.",
    ],
)
def test_explicit_keyword_search_overrides_category_lexicon_and_history(monkeypatch, prompt):
    monkeypatch.setattr(
        capabilities, "category_lexicon", lambda: frozenset({"bearing", "bearings"})
    )
    messages = T1LookupWorkflow._with_category_hint(
        prompt,
        prompt,
        {"conversation_history": [{"role": "user", "content": "Show the Bearings category."}]},
    )
    hint = messages[-1].text
    assert "inventory search scope" in hint
    assert '"bearing"' in hint
    assert "category_id=None" in hint
    assert "include_coverage=True" in hint
    assert "rather than free-text" not in hint


@pytest.mark.parametrize(
    "prompt",
    [
        "Search the parts catalog for bearing in the Bearings category.",
        "Search the parts catalog for bearing. Show only active parts.",
        "Search the parts catalog for bearing. Limit to the first 3 matches.",
        "Show parts in the Bearings category.",
        "Search the parts catalog for bearing. Only those with quantity over 10.",
        "Search the parts catalog for bearing for the selected machine.",
    ],
)
def test_explicit_filters_are_not_replaced_by_a_broad_search(prompt):
    assert T1LookupWorkflow._catalog_keyword(prompt) is None


def test_explicit_category_search_keeps_category_guidance(monkeypatch):
    monkeypatch.setattr(capabilities, "category_lexicon", lambda: frozenset({"bearings"}))
    prompt = "Show parts in the Bearings category."
    hint = T1LookupWorkflow._with_category_hint(prompt, prompt, {})[-1].text
    assert "resolve the category first" in hint
    assert "inventory search scope" not in hint


@pytest.mark.asyncio
async def test_native_keyword_page_preserves_count_offset_and_all_activity_states():
    client = object.__new__(InvenTreeClient)
    client._request = AsyncMock(
        return_value={"count": 105, "next": "/api/part/?offset=100", "results": [{"pk": 1}] * 100}
    )
    page = await client.search_parts_page(query="bearing", limit=100)
    assert page["population_count"] == 105
    assert page["has_more"] is True
    params = client._request.call_args.kwargs["params"]
    assert params["search"] == "bearing"
    assert "category" not in params and "active" not in params
    assert params["offset"] == 0
    await client.search_parts_page(query="bearing", category=7, active=False, offset=100)
    params = client._request.call_args.kwargs["params"]
    assert (params["category"], params["active"], params["offset"]) == (7, "false", 100)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "count,offset,has_more,complete",
    [(5, 0, False, True), (105, 0, True, False), (105, 100, False, False)],
)
async def test_part_tool_reports_page_coverage_honestly(
    monkeypatch, count, offset, has_more, complete
):
    rows = [
        {"pk": i, "name": f"Bearing {i}", "IPN": f"BRG-{i}", "active": i % 2 == 0} for i in range(5)
    ]
    provider = SimpleNamespace(
        search_parts_page=AsyncMock(
            return_value={"results": rows, "population_count": count, "has_more": has_more}
        )
    )
    monkeypatch.setattr(parts, "get_data_provider", lambda: provider)
    result = await parts.search_parts(
        query="bearing", is_active=None, include_coverage=True, offset=offset
    )
    assert result["results"] == rows
    assert result["population_count"] == count
    assert result["complete_population"] is complete
    assert result["display_truncated"] is not complete
    assert result["returned_count"] == 5
    assert provider.search_parts_page.call_args.kwargs["category"] is None


@pytest.mark.asyncio
async def test_post_filter_does_not_claim_native_count_is_filtered_total(monkeypatch):
    provider = SimpleNamespace(
        search_parts_page=AsyncMock(
            return_value={
                "results": [{"assembly": True}, {"assembly": False}],
                "population_count": 50,
                "has_more": True,
            }
        )
    )
    monkeypatch.setattr(parts, "get_data_provider", lambda: provider)
    result = await parts.search_parts(
        query="bearing", is_active=None, is_assembly=True, include_coverage=True
    )
    assert result["population_count"] is None
    assert result["complete_population"] is False
    assert result["returned_count"] == 1


@pytest.mark.asyncio
async def test_bound_keyword_scope_prevents_inferred_category_and_filter_drift(monkeypatch):
    from ai.core.tools.invocation_guard import bind_capability_run, capability_run_context

    rows = [{"pk": index, "active": index % 2 == 0} for index in range(5)]
    provider = SimpleNamespace(
        search_parts_page=AsyncMock(
            return_value={"results": rows, "population_count": 5, "has_more": False}
        )
    )
    monkeypatch.setattr(parts, "get_data_provider", lambda: provider)
    before = capability_run_context.get()
    with bind_capability_run(
        workflow="wf8",
        modality="text",
        selected_tools=[parts.search_parts],
        catalog_search_query="bearing",
    ):
        result = await parts.search_parts(
            query="bearings", category_id=7, has_stock=True, is_assembly=True
        )
    assert capability_run_context.get() is before
    assert result["results"] == rows and result["complete_population"]
    assert result["filters"]["query"] == "bearing"
    assert provider.search_parts_page.call_args.kwargs == {
        "query": "bearing",
        "category": None,
        "active": None,
        "limit": 25,
        "offset": 0,
    }


def _structured_model(view, intent, *, broken):
    calc = view["calculations"][0]
    groups = [row["fact_id"] for row in view["facts"] if row["kind"] == "group_row"]
    profile = [row["fact_id"] for row in view["facts"] if row["kind"] == "dataset_profile"]

    def claim(claim_id, template, facts):
        return {
            "claim_id": claim_id,
            "claim_role": "answer",
            "claim_type": "calculation",
            "evidence_classification": "calculated",
            "fact_refs": facts,
            "calculation_output_refs": [calc["calculation_id"]],
            "evidence_refs": [calc["evidence_set"]],
            "entity_refs": [],
            "render_template": template,
            "paraphrase": "",
        }

    if intent == "trend_analysis":
        claims = [claim("c1", "analysis.timeline_breakdown", groups)]
        facets = [{"name": "timeline", "status": "answered", "claim_ids": ["c1"]}]
    else:
        claims = [
            claim("c1", "analysis.aggregate_summary", profile),
            claim("c2", "analysis.group_breakdown", groups),
        ]
        facets = [
            {"name": "profile", "status": "answered", "claim_ids": ["c1"]},
            {"name": "aggregate", "status": "answered", "claim_ids": ["c2"]},
        ]
    facets.append({"name": "limitations", "status": "not_applicable", "claim_ids": []})
    if broken == "absence" and intent == "trend_analysis":
        claims[0]["render_template"] = "analysis.absence_of_records"
        claims[0]["fact_refs"] = [
            row["fact_id"]
            for row in view["facts"]
            if row["kind"] == "group_row" and row["values"].get("event_count") == "0"
        ]
    elif broken is True:
        claims[0]["render_template"] = "analysis.record_line"
    return SynthesisClaimSet.model_validate_json(
        json.dumps({"facets": facets, "claims": claims, "assumptions": [], "unknowns": []})
    )


@pytest.mark.parametrize("broken_model", [False, True, "absence"])
def test_four_record_scoped_charts_model_recovery_identity_and_authority(monkeypatch, broken_model):
    ensure_island_tables()
    suffix = os.urandom(5).hex()
    user = get_user_model().objects.create_user(username=f"followup-{suffix}")
    grant_work_view(user)
    clients = [
        Client.objects.create(code=f"followup-{suffix}-{i}", name=f"Fixture client {suffix} {i}")
        for i in range(2)
    ]
    grant = ClientScopeGrant.objects.create(user=user, client=clients[0])
    machine = AssetMachine.objects.create(
        name=f"Aeration Blower No. 2 {suffix}",
        model="Turbo Generation 5",
        serial=f"TC-AER-BLW-002-{suffix}",
        location="Fixture plant / Aeration",
        client=clients[0],
    )
    other = AssetMachine.objects.create(name=f"Other client machine {suffix}", client=clients[1])
    for index, status in enumerate(["planned", "completed", "completed", "completed"]):
        row = WorkOrder.objects.create(
            title=f"Recorded work {index}",
            machine=machine,
            lifecycle_status=status,
            is_active=status != "completed",
        )
        WorkOrder.objects.filter(pk=row.pk).update(
            created_at=datetime(2026, 7, 26, 3, index, tzinfo=UTC)
        )
    WorkOrder.objects.create(title="Other client work", machine=other)
    settings = config.get_settings().model_copy(
        update={
            "feature_chat_presentation": True,
            "feature_ai_analysis_router_enforce": True,
            "evidence_gate_mode": "enforce",
            "feature_ai_thread_scope_enforce": False,
            "aimms_analysis_intent_holdback": "record_retrieval,manual_fact,source_inventory,fleet_aggregate,manual_wo_comparison",
        }
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    window = plans.default_trend_window
    monkeypatch.setattr(
        plans,
        "default_trend_window",
        lambda _today, *, months: window(date(2026, 10, 25), months=months),
    )
    monkeypatch.setattr(
        executor,
        "synthesize_claims",
        lambda view, intent, **_kwargs: _structured_model(view, intent, broken=broken_model),
    )
    context = _context()
    principal = _principal(str(user.pk))
    repository = ThreadRepository(user.pk, context.server_policy_key)

    def no_legacy():
        raise AssertionError("Scoped graphics must retain the validated executor")

    service = NormalizedTurnService(workflow_factory=no_legacy)

    async def submit(thread, prompt, key):
        token = principal_context.set(principal)
        try:
            return await service.process(
                actor=principal,
                thread_id=thread.pk,
                content=prompt,
                modality="text",
                trusted_context=context,
                modality_metadata={},
                idempotency_key=key,
                correlation_id=context.correlation_id,
                emitter=InMemoryEventEmitter(),
            )
        finally:
            principal_context.reset(token)

    with override_settings(
        AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
        AIMMS_MACHINE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER=fixture_client_resolver,
        AIMMS_PLANT_TIMEZONE="UTC",
    ):
        for case in ["trend", "status"]:
            prompt = f"For machine ID {machine.pk}, " + (
                "plot the recorded monthly work-order counts for the last 12 months as an actual line chart with a supporting data table. Do not modify anything."
                if case == "trend"
                else "show a visual comparison of open and completed work-order counts using existing recorded data. Include exact counts and coverage. Do not change any records."
            )
            for replay in range(2):
                thread, _ = repository.get_or_create(title=f"{case} {replay} {suffix}")
                result = asyncio.run(submit(thread, prompt, "first-attempt"))
                saved = ChatTurn.objects.get(pk=result.turn_id).canonical_result
                assert saved["response_state"] == "complete", saved
                assert saved["evidence_gate"]["verdict"] == "pass"
                assert saved["evidence_gate"]["synthesis"] == (
                    "deterministic_fallback"
                    if broken_model is True or (broken_model == "absence" and case == "trend")
                    else "model"
                )
                dataset = saved["presentation_data"][0]
                assert saved["evidence_analysis"]["coverage"]["population_count"] == 4
                if case == "trend":
                    assert "No matching records exist" not in saved["message"]
                    assert len(dataset["samples"]) == 12
                    assert sum(point["value"] for point in dataset["samples"]) == 4
                    assert (
                        next(
                            point["value"]
                            for point in dataset["samples"]
                            if point["observed_at"].startswith("2026-07-01")
                        )
                        == 4
                    )
                else:
                    assert dataset["population_count"] == 4
                    assert [point["value"] for point in dataset["points"]] == [1, 3, 0]
                assert not any(
                    entity["model"] == "assetmachine" and entity["pk"] != machine.pk
                    for entity in saved.get("entities", [])
                )
                assert asyncio.run(submit(thread, prompt, "first-attempt")).replayed
                assert ChatTurn.objects.get(pk=result.turn_id).canonical_result == saved
                thread.refresh_from_db()
                assert thread.analysis_scope_version == 0 and thread.focus_version == 0
                if (
                    directory := os.environ.get("AIMMS_TEST_PRESENTATION_ARTIFACT_DIR")
                ) and broken_model != "absence":
                    path = Path(directory)
                    path.mkdir(parents=True, exist_ok=True)
                    (
                        path / f"followup-{case}-{'recovered' if broken_model else 'model'}.json"
                    ).write_text(json.dumps(saved))
        identities = [
            f"What model, serial number, and recorded location are saved for Aeration Blower No. 2 (machine ID {machine.pk})? Cite the supporting record.",
            "Show the selected machine's recorded model, serial number, and location in a read-only asset summary card. Use the existing machine record.",
            f"For Aeration Blower No. 2, machine ID {machine.pk}, show the saved model, serial number, and location as a read-only asset card.",
            f"Show the recorded identity for this machine, Aeration Blower No. 2 (ID {machine.pk}), in a read-only asset summary card.",
            f"For Aeration Blower No. 2, machine ID {machine.pk}, show the saved model, serial number, and location as a read-only asset card.",
        ]
        for index, prompt in enumerate(identities):
            thread, _ = repository.get_or_create(title=f"Identity {index} {suffix}")
            if index == 1:
                repository.set_focus(
                    thread.pk, {"kind": "machine", "id": machine.pk}, expected_focus_version=0
                )
            result = asyncio.run(submit(thread, prompt, "identity"))
            saved = ChatTurn.objects.get(pk=result.turn_id).canonical_result
            assert saved["entities"][0]["label"] == machine.name
            assert "[UNTRUSTED-CONTENT-" not in saved["message"]
            assert "[UNTRUSTED-CONTENT-BEGIN]" in machine_identity(machine)["name"]
            assert "Turbo Generation 5" in saved["message"] and "TC-AER-BLW-002" in saved["message"]
            if directory := os.environ.get("AIMMS_TEST_PRESENTATION_ARTIFACT_DIR"):
                (Path(directory) / f"followup-identity-{index}.json").write_text(json.dumps(saved))
        limited = asyncio.run(
            submit(
                thread,
                f"For machine ID {machine.pk}, show the monthly recorded work-order trend for the past 37 months.",
                "limit",
            )
        )
        saved = ChatTurn.objects.get(pk=limited.turn_id).canonical_result
        assert "1-36" in saved["message"] and "quarter" not in saved["message"]
        assert not saved.get("presentation_data")
        if directory := os.environ.get("AIMMS_TEST_PRESENTATION_ARTIFACT_DIR"):
            (Path(directory) / "followup-range-limit.json").write_text(json.dumps(saved))
        denied = asyncio.run(
            submit(
                thread,
                f"For machine ID {other.pk}, show a visual comparison of open and completed work-order counts.",
                "cross-client",
            )
        )
        assert not ChatTurn.objects.get(pk=denied.turn_id).canonical_result.get("presentation_data")
        grant.delete()
        denied = asyncio.run(
            submit(
                thread,
                f"For machine ID {machine.pk}, show the monthly recorded work-order trend for the last 12 months.",
                "revoked",
            )
        )
        assert not ChatTurn.objects.get(pk=denied.turn_id).canonical_result.get("presentation_data")
