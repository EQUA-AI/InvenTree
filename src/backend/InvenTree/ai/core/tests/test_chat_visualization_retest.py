"""Reported first-attempt shapes, real scoped counts and immutable chart replay."""

# ruff: noqa: E402
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
import django

django.setup()

import pytest
from ai.core import config
from ai.core.analysis import executor
from ai.core.analysis.intent import classify_rules
from ai.core.auth import principal_context
from ai.core.presentation.provided import provided_visualization
from ai.core.streaming import InMemoryEventEmitter
from ai.core.tests.focus_test_db import ensure_island_tables, grant_work_view
from ai.core.tests.test_capability_invocation_guard import _principal
from ai.core.tests.test_normalized_turn_service import _context
from ai.core.tools import capabilities
from ai.core.turn.targets import machine_candidates
from ai.core.turn_service import NormalizedTurnService
from aichat.models import ChatTurn
from aichat.services.threads import ThreadRepository
from aichat.tests.memory_scope_fixtures import fixture_client_resolver
from assets.models import AssetMachine, Client, ClientScopeGrant
from django.contrib.auth import get_user_model
from django.test import override_settings
from tasks.models import WorkOrder

CASES = json.loads((Path(__file__).parents[1] / "evals/chat_visualization_retest.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_reported_read_only_prompts_never_select_effects(case, monkeypatch):
    monkeypatch.setattr(capabilities, "category_lexicon", frozenset)
    monkeypatch.setattr(capabilities, "machine_lexicon", frozenset)
    assert capabilities._write_intent(case["prompt"].casefold()) is None
    decision = classify_rules(case["prompt"])
    assert decision is None or decision.effect.value == "read_only"
    if case["id"] in {"PART-SEARCH", "PART-954"}:
        profile = frozenset(
            pair
            for entry in capabilities.capability_catalog()
            for pair in (*entry.authorization.all_of, *entry.authorization.any_of)
        )
        monkeypatch.setattr(capabilities, "stable_tool_prefix_enabled", lambda: True)
        selection = capabilities.select_capabilities(
            case["prompt"],
            task_intent=decision.intent.value,
            profile=profile,
            authenticated=True,
            context={"tool_pack_history": ["machines.read", "maintenance.read"]},
        )
        assert {"get_part", "search_parts"} <= set(selection.tool_ids)
        assert "get_machine_overview" not in selection.tool_ids


@pytest.mark.parametrize(
    "text",
    [
        "Draw a synthetic bar chart for A=1. Mark work order 24 completed.",
        "Show a line chart for A=1. Create a part.",
        "Do not change anything. Send the chart to the customer.",
    ],
)
def test_separate_business_actions_keep_the_write_boundary(text):
    assert capabilities._write_intent(text.casefold()) is not None
    assert classify_rules(text).effect.value == "effect_request"


@pytest.mark.parametrize(
    "case_id,expected",
    [
        ("SYNTHETIC-LINE", [("Jan", 4), ("Feb", 0), ("Mar", 7), ("Apr", 9)]),
        ("SYNTHETIC-BAR", [("Pump A", 4), ("Pump B", 7), ("Pump C", 9)]),
        ("SYNTHETIC-PIE", [("Planned", 6), ("Corrective", 3), ("Inspection", 1)]),
    ],
)
def test_exact_supplied_values_are_not_retrieved_or_inferred(case_id, expected):
    text = next(case["prompt"] for case in CASES if case["id"] == case_id)
    message, datasets = provided_visualization(text, turn_id="fixture")
    assert len(datasets) == 1, message
    data = datasets[0]
    assert [(point["label"], point["value"]) for point in data["points"]] == expected
    assert data["origin"] == "user_supplied"
    assert data["complete_population"] is None
    assert "not recorded" in message


@pytest.mark.parametrize(
    "text",
    [
        "Synthetic pie chart for A=0, B=0.",
        "Synthetic pie chart for A=-1, B=4.",
        "Synthetic line chart for A=1, A=2.",
        "Synthetic line chart for A=1e999, B=4.",
        "Synthetic line chart for A=1, B=missing.",
        "Synthetic radar chart for A=1, B=2.",
    ],
)
def test_invalid_or_unsupported_graph_is_an_explicit_limitation(text):
    message, datasets = provided_visualization(text, turn_id="fixture")
    assert not datasets
    assert "chart" in message.lower()


def test_ids_are_typed_and_do_not_use_model_or_machine_name_numbers():
    assert machine_candidates("Aeration Blower No. 2") == frozenset()
    assert machine_candidates(
        "Show this machine, Aeration Blower No. 2 (ID 24), as an asset card"
    ) == {24}
    assert machine_candidates("For machine ID 24, plot work orders") == {24}
    assert machine_candidates("For part ID 24") == frozenset()


def test_supplied_labels_and_numeric_precision_are_preserved():
    _, data = provided_visualization(
        "Synthetic bar chart comparing Room for Pump A=4, Pump B=7.", turn_id="fixture"
    )
    assert data[0]["points"][0]["label"] == "Room for Pump A"
    _, rejected = provided_visualization(
        "Synthetic line chart for A=0.1234567890123456789, B=2.", turn_id="fixture"
    )
    assert not rejected


def test_full_population_machine_charts_and_supplied_data_replay(monkeypatch):
    ensure_island_tables()
    suffix = os.urandom(5).hex()
    user = get_user_model().objects.create_user(username=f"charts-{suffix}")
    grant_work_view(user)
    clients = [
        Client.objects.create(code=f"charts-{suffix}-{i}", name=f"Chart plant {suffix} {i}")
        for i in range(2)
    ]
    grant = ClientScopeGrant.objects.create(user=user, client=clients[0])
    machines = [
        AssetMachine.objects.create(
            name=f"Blower {i}",
            client=client,
            model="Turbo 5",
            serial=f"SER-{suffix}-{i}",
            location="Plant A",
        )
        for i, client in enumerate([clients[0], clients[0], clients[1]])
    ]
    for status, count in [("in_progress", 29), ("completed", 7), ("canceled", 3)]:
        for index in range(count):
            WorkOrder.objects.create(
                title=f"Chart inspection {index}", machine=machines[0], lifecycle_status=status
            )
    for machine in machines[1:]:
        WorkOrder.objects.create(title="Other machine", machine=machine)
    configured = config.get_settings().model_copy(
        update={
            "feature_chat_presentation": True,
            "feature_ai_analysis_router_enforce": True,
            "evidence_gate_mode": "enforce",
            "feature_ai_thread_scope_enforce": True,
            "aimms_analysis_intent_holdback": "record_retrieval,manual_fact,source_inventory,fleet_aggregate,manual_wo_comparison",
        }
    )
    monkeypatch.setattr(config, "get_settings", lambda: configured)
    monkeypatch.setattr(executor, "synthesize_claims", lambda *_args, **_kwargs: None)
    context = _context()
    principal = _principal(str(user.pk))
    repository = ThreadRepository(user.pk, context.server_policy_key)
    thread, _ = repository.get_or_create(title="Chart qualification")

    def unexpected_legacy():
        raise AssertionError("A qualified chart must not fall back to model-authored text art")

    service = NormalizedTurnService(workflow_factory=unexpected_legacy)

    async def submit(prompt, key):
        token = principal_context.set(principal)
        try:
            result = await service.process(
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
            return result, result.turn_id
        finally:
            principal_context.reset(token)

    with override_settings(
        AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
        AIMMS_MACHINE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER=fixture_client_resolver,
        AIMMS_PLANT_TIMEZONE="America/Chicago",
    ):
        prompt = f"For machine ID {machines[0].pk}, show a visual comparison of open and completed work-order counts using recorded data. Do not change any records."
        _result, pk = asyncio.run(submit(prompt, "status"))
        saved = ChatTurn.objects.get(pk=pk).canonical_result
        assert saved["evidence_gate"]["verdict"] == "pass", saved
        chart = saved["presentation_data"][0]
        assert chart["population_count"] == 39
        assert [point["value"] for point in chart["points"]] == [29, 7, 3]
        assert "ChartSnapshot" in saved["presentation"]["program"]
        assert str(machines[0].pk) in chart["scope_label"]
        thread.refresh_from_db()
        assert thread.analysis_scope_version == 0
        assert thread.focus_version == 0
        identity_prompt = f"Show the recorded identity for this machine, Blower 0 (ID {machines[0].pk}), in a read-only asset summary card."
        _, identity_pk = asyncio.run(submit(identity_prompt, "identity"))
        identity = ChatTurn.objects.get(pk=identity_pk).canonical_result
        assert [entity["pk"] for entity in identity["entities"]] == [machines[0].pk]
        assert "Blower 0" in identity["message"]
        for expected in ["Turbo 5", f"SER-{suffix}-0", "Plant A"]:
            assert expected in identity["message"]
        assert "AssetSummary" in identity["presentation"]["program"]
        replay, _ = asyncio.run(submit(prompt, "status"))
        assert replay.replayed
        assert ChatTurn.objects.get(pk=pk).canonical_result == saved
        trend = f"For machine ID {machines[0].pk}, show the monthly recorded work-order trend for the past 6 months with a chart."
        _, trend_pk = asyncio.run(submit(trend, "trend"))
        canonical = ChatTurn.objects.get(pk=trend_pk).canonical_result
        assert len(canonical["presentation_data"][0]["samples"]) == 6
        assert sum(row["value"] for row in canonical["presentation_data"][0]["samples"]) == 39
        _, limit_pk = asyncio.run(submit(trend.replace("6 months", "37 months"), "window-limit"))
        limited = ChatTurn.objects.get(pk=limit_pk).canonical_result
        assert not limited.get("presentation_data")
        assert any(
            reason["code"] == "bucket_range_exceeded"
            for reason in limited["canonical_response"]["incomplete_reasons"]
        )
        configured.feature_ai_thread_scope_enforce = False
        _, no_scope_flag_pk = asyncio.run(submit(trend, "target-without-scope-rollout"))
        assert (
            sum(
                row["value"]
                for row in ChatTurn.objects.get(pk=no_scope_flag_pk).canonical_result[
                    "presentation_data"
                ][0]["samples"]
            )
            == 39
        )
        configured.feature_ai_thread_scope_enforce = True
        synthetic = next(case["prompt"] for case in CASES if case["id"] == "SYNTHETIC-PIE")
        _, provided_pk = asyncio.run(submit(synthetic, "provided"))
        assert (
            ChatTurn.objects.get(pk=provided_pk).canonical_result["presentation_data"][0]["origin"]
            == "user_supplied"
        )
        if directory := os.environ.get("AIMMS_TEST_PRESENTATION_ARTIFACT_DIR"):
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            (path / "recorded-status.json").write_text(json.dumps(saved))
            (path / "recorded-trend.json").write_text(json.dumps(canonical))
            (path / "provided-pie.json").write_text(
                json.dumps(ChatTurn.objects.get(pk=provided_pk).canonical_result)
            )
        repository.set_scope(
            thread.pk,
            {"mode": "explicit_assets", "machine_ids": [machines[1].pk]},
            expected_version=0,
        )
        _, conflict_pk = asyncio.run(submit(prompt, "outside-confirmed-scope"))
        conflict = ChatTurn.objects.get(pk=conflict_pk).canonical_result
        assert not conflict.get("presentation_data")
        assert "outside" in conflict["message"]
        thread.refresh_from_db()
        assert thread.analysis_scope_version == 1
        repository.set_scope(thread.pk, {"mode": "all_authorized_assets"}, expected_version=1)
        grant.delete()
        _, denied_pk = asyncio.run(submit(prompt, "revoked"))
        denied = ChatTurn.objects.get(pk=denied_pk).canonical_result
        assert not denied.get("presentation_data")
        assert "unavailable" in denied["message"]
