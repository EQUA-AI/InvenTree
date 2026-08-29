"""S10 WP-A6: the analysis executor — buffered, validated, fail-honest."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.analysis import executor as executor_module
from ai.core.analysis.evidence import FactValue, coverage_fact, facts_from_work_order_row
from ai.core.analysis.executor import run_analysis


class RecordingEmitter:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def emit(self, event) -> None:
        self.events.append(event.to_dict() if hasattr(event, "to_dict") else dict(event.__dict__))


def _service():
    async def _call_sync(fn, *args, **kwargs):  # noqa: RUF029 - service contract is async
        return fn(*args, **kwargs)

    def _rehydrate(actor):
        return SimpleNamespace(pk=5, is_authenticated=True)

    return SimpleNamespace(_call_sync=_call_sync, _rehydrate_user_for_grounding=_rehydrate)


def _run(intent: str = "record_retrieval", emitter=None):
    return SimpleNamespace(
        actor=SimpleNamespace(user_pk="5"),
        thread=SimpleNamespace(pk="thread_1"),
        turn=SimpleNamespace(pk="turn_1"),
        trusted_context=SimpleNamespace(locale="en"),
        task_intent=SimpleNamespace(intent=SimpleNamespace(value=intent)),
        routing_content="how many open work orders are there?",
        emitter=emitter,
        analysis_scope=None,
        extras={},
        validation_result=None,
    )


def _row(work_order_id: int = 41, machine_id: int = 12) -> dict:
    return {
        "work_order_id": work_order_id,
        "reference": f"WO-{work_order_id:04d}",
        "title": "Replace coolant filter",
        "board_status": "in_progress",
        "lifecycle_status": "released",
        "work_order_type": "corrective",
        "priority": "high",
        "machine_id": machine_id,
        "machine": "Feed Pump East",
        "due_date": "2026-09-01",
        "created_at": "2026-08-20T08:00:00+00:00",
        "updated_at": "2026-08-21T08:00:00+00:00",
        "actual_started_at": None,
        "actual_completed_at": None,
    }


def _fake_records(user, store, *, scope):
    """Deterministic stand-in for the ORM page retrieval."""
    as_of = "2026-08-28T00:00:00+00:00"
    for row in (_row(41), _row(42)):
        facts_from_work_order_row(
            store, row, retrieval_id="ret_test", as_of=as_of, source_revision="snap_t"
        )
    coverage_fact(
        store,
        {"population_count": 2, "returned_count": 2, "complete_population": True},
        retrieval_id="ret_test",
        source_class="work_order",
        as_of=as_of,
    )
    pending = store.open_evidence_set(
        source_class="work_order",
        filters={"machine_ids": [12]},
        population_count=2,
        evaluated_count=2,
        complete_population=True,
        calculation={"operation": "count", "result": "2"},
    )
    pending.add_member("work_order", "41")
    pending.add_member("work_order", "42")
    pending.displayed_count = 2
    store.add_calculation(
        operation="count",
        input_refs=(),
        values={"count": FactValue("int", 2)},
        evidence_set_handle=pending.handle,
        complete_population=True,
    )
    store.record_envelope({"retrieval_id": "ret_test", "source_class": "work_order"})
    store.set_primary_coverage({
        "population_count": 2,
        "returned_count": 2,
        "complete_population": True,
        "display_truncated": False,
        "date_field": "created_at",
        "timezone": "UTC",
        "filters": [],
        "as_of": as_of,
        "snapshot_label": None,
        "excluded_null_date_count": None,
        "incomplete_reason": None,
    })
    return {}


@pytest.fixture(autouse=True)
def _executor_seams(monkeypatch):
    """Deterministic seams: no ORM, no provider, permissive reauth."""
    monkeypatch.setattr(executor_module, "_retrieve_records", _fake_records)
    monkeypatch.setattr(executor_module, "synthesize_claims", lambda *_a, **_k: None)
    monkeypatch.setattr(executor_module, "_reauthorize", lambda _user, _store: True)
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: SimpleNamespace(analysis_turn_deadline_s=5.0, analysis_synthesis_timeout_s=0.5),
    )


def test_happy_path_produces_a_validated_complete_v2() -> None:
    emitter = RecordingEmitter()
    outcome = asyncio.run(run_analysis(_service(), _run(emitter=emitter)))

    assert outcome.turn_state == "complete"
    response = outcome.response
    assert response.response_version == 2
    assert "2 matching records" in response.detailed_response
    assert "[1]" in response.detailed_response
    assert response.payload.claims
    # The attachment is the consolidated wire object.
    attachment = outcome.attachment
    assert attachment["response_state"] == "complete"
    assert attachment["coverage"]["population_count"] == 2
    assert attachment["claims"][0]["citation_ordinals"] == [1]
    assert attachment["citations"][0]["ordinal"] == 1
    assert attachment["no_data_reason"] is None
    # Validated chips only; the internal join ref never leaves.
    assert outcome.entities
    assert all("ref" not in entity for entity in outcome.entities)
    # One evidence set rides to the terminal write.
    assert len(outcome.evidence_set_specs) == 1
    assert outcome.evidence_set_specs[0]["members"]
    assert outcome.gate["verdict"] == "pass"
    assert outcome.gate["synthesis"] == "deterministic"


def test_only_content_free_progress_precedes_the_result() -> None:
    """§13.2 wire clause at the executor boundary: never emitted, anywhere."""
    emitter = RecordingEmitter()
    outcome = asyncio.run(run_analysis(_service(), _run(emitter=emitter)))

    stages = []
    for event in emitter.events:
        blob = json.dumps(event)
        # No record identifiers, no counts, no prose leave before validation.
        assert "WO-0041" not in blob
        assert "Feed Pump" not in blob
        data = event.get("data") or event
        assert (event.get("event_type") or event.get("type")) in (
            "STATE_DELTA",
            "EventType.STATE_DELTA",
        ) or "STATE_DELTA" in str(event.get("event_type"))
        stages.append(data.get("stage"))
    assert stages == ["confirming_scope", "reviewing_records", "validating_evidence"]
    assert [e["stage"] for e in outcome.emitted_events] == stages


def test_shadow_mode_emits_nothing_and_still_validates() -> None:
    emitter = RecordingEmitter()
    outcome = asyncio.run(run_analysis(_service(), _run(emitter=emitter), shadow=True))
    assert emitter.events == []
    assert outcome.gate["verdict"] == "pass"
    assert outcome.emitted_events == []


def test_retrieval_failure_abstains_with_typed_reasons(monkeypatch) -> None:
    def _broken(user, store, *, scope):
        raise RuntimeError("db down")

    monkeypatch.setattr(executor_module, "_retrieve_records", _broken)
    outcome = asyncio.run(run_analysis(_service(), _run()))
    assert outcome.turn_state == "incomplete"
    assert outcome.attachment["no_data_reason"] == "retrieval_failure"
    assert outcome.response.incomplete_reasons
    assert outcome.evidence_set_specs == []
    assert "no conclusion was produced" in outcome.response.detailed_response.lower()


def test_deadline_with_no_facts_is_a_typed_abstention(monkeypatch) -> None:
    service = _service()

    async def _slow_call_sync(fn, *args, **kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: SimpleNamespace(analysis_turn_deadline_s=0.05, analysis_synthesis_timeout_s=0.5),
    )
    run = _run()

    async def _go():
        service._call_sync = _slow_call_sync_first(service._call_sync, _slow_call_sync)
        return await run_analysis(service, run)

    def _slow_call_sync_first(original, slow):
        calls = {"n": 0}

        async def wrapper(fn, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:  # the user rehydration stays fast
                return await original(fn, *args, **kwargs)
            return await slow(fn, *args, **kwargs)

        return wrapper

    outcome = asyncio.run(_go())
    assert outcome.turn_state == "incomplete"
    assert any(reason.code == "retrieval_timeout" for reason in outcome.response.incomplete_reasons)


def test_revocation_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(executor_module, "_reauthorize", lambda _user, _store: False)
    outcome = asyncio.run(run_analysis(_service(), _run()))
    assert outcome.turn_state == "failed"
    assert outcome.gate["verdict"] == "fail_closed"
    assert "reauthorization_failed" in outcome.gate["codes"]
    assert "withheld" in outcome.response.detailed_response
    assert outcome.evidence_set_specs == []
    assert outcome.entities == []


def test_empty_complete_population_reports_the_no_data_reason(monkeypatch) -> None:
    def _empty(user, store, *, scope):
        coverage_fact(
            store,
            {"population_count": 0, "returned_count": 0, "complete_population": True},
            retrieval_id="ret_test",
            source_class="work_order",
            as_of="2026-08-28T00:00:00+00:00",
        )
        store.set_primary_coverage({
            "population_count": 0,
            "returned_count": 0,
            "complete_population": True,
            "display_truncated": False,
            "date_field": "created_at",
            "timezone": "UTC",
            "filters": [],
            "as_of": "2026-08-28T00:00:00+00:00",
            "snapshot_label": None,
            "excluded_null_date_count": None,
            "incomplete_reason": None,
        })
        return {}

    monkeypatch.setattr(executor_module, "_retrieve_records", _empty)
    outcome = asyncio.run(run_analysis(_service(), _run()))
    # Zero answers -> the records facet is unavailable -> honest abstention,
    # with the DISTINCT no-data reason for "proven empty" (§8.8).
    assert outcome.attachment["no_data_reason"] == "complete_population_no_matches"


def test_model_synthesis_is_organization_only_and_still_validated(monkeypatch) -> None:
    """A model set that references a ghost fact loses to the validator."""
    from ai.core.analysis.schemas import SynthesisClaimSet

    poisoned = SynthesisClaimSet.model_validate_json(
        json.dumps({
            "facets": [{"name": "records", "status": "answered", "claim_ids": ["c1"]}],
            "claims": [
                {
                    "claim_id": "c1",
                    "claim_role": "answer",
                    "claim_type": "direct_source_fact",
                    "evidence_classification": "documented",
                    "fact_refs": ["fact_ghost"],
                    "calculation_output_refs": [],
                    "evidence_refs": [],
                    "entity_refs": [],
                    "render_template": "analysis.inference_note",
                    "paraphrase": "everything is fine",
                }
            ],
            "assumptions": [],
            "unknowns": [],
        })
    )
    monkeypatch.setattr(executor_module, "synthesize_claims", lambda *_a, **_k: poisoned)
    outcome = asyncio.run(run_analysis(_service(), _run()))
    # The single answer claim is unresolvable -> dropped -> abstention.
    assert outcome.turn_state == "incomplete"
    assert outcome.gate["verdict"] in ("abstain", "downgrade")
