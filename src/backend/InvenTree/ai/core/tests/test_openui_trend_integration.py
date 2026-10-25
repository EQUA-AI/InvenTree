"""Real scoped ORM → enforced analysis → presentation → durable replay.

Only the optional language-model organizer is replaced: the deterministic
fallback, authorization, query, snapshot, validator and turn service are real.
No provider, customer records or deployment flags are changed by these tests.
"""

# ruff: noqa: E402
from __future__ import annotations

import asyncio
import datetime
import json
import os
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
import django

django.setup()

from ai.core import config
from ai.core.analysis import executor
from ai.core.auth import principal_context
from ai.core.streaming import InMemoryEventEmitter
from ai.core.tests.focus_test_db import ensure_island_tables, grant_work_view
from ai.core.tests.test_capability_invocation_guard import _principal
from ai.core.tests.test_normalized_turn_service import _context
from ai.core.turn_service import NormalizedTurnService
from aichat.models import ChatTurn
from aichat.services.threads import ThreadRepository
from aichat.tests.memory_scope_fixtures import fixture_client_resolver
from assets.models import AssetMachine, Client, ClientScopeGrant
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from tasks.models import WorkOrder


def test_real_twelve_month_series_is_validated_presented_and_replayed(monkeypatch):
    ensure_island_tables()
    suffix = os.urandom(5).hex()
    user = get_user_model().objects.create_user(username=f"trend-{suffix}")
    grant_work_view(user)
    clients = [
        Client.objects.create(code=f"trend-{suffix}-{i}", name=f"Plant {i}") for i in range(2)
    ]
    ClientScopeGrant.objects.create(user=user, client=clients[0])
    machines = [
        AssetMachine.objects.create(name=f"Pump {i}", client=c) for i, c in enumerate(clients)
    ]
    now = timezone.now()
    for index, machine in enumerate([machines[0], machines[0], machines[1]]):
        order = WorkOrder.objects.create(title=f"Recorded inspection {index}", machine=machine)
        WorkOrder.objects.filter(pk=order.pk).update(
            created_at=now - datetime.timedelta(days=index)
        )
    settings = config.get_settings().model_copy(
        update={
            "feature_chat_presentation": True,
            "feature_ai_analysis_router_enforce": True,
            "evidence_gate_mode": "enforce",
            "aimms_analysis_intent_holdback": "record_retrieval,manual_fact,source_inventory,fleet_aggregate,manual_wo_comparison",
        }
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(executor, "synthesize_claims", lambda *_args, **_kwargs: None)
    context = _context()
    principal = _principal(str(user.pk))
    repository = ThreadRepository(user.pk, context.server_policy_key)
    thread, _ = repository.get_or_create(title="Recorded work-order trend")

    def unexpected_legacy():
        raise AssertionError("The qualified trend must use the validated analysis rail")

    service = NormalizedTurnService(workflow_factory=unexpected_legacy)
    query = (
        "Show the monthly trend of recorded work orders over the last 12 months, "
        "with a chart and supporting data table. State the date interval, scope, timezone, "
        "and data age. Use only saved records; do not change anything."
    )

    async def submit():
        token = principal_context.set(principal)
        try:
            events = []

            class Collector:
                async def handle(self, event):
                    events.append(event.to_dict())

            emitter = InMemoryEventEmitter()
            await emitter.subscribe(Collector())
            result = await service.process(
                actor=principal,
                thread_id=thread.pk,
                content=query,
                modality="text",
                trusted_context=context,
                modality_metadata={},
                idempotency_key=f"trend-{suffix}",
                correlation_id=context.correlation_id,
                emitter=emitter,
            )
            return result, events
        finally:
            principal_context.reset(token)

    with override_settings(
        AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
        AIMMS_MACHINE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER=fixture_client_resolver,
        AIMMS_PLANT_TIMEZONE="America/Chicago",
    ):
        result, events = asyncio.run(submit())
        canonical = ChatTurn.objects.get(pk=result.turn_id).canonical_result
        assert canonical["workflow_used"] == "analysis_executor"
        assert canonical["evidence_gate"]["verdict"] == "pass"
        dataset = canonical["presentation_data"][0]
        assert dataset["timezone"] == "America/Chicago"
        assert len(dataset["samples"]) == 12
        assert sum(sample["value"] for sample in dataset["samples"]) == 2
        assert any(sample["value"] == 0 for sample in dataset["samples"])
        assert "TrendPreview" in canonical["presentation"]["program"]
        coverage = canonical["evidence_analysis"]["coverage"]
        assert coverage["date_field"] == "created_at"
        assert coverage["timezone"] == "America/Chicago"
        assert coverage["as_of"]
        assert any(value.startswith("from: ") for value in coverage["filters"])
        assert any(value.startswith("to: ") for value in coverage["filters"])
        assert any(event.get("kind") == "presentation" for event in events)
        replay, replay_events = asyncio.run(submit())
        assert replay.replayed is True
        assert any(
            event.get("presentation") == canonical["presentation"] for event in replay_events
        )
        assert ChatTurn.objects.get(pk=result.turn_id).canonical_result == canonical
        if directory := os.environ.get("AIMMS_TEST_PRESENTATION_ARTIFACT_DIR"):
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            (path / "recorded-trend.json").write_text(json.dumps(canonical))
