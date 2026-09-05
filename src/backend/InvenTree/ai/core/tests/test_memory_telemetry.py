"""M1 PR F (plan §9.5, §9.8, GR-33/40): the memory layer's telemetry surfaces."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core import tracing, usage
from ai.core.quota import slo
from ai.core.turn_service import NormalizedTurnService

SUMMARY = 'Pump 3 diagnosis\n{"label": "Pump 3 diagnosis", "machine_facts": ["seal worn"]}'


def test_stage_targets_and_breach_codes():
    assert slo.STAGE_TARGETS["memory_context"] == (0.10, 0.15, 0.40)
    assert slo.stage_breach(0.05, "memory_context") is None
    assert slo.stage_breach(0.12, "memory_context") == "p50"
    assert slo.stage_breach(0.2, "memory_context") == "p95"
    assert slo.stage_breach(0.5, "memory_context") == "hard"
    assert slo.stage_breach(9.0, "unknown_stage") is None


def test_cache_write_tokens_are_canonical_and_summed():
    assert "cache_write_tokens" in usage.CANONICAL_TOKEN_KEYS
    ledger = usage.TurnUsageLedger()
    ledger.record("wf8_lookup", {"input_token_count": 1400, "cache_write_input_token_count": 1024})
    ledger.record("luna_diagnostics", {"input_tokens": 50, "cache_creation_input_tokens": 10})
    ledger.record("routing_classifier", {"input_tokens": 20})  # provider omits the counter
    totals = ledger.totals()
    assert totals["cache_write_tokens"] == 1034
    assert totals["input_tokens"] == 1470


def test_maf_usage_extractor_surfaces_the_nested_cache_write_counter():
    response = SimpleNamespace(
        usage_details={
            "input_token_count": 1400,
            "output_token_count": 12,
            "prompt_tokens_details": {"cached_tokens": 1024, "cache_write_tokens": 1024},
        }
    )
    metrics = usage.maf_response_usage_metrics(response)
    assert metrics["cache_write_input_token_count"] == 1024
    ledger = usage.TurnUsageLedger()
    ledger.record("wf8_lookup", metrics)
    assert ledger.events[0]["cache_write_tokens"] == 1024
    # Absent stays absent: no zero is invented.
    plain = usage.maf_response_usage_metrics(
        SimpleNamespace(usage_details={"input_token_count": 5})
    )
    assert "cache_write_input_token_count" not in plain


class _TestTurnService(NormalizedTurnService):
    @staticmethod
    async def _call_sync(function, *args, **kwargs):
        return function(*args, **kwargs)


class _Repository:
    def __init__(self):
        self._thread = SimpleNamespace(pk="t", summary=SUMMARY, summary_through_sequence=2)
        self._messages = [
            SimpleNamespace(
                role="user" if i % 2 else "assistant", content=f"message {i}", sequence=i
            )
            for i in range(1, 7)
        ]

    def get(self, thread_id):
        return self._thread

    def recent_messages(self, thread_id, limit, exclude_latest=0):
        rows = self._messages[: len(self._messages) - exclude_latest]
        return rows[-limit:]


def _run():
    return SimpleNamespace(
        context_bundle=None,
        repository=_Repository(),
        thread=SimpleNamespace(pk="t"),
        turn=SimpleNamespace(pk="turn_1"),
        task_intent=None,
        actor=SimpleNamespace(is_staff=False, is_superuser=False),
        question_resolution=None,
        modality="text",
        trusted_context=SimpleNamespace(locale="en"),
        server_pinned_workflow=None,
        correlation_id="00000000-0000-0000-0000-000000000001",
    )


def test_memory_context_span_carries_only_allowlisted_counts(monkeypatch):
    from ai.core.config import Settings

    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: Settings(_env_file=None, FEATURE_THREAD_COMPACTION=True, CHAT_HISTORY_MESSAGES=12),
    )
    spans: list[tuple[str, dict]] = []
    attrs: dict = {}

    class _Span:
        def set_attribute(self, key, value):
            attrs[key] = value

    from contextlib import contextmanager

    @contextmanager
    def fake_turn_span(name, **kwargs):
        spans.append((name, kwargs))
        yield _Span()

    monkeypatch.setattr(tracing, "turn_span", fake_turn_span)
    service = _TestTurnService(workflow_factory=lambda: None)
    bundle = asyncio.run(service.build_context_bundle(_run()))
    assert spans == [
        ("aimms.memory_context", {"correlation_id": "00000000-0000-0000-0000-000000000001"})
    ]
    assert attrs["aimms.memory_db_round_trips"] == 2  # legacy two-hop fake repository
    assert attrs["aimms.memory_degrade_reason"] == "none"
    assert attrs["aimms.topology_depth"] == 0
    assert isinstance(attrs["aimms.memory_wall_ms"], int)
    assert set(attrs) <= tracing._ALLOWED_ATTRS
    assert "aimms.memory_stage_breach" not in attrs  # far inside p50 on a fake
    assert bundle.summary_item is not None


def test_stage_breach_is_reported_when_the_recall_is_slow(monkeypatch):
    from ai.core.config import Settings

    monkeypatch.setattr("ai.core.config.get_settings", lambda: Settings(_env_file=None))
    attrs: dict = {}

    class _Span:
        def set_attribute(self, key, value):
            attrs[key] = value

    from contextlib import contextmanager

    @contextmanager
    def fake_turn_span(name, **kwargs):
        yield _Span()

    monkeypatch.setattr(tracing, "turn_span", fake_turn_span)

    class _SlowService(_TestTurnService):
        @staticmethod
        async def _call_sync(function, *args, **kwargs):
            await asyncio.sleep(0.12)
            return function(*args, **kwargs)

    asyncio.run(_SlowService(workflow_factory=lambda: None).build_context_bundle(_run()))
    assert attrs["aimms.memory_stage_breach"] in ("p50", "p95")
    assert attrs["aimms.memory_wall_ms"] >= 100
